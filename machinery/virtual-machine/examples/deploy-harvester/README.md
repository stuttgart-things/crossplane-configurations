# deploy-harvester — Harvester/KubeVirt VM with Ansible provisioning

An apply-ordered set of manifests to stand up one Harvester `XVirtualMachine` in
LabUL with Ansible base-OS provisioning. The `ansible-credentials` Secret is
sourced from Vault via ESO. Files are numbered so a directory apply runs them in
dependency order — all `platform-*` setup first, the `user` request last:

```bash
kubectl apply -f machinery/virtual-machine/examples/deploy-harvester/
```

This is the Harvester sibling of [`deploy-vsphere`](../deploy-vsphere/). The
difference: `provider: harvester` routes to a **HarvesterVM**, which builds a
KubeVirt VirtualMachine through **provider-kubernetes** instead of OpenTofu — so
there is no tfvars Secret, and the VM provider config (file `2`) is a
provider-kubernetes `ClusterProviderConfig`, not an OpenTofu one.

| File | Scope | What |
|------|-------|------|
| `0-platform-namespaces.yaml`        | namespaced (creates) | `harvester-vms` (the XR) + `tekton-ci` (the Ansible PipelineRun) |
| `1-platform-configuration.yaml`     | cluster              | installs the `virtual-machine` package (pulls Functions + deps, incl. harvester-vm → provider-kubernetes) |
| `2-platform-providerconfig-harvester.yaml`  | cluster      | provider-kubernetes `ClusterProviderConfig` **`harvester`** — the VM target (InjectedIdentity = same cluster) |
| `2b-platform-providerconfig-harvester-remote.yaml` | cluster | **alternative** to `2`: same `harvester` config pointed at a REMOTE Harvester via a kubeconfig Secret. Commented out; use one or the other. See its header. |
| `3-platform-providerconfig-kubernetes.yaml` | cluster      | provider-kubernetes `ClusterProviderConfig` **`in-cluster`** — Ansible provider (name set by file `4`'s `ansible.crossplaneProviderConfig`) |
| `4-platform-environmentconfig.yaml` | cluster              | LabUL `EnvironmentConfig` (Harvester-only slice + cross-provider `ansible` block) |
| `5-platform-clustersecretstore.yaml`| cluster              | ESO `ClusterSecretStore` → Vault (serves the Ansible creds) |
| `6-platform-externalsecret-ansible-credentials.yaml` | namespaced | `ExternalSecret` → `ansible-credentials` in `tekton-ci` (VM SSH creds) |
| `7-user-xr.yaml`                    | namespaced           | the VM request — **the only file a consumer edits** |

`platform` files are one-time cluster/namespace setup (admin); `user` is the
per-VM request. `kubectl apply -f <dir>` processes files in lexical filename
order, so the numeric prefixes guarantee the sequence.

## Two providers, two jobs

Both jobs run through **provider-kubernetes**, but via two different configs:

- **VM** (`provider: harvester`) → config `2`/`2b` (`harvester`): the HarvesterVM
  creates the root-disk PVC (VolumeClaim), the cloud-init Secret (CloudInit) and
  the KubeVirt `VirtualMachine` as `Object`s on the Harvester cluster.
- **Ansible** (`ansible: true`) → config `3` (`in-cluster`): the emitted
  `AnsibleRun` wraps a Tekton `PipelineRun` in a provider-kubernetes `Object`,
  configured by name via `crossplaneProviderConfig`.
  > The name is **env-configurable**: it comes from the EnvironmentConfig
  > top-level `ansible.crossplaneProviderConfig` (file `4`), so it must match
  > `metadata.name` in file `3`. This bundle uses **`in-cluster`**. (If the
  > EnvironmentConfig omits the key, the Composition falls back to `in-cluster`
  > too — the harvester-vm/vm-provision leaf default.) To use a different
  > config, change both file `3` and file `4`.

The Ansible config is **always `in-cluster`** even when the VM config targets a
remote Harvester (2b): the Tekton PipelineRun runs on the management cluster.

## Adjust before a real apply

- **No live Harvester baked in.** File `4`'s `images`, `storageClassName`,
  `namespace`, `networkName`, `user`/`sshKey` are EXAMPLE values — substitute
  your Harvester environment.
- **Target namespace** — the KubeVirt VM, its PVC and cloud-init Secret land in
  the EnvironmentConfig `harvester.namespace` (default `vms`). That namespace
  must exist on the Harvester cluster; it is **not** created by file `0` (which
  only creates the management-cluster namespaces). For the same-cluster
  InjectedIdentity default, `kubectl create namespace vms` first.
- **Vault/ESO** (`5`/`6`): `server`, KV `path`, `mountPath` (`<cluster>-eso`)
  and the `default` KV path are LabUL values — swap for your cluster. If the
  `ClusterSecretStore` is already GitOps-managed (fleet default), **delete `5`**
  and keep `6` pointing at it. `6` templates `vm_ssh_user` / `vm_ssh_password`
  from Vault into `ANSIBLE_USER` / `ANSIBLE_PASSWORD` — keep `ANSIBLE_USER` in
  sync with file `4`'s `harvester.user`.
- **ESO must be installed** — `external-secrets.io/v1` CRDs + a Vault role
  binding the `eso` ServiceAccount with read on `cicd-harvester-labul/data/*`.
- **Remote Harvester** — to provision onto a separate Harvester cluster, use
  `2b` (kubeconfig Secret) instead of `2`; materialize the kubeconfig Secret
  via ESO (mirror `6`) so it's never committed.

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
3. **QEMU guest agent in the image** — the VM IP (which gates the AnsibleRun) is
   reported by the in-guest agent onto the VMI. The harvester-vm XRD defaults
   `cloudInit.packages` to `[qemu-guest-agent]`; keep it unless your image bakes
   the agent in.

To deploy a VM **without** provisioning, set `ansible: false` in `7-user-xr.yaml`
— then only `2`/`2b` and `4` matter and `3`/`5`/`6` + the above are unneeded.

`crossplane render` can't exercise the ESO/runtime pieces (`3`/`5`/`6`); their
shapes mirror `machinery/vsphere-vm/examples/external-secret.yaml` and
`cicd/ansible-run/README.md`.

## Observe progress

First, confirm the ESO secret synced (the Ansible step blocks on it):

```bash
kubectl get clustersecretstore vault-cicd-harvester-labul
kubectl get externalsecret -A | grep ansible-credentials   # want SecretSynced / READY=True
```

Then watch the composed chain. Best single view (needs the crossplane CLI):

```bash
crossplane beta trace xvirtualmachine.resources.stuttgart-things.com/vm-harvester -n harvester-vms
```

Or piece by piece:

```bash
# 1) top-level XR — SYNCED/READY + status (share.ip, vm.ready, ansibleReady)
kubectl -n harvester-vms get xvirtualmachine vm-harvester
kubectl -n harvester-vms get xvirtualmachine vm-harvester -o jsonpath='{.status}{"\n"}'

# 2) VM side: HarvesterVM → VolumeClaim + CloudInit + the KubeVirt VM/VMI Objects
kubectl -n harvester-vms get harvestervm,volumeclaim,cloudinit
kubectl -n vms get virtualmachine,virtualmachineinstance   # on the Harvester cluster

# 3) Ansible side: gated on the VM having an IP → AnsibleRun → Object → PipelineRun
kubectl -n harvester-vms get ansiblerun
kubectl -n tekton-ci get pipelinerun -w
```

Expected order: the VolumeClaim/CloudInit/VM build first; the QEMU guest agent
reports the VM IP onto `status.share.ip`; **then** the `AnsibleRun` appears (it's
gated on a non-empty IP, so it won't exist until the VM is up) and creates the
PipelineRun in `tekton-ci` via the `in-cluster` provider config. XR `READY=True`
once the PipelineRun succeeds.
