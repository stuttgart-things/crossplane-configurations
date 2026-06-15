# proxmoxvm

A Crossplane v2 Configuration that provisions a Proxmox VE VM from a namespaced
`NativeProxmoxVM` XR (group `resources.stuttgart-things.com`) via the **native**
[`provider-proxmox-bpg`](https://github.com/valkiriaaquatica/provider-proxmox-bpg)
managed `EnvironmentVM` resource — no Terraform/OpenTofu.

It is the native-provider sibling of the OpenTofu-based [`proxmox-vm`](../proxmox-vm)
Configuration:

| | `proxmox-vm` (OpenTofu) | `proxmoxvm` (native) |
|---|---|---|
| Engine | OpenTofu `Workspace` → Telmate `proxmox_vm_qemu` | `provider-proxmox-bpg` `EnvironmentVM` MR (bpg `proxmox_virtual_environment_vm`) |
| Upstream TF provider | `Telmate/proxmox` | `bpg/terraform-provider-proxmox` |
| Template clone | by **name** (`sthings-u26`) | by numeric **VMID** (`clone[].vmId`) |
| Guest bootstrap | SSH `remote-exec` (hostname/machine-id/reboot) | **cloud-init** (`initialization`) |
| Credentials | tfvars Secret | Proxmox creds Secret via ProviderConfig |

## How it works

The Composition is a `function-kcl` pipeline:

1. **load-environment** (`function-environment-configs`) — loads the
   per-environment placement from the `EnvironmentConfig` selected by the
   config-scoped label
   `proxmoxvm.resources.stuttgart-things.com/environment=<spec.environmentConfig>`.
2. **render** (`function-kcl`) — emits a native `EnvironmentVM`; per-VM fields
   (name/cpu/memory/disk/…) come from the XR, placement (node/datastore/bridge/
   vlanTag/pool/templateVmId) defaults from the EnvironmentConfig and is
   overridable per XR. Cloud-init (`initialization`) replaces the legacy SSH
   remote-exec bootstrap. **Note:** Upjet encodes each Terraform block as a
   single-element list, so `cpu`/`memory`/`disk`/`networkDevice`/`clone`/
   `initialization` are lists in `forProvider`.
3. **patch-status** — surfaces `status.share.ip / vmId / started` onto the XR.

So an XR only states intent; the lab topology is injected:

```yaml
apiVersion: resources.stuttgart-things.com/v1alpha1
kind: NativeProxmoxVM
metadata:
  name: proxmoxvm-test1
  namespace: default
spec:
  environmentConfig: default     # or labul
  vm:
    name: proxmoxvm-test1
    cpu: "2"
    memory: "4096"
    disk: "64"
```

## Template VMID (clone)

The bpg provider clones a template by its **numeric VMID**, not its name. Resolve
your prepared template to its VMID and set it on the EnvironmentConfig
(`templateVmId`) — or per XR via `spec.vm.templateVmId`. This is the one
behavioural difference from the Telmate-based `proxmox-vm` module, which clones by
name. In the LabUL fleet, `sthings-u26` is **VMID 110** on `ul-pve01` (the example
EnvironmentConfig is set accordingly).

Two template-specific gotchas the example values already account for:

- **Disk bus is `virtio0`, not `scsi0`.** The `sthings-u26` root disk lives on
  `virtio0`; a `scsi0` `diskInterface` would target a non-existent disk on clone.
  The EnvironmentConfig defaults `diskInterface: virtio0`.
- **The Packer templates ship no cloud-init drive.** When `spec.cloudInit` is set,
  bpg adds a cloud-init drive on clone, which needs a datastore. The Composition
  defaults `initialization.datastoreId` to the root disk's datastore; override via
  `spec.cloudInit.datastoreId`. (The Telmate path instead provisions over SSH via
  Ansible — key-based cloud-init is a newer path for these templates, so validate
  it on first use.)

## IP surfacing (`status.share.ip`)

The IP is read from the observed `EnvironmentVM`'s
`status.atProvider.ipv4Addresses` (a list-of-lists, one per interface). It is
only populated when the **QEMU guest agent** is installed in the template and
`spec.vm.agentEnabled` is true (the default). Loopback (`127.0.0.1`) is filtered
out; the first remaining address wins. It stays `[]` on the first reconcile and
offline `render` — by design, no template error.

## Ansible (optional)

Set `spec.ansible.enabled: true` to run base-OS provisioning. Once the VM is
Ready with an IP, the Composition emits an `AnsibleRun` (Tekton) whose inventory
is auto-populated with the VM IP. Shared fields (`playbooks`, `varsFile`,
`gitRepoUrl`, `ansibleWorkingImage`, `credentialsSecretName`,
`crossplaneProviderConfig`, `pipelineNamespace`) fall back to the
EnvironmentConfig `ansible` sub-block when unset. This reuses the
[`ansible-run`](../../cicd/ansible-run) Configuration and needs its preconditions
on the cluster: Tekton, the ansible credentials Secret (default
`ansible-credentials`), and an in-cluster `provider-kubernetes` config.
`status.share.ansibleReady` reflects the run's readiness.

## Cluster preconditions

- `provider-proxmox-bpg` installed (declared as `dependsOn`; also see
  `examples/provider.yaml`).
- A `ClusterProviderConfig` (`proxmoxbpg.m.crossplane.io/v1beta1`) named per the
  XR's `spec.providerConfigRef` (default kind `ClusterProviderConfig`, name
  `default`), pointing at a Proxmox credentials Secret. The Composition emits the
  namespaced (`.m`) `EnvironmentVM` — a namespaced composite cannot compose a
  cluster-scoped resource — so it references a `.m` (Cluster)ProviderConfig. See
  `examples/clusterproviderconfig.yaml`.
  > The Secret's `credentials` value is a JSON blob whose keys map to the bpg
  > provider config: `endpoint`, `api_token` (or `username`+`password`),
  > `insecure`, and an optional `ssh` block (`ssh_username` / `ssh_password` /
  > `ssh_private_key`). bpg uses SSH to the node for some operations (e.g.
  > uploading cloud-init snippets), so SSH creds may be required depending on
  > the flow.
- The `EnvironmentConfig`(s) for the target environment(s) — see
  `examples/environmentconfig.yaml`.

## Render locally

```bash
CONFIG=machinery/proxmoxvm XR=machinery/proxmoxvm/examples/xr.yaml task render
```

The render needs the EnvironmentConfig passed via `--extra-resources` (the
`task render` / verify harness defaults to `examples/environmentconfig.yaml`).
Offline render does not apply XRD defaults or contact the cluster, so the IP and
clone block (which needs `templateVmId`) only fully materialise with the
EnvironmentConfig supplied.
