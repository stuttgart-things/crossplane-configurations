# cni

A Crossplane **v2** Configuration that installs a CNI on a target cluster from a namespaced `Cni` XR (group `config.stuttgart-things.com`). Cilium today; the provider is an enum so more can follow.

The Composition is a thin [`function-kcl`](https://github.com/crossplane-contrib/function-kcl) wrapper around the [`xplane-cni`](https://github.com/stuttgart-things/kcl/tree/main/crossplane/xplane-cni) KCL module (pulled from OCI at render time).

## What it does

| # | Kind | Purpose | Condition |
|---|------|---------|-----------|
| 1 | `kubernetes.m.crossplane.io/v1alpha1` Object | observe the target `RemoteCluster` — the install gate | when `spec.clusterName` is set |
| 2 | `helm.m.crossplane.io/v1beta1` Release | the CNI chart | once the gate opens |

Render and status happen in a single KCL pass — `status.ready` starts `false` and flips `true` once the Release reports Ready.

## Why this is its own component

A cluster with no CNI has **NotReady nodes, so nothing schedules** — and a Helm install aimed at such a cluster does not fail fast, it times out and retries. That makes CNI the thing every other platform component has to wait on rather than race.

`status.ready` is the signal. The [platform](../platform) Configuration holds its `FluxInit`/`FluxApps` children until it flips.

> A `protection.crossplane.io` `Usage` does **not** solve this. Usage orders *deletion* — it blocks removal of the `of` resource while the `by` exists — and has no effect on creation order. Creation ordering has to be a render gate.

## kube-proxy and the API server address

This fleet's kind clusters run **without kube-proxy**, so cilium has to replace it. It then cannot reach the API server through the `10.96.0.1` service VIP, because nothing programs that VIP until cilium itself is up — a circular dependency that presents as cilium pods stuck on `dial tcp 10.96.0.1:443: network is unreachable`.

`spec.cilium.k8sServiceHost` breaks the circle by naming the control-plane directly, defaulting to `{clusterName}-control-plane` — exactly the container kind creates. The Composition **rejects** `kubeProxyReplacement` with no resolvable address rather than letting the cluster deadlock silently.

With `kubeProxyReplacement: false` the host/port are omitted entirely: a real kube-proxy makes normal discovery work, and pinning a host would then be wrong, not merely redundant.

## API

- **Group:** `config.stuttgart-things.com`
- **Version:** `v1alpha1`
- **Kind:** `Cni`
- **Scope:** `Namespaced` (v2 XRD — no claim)

### Spec

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `clusterName` | string | no\* | | RemoteCluster name — derives `helmProviderConfigRef`, the API server address, and the observe gate |
| `helmProviderConfigRef` | string | no\* | `{clusterName}-helm` | Helm `ClusterProviderConfig` for the target cluster |
| `observeProviderConfigRef` | string | no | `in-cluster` | Kubernetes `ClusterProviderConfig` (InjectedIdentity) to observe the RemoteCluster |
| `namespace` | string | no | `kube-system` | Namespace on the target cluster |
| `provider` | string | no | `cilium` | Which CNI (`enum`, so a typo is rejected at apply time) |
| `chart.version` | string | no | `1.19.6` | Chart version |
| `chart.repository` | string | no | `https://helm.cilium.io/` | Chart repo |
| `cilium.kubeProxyReplacement` | boolean | no | `true` | Required on clusters built without kube-proxy |
| `cilium.k8sServiceHost` | string | no | `{clusterName}-control-plane` | Direct API server address — see above |
| `cilium.k8sServicePort` | integer | no | `6443` | |
| `cilium.ipamMode` | string | no | `kubernetes` | |
| `cilium.operatorReplicas` | integer | no | `1` | 1 suits single-node/kind; raise for HA |
| `values` | object | no | `{}` | Raw Helm values, **merged last** — win over everything computed above |

\* **Either `clusterName` or `helmProviderConfigRef` must be set** (enforced by a CEL rule). An explicit ref always wins over the `clusterName`-derived default.

### Status

| Field | Description |
|-------|-------------|
| `provider` | Resolved CNI provider |
| `version` | Chart version actually installed |
| `releaseReady` | Helm Release Ready |
| `clusterType` | Distribution observed from the target RemoteCluster |
| `ready` | True once the CNI is up — **what other components gate on** |

## Cluster preconditions

- A Helm `ClusterProviderConfig` (`helm.m.crossplane.io/v1beta1`) matching `spec.helmProviderConfigRef` (or `{clusterName}-helm`).
- With `spec.clusterName`: a `RemoteCluster` of that name on the management cluster, plus RBAC letting provider-kubernetes observe it — see [`examples/rbac.yaml`](examples/rbac.yaml).

## Examples

| File | Purpose |
|------|---------|
| [`xr-min.yaml`](examples/xr-min.yaml) | Only XRD-required fields — exercises every default |
| [`xr.yaml`](examples/xr.yaml) | Realistic usage — the copy-paste template |
| [`xr-max.yaml`](examples/xr-max.yaml) | Every field set — catches templating breakage |
| [`rbac.yaml`](examples/rbac.yaml) | RemoteCluster observe grant |
| [`functions.yaml`](examples/functions.yaml) | Pipeline Functions |
| [`configuration.yaml`](examples/configuration.yaml) | Runtime install manifest |
