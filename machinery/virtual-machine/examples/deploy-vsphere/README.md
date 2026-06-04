# deploy-vsphere — vSphere VM with Ansible provisioning

An apply-ordered set of manifests to stand up one vSphere `XVirtualMachine` in
LabUL with Ansible base-OS provisioning. The `vsphere-tfvars` and
`ansible-credentials` Secrets are sourced from Vault via ESO. Files are numbered
so a directory apply runs them in dependency order — all `platform-*` setup
first, the `user` request last:

```bash
kubectl apply -f machinery/virtual-machine/examples/deploy-vsphere/
```

| File | Scope | What |
|------|-------|------|
| `0-platform-namespaces.yaml`        | namespaced (creates) | `vsphere-vms` (the VM) + `tekton-ci` (the Ansible PipelineRun) |
| `1-platform-configuration.yaml`     | cluster              | installs the `virtual-machine` package (pulls Functions + deps, incl. provider-kubernetes) |
| `2-platform-providerconfig-opentofu.yaml`   | cluster      | OpenTofu `ClusterProviderConfig` (`default`) — VM provider, the default |
| `2b-platform-providerconfig-opentofu-namespaced.yaml` | namespaced | **alternative** to `2`: namespaced OpenTofu `ProviderConfig` in `vsphere-vms`. Commented out; use one or the other. See its header. |
| `3-platform-providerconfig-kubernetes.yaml` | cluster      | provider-kubernetes `ClusterProviderConfig` **`in-cluster`** — Ansible provider (name set by file `4`'s `ansible.crossplaneProviderConfig`) |
| `4-platform-environmentconfig.yaml` | cluster              | LabUL `EnvironmentConfig` (vSphere-only slice) |
| `5-platform-clustersecretstore.yaml`| cluster              | ESO `ClusterSecretStore` → Vault (serves both ExternalSecrets) |
| `6-platform-externalsecret-tfvars.yaml` | namespaced       | `ExternalSecret` → `vsphere-tfvars` in `vsphere-vms` (VM build creds) |
| `7-platform-externalsecret-ansible-credentials.yaml` | namespaced | `ExternalSecret` → `ansible-credentials` in `tekton-ci` (VM SSH creds) |
| `8-user-xr.yaml`                    | namespaced           | the VM request — **the only file a consumer edits** |

`platform` files are one-time cluster/namespace setup (admin); `user` is the
per-VM request. `kubectl apply -f <dir>` processes files in lexical filename
order, so the numeric prefixes guarantee the sequence.

## Two providers, two jobs

- **VM** (`vsphere`) runs through **provider-opentofu** — config `2`/`2b`.
- **Ansible** (`ansible: true`) runs through **provider-kubernetes**: the
  emitted `AnsibleRun` wraps a Tekton `PipelineRun` in a provider-kubernetes
  `Object`, configured by name via `crossplaneProviderConfig` — config `3`.
  > The name is **env-configurable**: it comes from the EnvironmentConfig
  > top-level `ansible.crossplaneProviderConfig` (file `4`), so it must match
  > `metadata.name` in file `3`. This bundle uses **`in-cluster`**. (If the
  > EnvironmentConfig omits the key, the Composition falls back to `in-cluster`
  > too.) To use a different config, change both file `3` and file `4`.

## Adjust before a real apply

- **Vault/ESO** (`5`/`6`/`7`): `server`, KV `path`, `mountPath` (`<cluster>-eso`)
  and the `default` KV path are LabUL values — swap for your cluster. If the
  `ClusterSecretStore` is already GitOps-managed (fleet default), **delete `5`**
  and keep `6`/`7` pointing at it. `7` reuses the VM SSH creds (`vm_ssh_user` /
  `vm_ssh_password`) from the same Vault path, templated into `ANSIBLE_USER` /
  `ANSIBLE_PASSWORD`.
- **ESO must be installed** — `external-secrets.io/v1` CRDs + a Vault role
  binding the `eso` ServiceAccount with read on `cicd-vsphere-labul/data/*`.
- **Topology** (`4`): the datacenter/datastore/network paths are LabUL examples.

## Ansible prerequisites (external — not in this folder)

The `ansible: true` path additionally needs cluster infra this bundle does
**not** ship, because it's shared and/or environment-specific:

1. **Tekton stack** — the Tekton Pipelines controller, plus the Pipeline the
   `AnsibleRun` references (the `stuttgart-things/stage-time` Ansible pipeline).
   `tekton-ci` (created by `0`) is only the run namespace, not the controller.
2. **RBAC for the provider-kubernetes ServiceAccount** — it must be allowed to
   manage Tekton resources in `tekton-ci`. The SA name is dynamic; find it with
   `kubectl get sa -n crossplane-system | grep provider-kubernetes` and bind a
   ClusterRole as shown in `cicd/ansible-run/README.md` (precondition #3).

To deploy a VM **without** provisioning, set `ansible: false` in `8-user-xr.yaml`
— then only `2`/`2b`, `4`, `5`, `6` matter and `3`/`7` + the above are unneeded.

`crossplane render` can't exercise the ESO/runtime pieces (`3`/`5`/`6`/`7`);
their shapes mirror `machinery/vsphere-vm/examples/external-secret.yaml` and
`cicd/ansible-run/README.md`.

## Observe progress

First, confirm the ESO secrets synced (the VM/Ansible steps block on them):

```bash
kubectl get clustersecretstore vault-cicd-vsphere-labul
kubectl get externalsecret -A | grep -E 'vsphere-tfvars|ansible-credentials'   # want SecretSynced / READY=True
```

Then watch the composed chain. Best single view (needs the crossplane CLI):

```bash
crossplane beta trace xvirtualmachine.resources.stuttgart-things.com/vm-standard -n vsphere-vms
```

Or piece by piece:

```bash
# 1) top-level XR — SYNCED/READY + status (share.ip, vmReady, ansibleReady)
kubectl -n vsphere-vms get xvirtualmachine vm-standard
kubectl -n vsphere-vms get xvirtualmachine vm-standard -o jsonpath='{.status}{"\n"}'

# 2) VM side: VMProvision → opentofu Workspace (the actual terraform apply)
kubectl -n vsphere-vms get vmprovision,workspace.opentofu.m.upbound.io
kubectl -n vsphere-vms describe workspace.opentofu.m.upbound.io   # apply logs/errors if stuck

# 3) Ansible side: gated on the VM having an IP → AnsibleRun → Object → PipelineRun
kubectl -n vsphere-vms get ansiblerun
kubectl -n tekton-ci get pipelinerun -w
```

Expected order: the Workspace applies the VM first; the IP populates
`status.share.ip`; **then** the `AnsibleRun` appears (it's gated on a non-empty
IP, so it won't exist until the VM is up) and creates the PipelineRun in
`tekton-ci` via the `in-cluster` provider config. XR `READY=True` once the
PipelineRun succeeds.
