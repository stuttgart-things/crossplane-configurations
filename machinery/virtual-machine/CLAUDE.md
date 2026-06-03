# CLAUDE.md — `virtual-machine` Configuration

Context for working on this Crossplane Configuration. Repo-wide conventions
(Crossplane v2, registries, function-name rules, `task` workflows) live in the
**root `CLAUDE.md`** — read that too. This file focuses on `virtual-machine`.

## What it does
The high-level, opinionated VM entrypoint. A namespaced `XVirtualMachine` XR
(group `resources.stuttgart-things.com`, `v1alpha1`) asks for a VM by intent —
t-shirt `size`, `provider` (vsphere/proxmox/harvester), `environment`
(labul/labda), `os` — and the Composition resolves the per-environment topology
and **routes by provider**:

```
                          ┌─ vsphere/proxmox ─► VMProvision (vm-provision) ─► VsphereVM/ProxmoxVM (+ AnsibleRun)
XVirtualMachine  (this) ──┤
   size/provider/env      └─ harvester ───────► HarvesterVM  (harvester-vm)  ─► KubeVirt VM (+ AnsibleRun)
```

It composes `vm-provision` (vsphere/proxmox) and `harvester-vm` (harvester); it
does NOT emit provider VMs directly and declares no providers in `dependsOn`.

## Routing in the render KCL (read before editing)
The render step builds **both** a `_vmProvision` and a `_harvesterVM`, then
`items = [_harvesterVM] if _provider == "harvester" else [_vmProvision]`. The
tofu-only computations (`_template`, `_tfvars*`, `_disk`, `_vsphereBlock`,
`_proxmoxBlock`) still evaluate for harvester but go unused — harmless. The
harvester branch maps `size` → `vm.cpu.cores` (int), `vm.resources.memory`
(`<ram>Mi`), `vm.resources.cpu`, `volume.storage` (`<disk>Gi`); `os` → `imageId`
via the sub-block `images` map; and reuses `_ansibleSpec` (its `enabled`/
`playbooks`/`varsFile` field names match `HarvesterVM.spec.ansible`). It does
**not** set `cloudInit.packages` — the `qemu-guest-agent` default lives in the
`harvester-vm` XRD. `count` is ignored for harvester (single VM).

`patch-status` reads either a `VMProvision` or a `HarvesterVM` from `ocds`
(`kind in [...]`); VM readiness is `vmReady` (VMProvision) or `vm.ready`
(HarvesterVM): `_vmStatus?.vmReady or (_vmStatus?.vm?.ready or False)`.

## Composition pipeline (`apis/composition.yaml`)
1. **`load-environment`** (`function-environment-configs`) — selects an
   `EnvironmentConfig` by the **config-scoped** label
   `virtual-machine.resources.stuttgart-things.com/environment` (value from
   `spec.environment`, `fromFieldPathPolicy: Optional`) and merges its `data`
   into the pipeline context under `apiextensions.crossplane.io/environment`.
2. **`render`** (`function-kcl`, inline) — maps the t-shirt `size` to
   cpu/ram/disk via an inline table, picks the provider sub-block of the loaded
   topology by `spec.provider`, maps `spec.os` to a template name via the
   sub-block's `templates`, and emits the `VMProvision`.
3. **`patch-status`** (`function-kcl`, inline) — mirrors VM IP / provider /
   size / environment and VM/Ansible readiness onto the XR status.
4. **`automatically-detect-ready-composed-resources`** (`function-auto-ready`).

## The EnvironmentConfig is nested-per-provider (read before editing the KCL)
Unlike the leaf Configurations (`proxmox-vm`, `vsphere-vm`) whose
EnvironmentConfig `data` is **flat**, this one's `data` holds a `vsphere`, a
`proxmox` and a `harvester` sub-block. The render step reads
`_ctx["apiextensions.crossplane.io/environment"][spec.provider]`. Consequences:
- The **provider dimension is NOT in the selector** — one EnvironmentConfig per
  environment serves all providers. The selector matches on `environment`
  only, so there is still exactly one match (the rule that bit proxmox-vm).
- `templates` (vsphere/proxmox) / `images` (harvester) is a map keyed by
  `spec.os` (`ubuntu24`/`ubuntu22`) → provider template / Harvester imageId.
  Adding an OS enum value means adding an entry in every sub-block.
- Per-sub-block keys consumed by the KCL: vsphere → `templates, firmware,
  folderPath, datacenter, datastore, resourcePool, network, tfvarsSecretName,
  tfvarsSecretKey`; proxmox → `templates, firmware, node, datastore,
  folderPath, network, tfvarsSecretName, tfvarsSecretKey`; harvester →
  `images, providerConfigRef, storageClassName, namespace, networkName, user,
  sshKey` (all default-guarded in the KCL, so a missing key degrades to a
  built-in default rather than erroring).

## Sub-XR shape coupling
The emitted `VMProvision` spec must match the **current** XRD of
`machinery/vm-provision`. `crossplane render` here only emits it as a raw
resource (no validation against the vm-provision XRD), so a field-name drift
only surfaces on a live cluster. Re-check `machinery/vm-provision/apis/
definition.yaml` when changing the KCL or bumping vm-provision.

## XRD defaults vs the size map
Only `os` (ubuntu24), `count` ("1"), `ansible` (true) and `providerRef.*` carry
XRD defaults. cpu/ram/disk are NOT XRD fields — they come purely from the
inline size table in the KCL, so `crossplane render` reflects them correctly
even though render doesn't apply XRD defaults.

## Local render
`crossplane render` does NOT resolve EnvironmentConfigs from a cluster — pass
the example via `--extra-resources`, or the topology comes out empty:
```bash
crossplane render examples/xr.yaml apis/composition.yaml examples/functions.yaml \
  --extra-resources examples/environmentconfig.yaml --include-function-results
# or: CONFIG=machinery/virtual-machine XR=xr.yaml task render
```
The emitted `VMProvision` is inert under render (no live VsphereVM/ProxmoxVM
status), so `status.share.ip` stays `[]` and readiness stays false — expected.
