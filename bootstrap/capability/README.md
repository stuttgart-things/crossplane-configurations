# capability

Configures what a management cluster can **do**.

A provider is installed on every management cluster and on its own does nothing
useful:

| missing | consequence |
|---|---|
| `ClusterProviderConfig` | the provider runs and does not know where to connect |
| credentials | it knows where, and may not log in |
| `EnvironmentConfig` | it is logged in and does not know which node, datastore or template to place a VM on |

A namespaced `Capability` XR names the capabilities a cluster should have and
their placement facts; the Composition emits those objects onto the target
cluster through `provider-kubernetes`, one set per enabled capability.

```yaml
apiVersion: config.stuttgart-things.com/v1alpha1
kind: Capability
metadata:
  name: u26-rke2-1-capability
  namespace: default
spec:
  clusterName: u26-rke2-1
  kubernetesProviderConfigRef: u26-rke2-1-kubernetes
  environment: labul
  capabilities:
    proxmoxvm:
      enabled: true
      placement:
        node: ul-pve01
        datastore: V5010-01-1
        bridge: vmbrvlan
        vlanTag: "102"
        pool: stuttgart-things
        templateVmId: "211"
```

That XR produces five objects on the target: the `ClusterSecretStore`, the
credential `ExternalSecret`, the `ClusterProviderConfig`, the
`EnvironmentConfig`, and the cloud-init password `ExternalSecret`. Everything not stated — `cpuType`, `bios`,
`diskInterface`, the annotation, `providerConfigName` — is a catalog default or
derived.

## Structure vs. values

