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
name. In the LabUL fleet, `sthings-u26` is **VMID 211** on **`ul-pve11`** (the
example EnvironmentConfig is set accordingly).

Two things that number carries. **Not 110**, which is also called `sthings-u26`:
it ships `/etc/cloud/cloud-init.disabled`, so cloud-init never runs and every
clone keeps the hostname `ubuntu` regardless of what is injected
(stuttgart-things#2432) — the VM comes up healthy, just not as the host you asked
for. And **not `ul-pve01`**: the LabUL rebuild moved the ubuntu26 templates, and
bpg derives the clone *source* node from `node` unless `templateNode` is set, so
a stale value fails the build with `unable to find configuration file for VM 211
on node 'ul-pve01'`.

Three template-specific gotchas the example values already account for:

- **Disk bus is `virtio0`, not `scsi0`.** The `sthings-u26` root disk lives on
  `virtio0`; a `scsi0` `diskInterface` would target a non-existent disk on clone.
  The EnvironmentConfig defaults `diskInterface: virtio0`.
- **The clone allocates on the TEMPLATE's datastore unless you say otherwise.**
  Proxmox puts the new disk next to the source and bpg then moves it to
  `spec.vm.datastore` — a `qmclone` immediately followed by a `qmmove` in the PVE
  task log. So the full image is written twice, and the clone needs
  `Datastore.AllocateSpace` on the template's storage even when the VM is not
  meant to live there. Set `spec.vm.cloneDatastore` to clone straight onto the
  target.

  This is not hypothetical: in LabUL the templates live on the NFS store
  `DD-sthings`, and an ACL added there in early 2026-08 (group
  `LabUL-VC-Benutzer-LabUL` → role `SVATemplates`, i.e. `AllocateTemplate` +
  `Audit` only) took that priv away. Proxmox resolves the **deepest matching ACL
  path** instead of unioning up the tree, so an `Administrator` grant at `/` does
  not restore it, and every clone fails with
  `403 Permission check failed (/storage/DD-sthings, Datastore.AllocateSpace)`.

  `cloneDatastore` is **opt-in and must stay that way**: bpg's clone block is
  ForceNew, so a value where a live VM previously rendered none is a spec change
  on an immutable field and the provider answers with destroy + recreate.

  **Rolling it out per environment.** Putting `cloneDatastore` on an
  EnvironmentConfig hits every VM under it — including the ones already built,
  same ForceNew problem at fleet scale, and unattended wherever
  `compositionUpdatePolicy: Automatic`. Use the `none` sentinel, in this order:

  1. bump the package to **v0.11.0 or later** everywhere that EnvironmentConfig
     is used;
  2. set `cloneDatastore: none` on every **already-built** VM's XR and confirm
     each still renders a clone block *without* `datastoreId`;
  3. only then add the real value to the EnvironmentConfig.

  `none` renders exactly as if the field were absent and is the **only** way an
  XR can decline an EnvironmentConfig value — `cloneDatastore: ""` is falsy in
  KCL and falls straight through to the environment.

  **Do not skip step 1.** Against v0.10.0 the sentinel is read as a datastore
  literally *named* `none`, which triggers the destroy + recreate the whole
  procedure exists to avoid.
- **The Packer templates ship no cloud-init drive.** When `spec.cloudInit` is set,
  bpg adds a cloud-init drive on clone, which needs a datastore. The Composition
  resolves `initialization.datastoreId` as `spec.cloudInit.datastoreId` →
  EnvironmentConfig `cloudInitDatastore` → the root disk's datastore. (The Telmate
  path instead provisions over SSH via Ansible — key-based cloud-init is a newer
  path for these templates, so validate it on first use.)
