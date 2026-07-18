# flux-init

A Crossplane **v2** Configuration that bootstraps [Flux](https://fluxcd.io/) on a target cluster from a namespaced `FluxInit` XR (group `config.stuttgart-things.com`) via the [flux-operator](https://github.com/controlplaneio-fluxcd/flux-operator).

The Composition is a thin [`function-kcl`](https://github.com/crossplane-contrib/function-kcl) wrapper around the [`xplane-flux-init`](https://github.com/stuttgart-things/kcl/tree/main/crossplane/xplane-flux-init) KCL module (pulled from OCI at render time).

## What it does

| # | Kind | Purpose | Condition |
|---|------|---------|-----------|
| 1 | `helm.m.crossplane.io/v1beta1` Release | flux-operator chart | always |
| 2 | `kubernetes.m.crossplane.io/v1alpha1` Object | `FluxInstance` CR (`uses` operator) | always |
| 3 | `protection.crossplane.io/v1beta1` Usage | tear down FluxInstance before the operator | always |
| 4..N | `kubernetes.m.crossplane.io/v1alpha1` Object | `OCIRepository`/`GitRepository` CR | per `spec.instance.sources` entry |

Ordering is enforced with `crossplane.io/uses`: operator → instance → sources. Render and status happen in a single KCL pass — `status.ready` starts `false` and flips `true` once the operator, instance and all sources report Ready.

## API

- **Group:** `config.stuttgart-things.com`
- **Version:** `v1alpha1`
- **Kind:** `FluxInit`
- **Scope:** `Namespaced` (v2 XRD — no claim)

### Spec

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `clusterName` | string | no\* | | RemoteCluster name — derives the provider refs (`{clusterName}-helm`/`-kubernetes`) and observes the RemoteCluster to gate the install |
| `helmProviderConfigRef` | string | no\* | `{clusterName}-helm` | Helm `ClusterProviderConfig` for the target cluster |
| `kubernetesProviderConfigRef` | string | no\* | `{clusterName}-kubernetes` | Kubernetes `ClusterProviderConfig` for the target cluster |
| `observeProviderConfigRef` | string | no | `in-cluster` | Kubernetes `ClusterProviderConfig` (InjectedIdentity) to observe the RemoteCluster |
| `namespace` | string | no | `flux-system` | Namespace for the operator + FluxInstance |
| `operatorChart.version` | string | no | from EnvironmentConfig | flux-operator chart version |
| `operatorChart.repoURL` | string | no | from EnvironmentConfig | flux-operator chart repo |
| `instance.distribution` | string | no | from EnvironmentConfig | Flux distribution version |
| `instance.components` | []string | no | from EnvironmentConfig | Flux controllers to install |
| `instance.sources` | []object | no | | OCI/Git sources — omit to install controllers only |
| `instance.sources[].name` | string | yes | | Unique source name (used in resource naming) |
| `instance.sources[].kind` | string | no | `OCIRepository` | `OCIRepository` or `GitRepository` |
| `instance.sources[].url` | string | yes | | OCI or Git URL |
| `instance.sources[].ref` | string | no | `latest` | Tag/branch ref |
| `instance.sources[].path` | string | no | `.` | Path within the source |
| `instance.sources[].pullSecret` | string | no | | Secret (in `flux-system`) for private registries |
| `kustomize` | object | no | | Passed through verbatim to `FluxInstance.spec.kustomize` (controller tuning / extra patches) |
| `sops.enabled` | boolean | no | `false` | Inject a SOPS decryption patch onto every Flux Kustomization |
| `sops.secretName` | string | no | `sops-age` | Name of the age decryption Secret referenced by the patch |
| `sops.ageKeySecretRef` | object | no | | **Preferred** — copy the age key from an existing Secret on the target cluster (`{name, namespace, key}`, key default `age.agekey`); no plaintext in the XR |
| `sops.ageKey` | string | no | | Inline age private key (dev only — plaintext in the XR); ignored when `ageKeySecretRef` is set |

\* **Either `clusterName` or both provider refs must be set** (enforced by a CEL rule). Explicit refs always win over the `clusterName`-derived defaults.

**Precedence:** `spec.*` > `flux-defaults` EnvironmentConfig > module fallback.

### Targeting by clusterName

Instead of passing both provider refs, set `clusterName` to a registered
`RemoteCluster` (provider-kubeconfig). The Composition then:

1. derives `helmProviderConfigRef` = `{clusterName}-helm` and
   `kubernetesProviderConfigRef` = `{clusterName}-kubernetes` (the names
   provider-kubeconfig creates), and
2. emits an **Observe** Object for the RemoteCluster and **withholds the Flux
   resources** until it resolves (`status.clusterType` is set) — so the derived
   provider configs exist before installing through them.

The observed distribution is surfaced as `status.clusterType`. See
`examples/xr-clustername.yaml`.

### SOPS decryption

`FluxInstance.spec.sync` cannot carry decryption, so SOPS is enabled the
flux-operator way — a `kustomize.patches` entry that adds `spec.decryption`
(`provider: sops`, `secretRef: {name: <secretName>}`) to **every** Flux
`Kustomization`. Setting `sops.enabled: true` injects that patch automatically.
Raw `kustomize.patches` (e.g. controller `--concurrent` tuning) are merged with
it. See `examples/xr-sops.yaml`.

The `sops-age` decryption Secret can be provided three ways:

1. **`ageKeySecretRef` (preferred)** — copy the key from an existing Secret on
   the target cluster into `sops-age` via provider-kubernetes `references`
   (`patchesFrom`). No plaintext in the XR.
2. **`ageKey` (dev only)** — inline plaintext key, base64-encoded into the
   Secret's `data`. Visible in the management-cluster etcd; avoid for production.
3. **Out of band** — omit both; only the patch is injected and `sops-age` is
   managed elsewhere (e.g. the `sops.stuttgart-things.com` distribution).

### Status

| Field | Description |
|-------|-------------|
| `operatorReady` | flux-operator Helm Release is Ready |
| `instanceReady` | FluxInstance CR is Ready |
| `sourcesReady` | all source Objects Ready (`true` when none defined) |
| `ready` | all of the above (+ observe resolved when `clusterName` is set) |
| `sourceCount` | number of source Objects created |
| `clusterType` | distribution observed from the RemoteCluster (when `clusterName` is set) |

## Cluster preconditions

- A Helm `ClusterProviderConfig` (`helm.m.crossplane.io/v1beta1`) named per `spec.helmProviderConfigRef`.
- A Kubernetes `ClusterProviderConfig` (`kubernetes.m.crossplane.io/v1alpha1`) named per `spec.kubernetesProviderConfigRef`.
- A `flux-defaults` EnvironmentConfig (`examples/environment-config.yaml`).
- **When using `clusterName`:** a `RemoteCluster` (provider-kubeconfig) for the target cluster exposing `status.clusterType`; an in-cluster `ClusterProviderConfig` (InjectedIdentity) named per `observeProviderConfigRef`; and RBAC for the provider-kubernetes SA to `get,list,watch,patch` `remoteclusters` (`kubeconfig.stuttgart-things.com`).
- The `provider-kubernetes` ServiceAccount must be allowed to manage the Flux CRDs the composed Objects wrap (`fluxcd.controlplane.io`, `source.toolkit.fluxcd.io`). On the fleet clusters that SA is cluster-admin; on tighter clusters (e.g. kind) apply `examples/rbac.yaml`, otherwise the FluxInstance Object stalls with `... cannot get resource "fluxinstances" ... is forbidden`. If the cluster also runs [flux-apps](../flux-apps/), prefer [`../platform/examples/rbac.yaml`](../platform/examples/rbac.yaml) — one file covering every flux group, with the provider ServiceAccount name pinned so the binding survives provider upgrades.

See `examples/cluster-provider-config.yaml` for a kubeconfig-Secret-backed example, and `examples/rbac.yaml` for the RBAC grant.

## Verified

Live-tested on a kind cluster (`in-cluster` ClusterProviderConfig as target): `FluxInit` reached `ready=true` — flux-operator installed, `FluxInstance` reconciled (Flux v2.8.3), all four controllers running.

## DEV

Local render (no cluster). The `--extra-resources` flag supplies the EnvironmentConfig the `load-environment` step expects:

```bash
crossplane render examples/xr.yaml \
  apis/composition.yaml \
  examples/functions.yaml \
  --extra-resources=examples/environment-config.yaml \
  --include-function-results
```
