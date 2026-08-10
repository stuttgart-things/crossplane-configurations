# CLAUDE.md — `proxmoxvm` Configuration

Context for working on this Crossplane Configuration. Repo-wide conventions
(Crossplane v2, registries, function-name rules, `task` workflows) live in the
**root `CLAUDE.md`** — read that too. This file focuses on `proxmoxvm`.

## What it does
Turns a namespaced `NativeProxmoxVM` XR (group `resources.stuttgart-things.com`,
`v1alpha1`) into a Proxmox VE VM via the native
[`provider-proxmox-bpg`](https://github.com/valkiriaaquatica/provider-proxmox-bpg)
managed `EnvironmentVM` resource (built on `bpg/terraform-provider-proxmox`).
It is the NATIVE-provider sibling of the OpenTofu-based `proxmox-vm`
Configuration, mirroring `vspherevm`'s relationship to `vsphere-vm`.

## Composed managed resource (the important bit)
- apiVersion `virtualenvironmentvm.proxmoxbpg.m.crossplane.io/v1alpha1`, kind
  **`EnvironmentVM`** (capital `VM`). The **namespaced (`.m`)** variant — a
  namespaced composite cannot compose a cluster-scoped MR — referencing a `.m`
  `ClusterProviderConfig` (`proxmoxbpg.m.crossplane.io/v1beta1`).
- **Upjet encodes every Terraform block as a SINGLE-ELEMENT LIST.** So
  `forProvider.cpu`, `memory`, `disk`, `networkDevice`, `clone`,
  `initialization`, `agent`, `operatingSystem` are all lists, e.g.
  `cpu = [{ cores = 2, type = "x86-64-v2-AES" }]`. Emitting a bare map is a
  schema error.
- Clone is by numeric **template VMID** (`clone[].vmId`), NOT by name — the key
  behavioural difference from the Telmate `proxmox-vm` module. Supplied via
  EnvironmentConfig `templateVmId` (or per-XR `spec.vm.templateVmId`). The clone
  list is guarded (`[] if templateVmId == ""`) so offline render does not
  `int("")`. LabUL `sthings-u26` = **VMID 110** on `ul-pve01`.
- **Template gotchas (baked into the examples):** the `sthings-u26` root disk is
  on **`virtio0`** (not `scsi0`) — `diskInterface` defaults to `virtio0`. The
  Packer templates carry **no cloud-init drive**, so bpg adds one on clone and
  needs a datastore: `initialization.datastoreId` defaults to the root disk's
  datastore (`_ciDatastore = _ci?.datastoreId or _datastore`), overridable via
  `spec.cloudInit.datastoreId`.
- **SMBIOS / PegaProx `illegal base64 data` (live-test blocker, 2026-06-15;
  mitigation in the Composition).** bpg base64-decodes the SMBIOS type1 fields
  (manufacturer/product/version/serial/family) on **every** read. The LabUL node
  `ul-pve01` is managed by **PegaProx** (github.com/PegaProx/project-pegaprox),
  whose *SMBIOS Auto-Configurator* — a systemd service on the node — stamps every
  NEW VMID with PLAIN-TEXT `smbios1` (`manufacturer=Proxmox,product=PegaProxManagment,
  version=v1,serial=PVE<ts>,family=ProxmoxVE`, no `base64=1`). It fires on each new
  VMID, so it hits every bpg **clone** → `observe`/`refresh` fails with N×
  `illegal base64 data at input byte N` (the VM boots but never goes Ready, delete
  blocks). NOT set by Packer/dagger/the hashicorp proxmox plugin — confirmed via
  `qm` config (`smbios1`) + GitHub code search (`PegaProxManagment` exists only in
  the PegaProx repo); its `serial` timestamp post-dates the Packer build. We can't
  touch the node, so the **`render` KCL emits an `smbios` block** (`manufacturer`/
  `product`, defaults `stuttgart-things`/`crossplane-proxmoxvm`, overridable via
  EnvironmentConfig `smbiosManufacturer`/`smbiosProduct`): bpg writes it `base64=1`
  on create, which it reads back fine. This bpg version's CRD has **no `base64`
  toggle** in the smbios schema; bpg always encodes when a block is present.

  **The workaround protects the CREATE ONLY — it does not survive (#202).** An
  earlier revision of this file claimed PegaProx then *skips* the VM, because
  `needs_smbios_update()` leaves any VM that already has SMBIOS keys alone. That
  is not what happens on `ul-pve01`: VMID 191 was created WITH the base64 block
  on 2026-07-25 and re-stamped in plain text ~18h later (the stamp time is
  encoded in the serial), after which every `observe` failed. So treat the block
  as delaying the failure, not preventing it — and do not read "vmid 144 reached
  Ready and survived re-observes" as proof of durability; short-lived VMs are
  simply deleted before the sweep reaches them.

  Recovery does not need a rebuild: write `smbios1` back as `base64=1,…` — KEEP
  the `uuid`, it is the guest's system UUID — and force a reconcile. `GET /config`
  without `current=1` returns pending changes, so `observe` recovers immediately.
  **But bpg then applies the pending change by CYCLING THE VM** (observed on 191:
  `qmshutdown` 08:58:58, `qmstart` 08:59:01, ~3s, guest came back clean). Plan an
  outage. Full procedure in README.md.

## Composition pipeline (`apis/composition.yaml`)
1. **`load-environment`** (`function-environment-configs`) — config-scoped label
   `proxmoxvm.resources.stuttgart-things.com/environment` (value from
   `spec.environmentConfig`, Optional). The label key is namespaced to this
   Configuration — a generic key collides (`expected exactly one required
   resource, got 2`).
2. **`render`** (`function-kcl`) — emits the `EnvironmentVM`; cloud-init via the
   `initialization` block (ipConfig / userAccount / dns) replaces the Telmate
   remote-exec hostname/machine-id/reboot. Also emits an optional `AnsibleRun`
   gated on the VM being Ready with an IP.
   **Hostname = the PVE VM name, and there is no second lever.** Any VM with an
   `initialization` block (we always emit one, for ipConfig) gets PVE-generated
   user-data carrying `hostname: <VM name>`, and user-data outranks every NoCloud
   meta-data source. So `forProvider.name` is set to `_hostname`
   (`cloudInit.hostname` -> `vm.name` -> `metadata.name`), NOT to `metadata.name`
   — the MR's own `metadata.name` stays `_name` for cluster uniqueness, and PVE
   does not require unique VM names. v0.6.0 (#200) instead shipped a NoCloud DMI
   seed (`smbios.serial = ds=nocloud;h=…`); **it never worked** and v0.7.0 removed
   it. Measured on LabUL 2026-07-29: the seed decoded verbatim on the guest and
   was still ignored, both batch VMs taking their PVE names. The #200 smoke test
   passed only because that VM was hand-cloned with no `initialization` block.
   `metaDataFileId` is not an alternative either — it replaces the generated
   meta-data, while `hostname:` lives in the generated USER-data. All of it is
   inert on templates shipping `/etc/cloud/cloud-init.disabled` (stuttgart-things
   #2432; LabUL VMID 192 onward is fixed). Contrast `vspherevm`, which keeps
   `forProvider.name = _name` and injects the hostname via `guestinfo.metadata`.
3. **`patch-status`** — reads `atProvider.ipv4Addresses` (list-of-lists; loopback
   filtered) → `status.share.ip`, plus `vmId` / `started` / `ansibleReady`.
4. **`function-auto-ready`** — propagates the composed VM's readiness to the XR.

## Override semantics
Each env-sourced field has the precedence **XR spec → EnvironmentConfig → KCL
builtin**, applied inline in the `render` KCL via `_x or _env or default`
chains. For this to work the XRD must **not** default the env-sourced fields
(cpuType, osType, bios, diskInterface, networkModel, annotation, node, datastore,
bridge, vlanTag, pool, templateVmId, cloudInit.username, providerConfigRef) —
an XRD default would make the field always present, so the EnvironmentConfig
value would never win. Only purely per-VM fields (cpu, memory, disk,
agentEnabled, cloudInit.ipv4Address) carry XRD defaults.

## EnvironmentConfig data keys
`node, datastore, bridge, vlanTag, pool, templateVmId, cpuType, osType, bios,
diskInterface, networkModel, annotation, ciUsername, providerConfigName,
providerConfigKind`, optional `smbiosManufacturer` / `smbiosProduct` (PegaProx
workaround override), plus an optional `ansible` sub-block. See
`examples/environmentconfig.yaml` (values are LabUL placeholders — set the real
`templateVmId` before cluster use).

## Credentials
The `ClusterProviderConfig` references a Secret whose `credentials` JSON maps to
the bpg provider config (`internal/clients/proxmoxbpg.go::buildConfiguration`):
top-level `endpoint, username, password, api_token, auth_ticket,
csrf_prevention_token, insecure, tmp_dir, random_vm_id*`; SSH block
`ssh_username / ssh_password / ssh_private_key`. bpg may need SSH to the node
for cloud-init snippet uploads / some disk paths — validate per flow.

**Credential JSON gotchas (validated against LabUL 2026-06-15):**
- **Every value is a string.** `buildConfiguration` unmarshals each into a Go
  string, so `insecure` MUST be `"true"`/`"false"` — a bare JSON bool `true`
  fails the whole connect with `cannot unmarshal bool into Go value of type
  string` (looks like an apply/observe error, not a creds error). The repo
  examples were wrong (bool) and are now fixed.
- **`endpoint` is the bare base URL** (`https://host:8006/`). bpg appends
  `/api2/json` itself; a `…:8006/api2/json` value is tolerated but non-standard.
- **`username` is realm-qualified**, e.g. `phermann@LabUL` (realm `LabUL` is a
  real PVE realm here — `pam`/`pve` return 401).
- ESO path (deploy-proxmox bundle) maps Vault KV `cicd-proxmox-labul`/`default`:
  `pve_api_url→endpoint, pve_api_user→username, pve_api_password→password,
  vm_ssh_user→ssh_username, vm_ssh_password→ssh_password`. CSS reuses the live
  `vault-cicd-vsphere-labul` coords (same Vault server, `test-k3s-eso` auth mount)
  with the path swapped.

## Local render
`crossplane render` does NOT resolve EnvironmentConfigs from a cluster — pass
the example via `--extra-resources`, or the env-sourced fields (incl. the clone
`templateVmId`) come out empty:
```bash
crossplane render examples/xr.yaml apis/composition.yaml examples/functions.yaml \
  --extra-resources examples/environmentconfig.yaml --include-function-results
# or: CONFIG=machinery/proxmoxvm XR=xr.yaml task render
```

## Provider sourcing (decision)
Depends on the upstream `xpkg.upbound.io/valkiriaaquaticamendi/provider-proxmox-bpg`
directly (single-maintainer package). Revisit forking + republishing under
`ghcr.io/stuttgart-things` before fleet rollout (contrast: `vspherevm` uses the
in-house `ghcr.io/stuttgart-things/provider-vspherevm-xpkg`). Tracked in the
greenfield issue #78.