- **Put the cloud-init drive on a different storage than the data disk.** The
  cidata image is the one disk PVE frees and re-allocates on every stop/start, and
  on LabUL's `V5010-01-1` that round trip is broken. The stop fails to stat
  `/dev/V5010-01-1/vm-<id>-cloudinit.qcow2` and leaves the LV in the VG metadata
  with no device node behind it; the next start then dies on

  ```
  lvcreate ... error: Logical Volume "vm-<id>-cloudinit.qcow2" already exists in volume group "V5010-01-1"
  ```

  and the VM stays unbootable. Recovery is to **pause the MR first** — otherwise
  the provider's retry loop orphans a fresh LV within seconds (~20 failed starts
  observed) — then delete the volume through the API and unpause:

  ```
  kubectl annotate environmentvm.virtualenvironmentvm.proxmoxbpg.m.crossplane.io <name> crossplane.io/paused=true
  DELETE /nodes/<node>/storage/V5010-01-1/content/V5010-01-1:vm-<id>-cloudinit.qcow2
  ```

  The cidata image is regenerated on start and the data disk is untouched. Not
  node-bound (seen on `ul-pve11` and `ul-pve02`, 6 of 6 stopped VMs) and limited to
  the cloud-init volumes — the `raw` data disks are fine. The LabUL admin confirmed
  on 2026-08-20 that the same VM with its cidata image on the NFS store
  `DD-sthings` stops and starts cleanly, which is why the shipped LabUL
  EnvironmentConfig sets `cloudInitDatastore: DD-sthings`. The storage-side root
  cause is unfixed.
## SMBIOS (`PegaProxManagment` / bpg `illegal base64 data`)

> **RESOLVED 2026-08-10 — the root cause was removed, not worked around.** The
> LabUL Proxmox admin **deleted the SMBIOS Auto-Configurator from the nodes**.
> Everything below still describes the mechanism and stays useful for two
> reasons: the component is reinstallable from the PegaProx UI in a few clicks
> (their own tracker has an issue about installing it from *Settings*), and it
> **does not repair existing VMs** — 133 of 137 VMs in the cluster still carry a
> plain-text stamp, so any older VM later brought under Crossplane management
> still needs the repair procedure.
>
> Verified rather than assumed: a fresh VM built by this Composition (VMID 250,
> running, on `ul-pve01`, pool `stuttgart-things`) kept `base64=1` for 50+
> minutes — about 5.5x the ~9-minute window in which a comparable VM was stamped
> earlier the same day. Two VMs created by a colleague after the removal also
> received no stamp at all.
>
> **Keep the Composition's `smbios` block.** It costs nothing and is the first
> line of defence if the component ever comes back — which would happen silently.


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

### The mitigation protects the CREATE, not the VM's whole life

`needs_smbios_update()` does **not** reliably skip an already-stamped VM. Witnessed on
`u26-kind1` (VMID 191, LabUL): built 2026-07-25 with the base64 SMBIOS the Composition
emits, then re-stamped by PegaProx in plain text anyway, and from then on every
`observe` failed. So treat the mitigation as protecting the create, not the VM's whole
life — a VM that has been Synced for weeks can still fall over this.

**PegaProx encodes the moment it stamped in the serial**, which is how you date the
event without node access: `serial=PVE2607260411212499` -> `PVE` + `260726` +
`041121` + counter = **2026-07-26 04:11:21**, one day after that VM was built.

The failure is easy to misread, because the VM itself is fine — `Ready=True`, guest
running, uptime intact. Only `Synced` flips, so what you actually lose is drift
detection, and a delete would block.

### Repairing a re-stamped VM (no restart needed)

Read the current value, keep the `uuid`, write back the base64 form:

