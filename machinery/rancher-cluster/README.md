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
| Node registration | you bring the machines — run Rancher's registration command on each, which `spec.nodeRegistration.publish` hands you (see [Node registration](#node-registration-specnoderegistrationpublish)) | **automatic** — Rancher creates Harvester VMs and joins them |
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
| `clusterSpec` | | — | Free-form passthrough merged **under** the whole `provisioning.cattle.io` Cluster spec — see [Cluster options](#cluster-options-machineglobalconfig-and-clusterspec) |
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
| `nodeRegistration.publish` | | `false` | Publish Rancher's node registration command into a Secret beside this XR |
| `nodeRegistration.secretName` | | `<name>-node-command` | Name of that Secret |
| `nodeRegistration.secretNamespace` | | — | Also mirror it into this namespace on the control plane (for `ansible-run`'s `extraEnvSecretName`) |
| `nodeRegistration.tokenName` | | `default-token` | Name of the `ClusterRegistrationToken` Rancher created |
| `bootstrap.namespace` | | `crossplane-bootstrap` | Namespace created on the downstream cluster |
| `bootstrap.labels` | | — | Labels for that downstream namespace |
| `argocd.register` | | `false` | Register the cluster in Argo CD via clusterbook-operator |
| `argocd.namespace` | | **env** → `argocd` | Argo CD namespace (kubeconfig + cluster Secret) |
| `argocd.providerConfigRef` | | **env** → `rancherProviderConfigRef` | ClusterProviderConfig for the cluster running Argo CD + clusterbook-operator |
| `argocd.server` | | **auto-discovered** | Direct API endpoint of the downstream cluster. Normally omitted — discovered from the downstream `kubernetes` Endpoints. Set only to force a VIP/LB for HA. |
| `argocd.labels` | | **env** `defaultLabels` | Labels on the `ClusterbookCluster` / Argo cluster Secret — the platform profile toggles the ApplicationSets select on. Merged over `defaultLabels`, per key |
| `argocd.annotations` | | **env** `defaultAnnotations` | Annotations on the same Secret — where the platform ApplicationSets read their component parameters (`vault-server`, `nfs-server`, …). Merged over `defaultAnnotations`, per key |
| `argocd.reservation.enabled` | | `false` | Reserve a clusterbook IP (`skipReservation: false`). This is what stamps the `allocation-ip` label every `network-platform` AppSet gates on |
| `argocd.reservation.networkKey` | | **env** `clusterbookNetworkKey` | Network pool the IP comes from (e.g. `10.31.103`). Required for a reservation — the render fails without it |
| `argocd.reservation.providerConfigRef` | | **env** → `default` | `ClusterbookProviderConfig` backing the reservation |
| `argocd.reservation.createDNS` | | `true` | Create the wildcard DNS record for the reserved IP |
| `argocd.reservation.releaseOnDelete` | | `true` | Give IP + DNS record back on delete. Needs clusterbook >= v1.26.0 |
| `vaultAuth.enabled` | | `false` | Prepare the cluster for Vault/OpenBao **kubernetes** auth (see below) |
| `vaultAuth.reviewerServiceAccount` | | `vault-auth-reviewer` | Token-reviewer SA created downstream |
| `vaultAuth.reviewerNamespace` | | `kube-system` | Namespace of that SA and its token Secret |
| `vaultAuth.certManagerServiceAccount` | | `certmanager` | SA cert-manager logs in to Vault as |
| `vaultAuth.composeMount` | | `false` | Also compose the `VaultK8sAuth` that **creates** the mount. Implies `enabled`. |
| `vaultAuth.mountClusterName` | | `<name>-sthings` | First half of the mount path (`<this>-<roleName>`) |
| `vaultAuth.roleName` | | `certmanager` | Second half of the mount path, and the Vault role name |
| `vaultAuth.tokenPolicies` | | `["pki-issue"]` | Policies the login token gets — referenced, never created |
| `vaultAuth.approleSecret` | | `vault` | Secret with `terraform.tfvars` (`vault_role_id`, `vault_secret_id`) |
| `vaultAuth.opentofuProviderConfigName` | | `in-cluster` | OpenTofu `ClusterProviderConfig` the child XR drives Vault through |

Status: `kubeconfigSecret`, `clusterProviderConfig`, `argocdClusterSecret`,
`vaultReviewerSecret`, `vaultKubernetesHost`, `nodeCommandSecret`,
`rancherClusterId`.

## Cluster options (`machineGlobalConfig` and `clusterSpec`)

Two free-form escape hatches, at two levels. Neither is schema-checked here — the
`provisioning.cattle.io` CRD is Rancher's, not ours — so a misspelled key reaches
the cluster and is rejected (or ignored) there, never at admission.

| | Merges into | Use for |
|---|---|---|
| `spec.machineGlobalConfig` | `rkeConfig.machineGlobalConfig` | the distro's own config file: `cni`, `disable`, `kube-apiserver-arg`, `tls-san`, `node-label`, … |
| `spec.clusterSpec` | the whole `Cluster.spec` | everything else the CRD offers |

### The CNI, including turning it off

`cni` lives in `machineGlobalConfig`, and **the key is not the same for the two
distros** — this is the part that bites, because nothing rejects the wrong one:

```yaml
# rke2
machineGlobalConfig:
  cni: none            # or calico, cilium, canal (the default)

# k3s — `cni` is not a k3s config key at all
machineGlobalConfig:
  flannel-backend: none
  disable-network-policy: true
  disable-kube-proxy: true   # Cilium replaces it — see step 3 below
```

Get it wrong on k3s and you do not get an error, you get flannel. (The lab's
ansible path spells the same decision `rke2_cni: none` + `install_cilium: true`,
and gates on both for the same reason.)

**A cluster built with no CNI does NOT get past step 2 on its own.** This used to say
the opposite, and was wrong on two counts:

- **Every step after the join goes through Rancher's proxy**, and the proxy goes
  through `cattle-cluster-agent` — an ordinary Deployment in the pod network, not
  `hostNetwork` (checked on a live custom-node k3s: pod IP `10.42.0.13`, host
  `10.31.102.123`). With no CNI that pod never starts, so the bridged kubeconfig,
  the wired `ClusterProviderConfig`, the bootstrap namespace, the Argo CD
  registration and `vaultAuth` all wait on a proxy that cannot answer.
- **Argo CD does not install Cilium on a clusterbook cluster.** There is no
  `cilium-install-*` ApplicationSet for `network-platform` — only
  `cilium-install-kind`. The `network-platform/cilium-lb` / `cilium-gateway` labels
  enable AppSets that **configure** a Cilium that must already be running; set on a
  cluster without it they fail silently
  ([#421](https://github.com/stuttgart-things/crossplane-configurations/issues/421)).

The CNI therefore has to reach the cluster **past** Rancher, at the API server
directly:

1. The node's join play publishes the node's own admin kubeconfig to Vault —
   `sthings.rke.rancher_register` with `rancher_upload_kubeconfig: true`, the
   AnsibleRun's `vaultSecretName` pointing at an AppRole with write on `kubeconfigs/`.
2. A [`ClusterAccess`](../../bootstrap/remote-cluster/) XR reads it and emits
   `{clusterName}-kubernetes` / `{clusterName}-helm` ClusterProviderConfigs aimed at
   `node:6443`.
3. A [`Cni`](../../bootstrap/cni/) XR installs Cilium through `{clusterName}-helm`.
   **Leave `cilium.kubeProxyReplacement` on** (the default) and disable kube-proxy
   in `machineGlobalConfig` (`disable-kube-proxy: true`). An earlier version of this
   step said the opposite, and that was wrong: the `network-platform/cilium-lb` and
   `cilium-gateway` AppSets configure L2 announcements and Gateway API, and Cilium
   supports both **only** with kube-proxy replacement. Turning it off gives you a
   cluster whose LoadBalancer IPs are never announced.

   Without kube-proxy, Cilium cannot reach the API server through the `kubernetes`
   Service, so set `cilium.k8sServiceHost`. On a **single-node** cluster use
   `127.0.0.1` — the agent and operator run in the host network, and the value is
   not tied to a DHCP lease. With more than one node, point it at an address every
   node can reach and that outlives a lease (a VIP or a DNS name). Features go
   through `spec.values`, e.g. `l2announcements.enabled` / `externalIPs.enabled`.

   For `cilium-gateway`, the Gateway API CRDs have to exist **before** Cilium
   starts — Cilium enables its Gateway controller only if it finds them. A cluster
   with k3s' traefik disabled does not have them. **Do not rely on the ones rke2
   brings either.** Measured on `rancher-join-test4` (rke2 v1.36.4, #422): its
   `rke2-traefik-crd` Helm release ships Gateway API **v1.5.1**, and like every rke2
   helm-install job it only runs once a pod network exists — i.e. *after* Cilium.
   That version matches neither Cilium 1.19 (Gateway API v1.4.1) nor 1.20 (v1.6.1),
   and the rke2 docs state the Gateway API CRDs are *removed* when traefik is
   disabled after having been enabled — taking every Gateway and HTTPRoute with
   them. So own them instead: install the CRDs pinned to the version your Cilium
   supports before Cilium, and keep rke2 out of it.

   On rke2 that means the documented switches, not chart names under `disable`:
   `ingress-controller: none` (traefik is the default since rke2 v1.36), and the
   Gateway API CRD chart in `disable`. The rke2 reference lists
   `rke2-gateway-api-crd` as a valid `disable` item — and does **not** list
   `rke2-traefik` or `rke2-ingress-nginx`, which test4 used. On v1.36.4 the chart
   was still called `rke2-traefik-crd`, so check the name for your rke2 version
   (`kubectl -n kube-system get helmcharts`). Installing the CRDs from the `Cni` is
   planned (#438).
4. `cattle-cluster-agent` starts, the proxy answers, and steps 2–4 here proceed.
   Only then flip the `network-platform/cilium-*` labels.

Do not take Cilium from rke2's bundled chart (`cni: cilium`) instead — this fleet
never installs Cilium through a Rancher helm-controller release (see
`crossplane/knowledge/cilium-not-via-rancher-helm.md` in stuttgart-things).

### `clusterSpec` — everything else

The Composition writes only `kubernetesVersion`, `rkeConfig.machineGlobalConfig`
and, on the harvester path, `cloudCredentialSecretName` + `rkeConfig.machinePools`.
`clusterSpec` reaches the rest without this XRD growing a field per CRD row:

```yaml
spec:
  clusterSpec:
    localClusterAuthEndpoint: {enabled: true}   # ACE — kubeconfig past the Rancher proxy
    rkeConfig:
      machineSelectorConfig:                    # per-role config; custom-node clusters need it
        - machineLabelSelector:
            matchLabels: {rke.cattle.io/control-plane-role: 'true'}
          config: {protect-kernel-defaults: true}
      registries: {...}                         # mirrors / air-gap
      etcd: {snapshotScheduleCron: '0 */5 * * *', snapshotRetention: 5}
      upgradeStrategy: {...}
      chartValues: {...}                        # values for the bundled charts
```

`kubectl explain cluster.provisioning.cattle.io.spec --recursive` on your Rancher
is the authoritative list.

**Composed values win the merge**, so a passthrough can never take the harvester
path apart by redefining its machine pool. Winning silently would be the failure
mode this repo keeps paying for, though, so the keys the **active** path owns are
a render **error** naming the field to use instead:

| Key in `clusterSpec` | Refused on | Use instead |
|---|---|---|
| `kubernetesVersion` | both paths | `spec.kubernetesVersion` |
| `cloudCredentialSecretName` | `harvester` | `spec.harvester.cloudCredentialSecretName` |
| `rkeConfig.machinePools` | `harvester` | `spec.harvester.*` (sizing), or `infrastructure: generic` |

`rkeConfig.machinePools` **is** allowed on `generic` — that path composes none, so
a pool set here is added rather than conflicting.

`rkeConfig.machineGlobalConfig` is the one deliberate exception: it deep-merges
with `spec.machineGlobalConfig` per key (the latter wins), because the two halves
are the same distro config file and splitting them across the two fields is
reasonable.

Still not reachable: the Cluster's `metadata.annotations` (only `clusterLabels` is
plumbed through).

## Node registration (`spec.nodeRegistration.publish`)

An `infrastructure: generic` cluster has **no machine pools**, so nothing joins it
on its own: the nodes are machines somebody else provisioned, and each one is
joined by running Rancher's registration command on it. Until now that command was
read out of the Rancher UI by hand — the one manual step between "Crossplane built
me a VM" (`NativeProxmoxVM` / `NativeVsphereVM`) and "Crossplane built me a Rancher
cluster on it". Setting `publish: true` puts it in a Secret instead:

```bash
kubectl -n default get secret k3s-test-node-command \
  -o jsonpath='{.data.nodeCommand}' | base64 -d
```

```
curl -fL https://rancher.example/system-agent-install.sh | sudo sh -s - \
  --server https://rancher.example --label 'cattle.io/os=linux' \
  --token <token> --ca-checksum <sum>
```

| Key | What it is |
|---|---|
| `nodeCommand` | The installer line, server URL + registration token + CA checksum filled in, ready to run |
| `insecureNodeCommand` | The same with `--insecure`, for a node that does not trust the Rancher certificate |

**On Rancher 2.15+ the command Rancher stores is only a template.** Every command
field of the `ClusterRegistrationToken` (and its `manifestUrl`) carries a literal
`{token}`; Rancher substitutes the real token at its API layer, and keeps the token
itself in the Secret `status.tokenSecretName` names (`crt-token-<name>`). Read through
the Kubernetes API — which is all a Composition can do — the command authenticates
as `{token}`. The node's `/v3/connect/agent` call then gets
`500 machine not found by request`, and `system-agent-install.sh` retries it silently
for hours. Measured on Rancher 2.15.1, 2026-09-15; 2.14.3 still embeds the token.

So the published Secret is **built**: the raw template is collected into
`<secretName>-raw`, the token is read from `crt-token-<tokenName>` — only when the
template actually contains `{token}`, so a 2.14 Rancher never gets an Object whose
source does not exist — and `<secretName>` is written with the token substituted.
It is only published once it is runnable; a command that still says `{token}` is
worse than none. The token expires (`expiresAt`, about 30 days on 2.15) and Rancher
rotates it; the substituted command follows.

**Upgrading from v0.8.x:** `<secretName>` used to be written directly as a connection
Secret. It is now taken over by the publishing Object and rewritten with the
runnable command; the template moves to `<secretName>-raw`. Nothing has to be
deleted.

**Append the role flags yourself** — `--etcd --controlplane --worker` for an
all-in-one node, and `--node-name` / `--address` on a multi-NIC host. Rancher does
not put them in `nodeCommand`, and that is what lets one published command serve
every node of the cluster whatever role each takes.

Nothing else in the Configuration changes: steps 2–4 already wait for the
downstream API, and it answers once the first node has joined.

### How it is obtained

Two hops, because the token is not where the cluster is:

1. Rancher stamps `status.clusterName` on the `provisioning.cattle.io` Cluster once
   it reconciles it — the management cluster ID, `c-m-xxxxx`, published here as
   `status.rancherClusterId`.
2. The `management.cattle.io/v3` `ClusterRegistrationToken` (`tokenName`, Rancher's
   `default-token`) lives in a namespace of that name. An Observe-only `Object`
   extracts its commands into `<secretName>-raw`.
3. On Rancher 2.15+ (the command contains `{token}`), a second Observe-only `Object`
   reads the token from `crt-token-<tokenName>` in the same namespace.
4. An `Object` on `providerConfigRef` writes `<secretName>` with the runnable command,
   and the same into `secretNamespace` when set.

Both are `Observe` only and both run on the Rancher cluster
(`rancherProviderConfigRef`). They are also **eventually consistent** — the second
hop cannot be emitted before the first has observed an ID, so expect the Secret a
little after the XR.

### Feeding it to Ansible (`secretNamespace`)

The intended consumer is an `AnsibleRun` that executes the command on a machine
this platform built, which is the same staging `bootstrap/cluster` already uses for
the k3s/rke2 install: VM → IP known → play. `ansible-run`'s `extraEnvSecretName`
turns a Secret's keys into environment variables of the same name in the ansible
step, so a play can read `lookup('env', 'nodeCommand')` and the token never travels
through a PipelineRun param or an XR spec.

That Secret has to live in the **PipelineRun's** namespace, while the published one
lands beside this XR — hence `secretNamespace`, which mirrors the same keys there
(on `providerConfigRef`, the control-plane cluster):

```yaml
spec:
  nodeRegistration:
    publish: true
    secretNamespace: tekton-ci
```

The plan this is step one of — the join play and the `extraEnvSecretName`
pass-through that `proxmoxvm` / `vspherevm` still need — is
[#422](https://github.com/stuttgart-things/crossplane-configurations/issues/422).

### It is a credential

Off by default, and not for symmetry with the other toggles: **anyone who can read
that Secret can attach a node to the cluster**. Publishing moves the token out of
`c-m-xxxxx` on the Rancher cluster and into a namespace where more people can read
it, and `secretNamespace` moves it again. A smaller grant than `vaultAuth.enabled`,
the same kind of decision — so it is asked for rather than assumed.

## Optional: create the mount too (`spec.vaultAuth.composeMount: true`)

`enabled` prepares the cluster; `composeMount` also composes the `VaultK8sAuth`
child XR that creates the Vault auth mount, so the cluster reaches a working
`vault-pki` ClusterIssuer without anyone applying a second XR by hand.

It does **not** remove the ordering: the child needs the reviewer Secret and the
API address that only exist once the cluster does, so it becomes ready later than
the rest — the same shape as the Argo CD registration block. What it removes is a
human running `terraform apply` with the OpenBao **root token**; the child
authenticates with a scoped AppRole instead.

**The mount path is derived once.** It exists in two places that must agree — the
path the backend is mounted at, and the `vault-k8s-auth-mount` annotation the
`cert-manager-vault-pki` ApplicationSet reads. With `composeMount` both come from
`mountClusterName` + `roleName`, and the annotations are filled in for you
(`vault-auth-method`, `-mount`, `-role`, `-sa`). An annotation you set explicitly
still wins. Writing the path twice is how it drifts, and a mismatch does not fail
loudly: the issuer reports `Ready` on a successful *login* and only the signing
request is denied.

Requires, on the control plane:

- the **`vault-auth` Configuration**, declared as a `dependsOn` so a version below
  the floor is a refused install rather than a child XR whose fields the older
  schema silently drops. It must stay installed under the package-manager-derived
  **long** name — a short name plus a dependent is the documented Lock collision.
- a Secret named by `approleSecret` (default `vault`) holding `terraform.tfvars`
  with `vault_role_id` and `vault_secret_id`. AppRole, not a token. It needs
  `sudo` **and** `delete` on `sys/auth/*`: without `sudo` it authenticates and
  then 403s on the mount, without `delete` a teardown strands the Workspace in
  its finalizer and orphans the mount.
- the `clusterbook.stuttgart-things.com/vault-server` annotation. Missing it with
  `composeMount` set **fails the render** rather than quietly composing nothing.

## Optional: Vault kubernetes-auth prerequisites (`spec.vaultAuth.enabled: true`)

A cluster built from this XR comes up with its `vault-pki` ClusterIssuer
**not-Ready**, because the OpenBao auth mount its annotations name does not exist
yet. Creating that mount is `bootstrap/vault-auth`'s job (a `VaultK8sAuth` XR
through provider-opentofu). What `VaultK8sAuth` *cannot* do is the half that
lives on the new cluster and on the control plane — correctly so, it configures
Vault and nothing else. That half is what this block supplies:

1. A token-reviewer `ServiceAccount` bound to **`system:auth-delegator`** —
   TokenReview and nothing more. Not `cluster-admin` like the Argo CD manager:
   this token leaves the cluster.
2. Its credentials mirrored **back** to a Secret next to this XR
   (`<name>-vault-reviewer`, keys `token` + `ca.crt`). This is the same
   create-then-observe split as the Argo CD SA — provider-kubernetes does not
   surface `connectionDetails` on an Object that also manages the target.
3. The downstream API endpoint as `status.vaultKubernetesHost`. `VaultK8sAuth`
   defaults `kubernetesHost` to `https://kubernetes.default.svc:443`, the
   in-cluster address, which is wrong whenever Vault runs elsewhere.
4. With the vault-pki block also on: the `certmanager` ServiceAccount in
   `cert-manager`. Its absence surfaces as a *Vault* error —
   `while requesting a token for the service account /certmanager:
   serviceaccounts "certmanager" not found` — and nothing else owns it. The
   matching `cert-manager-tokenrequest` Role arrives from the
   `cert-manager-vault-pki` ApplicationSet. It depends on `vaultAuth` alone, not
   on the vault-pki **source** Secrets: kubernetes auth reads no token, so a
   control plane with a CA source only (`vaultPkiSourceCaName`, no
   `vaultPkiSourceTokenName`) is a complete setup. Until #435 the whole block
   needed both sources and silently rendered neither this ServiceAccount nor the
   CA Secret without a token source — see `examples/xr-vault-k8s-auth.yaml`.

Then point a `VaultK8sAuth` at the two published values:

```yaml
spec:
  kubernetesHost: <status.vaultKubernetesHost>
  k8sAuths:
    - name: certmanager
      backendConfig:
        secretName: <status.vaultReviewerSecret>
        secretNamespace: <namespace of this XR>
```

**This is still two phases** — the reviewer token cannot exist before the cluster
does. What it removes is the human and the OpenBao **root token**: until
2026-09-08 somebody ran `terraform apply` in `stuttgart-things/harvester` by hand
for every new cluster. Composing the `VaultK8sAuth` itself is the open half of
[#392](https://github.com/stuttgart-things/crossplane-configurations/issues/392);
see [`docs/392-handover.md`](../../docs/392-handover.md) for the decision it
still needs.

`enabled` is off by default because it is a real grant: anyone holding that
token can review logins for the cluster.

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
`argocdProviderConfigRef`, `clusterbookNetworkKey`,
`clusterbookProviderConfigRef`, `vaultPkiSourceTokenName`,
`vaultPkiSourceTokenNamespace`, `vaultPkiSourceTokenKey`, `vaultPkiSourceCaName`,
`vaultPkiSourceCaNamespace`, `vaultPkiSourceCaKey`, `vaultPkiTargetNamespace`,
`vaultPkiCaSecretName`, `vaultPkiCaSecretKey`, `vaultPkiTokenSecretKey`, plus the
two maps `defaultLabels` / `defaultAnnotations` (see
[Platform profile labels + annotations](#platform-profile-labels--annotations-the-argo-cd-contract)).
See [`examples/environment-config.yaml`](examples/environment-config.yaml).

> `clusterbookNetworkKey` is the fallback for `spec.argocd.reservation.networkKey`.
> A reservation without one is refused by the `ClusterbookCluster` CRD itself
> (CEL: *networkKey is required unless clusterType is 'kind' or skipReservation
> is true*), so the Composition fails the render rather than emitting an `Object`
> that never goes Ready.
> `vaultPkiCaSecretName` / `vaultPkiCaSecretKey` exist but should stay at their
> defaults: `appset-cert-manager-vault-pki` hardcodes
> `caBundleSecretRef: {name: vault-pki-ca, key: ca.crt}`, so a different name here
> only decouples the Secret this Composition pushes from the one the ClusterIssuer
> reads.

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
4. Emits a `ClusterbookCluster` with `preserveKubeconfigServer: true`, so
   clusterbook keeps the direct `server` verbatim (no IP/DNS rewrite). Without
   `spec.argocd.reservation.enabled` it also carries `skipReservation: true` and
   the operator only builds the Argo cluster Secret; with it, the operator
   additionally reserves an IP from `networkKey` (+ optional DNS) and stamps the
   `allocation-ip` label and the `ip`/`fqdn` annotations the platform
   ApplicationSets need.

### Why the direct endpoint + a self-minted SA token

The Rancher proxy URL + session token are fragile for GitOps: the token can
expire and the proxy CA won't validate a direct connection. A `cluster-admin`
`argocd-manager` SA token (`ttl=0`) against the cluster's own API endpoint is the
standard Argo CD pattern — non-expiring and Rancher-independent. The Composition
mints it **and** discovers the endpoint for you, so neither a token Secret nor the
server IP is something the user provides.

### Additional preconditions (only when `register: true`)

- [`clusterbook-operator`](https://github.com/stuttgart-things/clusterbook-operator)
  **>= v0.18.0** installed on the Argo CD cluster (provides the
  `ClusterbookCluster` CRD). The floor is `spec.skipReservation`, which this
  Composition always emits and which landed in v0.18.0 — an older CRD prunes the
  unknown field, and the operator then tries a real reservation for a cluster that
  never asked for one.
- [`clusterbook`](https://github.com/stuttgart-things/clusterbook) **>= v1.26.0**
  wherever the operator points, because `releaseOnDelete` defaults to `true` here
  and before v1.26.0 the DNS half of a release failed while reporting success
  ([clusterbook#187](https://github.com/stuttgart-things/clusterbook/issues/187)).
- The Argo CD namespace (`spec.argocd.namespace`, default `argocd`) exists there.
- The discovered endpoint (the downstream apiserver IP, or `spec.argocd.server` if
  set) is reachable from the Argo CD cluster's pods.

### Platform profile labels + annotations (the Argo CD contract)

`spec.argocd.labels` / `.annotations` (base values from the EnvironmentConfig's
`defaultLabels` / `defaultAnnotations`, per-key overridable on the XR) land on the
`ClusterbookCluster`, and the operator copies them onto the Argo CD cluster Secret
— which is what the platform ApplicationSets in
[`stuttgart-things/argocd`](https://github.com/stuttgart-things/argocd) select and
template on. The authoritative, annotated list of every label and annotation is
[`platforms/cluster.reference.yaml`](https://github.com/stuttgart-things/argocd/blob/main/platforms/cluster.reference.yaml);
what is worth knowing here:

- **`env` is not decoration.** Four cicd AppSets build a git path out of it
  (`appset-crossplane-platform-baseline` →
  `crossplane/platform/baseline/*/vars/<env>.yaml`, and the three `appset-cxp-*` →
  `crossplane/xrs/<kind>/<env>/<cluster>/`). Unset it renders as an empty path
  segment: nothing matches, no Application is generated, and no error is raised
  anywhere. `tier` (`dev` | `prod`) drives how permissive the per-cluster
  AppProject from `config/cluster-project` is; `role` is free-form and unread.
- **Umbrella enrols, component opts out.** `<profile>: 'true'` enrols the cluster
  in the **whole** profile; `<profile>/<component>: 'false'` takes one component
  back out. The component selectors are `NotIn ["false"]`, and a label-selector
  `NotIn` matches an **absent** key too — so leaving a component label off means
  *included*, not excluded. The EnvironmentConfig defaults therefore carry only
  the four umbrellas (at `'false'`, the actual master switch — `matchLabels`
  never matches `'false'`) plus the exclusions we mean fleet-wide
  (`cicd-platform/openebs`, `storage-platform/longhorn`,
  `network-platform/cilium-gateway-secondary`). Pinning every toggle to `'false'`
  would not make a bare XR safer, it would turn each enabled profile into an
  empty one and put the EnvironmentConfig in lockstep with every AppSet added to
  the catalog.
  The three `appset-cxp-*` sets (`cicd-platform/crossplane-ansible`,
  `…/crossplane-proxmoxvm`, `…/crossplane-vspherevm`) are opt-**in** instead —
  they select with `matchLabels: 'true'`, so an absent label is already off.
- **A component label without its umbrella does nothing.** Not in the AppSets, and
  not here either: the Composition's inlined vault-pki block (below) mirrors
  `appset-cert-manager-vault-pki`'s gate exactly — `network-platform: 'true'` and
  `network-platform/cert-manager-vault-pki` not `'false'` — so a cluster that only
  carries the umbrella gets both the ClusterIssuer *and* the prerequisites it
  needs, instead of an issuer with none. Each prerequisite hangs off its own
  input: the CA Secret off `vaultPkiSourceCaName`, the token Secret off
  `vaultPkiSourceTokenName`, the `certmanager` ServiceAccount off `vaultAuth`.
- **`storage-platform.stuttgart-things.com/nfs-config` is a gate, not a toggle.**
  `appset-nfs-csi-storageclasses` matches it with `Exists`, which `'false'`
  satisfies just as well as `'true'`. Set it on the XR only, together with the
  `storage-platform.stuttgart-things.com/nfs-server` / `/nfs-share` annotations it
  claims are present — never as a `'false'` default.
- **No reservation, no network platform.** All nine `network-platform` AppSets, plus
  `machinery`, `kargo-httproute` and `tekton-dashboard-httproute`, additionally
  gate on the operator-stamped `clusterbook.stuttgart-things.com/allocation-ip`.
  That label only exists when `spec.argocd.reservation.enabled` is `true`; with the
  default (`skipReservation: true`) a cluster is registered in Argo CD and
  otherwise bare.
- **Never set the `[auto]` annotations** (`cluster-name`, `ip`, `fqdn`,
  `fqdn-secondary`, `lb-range-*`) — the operator stamps those from the reservation.
- Preview platforms (`homerun2-pr-preview`, `machinery-pr-preview`,
  `machinery-catalog-locator-pr-preview`,
  `machinery-catalog-publisher-pr-preview`, `schmetterpause-pr-preview`) are
  single-label opt-ins with no umbrella — set one to `'true'` per XR.

A platform cluster (rather than a bare registration) therefore looks like:

```yaml
spec:
  argocd:
    register: true
    reservation:
      enabled: true                       # → allocation-ip + ip/fqdn annotations
      networkKey: '10.31.103'             # or EnvironmentConfig clusterbookNetworkKey
    labels:
      env: LabUL                            # git-path segment for the cicd AppSets
      network-platform: 'true'              # → all nine network components
      security-platform: 'true'             # → external-secrets + kyverno
      network-platform/trust-manager-bundle: 'false'   # …minus the ones you don't want
```

> The Composition registers through `kubeconfigSecretRef`, and that matters: on the
> operator's `existingSecretRef` ("enrich") path the same `spec.labels` are written
> **prefixed** with `clusterbook.stuttgart-things.com/`, which no platform selector
> matches.

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
- `provider-kubernetes`'s ServiceAccount must be able to **delete
  `clusterproviderconfigs.kubernetes.m.crossplane.io`** — see
  [`examples/rbac.yaml`](examples/rbac.yaml). Step (2) creates one, and the
  ClusterRole Crossplane generates for the provider grants every verb except
  `delete`, so without this the XR never finishes deleting. Skip it only where
  the provider's SA is already cluster-admin.

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
  are still joining (`generic`: registration on each node; `harvester`: VMs
  booting).
- **The node command arrives late.** With `nodeRegistration.publish` the second
  hop cannot be emitted before the first has observed a cluster ID, so
  `status.rancherClusterId` appears before `status.nodeCommandSecret` and the
  Secret after both.

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
