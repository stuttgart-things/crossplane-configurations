# cluster

A Crossplane v2 Configuration that builds a whole Kubernetes cluster — machine, distribution, access, platform — from a single namespaced `ClusterStack` XR (group `config.stuttgart-things.com`).

## Why

Standing up `u26-kind1` today means hand-writing and hand-correlating **five** XRs across two repos:

| # | Object | File (stuttgart-things/crossplane) |
|---|---|---|
| 1 | `NativeProxmoxVM` (+ baseos) | `xrs/proxmoxvm/labul/kind1/nativeproxmoxvm-u26-kind1.yaml` |
| 2 | `AnsibleRun` — k3s + Cilium | `xrs/ansiblerun/labul/kind1/ansiblerun-k3s-u26-kind1.yaml` |
| 3 | `AnsibleRun` — kubeconfig → Vault | `xrs/ansiblerun/labul/kind1/ansiblerun-kubeconfig-vault-u26-kind1.yaml` |
| 4 | `RemoteCluster` | `xrs/proxmoxvm/labul/kind1/remotecluster-u26-kind1.yaml` |
| 5 | `Platform` | `xrs/proxmoxvm/labul/kind1/platform-u26-kind1.yaml` |

That is ~250 lines in which `u26-kind1` is repeated **8×** and the VM IP `10.31.102.108` **3×** — all by hand, all after the fact, because the IP is only knowable once the VM exists.

The point of this Configuration is **not** less YAML. It is that the IP and the name stop being copy-paste facts:

- the **VM IP** is discovered from the VM child and threaded into both ansible inventories;
- **`clusterName`** becomes the VM name, the guest hostname, the ansible `cluster_name`, the Vault path, the `ClusterAccess` name and `Platform.clusterName`;
- **`platform.cni.enabled`** is derived from the distribution rather than restated.

## Usage

```yaml
apiVersion: config.stuttgart-things.com/v1alpha1
kind: ClusterStack
metadata:
  name: u26-kind1
  namespace: crossplane-system
spec:
  provider: proxmox
  size: medium
  distribution: k3s
  environmentConfig: labul
  platform:
    fluxInit: {enabled: true}
    ipReservation: {enabled: true, createDNS: true}
```

`provider` is the only required field. See [`examples/xr-min.yaml`](examples/xr-min.yaml) (defaults only), [`examples/xr.yaml`](examples/xr.yaml) (reproduces `u26-kind1`), [`examples/xr-max.yaml`](examples/xr-max.yaml) (every field, vSphere + kind).

## What gets created

```mermaid
flowchart TD
    CS["ClusterStack<br/>the XR you apply"]
    VM["NativeProxmoxVM | NativeVsphereVM<br/>{name}-vm<br/>VM + base-OS ansible"]
    D["AnsibleRun<br/>{name}-distribution<br/>k3s / kind install"]
    K["AnsibleRun<br/>{name}-kubeconfig<br/>kubeconfig → Vault"]
    A["ClusterAccess<br/>{name}-access<br/>→ ClusterProviderConfigs"]
    P["Platform<br/>{name}-platform<br/>flux, apps, cilium, issuer"]

    CS --> VM
    VM ==>|"IP + baseos succeeded"| D
    D ==>|"succeeded"| K
    K ==>|"succeeded"| A
    A ==>|"ready"| P

    classDef xr fill:#e8f0fe,stroke:#4285f4,color:#000
    class CS,VM,D,K,A,P xr
```

Everything is a **child XR** — this Configuration never talks to a provider itself. Plus three `Usage` resources for teardown ordering.

| Depth | Resource | Name | From |
|---|---|---|---|
| 0 | `ClusterStack` | *yours* | you |
| 1 | `NativeProxmoxVM` / `NativeVsphereVM` | `{name}-vm` | [proxmoxvm](../../machinery/proxmoxvm/) / [vspherevm](../../machinery/vspherevm/) |
| 1 | `AnsibleRun` ×2 | `{name}-distribution`, `{name}-kubeconfig` | [ansible-run](../../cicd/ansible-run/) |
| 1 | `ClusterAccess` | `{name}-access` | [remote-cluster](../remote-cluster/) |
| 1 | `Platform` | `{name}-platform` | [platform](../platform/) |
| 1 | `Usage` ×3 | `platform-uses-vm`, `platform-uses-access`, `access-uses-vm` | core Crossplane |

