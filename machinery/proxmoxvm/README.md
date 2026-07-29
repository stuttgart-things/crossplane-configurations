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
## SMBIOS (`PegaProxManagment` / bpg `illegal base64 data`)

bpg base64-decodes the SMBIOS type1 fields (`manufacturer`/`product`/`version`/
`serial`/`family`) on **every** read. The LabUL Proxmox node (`ul-pve01`) is managed
by **[PegaProx](https://github.com/PegaProx/project-pegaprox)**, whose *SMBIOS
Auto-Configurator* — a systemd service on the node — stamps every **new** VM with
**plain-text** `smbios1` (`manufacturer=Proxmox,product=PegaProxManagment,version=v1,
serial=PVE…,family=ProxmoxVE`, no `base64=1` flag). Because it fires on each new VMID,
it hits every bpg **clone**, so without mitigation every VM fails `observe`/`refresh`
with `illegal base64 data at input byte N` (one per plain field) — the VM boots but
never reaches Ready, and delete blocks.

**Mitigation (built into the Composition):** the `render` step emits an explicit
`smbios` block (`manufacturer`/`product`) on the `EnvironmentVM`. bpg stores it
**base64-encoded** (`base64=1`) during create — which it reads back cleanly — and
PegaProx then **skips** the VM (its `needs_smbios_update()` leaves any VM that already
has SMBIOS keys alone). Override the values via the EnvironmentConfig
(`smbiosManufacturer` / `smbiosProduct`). This is a workaround for the node-level
PegaProx behaviour, which we don't control; the root-cause fix would be to disable or
base64-enable PegaProx's auto-configurator on the node.

## IP surfacing (`status.share.ip`)

The IP is read from the observed `EnvironmentVM`'s
`status.atProvider.ipv4Addresses` (a list-of-lists, one per interface). It is
only populated when the **QEMU guest agent** is installed in the template and
`spec.vm.agentEnabled` is true (the default). Loopback (`127.0.0.1`) is filtered
out; the first remaining address wins. It stays `[]` on the first reconcile and
offline `render` — by design, no template error.

## Guest hostname

**The PVE VM name is the guest hostname.** bpg has no hostname field, but PVE has
an implicit one: any VM with an `initialization` block — which this Composition
always emits, for `ipConfig` — gets PVE-generated cloud-init user-data containing

```yaml
hostname: <VM name>
fqdn: <VM name>
manage_etc_hosts: true
```

and `hostname` in **user-data** outranks every NoCloud meta-data source. So the
Composition sets `forProvider.name` to the requested hostname —
`spec.cloudInit.hostname` → `spec.vm.name` → `metadata.name` — rather than to the
resource name. PVE does not require VM names to be unique (VMID is the key), so
prefixing for grouping is unnecessary; use tags, a pool, or the description.

### Why the SMBIOS seed is not used

v0.6.0 ([#200](https://github.com/stuttgart-things/crossplane-configurations/pull/200))
shipped a NoCloud DMI seed (`smbios.serial = ds=nocloud;h=<host>;i=<id>`) as the
hostname mechanism. **It does not work here, and v0.7.0 removes it.**

Measured on LabUL 2026-07-29 with a 2-VM `vm-batch`: the seed was delivered
correctly — `serial` decoded to `ds=nocloud;h=web-0;i=fin-web-0` on the VM — and
was still ignored. Both guests came up as their PVE names, `fin-web-0` and
`fin-web-1`, matching the generated user-data exactly. The smoke test that
justified #200 passed only because that VM had been cloned by hand *without* an
`initialization` block, so nothing competed with the seed; every VM this
Composition emits has one.

`snippetsDatastore` / `metaDataFileId` is no alternative either: it replaces the
generated **meta-data**, while `hostname:` lives in the generated **user-data**,
which it does not touch. Overriding that needs `userDataFileId` — another
snippet, so bpg would have to SSH into the PVE node. There is no API path
(`POST /nodes/<node>/storage/<store>/upload` with `content=snippets` returns
`400 value 'snippets' does not have a value in the enumeration 'iso, vztmpl,
import'`) and node SSH is unavailable in LabUL, where the capability chart
supplies *guest* credentials under bpg's `ssh_username`/`ssh_password`. The
snippet is kept only to pin the instance-id.

### Contrast with `vspherevm`

`vspherevm` sets the hostname through `guestinfo.metadata` and keeps
`forProvider.name` as the resource name, because vSphere requires inventory names
to be unique and supplies no competing datasource. The two Configurations are
deliberately asymmetric here.

### Needs a template that runs cloud-init

All of this is inert if the image ships `/etc/cloud/cloud-init.disabled` —
`cloud-init status: disabled`, and the guest keeps `ubuntu` no matter what is
configured. That was true of the ubuntu26 templates until
[stuttgart-things#2432](https://github.com/stuttgart-things/stuttgart-things/issues/2432);
rebuilt templates (LabUL VMID 192 onward) run cloud-init and pick the name up.

## Ansible (optional)

Set `spec.ansible.enabled: true` to run base-OS provisioning. Once the VM is
Ready with an IP, the Composition emits an `AnsibleRun` (Tekton) whose inventory
is auto-populated with the VM IP.

> **Guest hostname.** The run also receives `vm_hostname+-<hostname>`
> automatically (same precedence as above); an explicit `vm_hostname` in
> `varsFile` wins. It is a **fallback** for templates where cloud-init cannot
> run — see [Guest hostname](#guest-hostname) — and it agrees with the VM name,
> so on a working template it is a no-op rather than a second source of truth.
> It cannot help a `vm-batch`, which emits one fleet-wide run and so has only
> one `vm_hostname` for N hosts; the VM name does work per-VM there.

Shared fields (`playbooks`, `varsFile`,
`gitRepoUrl`, `ansibleWorkingImage`, `credentialsSecretName`,
`crossplaneProviderConfig`, `pipelineNamespace`) fall back to the
EnvironmentConfig `ansible` sub-block when unset. This reuses the
[`ansible-run`](../../cicd/ansible-run) Configuration and needs its preconditions
on the cluster: Tekton, the ansible credentials Secret (default
`ansible-credentials`), and an in-cluster `provider-kubernetes` config.
`status.share.ansibleReady` reflects the run's readiness.

## Cluster preconditions

- The native Proxmox provider — **auto-installed via the Configuration's
  `dependsOn`**, so it is not a manual step. Crossplane names it
  `valkiriaaquaticamendi-provider-proxmox-bpg` (derived from the upstream package's
  own metadata; a clean short name needs the ghcr republish in issue #78). Do
  **not** also apply a short-named `provider-proxmox-bpg` for the same package — it
  becomes a duplicate that fights for CRD ownership and can freeze the resolver.
  `examples/provider.yaml` exists only to pin an exact version and is named to match
  the dependsOn-managed provider (so it adopts it rather than duplicating).
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
  > **All values are strings** — `insecure` must be `"true"`/`"false"` (a bare
  > JSON bool fails with `cannot unmarshal bool into Go value of type string`).
  > **`endpoint`** is the bare base URL (`https://host:8006/`) — do *not* append
  > `/api2/json`. **`username`** is realm-qualified (e.g. `phermann@LabUL`).
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
