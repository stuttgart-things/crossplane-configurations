# platform

A Crossplane v2 Configuration that wraps the bootstrap building-block Configurations behind a single namespaced `Platform` XR (group `config.stuttgart-things.com`).

The Composition is a `function-kcl` step that composes one **child XR** per enabled component — today a `FluxInit` (the [flux-init](../flux-init/) Configuration), with further Configurations to follow. The wrapped Configurations stay independently usable; `platform` supplies the shared inputs and aggregates the results.

## Why

Without it, every bootstrap Configuration on a cluster restates the same cluster identity, and there is no single object that answers "is this cluster's platform up, and what was it given?". `Platform` states that once and publishes it back.

## The shared contract

`status.shared` is the point of this Configuration. The cluster identity is stated **once** on `spec`, resolved by the Composition, injected into every child XR, and published on `status.shared`:

| Field | Source |
|---|---|
| `clusterName` | `spec.clusterName` |
| `helmProviderConfigRef` | `spec.helmProviderConfigRef`, else derived `{clusterName}-helm` |
| `kubernetesProviderConfigRef` | `spec.kubernetesProviderConfigRef`, else derived `{clusterName}-kubernetes` |
| `observeProviderConfigRef` | `spec.observeProviderConfigRef` (default `in-cluster`) |
| `namespace` | `spec.namespace` (default `flux-system`) |
| `clusterType` | **discovered** — lifted up from the `FluxInit` child, which reads it off the target `RemoteCluster` |

`clusterType` is the shape that matters as components are added: a fact discovered by one child, published once, consumed by the next — instead of each component re-observing the `RemoteCluster` itself.

`status.components` carries per-component readiness and outputs; `status.ready` is true when every **enabled** component is Ready (vacuously true when none are enabled, matching what `function-auto-ready` reports on the `Ready` condition).

```yaml
status:
  shared:
    clusterName: staging
    helmProviderConfigRef: staging-helm
    kubernetesProviderConfigRef: staging-kubernetes
    observeProviderConfigRef: in-cluster
    namespace: flux-system
    clusterType: k3s
  components:
    fluxInit:
      enabled: true
      ready: true
      operatorReady: true
      instanceReady: true
      sourcesReady: true
      sourceCount: 1
  componentCount: 1
  readyComponents: 1
  ready: true
```

## Usage

One `clusterName` is the whole shared contract — the provider config refs derive from it and every component inherits it:

```yaml
apiVersion: config.stuttgart-things.com/v1alpha1
kind: Platform
metadata:
  name: staging
  namespace: crossplane-system
spec:
  clusterName: staging
  fluxInit:
    enabled: true
    instance:
      sources:
        - name: fleet-infra
          kind: OCIRepository
          url: oci://ghcr.io/stuttgart-things/fleet-infra
          ref: latest
          path: clusters/staging
```

See [`examples/`](examples/): `xr-min.yaml` (defaults only), `xr.yaml` (the copy-paste template), `xr-max.yaml` (every field).

## Components

| Component | `spec` block | Child XR | Configuration |
|---|---|---|---|
| Flux bootstrap | `spec.fluxInit` | `FluxInit` | [flux-init](../flux-init/) |

Each component block mirrors the child XR's own spec (minus the shared identity, which is injected) and is passed through verbatim — `operatorChart`, `instance`, `kustomize` and `sops` all reach `FluxInit` unchanged. `enabled: false` omits the child entirely.

## Adding a component

1. Add a `spec.<component>` block to [`apis/definition.yaml`](apis/definition.yaml) (with `enabled`, default `true`) and a `status.components.<component>` block.
2. In [`apis/composition.yaml`](apis/composition.yaml), emit the child XR under an `if _<component>Enabled:` guard, injecting the shared refs; read its status back out of `ocds` and add its readiness to `_enabledReady`.
3. Add the wrapped Configuration to `dependsOn` in [`crossplane.yaml`](crossplane.yaml).

If a component needs a value another component discovers, put it on `status.shared` (as `clusterType` does) rather than re-deriving it.

## Cluster preconditions

Those of the wrapped Configurations — `platform` adds none of its own:

- A Helm `ClusterProviderConfig` (`helm.m.crossplane.io/v1beta1`) named `{clusterName}-helm`, or whatever `spec.helmProviderConfigRef` says.
- A Kubernetes `ClusterProviderConfig` (`kubernetes.m.crossplane.io/v1alpha1`) named `{clusterName}-kubernetes`.
- A `flux-defaults` EnvironmentConfig — consumed by the flux-init Composition, not by this one. See [`../flux-init/examples/environment-config.yaml`](../flux-init/examples/environment-config.yaml).

## Notes

- The XR kind is `Platform`, not `Workspace`: `provider-opentofu` already serves `kind: Workspace` (`opentofu.m.upbound.io`) on these clusters, so a second `workspaces` plural would make `kubectl get workspaces` ambiguous.
- `crossplane render` on a `Platform` renders the child XRs but **not** their compositions — it stops at the `FluxInit` object. To check what Flux itself will emit, render [`../flux-init`](../flux-init/) with the child XR.
