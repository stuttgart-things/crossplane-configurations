# rancher-cluster

A Crossplane v2 Configuration that provisions a **generic (custom-node) Rancher
cluster** and then makes that brand-new cluster usable by Crossplane — by wiring
its Rancher-managed kubeconfig into a `provider-kubernetes` `ClusterProviderConfig`
and bootstrapping a namespace on it as proof.

A namespaced `RancherCluster` XR (group `resources.stuttgart-things.com`) drives a
`function-go-templating` pipeline that emits three `provider-kubernetes` `Object`s:

| # | Step | Object | Target cluster |
|---|------|--------|----------------|
| 1 | **Provision** | `provisioning.cattle.io/v1` `Cluster` (k3s or rke2, no machine pools — nodes registered manually) | management |
| 2 | **Wire** | `kubernetes.m.crossplane.io/v1alpha1` `ClusterProviderConfig` named after the cluster | management |
| 3 | **Use** | a bootstrap `Namespace` | **downstream** (the new cluster) |

## How the kubeconfig is obtained

When Rancher provisions a cluster `<name>` in namespace `<rancherNamespace>`
(default `fleet-default`), it publishes the downstream kubeconfig as a Secret:

```bash
kubectl -n fleet-default get secret <name>-kubeconfig -o jsonpath='{.data.value}' | base64 -d
```

That kubeconfig uses the Rancher proxy URL + an API token, so it is reachable
from the management cluster where Crossplane runs — **no direct network path to
the downstream API server is required**. The wired `ClusterProviderConfig`'s
`secretRef` points straight at this Secret (key `value`); there is no copy or
transform. `provider-kubernetes` simply retries until Rancher has created the
Secret (which can take a few minutes while the cluster comes up), then the
downstream `Object`s reconcile.

```yaml
spec:
  credentials:
    source: Secret
    secretRef:
      namespace: fleet-default        # spec.rancherNamespace
      name: <name>-kubeconfig         # Rancher-managed
      key: value
```

## Spec fields

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `providerConfigRef` | ✅ | — | `ClusterProviderConfig` targeting the Rancher **management** cluster |
| `name` | ✅ | — | Cluster name; also the `<name>-kubeconfig` Secret prefix and the downstream CPC name |
| `kubernetesVersion` | ✅ | — | e.g. `v1.34.7+k3s1` / `v1.31.5+rke2r1` (the suffix selects the distro) |
| `distro` | | `k3s` | `k3s` or `rke2` |
| `rancherNamespace` | | `fleet-default` | Namespace of the provisioning Cluster + kubeconfig Secret |
| `clusterLabels` | | — | Extra labels on the `provisioning.cattle.io` Cluster |
| `machineGlobalConfig` | | — | Free-form `rkeConfig.machineGlobalConfig` passthrough |
| `bootstrap.namespace` | | `crossplane-bootstrap` | Namespace created on the downstream cluster |
| `bootstrap.labels` | | — | Labels for that downstream namespace |

Status: `kubeconfigSecret`, `clusterProviderConfig`.

## Cluster preconditions

On the Rancher **management** cluster (where Crossplane runs):

- `provider-kubernetes` installed with a `ClusterProviderConfig` matching the
  XR's `spec.providerConfigRef` — see
  [`examples/cluster-provider-config.yaml`](examples/cluster-provider-config.yaml)
  (`in-cluster`, `source: InjectedIdentity`).
- `provider-kubernetes`'s ServiceAccount must be able to **read Secrets** in
  `spec.rancherNamespace` (default `fleet-default`) — that is where Rancher
  writes `<name>-kubeconfig`.

## Caveats

- **Token TTL.** The Rancher kubeconfig token may carry a TTL
  (`kubeconfig-default-token-ttl-minutes`). If non-zero, the wired
  `ClusterProviderConfig` eventually goes stale and must be refreshed.
- **Eventual consistency.** Steps 2 and 3 stay `NotReady` until Rancher creates
  the kubeconfig Secret and the cluster's API is reachable — expected while the
  generic cluster's nodes are still being registered.

## Try it locally

```bash
cd machinery/rancher-cluster
crossplane render examples/xr.yaml apis/composition.yaml examples/functions.yaml --include-function-results
```

Or via the repo Taskfile:

```bash
CONFIG=machinery/rancher-cluster XR=xr.yaml task render
```
