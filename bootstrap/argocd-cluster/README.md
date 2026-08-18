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

> Put this XR in the **same namespace as its `ClusterStack`** — they describe one
> cluster. That layout was broken until v0.1.2: the composed Object was named
> `{clusterName}-kubeconfig`, exactly what `ClusterStack` calls its kubeconfig
> stage, and the two fought over one object (`Only one reference can have
> Controller set to true`). It is now `{name}-argocd-externalsecret`.

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

## Vault PKI: eight annotations, and none of them fail loudly

`certManagerVaultPki` is the one component whose parameters the AppSet reads as
**eight** separate annotations. Its own comment explains the trap:

> Both blocks are always passed; the chart renders only the one matching
> `method`. … **Missing annotations render as `""`, and the chart treats an empty
> method as `token`.**

So half a kubernetes block does not error — it quietly authenticates the wrong
way and then fails later on a token secret that may not exist. `spec.vault`
therefore models all of them, and the composition emits the three kubernetes
ones only when `authMethod: kubernetes` is actually chosen.

| field | annotation | when |
|---|---|---|
| `server` | `…/vault-server` | always |
| `pkiPath` | `…/vault-pki-path` | always |
| `authMethod` | `…/vault-auth-method` | always, default `token` |
| `tokenSecret` | `…/vault-token-secret` | always |
| `wildcardIssuerName` | `…/wildcard-issuer-name` | always — read by cert-manager-**cluster-ca**, not by vault-pki |
| `k8sAuthMount` | `…/vault-k8s-auth-mount` | only with `authMethod: kubernetes` |
| `k8sAuthRole` | `…/vault-k8s-auth-role` | only with `authMethod: kubernetes` |
| `k8sAuthServiceAccount` | `…/vault-k8s-auth-sa` | only with `authMethod: kubernetes` |

### The XR wires it up — it does not create the counterpart

Setting `certManagerVaultPki: true` writes annotations. The AppSet turns them
into a `ClusterIssuer`, and a ClusterIssuer is a *description* of how cert-manager
should talk to Vault. Three things have to exist for that description to work,
and **none of them come from this XR**:

| what | where | who creates it |
|---|---|---|
| `ClusterIssuer` | target cluster | the AppSet, from these annotations |
| `vault-pki-ca` (key `ca.crt`) | **target cluster**, ns `cert-manager` | `VaultPkiSecret` |
| k8s auth mount + role bound to cert-manager's SA | **inside Vault** | `VaultK8sAuth` |
| *or* a token Secret | **target cluster** | `VaultPkiSecret` |

The Vault side is the one that surprises: it is not a cluster object at all. Without
it the ClusterIssuer sits there and reports `permission denied`.

### Today the `Platform` XR does all of that — and `platformEnabled: false` removes it

`Platform.spec.vaultIssuer` composes **eleven** children: `VaultK8sAuth`,
`VaultPkiSecret`, the reviewer ServiceAccount with its token (which the Vault mount
needs for TokenReview), the cert-manager TokenRequest RBAC, the ClusterIssuer and a
wildcard certificate.

A cluster built for this Configuration runs `ClusterStack` with
`platformEnabled: false`, so all eleven are gone. That is a real gap, not a detail:
the boundary this Configuration draws — Flux **or** ArgoCD — holds for *deploying*
applications, but the prerequisites live in the same XR as the Flux path.

### The fix needs no new code: `Platform` with only `vaultIssuer`

```yaml
apiVersion: config.stuttgart-things.com/v1alpha1
kind: Platform
metadata: {name: <cluster>-vaultprep, namespace: default}
spec:
  clusterName: <cluster>
  kubernetesProviderConfigRef: <cluster>-kubernetes
  helmProviderConfigRef: <cluster>-helm
  fluxInit:
    enabled: false          # no Flux on the target cluster
  vaultIssuer:
    enabled: true
    vaultAddr: https://vault.infra.sthings-vsphere.labul.sva.de
    kubernetesHost: https://<node-ip>:6443
    pkiMount: pki
    pkiRole: sthings-vsphere
    authName: certmanager
    autoReviewer: true      # provisions the reviewer SA + token itself
    caSourceSecret: vault-pki-source-ca
    issuerName: vault-pki-k8s
    approleSecret: vault-approle
```

`apps` stays unset; `cni` and `ipReservation` default to `false`. Verified on
2026-08-18 against `argoplatform-test1`: ten children Ready in 100 seconds, **no
`FluxInit` composed**, no `flux-system` namespace on the target, and a test
Certificate issued through Vault in 30 seconds over Kubernetes auth with no token
anywhere.

`Platform` is not a second deployment path here — it is prerequisite provisioning.
The name misleads; what it does in this shape is "VaultIssuerPrep".

### So keep `certManagerVaultPki` OFF when `Platform` provides the issuer

`Platform` creates `vault-pki-k8s`. The AppSet would put a second issuer named
`vault-pki` beside it, with the same Vault credentials — duplication without
benefit. The `spec.vault` fields exist for the opposite case: taking the AppSet
route **instead of** `Platform`. Enabling both is the mistake to avoid.

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
