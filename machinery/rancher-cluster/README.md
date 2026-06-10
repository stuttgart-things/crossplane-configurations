# rancher-cluster

A Crossplane v2 Configuration that provisions a **Rancher cluster** — either
**generic** (custom-node, nodes registered manually) or **Harvester** (VM nodes
on a Harvester cluster via a machine pool) — and then makes that brand-new
cluster usable by Crossplane: wiring its Rancher-managed kubeconfig into a
`provider-kubernetes` `ClusterProviderConfig`, bootstrapping a namespace on it as
proof, and optionally registering it in Argo CD.

The `spec.infrastructure` discriminator (`generic` | `harvester`) selects the
provision path; **everything downstream is identical** (kubeconfig → wired CPC →
bootstrap → Argo CD).

A namespaced `RancherCluster` XR (group `resources.stuttgart-things.com`) drives a
`function-go-templating` pipeline that emits `provider-kubernetes` `Object`s:

| # | Step | Object | Target cluster |
|---|------|--------|----------------|
| 1 | **Provision** | per `infrastructure` — see [Provision path](#provision-path-generic-vs-harvester) below | management / Rancher |
| 2 | **Wire** | `kubernetes.m.crossplane.io/v1alpha1` `ClusterProviderConfig` named after the cluster | management |
| 3 | **Use** | a bootstrap `Namespace` | **downstream** (the new cluster) |
| 4 | **Register** *(optional)* | an assembled kubeconfig `Secret` + a `ClusterbookCluster` (→ Argo CD cluster Secret) | Argo CD cluster |

## Provision path: generic vs Harvester

`spec.infrastructure` only changes step 1; steps 2–4 are byte-identical either way.

| | `generic` (default) | `harvester` |
|---|---|---|
| Step-1 objects | one bare `provisioning.cattle.io/v1` `Cluster` (no machine pools) | a `rke-machine-config.cattle.io/v1` `HarvesterConfig` (the VM template) **plus** a `provisioning.cattle.io/v1` `Cluster` with one machine pool referencing it |
| Node registration | **manual** — run Rancher's registration command on each node | **automatic** — Rancher creates Harvester VMs and joins them |
| Extra spec | — | the `spec.harvester` block (cloud credential, image, network, sizing) |
| Example | [`examples/xr.yaml`](examples/xr.yaml) (co-located), [`examples/xr-split.yaml`](examples/xr-split.yaml) (split) | [`examples/xr-harvester.yaml`](examples/xr-harvester.yaml) |

For `harvester`, the `HarvesterConfig` is a **flat** CRD (fields at the top level,
no `spec`); `diskInfo`/`networkInfo` are built as JSON strings, and `userData`
defaults to a cloud-config that installs + enables `qemu-guest-agent` (so Rancher
can read the VM's IP). The machine pool carries all three roles
(`etcd`/`controlPlane`/`worker`) with `quantity: spec.harvester.quantity`.

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

Fields marked **env** below are resolved from the
[EnvironmentConfig](#per-environment-defaults-environmentconfig) when unset, so a
real XR is usually just `name` + per-cluster sizing + `argocd.register`.

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `name` | ✅ | — | Cluster name; also the `<name>-kubeconfig` Secret prefix and the downstream CPC name |
| `environmentConfig` | | `default` | Selects the EnvironmentConfig (label value) supplying the **env** defaults below |
| `providerConfigRef` | | **env** | `ClusterProviderConfig` for the **control-plane** cluster where Crossplane runs (wired CPC, bridged Secret, Argo CD objects land here). Co-located default: also the Rancher cluster. |
| `rancherProviderConfigRef` | | **env** → `providerConfigRef` | `ClusterProviderConfig` for the Rancher **management** cluster. Different from `providerConfigRef` ⇒ split control plane — see [Split control plane](#split-control-plane-rancherproviderconfigref). |
| `kubernetesVersion` | | **env** | e.g. `v1.34.7+k3s1` / `v1.31.5+rke2r1` (the suffix selects the distro) |
| `distro` | | **env** → `k3s` | `k3s` or `rke2` |
| `infrastructure` | | `generic` | `generic` (custom-node) or `harvester` (VM machine pool) |
| `rancherNamespace` | | **env** → `fleet-default` | Namespace of the provisioning Cluster + kubeconfig Secret |
| `clusterLabels` | | — | Extra labels on the `provisioning.cattle.io` Cluster |
| `machineGlobalConfig` | | — | Free-form `rkeConfig.machineGlobalConfig` passthrough |
| `harvester.cloudCredentialSecretName` | | **env** | Harvester cloud credential, `cattle-global-data:<name>` (Rancher UI → Cloud Credentials) |
| `harvester.imageName` | | **env** | Harvester VM image, `<namespace>/<image>` (e.g. `default/sthings-u26-k3s`) |
| `harvester.networkName` | | **env** | Harvester network, `<namespace>/<network>` (e.g. `default/vms`) |
| `harvester.vmNamespace` | | **env** → `default` | Harvester namespace the VMs are created in |
| `harvester.sshUser` | | **env** → `ubuntu` | SSH user baked into the image |
| `harvester.cpuCount` | | `2` | vCPUs per VM (per-cluster) |
| `harvester.memorySize` | | `4` | Memory per VM, GiB (per-cluster) |
| `harvester.diskSize` | | `40` | Root disk per VM, GiB (per-cluster) |
| `harvester.quantity` | | `1` | Number of VM nodes in the pool (per-cluster) |
| `harvester.userData` | | qemu-guest-agent cloud-config | cloud-init `userData` for the VMs (base64 is handled for you) |
| `bootstrap.namespace` | | `crossplane-bootstrap` | Namespace created on the downstream cluster |
| `bootstrap.labels` | | — | Labels for that downstream namespace |
| `argocd.register` | | `false` | Register the cluster in Argo CD via clusterbook-operator |
| `argocd.namespace` | | **env** → `argocd` | Argo CD namespace (kubeconfig + cluster Secret) |
| `argocd.providerConfigRef` | | **env** → `rancherProviderConfigRef` | ClusterProviderConfig for the cluster running Argo CD + clusterbook-operator |
| `argocd.server` | | **auto-discovered** | Direct API endpoint of the downstream cluster. Normally omitted — discovered from the downstream `kubernetes` Endpoints. Set only to force a VIP/LB for HA. |
| `argocd.labels` | | — | Labels on the `ClusterbookCluster` / Argo cluster Secret (for ApplicationSet selectors) |

Status: `kubeconfigSecret`, `clusterProviderConfig`, `argocdClusterSecret`.

## Per-environment defaults (EnvironmentConfig)

The lab-constant fields (the **env** rows above) live in an `EnvironmentConfig`,
not on every XR. The Composition's first pipeline step (`function-environment-configs`)
selects one by the **config-scoped** label
`rancher-cluster.resources.stuttgart-things.com/environment`, matched against the
XR's `spec.environmentConfig` (default `default`), and loads its `data` into the
pipeline environment. Precedence is always **XR spec → EnvironmentConfig →
built-in default**.

`data` keys: `providerConfigRef`, `rancherProviderConfigRef`, `rancherNamespace`,
`distro`, `kubernetesVersion`, `cloudCredentialSecretName`, `imageName`,
`networkName`, `vmNamespace`, `sshUser`, `argocdNamespace`,
`argocdProviderConfigRef`. See [`examples/environment-config.yaml`](examples/environment-config.yaml).

With it in place, a full Harvester + Argo CD XR is just:

```yaml
spec:
  name: k3s-xp
  environmentConfig: default
  infrastructure: harvester
  harvester: { cpuCount: 6, memorySize: 6, quantity: 1 }
  argocd: { register: true }     # target + server resolved automatically
```

> `crossplane render` does not read EnvironmentConfigs from a cluster — pass the
> example with `--extra-resources examples/environment-config.yaml`, or the env
> fields render empty.

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
2. **Observe-only** extracts the SA `token`, the downstream `ca.crt`, **and the
   cluster's API endpoint** (the apiserver IP behind the downstream `kubernetes`
   Endpoints) into a control-plane connection Secret (`<name>-argocd-sa`). Create
   and extract are **separate** Objects — provider-kubernetes does not surface
   `connectionDetails` on an Object that also manages/creates the target.
3. Assembles a `Secret` (`<name>-argocd-kubeconfig`, key `kubeconfig`) in the Argo
   CD namespace: the **auto-discovered** direct endpoint (`https://<apiserver-ip>:6443`,
   or `spec.argocd.server` if set) + downstream CA + SA token.
4. Emits a `ClusterbookCluster` with `skipReservation: true` and
   `preserveKubeconfigServer: true`, so clusterbook keeps the direct `server`
   verbatim (no IP/DNS rewrite) and just builds the Argo cluster Secret.

### Why the direct endpoint + a self-minted SA token

The Rancher proxy URL + session token are fragile for GitOps: the token can
expire and the proxy CA won't validate a direct connection. A `cluster-admin`
`argocd-manager` SA token (`ttl=0`) against the cluster's own API endpoint is the
standard Argo CD pattern — non-expiring and Rancher-independent. The Composition
mints it **and** discovers the endpoint for you, so neither a token Secret nor the
server IP is something the user provides.

### Additional preconditions (only when `register: true`)

- [`clusterbook-operator`](https://github.com/stuttgart-things/clusterbook-operator)
  installed on the Argo CD cluster (provides the `ClusterbookCluster` CRD).
- The Argo CD namespace (`spec.argocd.namespace`, default `argocd`) exists there.
- The discovered endpoint (the downstream apiserver IP, or `spec.argocd.server` if
  set) is reachable from the Argo CD cluster's pods.

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

> [!NOTE]
> **Encoding — confirmed on a live split.** `provider-kubernetes` decodes the
> `data.value` field once when materializing the connection Secret, so the bridged
> `<name>-kubeconfig-bridged` Secret decodes in **one** base64 pass (no
> double-base64). Verified end-to-end on a real Rancher/Harvester split: the wired
> CPC authenticates with the bridged Secret and bootstraps the downstream
> namespace. The same single-decode behavior is relied on by the Argo CD path
> (SA token + downstream CA extraction).

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

For **`infrastructure: harvester`**, additionally:

- A Harvester cloud credential exists on the Rancher cluster (Rancher UI →
  Cluster Management → Cloud Credentials). Find its Secret name with
  `kubectl -n cattle-global-data get secrets | grep '^cc-'` and pass
  `cattle-global-data:<name>` as `spec.harvester.cloudCredentialSecretName`.
- The `imageName` and `networkName` reference resources that already exist on the
  Harvester cluster.

## Caveats

- **Token TTL.** The Rancher kubeconfig token may carry a TTL
  (`kubeconfig-default-token-ttl-minutes`). If non-zero, the wired
  `ClusterProviderConfig` eventually goes stale and must be refreshed.
- **Eventual consistency.** Steps 2 and 3 stay `NotReady` until Rancher creates
  the kubeconfig Secret and the cluster's API is reachable — expected while nodes
  are still joining (`generic`: manual registration; `harvester`: VMs booting).

## Try it locally

```bash
cd machinery/rancher-cluster
# generic (co-located)
crossplane render examples/xr.yaml apis/composition.yaml examples/functions.yaml --include-function-results
# Harvester (split control plane)
crossplane render examples/xr-harvester.yaml apis/composition.yaml examples/functions.yaml --include-function-results
```

Or via the repo Taskfile:

```bash
CONFIG=machinery/rancher-cluster XR=xr.yaml task render            # or XR=xr-harvester.yaml
```

A full split-control-plane walkthrough (generic + Harvester, including the Argo CD
step) is in [`examples/MIXED-CLUSTER.md`](examples/MIXED-CLUSTER.md).
