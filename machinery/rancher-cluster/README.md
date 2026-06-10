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
| 4 | **Register** *(optional)* | an assembled kubeconfig `Secret` + a `ClusterbookCluster` (→ Argo CD cluster Secret) | management |

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
| `providerConfigRef` | ✅ | — | `ClusterProviderConfig` for the **control-plane** cluster where Crossplane runs (wired CPC, bridged Secret, Argo CD objects land here). Co-located default: also the Rancher cluster. |
| `rancherProviderConfigRef` | | = `providerConfigRef` | `ClusterProviderConfig` for the Rancher **management** cluster. Set it different from `providerConfigRef` to split the control plane from Rancher — see [Split control plane](#split-control-plane-rancherproviderconfigref). |
| `name` | ✅ | — | Cluster name; also the `<name>-kubeconfig` Secret prefix and the downstream CPC name |
| `kubernetesVersion` | ✅ | — | e.g. `v1.34.7+k3s1` / `v1.31.5+rke2r1` (the suffix selects the distro) |
| `distro` | | `k3s` | `k3s` or `rke2` |
| `rancherNamespace` | | `fleet-default` | Namespace of the provisioning Cluster + kubeconfig Secret |
| `clusterLabels` | | — | Extra labels on the `provisioning.cattle.io` Cluster |
| `machineGlobalConfig` | | — | Free-form `rkeConfig.machineGlobalConfig` passthrough |
| `bootstrap.namespace` | | `crossplane-bootstrap` | Namespace created on the downstream cluster |
| `bootstrap.labels` | | — | Labels for that downstream namespace |
| `argocd.register` | | `false` | Register the cluster in Argo CD via clusterbook-operator |
| `argocd.namespace` | | `argocd` | Argo CD namespace (kubeconfig + cluster Secret) |
| `argocd.providerConfigRef` | | `rancherProviderConfigRef` | ClusterProviderConfig for the cluster running Argo CD + clusterbook-operator |
| `argocd.server` | when `register` | — | Direct API endpoint of the downstream cluster (`https://host:6443`); use a VIP/LB for HA |
| `argocd.labels` | | — | Labels on the `ClusterbookCluster` / Argo cluster Secret (for ApplicationSet selectors) |

Status: `kubeconfigSecret`, `clusterProviderConfig`, `argocdClusterSecret`.

## Optional: Argo CD registration (`spec.argocd.register: true`)

Rancher's downstream kubeconfig points at the **auth-proxy** endpoint with a
Rancher session token (TTL-limited) and a CA that only validates the proxy — not
something Argo CD should depend on. Instead the Composition registers the cluster
by its **direct API endpoint** with a self-minted, non-expiring ServiceAccount
token, and hands the assembled kubeconfig to
[`clusterbook-operator`](https://github.com/stuttgart-things/clusterbook-operator),
which creates the Argo CD `cluster-<name>` Secret.

When enabled, the Composition (step 4) — all Argo CD objects target the cluster
that runs Argo CD + clusterbook (`spec.argocd.providerConfigRef`, default the
Rancher cluster):

1. Mints an `argocd-manager` ServiceAccount + `cluster-admin` binding + a
   long-lived token Secret on the **downstream** cluster (via the wired CPC).
2. **Observe-only** extracts the SA `token` + downstream `ca.crt` into a
   control-plane connection Secret (`<name>-argocd-sa`). Create and extract are
   **separate** Objects — provider-kubernetes does not surface `connectionDetails`
   on an Object that also manages/creates the target.
3. Assembles a `Secret` (`<name>-argocd-kubeconfig`, key `kubeconfig`) in the Argo
   CD namespace: `spec.argocd.server` (direct endpoint) + downstream CA + SA token.
4. Emits a `ClusterbookCluster` with `skipReservation: true` and
   `preserveKubeconfigServer: true`, so clusterbook keeps the direct `server`
   verbatim (no IP/DNS rewrite) and just builds the Argo cluster Secret.

### Why the direct endpoint + a self-minted SA token

The Rancher proxy URL + session token are fragile for GitOps: the token can
expire and the proxy CA won't validate a direct connection. A `cluster-admin`
`argocd-manager` SA token (`ttl=0`) against the cluster's own API endpoint is the
standard Argo CD pattern — non-expiring and Rancher-independent. The Composition
mints it for you, so no external token Secret is needed.

### Additional preconditions (only when `register: true`)

- [`clusterbook-operator`](https://github.com/stuttgart-things/clusterbook-operator)
  installed on the Argo CD cluster (provides the `ClusterbookCluster` CRD).
- The Argo CD namespace (`spec.argocd.namespace`, default `argocd`) exists there.
- `spec.argocd.server` is reachable from the Argo CD cluster's pods.

## Split control plane (`rancherProviderConfigRef`)

By default Crossplane and Rancher are **co-located** on one cluster
(`rancherProviderConfigRef` defaults to `providerConfigRef`), and the flow is
exactly the table above. To run Crossplane on a **separate control-plane
cluster** from Rancher, set `rancherProviderConfigRef` to a second
`ClusterProviderConfig` that targets the Rancher cluster
(see [`examples/xr-split.yaml`](examples/xr-split.yaml)).

The catch: every `Object` is reconciled by the **one** `provider-kubernetes` on
the control-plane cluster, and an `Object`'s `providerConfigRef` only selects
*where it is applied* — it does not move Secrets. So the wired CPC (which must
live on the control plane, because the bootstrap `Object` resolves it there)
needs Rancher's `<name>-kubeconfig`, but Rancher only publishes that on the
Rancher cluster. The Composition closes that gap with an extra step:

| Step | Object | `providerConfigRef` |
|------|--------|---------------------|
| 1 Provision | `provisioning.cattle.io/v1` Cluster | `rancherProviderConfigRef` (Rancher) |
| **1b Bridge** *(split only)* | `Object` that **Observes** `<name>-kubeconfig` on the Rancher cluster and surfaces its `value` as a connection Secret `<name>-kubeconfig-bridged` (in the XR namespace) on the control plane | `rancherProviderConfigRef` (Rancher) |
| 2 Wire | `ClusterProviderConfig` → `secretRef` at the **bridged** Secret | `providerConfigRef` (control plane) |
| 3 Use | bootstrap `Namespace` | the wired CPC (downstream) |
| 4 Register *(optional)* | reads the **bridged** Secret via extra-resources; emits the Argo CD objects | `providerConfigRef` (control plane) |

The bridge uses `provider-kubernetes`'s native `connectionDetails` +
`writeConnectionSecretToRef` — no external secret-sync, no new dependency. When
co-located, step 1b is not emitted and step 2 reads `<name>-kubeconfig` directly,
so rendered output is **byte-identical** to a spec without `rancherProviderConfigRef`.

> [!IMPORTANT]
> **Encoding — verify on a live split.** The bridge copies the Secret field at
> `data.value` (base64) into a connection Secret via `connectionDetails`. Whether
> `provider-kubernetes` re-encodes that when materializing the connection Secret
> (potential double-base64) has not yet been confirmed against a real Rancher +
> two-cluster setup. Render output is correct; confirm the wired CPC actually
> authenticates with the bridged Secret on first live use, and adjust the
> `fieldPath`/decode if needed.

## Cluster preconditions

On the Rancher **management** cluster (where Crossplane runs):

- `provider-kubernetes` installed with a `ClusterProviderConfig` matching the
  XR's `spec.providerConfigRef` — see
  [`examples/cluster-provider-config.yaml`](examples/cluster-provider-config.yaml)
  (`in-cluster`, `source: InjectedIdentity`).
- `provider-kubernetes`'s ServiceAccount must be able to **read Secrets** in
  `spec.rancherNamespace` (default `fleet-default`) — that is where Rancher
  writes `<name>-kubeconfig`.

For a **split control plane** (`rancherProviderConfigRef` set), additionally:

- A second `ClusterProviderConfig` on the control-plane cluster whose kubeconfig
  targets the Rancher cluster (referenced by `rancherProviderConfigRef`).
- That kubeconfig's identity must be able to read Secrets in
  `spec.rancherNamespace` on the Rancher cluster (the bridge `Object` Observes
  `<name>-kubeconfig` there).

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
