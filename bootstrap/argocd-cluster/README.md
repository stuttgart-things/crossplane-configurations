# argocd-cluster

Registers a cluster with **ArgoCD**. Sibling of [`remote-cluster`](../remote-cluster/):
where `ClusterAccess` makes a cluster targetable by Crossplane, `ArgocdCluster`
makes it targetable by ArgoCD — and, through the Clusterbook labels, decides
which platform components land on it.

```
ArgocdCluster (namespaced XR, on the management cluster)
  │
  ├─ Object → ExternalSecret        in argocd on the ARGOCD cluster
  │              └─ ESO pulls kubeconfig from Vault → Secret argocd/{clusterName}
  │
  └─ Object → ClusterbookCluster    cluster-scoped on the ARGOCD cluster
                 └─ operator reserves IP + DNS, renders Secret cluster-{clusterName}
                      └─ platform ApplicationSets select on its labels
                           └─ components appear on the TARGET cluster
```

The XR never handles the kubeconfig. It travels Vault → ESO → Secret and appears
in no XR status, no composition cache and no git repo.

## Verified end to end

Proven by hand on 2026-08-18 against `argoplatform-test1` before this
Configuration existed (stuttgart-things/stuttgart-things#2505):

| step | result |
|---|---|
| ExternalSecret → Secret | `SecretSynced=True`, `kubectl get nodes` through it works |
| ClusterbookCluster | `Ready=True`, reserved `10.31.102.8`, DNS wildcard created |
| rendered cluster Secret | `ownerReference` on the CR, labels and annotations as written |
| all profiles `false` | zero Applications on the target — only the AppProject |
| `security-platform` on | two Applications in **9 seconds**, components running on the target |

## Cluster preconditions

Three things must exist before the first XR. None are created here.

**1. A provider-kubernetes ClusterProviderConfig for the ArgoCD cluster.**
Default name `platform-sthings-registrar-kubernetes`. Produce it with a
`ClusterAccess` XR whose kubeconfig sits in Vault.

> The identity behind it should **not** be an admin kubeconfig. It has to live in
> the same `kubeconfigs/` mount that every registered cluster can read — an admin
> credential there inverts the trust direction, letting a registered cluster take
> over the cluster that registers it. A ServiceAccount scoped to
> `clusterbookclusters` (cluster-wide) and `externalsecrets` in `argocd` is
> enough; verified with `kubectl auth can-i`, where `get secrets` and `get nodes`
> both return `no`.

**2. A SecretStore on the ArgoCD cluster that can read the kubeconfig mount.**
Default `vault-kubeconfigs`. On LabUL that is a `ClusterSecretStore` against the
infra Vault, authenticating with Kubernetes auth — no credential at rest.

**3. The kubeconfig in Vault**, written by the `kubeconfig` stage of
`ClusterStack`. Raw YAML, not base64.

## Teardown — the ordering is load-bearing

Tested, and it does **not** clean up by itself:

```
1. remove the platform Applications   ← while the target cluster is REACHABLE
2. delete the ArgocdCluster XR        ← releases IP/DNS, deregisters
3. destroy the cluster
```

Doing 2 before 1 makes 1 impossible. Two reasons, both observed:

* The platform AppSets carry `preserveResourcesOnDeletion: true`. When they stop
  selecting a cluster, the generated parent Applications go — but the child
  Applications they manage are **preserved by design**, and they have no
  `ownerReference` (the link upwards is only `argocd.argoproj.io/tracking-id`).
  They end up `Unknown/Unknown` with
  `InvalidSpecError: error getting cluster by server … NotFound`.
* Some Applications carry `pre-delete-finalizer.argocd.argoproj.io`, which wants
  to clean up **on the target cluster**. With the cluster already gone, the delete
  hangs.

Same class of problem as `vault-auth` on the Crossplane side, which can never
destroy if its cluster dies first.

## What this does NOT do

* **It does not create the cluster.** That is `ClusterStack` with
  `platformEnabled: false` — a cluster gets its platform from Flux (`Platform`)
  or from ArgoCD (this), never both. That switch is the boundary between the two.
* **It does not write the kubeconfig to Vault.** That is the `kubeconfig` stage of
  `ClusterStack`.
* **It does not remove the generated Applications.** See teardown.

## Two traps the composition handles for you

**Omitting a label is not "off".** Most AppSet selectors match with
`NotIn ["false"]`, where a missing label means ON. The composition therefore emits
an explicit `'false'` for every component it disables, rather than leaving it out.

**`security-platform` selects opt-in.** Unlike the 33 AppSets that use
`NotIn ["false"]`, the two security AppSets match on the component label directly
— the umbrella alone fans out nothing. Filed as
[stuttgart-things/argocd#318](https://github.com/stuttgart-things/argocd/issues/318);
once aligned, that block needs no special care. The composition already writes
both labels explicitly, so it works either way.

**`nfs-csi-storageclasses` needs a gate label.** Setting the component switch
alone installs nothing — `storage-platform.stuttgart-things.com/nfs-config` must
exist too. The composition emits it whenever `spec.nfs.server` is set.

## Seven values the XR must not set

The Clusterbook controller stamps these onto the rendered Secret. Writing them
into the CR is shadowed at best:

```
labels:       …/allocation-ip, …/allocation-zone, …/cluster-type
annotations:  …/cluster-name, …/fqdn, …/ip, …/zone
```

`spec.networkKey` is the only input; the reservation is the operator's decision.
Note it is the **load balancer** address, not the node's — on
`argoplatform-test1` the node was `10.31.102.110` and the reservation
`10.31.102.8`.

## Status

`status.ready` is gated on the `ClusterbookCluster` reporting Ready, not merely on
both Objects applying. An applied CR whose reservation has not happened yet is not
a registered cluster.

`status.allocationIp` and `status.fqdn` are read back from the CR, never set.