## Gates: sticky, and keyed on success

Each stage opens when the previous one **succeeded** — not when it is Ready. An `AnsibleRun` whose PipelineRun failed still reports Ready once its Object is applied; unblocking on that would upload a kubeconfig from a cluster that was never installed.

Every gate is also **sticky**: `(previous succeeded) OR (this child already exists)`. Not emitting a composed resource is what makes Crossplane *delete* it, and bpg / VMware Tools read the VM address from the guest agent — so a momentarily empty value is normal, and without stickiness a blip would delete an `AnsibleRun` whose recreation re-runs the play against a live machine. The `AnsibleRun` children are additionally re-emitted **verbatim** from observed state, because a rebuild during that same blip would rewrite the inventory to an empty IP.

`status.stage` is the single field to look at when a build is stuck:

```console
$ kubectl get clusterstack -n crossplane-system
NAME        READY   STAGE      PROVIDER   DISTRIBUTION   ENDPOINT                     AGE
u26-kind1   true    ready      proxmox    k3s            https://10.31.102.108:6443   40m
```

## Turning the platform layer off

`spec.platformEnabled: false` stops once the cluster is targetable (ClusterAccess ready) — the useful shape for a machine that only needs to exist and be reachable. It is deliberately a **sibling** of `spec.platform` rather than a key inside it: `platform` is a verbatim passthrough of the `Platform` XRD's spec, and a block that preserves unknown fields must not also declare known ones, or schema converters emit `additionalProperties: false` and reject every passthrough key.

## The API endpoint is derived, not copied

When `platform.vaultIssuer` is enabled and `kubernetesHost` is unset, it is injected from `ClusterAccess`'s discovered `status.share.apiEndpoint`. Every `Platform` in the fleet states it by hand today — a literal node IP that goes stale the moment the machine is rebuilt with a new DHCP lease, and nothing notices until an issuer stops working.

An explicit value always wins, so pointing at a VIP or a load balancer stays possible.

This does **not** make the endpoint highly available: it is one node's address, and with multi-node it will be the *first* node's. That is a deliberate interim choice — HA for the control plane's state, not for its reachability — tracked in [#171](https://github.com/stuttgart-things/crossplane-configurations/issues/171) along with the one thing that has to happen early: `--tls-san` is set at **install** time, so a VIP introduced later needs its name in the certificate from the start or every existing cluster needs new certs.

## What you cannot set

- **`platform.cni.enabled`** — derived from the distribution's CNI ownership. k3s installs cilium itself (its config disables flannel and kube-proxy, so the role *must*); kind is built deliberately without one. Setting it by hand is how a cluster ends up with two CNIs. Your other `cni` keys (chart version, values) pass through untouched.
- **`platform.clusterName`** — supplied from `clusterName`.
- **Placement** — node, datastore, bridge, vlan, pool (Proxmox) or the MOIDs (vSphere) live in the per-environment `EnvironmentConfig`. Keeping them out is what makes `provider` a one-word switch rather than a second placement API.

## Immutable fields

