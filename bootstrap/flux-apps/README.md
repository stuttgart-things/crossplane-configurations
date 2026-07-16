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
| `sourceRef.kind` | string | no | `GitRepository` | Default source kind (`GitRepository`/`OCIRepository`/`Bucket`) |
| `sourceRef.name` | string | no | `flux-apps` | Default source name every app references |
| `sourceRef.namespace` | string | no | | Source namespace (defaults to the Kustomization namespace) |
| `interval` | string | no | `1h` | Default reconcile interval for all apps |
| `retryInterval` | string | no | `1m` | Default retry interval for all apps |
| `timeout` | string | no | `5m` | Default timeout for all apps |
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

### Status

| Field | Type | Description |
|-------|------|-------------|
| `ready` | boolean | True when every app's Kustomization is Ready |
| `appsReady` | boolean | Same as `ready` (all apps Ready) |
| `appCount` | integer | Number of apps requested |
| `readyCount` | integer | Number of apps currently Ready |

## Cluster preconditions

- A Kubernetes `ClusterProviderConfig` (`kubernetes.m.crossplane.io/v1alpha1`) whose name matches `spec.kubernetesProviderConfigRef` — see `examples/cluster-provider-config.yaml`.
- A Flux source (`GitRepository` / `OCIRepository` / `Bucket`) on the target cluster matching `spec.sourceRef` — e.g. installed by [`flux-init`](../flux-init/).
- Optionally a `flux-apps-defaults` EnvironmentConfig — see `examples/environment-config.yaml`.

## Examples

| File | Purpose |
|------|---------|
| `examples/xr-min.yaml` | Only the required provider config ref + one app (exercises defaults) |
| `examples/xr.yaml` | Realistic two-app deployment (tekton + crossplane) with substitutions |
| `examples/xr-max.yaml` | Every field set — per-app source, health checks, patches, images, SOPS |
| `examples/configuration.yaml` | Runtime install manifest (points at the OCI package) |
| `examples/functions.yaml` | Pipeline Functions the Composition references |
| `examples/environment-config.yaml` | Optional shared reconcile defaults |
| `examples/cluster-provider-config.yaml` | Target-cluster Kubernetes `ClusterProviderConfig` |

## Render locally

```bash
CONFIG=bootstrap/flux-apps XR=xr.yaml task render
```
