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
| `helmProviderConfigRef` | string | yes | | Helm `ClusterProviderConfig` name for the target cluster |
| `kubernetesProviderConfigRef` | string | yes | | Kubernetes `ClusterProviderConfig` name for the target cluster |
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

**Precedence:** `spec.*` > `flux-defaults` EnvironmentConfig > module fallback.

### Status

| Field | Description |
|-------|-------------|
| `operatorReady` | flux-operator Helm Release is Ready |
| `instanceReady` | FluxInstance CR is Ready |
| `sourcesReady` | all source Objects Ready (`true` when none defined) |
| `ready` | all of the above |
| `sourceCount` | number of source Objects created |

## Cluster preconditions

- A Helm `ClusterProviderConfig` (`helm.m.crossplane.io/v1beta1`) named per `spec.helmProviderConfigRef`.
- A Kubernetes `ClusterProviderConfig` (`kubernetes.m.crossplane.io/v1alpha1`) named per `spec.kubernetesProviderConfigRef`.
- A `flux-defaults` EnvironmentConfig (`examples/environment-config.yaml`).

See `examples/cluster-provider-config.yaml` for a kubeconfig-Secret-backed example.

## DEV

Local render (no cluster). The `--extra-resources` flag supplies the EnvironmentConfig the `load-environment` step expects:

```bash
crossplane render examples/xr.yaml \
  apis/composition.yaml \
  examples/functions.yaml \
  --extra-resources=examples/environment-config.yaml \
  --include-function-results
```