`provider`, `clusterName`, `distribution` and `size` are rejected on update by CEL. The first three are obvious; `size` is blunt on purpose — a size carries a control-plane node count, and CEL cannot see the catalog to tell a cpu change (in place) from a node-count change (a rebuild). Day-2 scaling is [#172](https://github.com/stuttgart-things/crossplane-configurations/issues/172).

`vm.templateVmId` / `vm.templateUuid` are create-only in the underlying providers: changing either forces delete+recreate rather than a re-clone.

## Deliberate re-runs are per stage

```yaml
runIDs:
  kubeconfig: "2"    # re-runs the upload only
```

Bumping a stage's id suffixes its `PipelineRun` and wrapped Object, which is what makes provider-kubernetes create a *new* run — a completed Tekton `PipelineRun` is immutable and the wrapped Object excludes `Update`, so editing anything else is a **silent no-op** (see [ansible-run](../../cicd/ansible-run/)).

It is a **map, not a single value**, and that is the whole point: one global id renames every stage, so repairing the kubeconfig upload also re-runs the k3s install against a live cluster. That is exactly what the fleet's hand-written XRs warn about in their headers — and exactly what happened on the first live build of this Configuration. A re-run has to name its stage.

The base-OS stage is absent on purpose: it runs from the VM XR's own `spec.ansible`, whose XRD has no re-run knob.

## Cluster preconditions

Beyond the wrapped Configurations' own (see each):

- **Tekton** plus the `ansible-credentials` Secret in the pipeline namespace, and an in-cluster provider-kubernetes config — the ansible stages need them.
- **`provider-kubeconfig`** with a `vault-kubeconfigs` ClusterProviderConfig — see [remote-cluster](../remote-cluster/).
- A per-environment **`EnvironmentConfig`** for the chosen provider (`proxmoxvm.…/environment` or `vspherevm.…/environment`) matching `spec.environmentConfig`.

## Verification status

Be precise about what has and has not been proven, because the gaps are where the next surprise lives.

**Proven on a real cluster** (kind1 → Proxmox LabUL, 2026-07-27): the full build chain. One `ClusterStack` produced a VM, ran base-OS provisioning, installed k3s, uploaded the kubeconfig to Vault, and had `ClusterAccess` read it back and emit both ClusterProviderConfigs — `status.ready: true`, endpoint discovered, cluster targetable by name. Deleting the `ClusterStack` removed every resource with no permanent finalizer hangs, and the Proxmox VM was destroyed.

**Teardown needs two phases — and with them it is clean.** Deleting a `ClusterStack` that has a Platform child in one step does **not** work: a live run tore the whole tree down in parallel within about three seconds, the `ClusterAccess`'s `RemoteCluster` (owner of the kubeconfig Secret and both ClusterProviderConfigs) went while the Platform's own resources still needed those credentials, and four resources were left behind — a stuck `flux-instance` Object, a `flux-operator` Release that was never deleted at all, and both ClusterProviderConfigs.

The `Usage` resources cannot prevent this. They are **composed children of the same XR**, so deleting the `ClusterStack` removes them in the same parallel sweep, and a Usage whose object is gone blocks nothing. Crossplane's own design note for the type describes this failure exactly (a cluster deleted before the Helm Release that lives on it) and enforces the relation through an admission webhook — a webhook that needs its rule object to still exist. The Usages are kept because they do work when a child is deleted on its own; they just cannot order the teardown of their own parent.

### The supported procedure

```bash
# Phase 1 — remove only the Platform child. ClusterAccess and the VM stay, so the
# kubeconfig Secret and both ClusterProviderConfigs still exist and the
# Platform's Objects can finalize against a reachable cluster.
kubectl patch clusterstack <name> -n <ns> --type=merge -p '{"spec":{"platformEnabled":false}}'
kubectl wait --for=delete platform/<name>-platform -n <ns> --timeout=5m

# Phase 2 — now delete the stack itself.
kubectl delete clusterstack <name> -n <ns>
```

Verified end to end on a live cluster: phase 1 removed the Platform child and its resources in 50 seconds with nothing wedged, and phase 2 left **zero** leftovers — no Objects, Releases, ClusterProviderConfigs, kubeconfig Secret, RemoteCluster or PipelineRuns, and the Proxmox VM destroyed.

A `ClusterStack` with `platformEnabled: false` from the start tears down cleanly in one step; the two phases are only needed once a Platform child exists.

## Not supported yet

- **Multi-node.** `size: medium-ha` renders an error, by design: no distribution in the catalog declares a verified multi-node path. Tracked as [#170](https://github.com/stuttgart-things/crossplane-configurations/issues/170) (static addressing + aggregate gate) and [#171](https://github.com/stuttgart-things/crossplane-configurations/issues/171) (the API endpoint is a single node IP, so three masters would be HA in name only).
- **rke2.** The fleet runs it through ansible, but no Crossplane path has built one — the catalog deliberately has no entry.
- **Day-2 scaling** — [#172](https://github.com/stuttgart-things/crossplane-configurations/issues/172).

## Local render

```bash
crossplane render examples/xr.yaml apis/composition.yaml examples/functions.yaml
# or: CONFIG=bootstrap/cluster XR=xr.yaml task render
```

Offline this emits **only the VM child** — every other stage is gated on live status that `render` has none of. That is expected, not a bug. To exercise the chain, pass `--observed-resources` with fake children carrying `status.share.ansibleSucceeded` / `status.succeeded` / `status.ready`.
