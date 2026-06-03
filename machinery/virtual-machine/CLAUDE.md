# CLAUDE.md — `virtual-machine` Configuration

Context for working on this Crossplane Configuration. Repo-wide conventions
(Crossplane v2, registries, function-name rules, `task` workflows) live in the
**root `CLAUDE.md`** — read that too. This file focuses on `virtual-machine`.

## What it does
The high-level, opinionated VM entrypoint. A namespaced `XVirtualMachine` XR
(group `resources.stuttgart-things.com`, `v1alpha1`) asks for a VM by intent —
t-shirt `size`, `provider` (vsphere/proxmox), `environment` (labul/labda), `os`
— and the Composition resolves the per-environment datacenter topology and
emits a single `VMProvision`. It is the top of a three-layer stack:

```
XVirtualMachine  (this) ──► VMProvision (vm-provision) ──► VsphereVM / ProxmoxVM (+ AnsibleRun)
   size/provider/env           router/aggregator              leaf OpenTofu Workspaces
```

It composes only `vm-provision`; it does NOT emit provider VMs directly and
declares no providers in `dependsOn`.

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
EnvironmentConfig `data` is **flat**, this one's `data` holds BOTH a `vsphere`
and a `proxmox` sub-block. The render step reads
`_ctx["apiextensions.crossplane.io/environment"][spec.provider]`. Consequences:
- The **provider dimension is NOT in the selector** — one EnvironmentConfig per
  environment serves both providers. The selector matches on `environment`
  only, so there is still exactly one match (the rule that bit proxmox-vm).
- `templates` is a map keyed by `spec.os` (`ubuntu24`/`ubuntu22`) → provider
  template name. Adding an OS enum value means adding a `templates` entry in
  every EnvironmentConfig.
- Per-sub-block keys consumed by the KCL: vsphere → `templates, firmware,
  folderPath, datacenter, datastore, resourcePool, network, tfvarsSecretName,
  tfvarsSecretKey`; proxmox → `templates, firmware, node, datastore,
  folderPath, network, tfvarsSecretName, tfvarsSecretKey`.

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