Structure lives in the
[`xplane-capability-catalog`](https://github.com/stuttgart-things/kcl/tree/main/crossplane/xplane-capability-catalog)
KCL module: which `ClusterProviderConfig` kind a provider reads, how one Vault
KV secret maps into the credential payload it expects, which placement fields
exist. Values are per-cluster and stay on the XR. Same split as
[platform](../platform/)'s app catalog, for the same reason.

Field names are **transcribed** from the capability Helm charts in
`stuttgart-things/stuttgart-things`, because the `proxmoxvm` and `vspherevm`
Compositions look them up by name. A tidier vocabulary would be a silent break.

## Why this exists next to the Helm charts

The charts do the same job and keep doing it for clusters without Vault. Two
things get better here, and both follow from running *inside* Crossplane:

**Two values stop being deploy-time parameters.** The charts take

```yaml
authMountPath: test-k3s-eso      # "appset injects <cluster>-eso per cluster"
secretStore.name: vault-cicd-proxmox-labul
```

Their own comment says the mount is injected from outside. Here the cluster name
is the XR's, so `<clusterName>-eso` is derived where the fact lives.

That only means something because the store is emitted **here**. A
`ClusterSecretStore` fixes ONE Vault KV mount, and capabilities do not share
one: proxmox credentials live under `cicd-proxmox-labul`, vsphere under its own,
while the cluster's existing `vault-cluster-secrets` points at `clusters`. So a
capability names its `mount` and gets a store; server, CA, auth mount, role and
ServiceAccount are cluster properties, stated once or derived. A capability may
instead name an existing store, in which case nothing is created — and the
derivation belongs to whoever made it.

**Package installation stays out.** The charts can install the Configuration and
the Provider, which is what their long comments about duplicate lock nodes are
about. That belongs to [management-plane](../management-plane/), which installs
packages under the CR names Crossplane itself derives — the shape in which the
[#247](https://github.com/stuttgart-things/crossplane-configurations/issues/247)
collision cannot occur.

## Two namespaces, not one

`spec.namespace` is where the provider reads its credentials.
`spec.workloadNamespace` is where the VM XRs live, and it is where a
capability's *workload* secrets go — the cloud-init password, for `proxmoxvm`.

The namespaced `EnvironmentVM` CRD resolves `passwordSecretRef` in the managed
resource's own namespace, so a cloud-init password placed beside the provider is
a Secret nothing reads. The symptom is not a missing Secret: without a
`cipassword` in the user-data Proxmox emits, cloud-init applies its
`lock_passwd` default and LOCKS the guest account on first boot, discarding what
the Packer build baked in. Key logins keep working, so the cluster looks
healthy — what breaks is every password-based `AnsibleRun`, each host simply
`UNREACHABLE`.

## Failing the render

A capability with a missing or unknown placement field aborts the whole render
and names every problem, across every capability, in one message:

```
capability configuration is incomplete: proxmoxvm: missing required placement ["node"]
```

* **Missing** — a missing `node` does not surface as a validation error. It
  surfaces minutes later as a VM the provider tries to place nowhere, in a
  message that names the provider and not this XR.
* **Unknown** — a typo'd `templateVMID` would otherwise be dropped in silence,
  leave `templateVmId` missing, and produce an error naming the field the user
  did *not* write.
* **All at once** — a render is all-or-nothing, so one field per reconcile is
  the difference between one round-trip and six.

An empty string counts as missing: an XRD default or a half-filled values file
arrives as `""`, exactly as unusable as absent for a node name.

## Readiness

Not one policy for all four objects.

| object | policy | why |
|---|---|---|
| `ClusterSecretStore` | `DeriveFromObject` | its `Valid` condition IS the proof that the Vault login works |
| `ExternalSecret` | `DeriveFromObject` | its `Ready` condition is the only signal that Vault answered — a capability whose credentials 403 must not report ready |
| `EnvironmentConfig` | `DeriveFromCelQuery` (`true`) | it has no conditions at all |
| `ClusterProviderConfig` | `DeriveFromCelQuery` (`true`) | same, and measured in [#294](https://github.com/stuttgart-things/crossplane-configurations/issues/294): `SuccessfulCreate` was never evaluated, `AllTrue` false on an empty condition list |

`DeriveFromObject` everywhere leaves the two status-less objects at
`Ready=False` forever while what they created is present and correct — found on
the first live apply against `u26-rke2-1`.

## Naming

Objects are `<capability>-<environment>` — the store included, rather than the
charts' `vault-<mount>` — and credentials `<capability>-creds-<environment>`. Two environments on one cluster (labda *and*
labul) must not share a `ClusterProviderConfig` or a Secret.

`environment` defaults to `clusterName` but usually should not stay there: the
placement facts are properties of a *lab*, so a second cluster in the same lab
wants the same label for its `EnvironmentConfig` to mean the same thing.

The credentials name deviates from the charts' `proxmox-creds-<env>` on purpose.
On a cluster that still has the chart installed, colliding would give one Secret
two owners and each ESO refresh would overwrite the other. Nothing else refers
to the name — only the `ClusterProviderConfig` this Configuration also emits —
so a migration means deleting the chart's Secret, not renaming anything.

## Two LabUL values that are easy to get wrong

Both were found by building a VM through this Configuration rather than by
reading the chart it was transcribed from:

* **`node: ul-pve11`.** The active LabUL Proxmox nodes are `ul-pve10` and
  `ul-pve11`. The capability Helm chart still carries `ul-pve01`, so copying it
  builds nothing and the error names the node rather than where the value came
  from.
* **`cloneDatastore: V5010-01-1` is required, not an optimisation.** Without it
  Proxmox allocates the clone on the *template's* datastore — the NFS store
  `DD-sthings`, where an ACL added in 2026-08 removed `Datastore.AllocateSpace`
  — and every clone 403s. It is safe to set on a NEW environment; on one that
  already has VMs it rewrites their `ForceNew` clone block (destroy + recreate),
  so pin those with the `none` sentinel first.

`xr-min.yaml` deliberately omits `cloneDatastore`: its job is to exercise the
XRD's required set and the catalog defaults, not to be deployable in LabUL.

## Cluster preconditions

- the `ClusterProviderConfig` named by `spec.kubernetesProviderConfigRef`
- external-secrets on the target with a `ClusterSecretStore`
  (`vault-cluster-secrets` is what [platform](../platform/)'s `external-secrets`
  app creates), and a Vault path holding the capability's keys
- the provider whose config is emitted, installed by
  [management-plane](../management-plane/)

## Not covered

The `sops` and `sops-git` credential backends the charts offer. They exist so a
cluster **without** Vault can still have credentials, and such a cluster cannot
use this Configuration at all — it would get an `ExternalSecret` nothing serves.
For those clusters the charts remain the answer.

## Capabilities

| capability | Vault keys | required placement |
|---|---|---|
| `proxmoxvm` | `pve_api_url`, `pve_api_user`, `pve_api_password`, `vm_ssh_user`, `vm_ssh_password` | `node`, `datastore`, `bridge`, `vlanTag`, `pool`, `templateVmId` |
| `vspherevm` | `vsphere_user`, `vsphere_password`, `vsphere_server` | `templateUuid`, `datastoreId`, `resourcePoolId`, `networkId`, `folder`, `domain` |

Adding one is an entry in the catalog module, not a change here.
