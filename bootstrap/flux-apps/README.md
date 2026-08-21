# flux-apps

A Crossplane **v2** Configuration that deploys application [Flux](https://fluxcd.io/) Kustomizations onto a target cluster from a namespaced `FluxApps` XR (group `config.stuttgart-things.com`).

The Composition is a thin [`function-kcl`](https://github.com/crossplane-contrib/function-kcl) wrapper around the [`xplane-flux-apps`](https://github.com/stuttgart-things/kcl/tree/main/crossplane/xplane-flux-apps) KCL module (pulled from OCI at render time). It is the generic, data-driven counterpart to the [`flux-app-kustomizations`](https://github.com/stuttgart-things/kcl/tree/main/flux/flux-app-kustomizations) KCL module — the hardcoded `tekton` / `crossplane` Kustomizations there become entries in `spec.apps` here.

## What it does

| # | Kind | Purpose | Condition |
|---|------|---------|-----------|
| 1..N | `kubernetes.m.crossplane.io/v1alpha1` Object | `kustomize.toolkit.fluxcd.io/v1` Kustomization applied to the target cluster | per `spec.apps` entry |

Each Kustomization references an **existing** Flux source (`GitRepository` / `OCIRepository` / `Bucket`) named by `spec.sourceRef` (default `flux-apps`) — the source itself is a precondition, typically bootstrapped by the [`flux-init`](../flux-init/) Configuration. Render and status happen in a single KCL pass — `status.ready` starts `false` and flips `true` once every app's Kustomization reports Ready.

## API

- **Group:** `config.stuttgart-things.com`
- **Version:** `v1alpha1`
- **Kind:** `FluxApps`
- **Scope:** `Namespaced` (v2 XRD — no claim)

### Spec

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `kubernetesProviderConfigRef` | string | yes | | Kubernetes `ClusterProviderConfig` name for the target cluster |
| `namespace` | string | no | `flux-system` | Namespace the Kustomization objects are created in |
| `sourceRef.kind` | string | no | `OCIRepository` | Default source kind (`OCIRepository`/`GitRepository`/`Bucket`) — apps ship as OCI artifacts |
| `sourceRef.name` | string | no | `flux-apps` | Default source name every app references |
| `sourceRef.namespace` | string | no | | Source namespace (defaults to the Kustomization namespace) |
| `interval` | string | no | `1h` | Default reconcile interval for all apps |
| `retryInterval` | string | no | `1m` | Default retry interval for all apps |
| `timeout` | string | no | `10m` | Default timeout for all apps — 10m because `wait: true` on a HelmRelease-wrapping app routinely needs more than 5m on first install |
| `prune` | boolean | no | `true` | Default garbage collection for all apps |
| `wait` | boolean | no | `true` | Default health-wait for all apps |
| `commonMetadata` | object | no | | Labels/annotations added to all resources of every app (overridable per app) |
| `apps` | []object | yes | | Kustomizations to deploy — one managed resource per entry |

### Per-app fields (`spec.apps[]`)

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `name` | string | yes | | Kustomization name (unique within the XR) |
| `path` | string | yes | | Path within the source to reconcile |
| `targetNamespace` | string | no | | Namespace all resources are applied to |
| `sourceRef` | object | no | `spec.sourceRef` | Per-app source override (`{kind, name, namespace}`) |
| `interval` / `retryInterval` / `timeout` | string | no | shared default | Per-app timing overrides |
| `prune` / `wait` / `force` | boolean | no | shared default | Per-app behaviour overrides |
| `dependsOn` | []object | no | | Other Kustomizations this app depends on (`{name, namespace}`) |
| `components` | []string | no | | Kustomize components to include |
| `substitute` | object | no | | `postBuild.substitute` — literal key/value variables |
| `substituteFrom` | []object | no | | `postBuild.substituteFrom` — ConfigMap/Secret sources (`{kind, name, optional}`) |
| `commonMetadata` | object | no | `spec.commonMetadata` | Per-app commonMetadata override |
| `healthChecks` / `healthCheckExprs` | []object | no | | Readiness gates (require `wait`) |
| `patches` / `images` | []object | no | | Strategic-merge / JSON6902 patches and image overrides |
| `decryption` | object | no | | SOPS decryption (`{provider, secretRef: {name}}`) |

**Precedence:** `spec.apps[].*` > `spec.*` > `flux-apps-defaults` EnvironmentConfig > module fallback.

### `postBuild` is always emitted (since v0.1.3 / module 0.2.0)

Even for an app that sets neither `substitute` nor `substituteFrom`. It used to be conditional, and that was a trap worth knowing about because the failure looks like a manifest bug rather than a missing block:

```
Namespace/${OPENEBS_NAMESPACE:-openebs} dry-run failed (Invalid):
metadata.name: Invalid value: "${OPENEBS_NAMESPACE:-openebs}"
```

`kustomize-controller` runs its variable-substitution pass **only when `spec.postBuild` is non-nil**. With no block at all, Flux never substitutes — so every `${VAR:-default}` fallback in the app's manifests silently stops being a fallback and the literal string is applied.

Hit on `u26-rke2-1` (2026-08-12) enabling `openebs`, whose catalog entry declares *zero required substitutions* precisely because all its variables have manifest defaults — so the app most likely to be enabled bare was the one guaranteed to break. `substituteFrom` stays conditional, where an empty list would really mean a source list with no sources.

### …and `substitute` is never empty (since v0.1.4 / module 0.3.0)

v0.1.3 emitted `postBuild` unconditionally but left `substitute` an empty map, on the assumption that a *present* `postBuild` was the whole condition. It was not: on `u26-rke2-1` the stored Kustomization carried `postBuild` and openebs still applied the literal `${OPENEBS_NAMESPACE:-openebs}`. An empty map does not survive the trip from KCL output through the Object to the stored resource, so what Flux read back was the nil block again — the exact bug, one layer down.

So a bare app now gets one key that exists only to keep the map non-empty:

```yaml
postBuild:
  substitute:
    XPLANE_FLUX_APPS: substitution-marker
```

It is deliberately visible and deliberately named: it appears in `kubectl get kustomization -o yaml` on every app this Configuration deploys, and the name says who put it there. A variable no manifest references costs nothing to substitute.

The marker key must not start with an underscore. The first attempt called it `_XPLANE_FLUX_APPS`, and KCL treats underscore-prefixed attributes as private and drops them from the output — reproducing the empty map, and with it the very bug being fixed. There is a test for that alone.

### Status

| Field | Type | Description |
|-------|------|-------------|
| `ready` | boolean | True when every app's Kustomization is Ready |
| `appsReady` | boolean | Same as `ready` (all apps Ready) |
| `appCount` | integer | Number of apps requested |
| `readyCount` | integer | Number of apps currently Ready |

## Cluster preconditions

- A Kubernetes `ClusterProviderConfig` (`kubernetes.m.crossplane.io/v1alpha1`) whose name matches `spec.kubernetesProviderConfigRef` — see `examples/cluster-provider-config.yaml`.
- A Flux source (`OCIRepository` / `GitRepository` / `Bucket`) on the target cluster matching `spec.sourceRef` — e.g. installed by [`flux-init`](../flux-init/).
- The `provider-kubernetes` ServiceAccount must be allowed to manage `kustomizations` (`kustomize.toolkit.fluxcd.io`). On the fleet clusters that SA is cluster-admin; on tighter clusters (e.g. kind) apply `examples/rbac.yaml`, otherwise every app Object stalls with `... cannot get resource "kustomizations" ... is forbidden`. **flux-init's `examples/rbac.yaml` does not cover this group** — on a flux-init + flux-apps cluster apply both.
- A `flux-apps-defaults` EnvironmentConfig — see `examples/environment-config.yaml`. **Required, not optional:** the `load-environment` step references it by name, and a missing one fails the whole pipeline with `Required environment config "flux-apps-defaults" not found`. It is also where the effective `sourceRef` default lives (see below).

## Examples

| File | Purpose |
|------|---------|
| `examples/xr-min.yaml` | Only the required provider config ref + one app (exercises defaults) |
| `examples/xr.yaml` | Realistic two-app deployment (tekton + crossplane) with substitutions |
| `examples/xr-max.yaml` | Every field set — per-app source, health checks, patches, images, SOPS |
| `examples/configuration.yaml` | Runtime install manifest (points at the OCI package) |
| `examples/functions.yaml` | Pipeline Functions the Composition references |
| `examples/environment-config.yaml` | Shared reconcile defaults — **required**, and the real source of the `sourceRef` default |
| `examples/cluster-provider-config.yaml` | Target-cluster Kubernetes `ClusterProviderConfig` |
| `examples/rbac.yaml` | `kustomize.toolkit` grant for the provider-kubernetes SA (tight clusters) |

## Verified

Live-tested on kind1 (2026-07-18) with `in-cluster` as the target, OCI-only: [`flux-init`](../flux-init/) installed Flux and an `OCIRepository` source (`oci://ghcr.io/stefanprodan/manifests/podinfo`), then a `FluxApps` XR referencing that source reconciled its Kustomization and rolled out the workload — `FluxApps` reached `ready=true`, `readyCount=1`, with the app's Deployment 2/2 in its `targetNamespace`.

```console
$ crossplane resource trace fluxapps kind1-apps -n crossplane-system
NAME                                                   SYNCED   READY   STATUS
FluxApps/kind1-apps (crossplane-system)                True     True    Available
└─ Object/kind1-apps-app-podinfo (crossplane-system)   True     True    Available
```

## Render locally

```bash
CONFIG=bootstrap/flux-apps XR=xr.yaml task render
```