```bash
# 1. current value — SAVE IT
curl -sk -b "PVEAuthCookie=$TICKET" \
  "$EP/api2/json/nodes/$NODE/qemu/$VMID/config" | jq -r '.data.smbios1'
# manufacturer=Proxmox,product=PegaProxManagment,version=v1,serial=PVE…,uuid=<UUID>,family=ProxmoxVE

# 2. write back base64, PRESERVING uuid
NEW="base64=1,manufacturer=$(printf %s "$MANUFACTURER" | base64 -w0),product=$(printf %s "$PRODUCT" | base64 -w0),uuid=$UUID"
curl -sk -X PUT -b "PVEAuthCookie=$TICKET" -H "CSRFPreventionToken: $CSRF" \
  --data-urlencode "smbios1=$NEW" "$EP/api2/json/nodes/$NODE/qemu/$VMID/config"

# 3. force a reconcile
kubectl annotate environmentvm.virtualenvironmentvm.proxmoxbpg.m.crossplane.io \
  <name> reconcile.crossplane.io/requested="$(date +%s)" --overwrite
```

**KEEP THE `uuid`.** It is the system UUID the guest sees; dropping it hands the VM a
new machine identity on its next boot (machine-id, cloud-init instance-id). Note that
`uuid` stays plain text even under `base64=1` — only `manufacturer`/`product`/etc. are
encoded. `version`, `serial` and `family` are deliberately not written back: the
Composition does not emit them either.

**THE VM WILL BE RESTARTED. Plan an outage.** `smbios1` is not hot-pluggable, so PVE
parks the write as a *pending* change and the running VM keeps the old plain-text
value. bpg does not leave it there: on its next reconcile it applies the pending
change the only way it can, by cycling the VM. Observed on VMID 191 (2026-08-10), the
write landing at 08:58:5x:

```
08:58:58  qmshutdown  <provider credential>  OK
08:59:01  qmstart     <provider credential>  OK
```

The gap was about three seconds and the guest came back clean — a single-node k3s
cluster on that VM returned Ready with every pod Running — but it is a **real reboot
of a live machine, triggered by the provider without further prompting**. On anything
carrying state, schedule it.

Two consequences worth knowing before you start:

- **It fires on the provider's own schedule**, not when you force a reconcile. On 191
  the restart happened roughly a minute *before* the
  `reconcile.crossplane.io/requested` annotation was set. Writing the value is what
  commits you.
- **`Synced` recovers regardless of the restart.** `GET /config` without `current=1`
  returns the config file *including* pending changes, and that is what bpg reads — so
  the base64 value is visible to `observe` immediately. The restart is about the
  *guest* seeing the new SMBIOS, not about fixing `Synced`.

An earlier revision of this section claimed no restart was needed. That was wrong,
and it was wrong in the risky direction — corrected here after observing the cycle.

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

## Guest password (`lock_passwd`)

**Enabling cloud-init costs you the baked-in password unless you set one.** PVE
generates its user-data from `ciuser`/`cipassword`. With no `cipassword`,
cloud-init falls back to its `lock_passwd: true` default and locks the account:

```console
$ passwd -S sthings
sthings L 2026-07-28 0 99999 7 -1
        ^ locked
```

Key-based logins keep working, so this hides well — but every password-based
Ansible run fails with `Permission denied (publickey,password)` and the host
comes out `UNREACHABLE`. The password the Packer build wrote is still in the
image; cloud-init overwrites it on first boot.

Point the environment at the Secret holding that same password — once, in the
EnvironmentConfig, so no XR has to remember it:

```yaml
# EnvironmentConfig
data:
  ciPasswordSecretName: proxmoxvm-ci-password   # in the XR's namespace
  ciPasswordSecretKey: password                 # optional, this is the default
```

A single XR can override it:

```yaml
spec:
  cloudInit:
    passwordSecretRef:
      name: some-other-secret
      key: password
```

The Secret must live in the XR's namespace — the namespaced `EnvironmentVM` CRD
declares only `{name, key}` and always resolves it locally. (`namespace` is still
accepted on the XR for compatibility and ignored; before v0.8.0 the Composition
forwarded it and the apply failed outright with
`.passwordSecretRef.namespace: field not declared in schema`, so the field never
worked at all.)

Templates that do NOT run cloud-init are unaffected — nothing overwrites the
baked-in password, which is why this only surfaced once
[stuttgart-things#2432](https://github.com/stuttgart-things/stuttgart-things/issues/2432)
was fixed.

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
