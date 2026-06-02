# CLAUDE.md — `proxmox-vm` Configuration

Context for working on this Crossplane Configuration. Repo-wide conventions
(Crossplane v2, registries, function-name rules, `task` workflows) live in the
**root `CLAUDE.md`** — read that too. This file focuses on `proxmox-vm`.

## What it does
Turns a namespaced `ProxmoxVM` XR (group `resources.stuttgart-things.com`,
`v1alpha1`) into an OpenTofu `Workspace` (`opentofu.m.upbound.io/v1beta1`,
provider-opentofu) that runs the Terraform module
`github.com/stuttgart-things/proxmox-vm` to create a VM on Proxmox VE. The
VM IP is surfaced on `status.share.ip`.

This Configuration mirrors the `vsphere-vm` schema (EnvironmentConfig-sourced
shared defaults + per-XR overrides, optional ESO-fed tfvars Secret). The
differences are the Terraform module, the var keys (`pve_*`), and the placement
fields (`spec.proxmox.{node,datastore,folderPath,network}` instead of the
vSphere set).

## Composition pipeline (`apis/composition.yaml`)
1. **`load-environment`** (`function-environment-configs`) — selects an
   `EnvironmentConfig` by the **config-scoped** label
   `proxmox-vm.resources.stuttgart-things.com/environment` (value from
   `spec.environmentConfig`, `fromFieldPathPolicy: Optional`) and loads its
   `data` into the pipeline environment.

   The label key is intentionally namespaced to this Configuration. A
   generic shared key (`resources.stuttgart-things.com/environment`, as used
   by `ansible-run`) collides across Configurations: if two configs'
   EnvironmentConfigs share `environment=default`, the Selector matches both
   and the step fails with `expected exactly one required resource, got 2`.
2. **`patch-and-transform`** (`function-patch-and-transform`) — renders the
   `Workspace`, patching its `forProvider.vars[]` from the XR + environment.

## Override semantics (the important bit)
Each shared-infra var has **two** patches in order:
1. `FromEnvironmentFieldPath <key>` (Optional) — sets the env default.
2. `FromCompositeFieldPath spec.<...>` (Optional) — overrides it **only
   when the XR sets the field**.

Precedence is therefore **XR spec → EnvironmentConfig → Workspace base value**.
For this to work the XRD must **not** default the env-sourced fields
(vm.template, vm.annotation, proxmox.node, proxmox.datastore, proxmox.folderPath,
proxmox.network, tfvars.*, providerRef.*) — an XRD default would make the field
always present on-cluster, so the Optional XR patch would always fire and the
EnvironmentConfig value would never win. Only the purely per-VM fields
(count, ram, disk, cpu, firmware) carry XRD defaults.

`vars[]` indices are positional and must stay aligned with the base list:
0 vm_count · 1 vm_name · 2 vm_num_cpus · 3 vm_memory · 4 vm_disk_size ·
5 vm_firmware · 6 vm_notes (annotation) · 7 vm_template · 8 pve_cluster_node ·
9 pve_datastore · 10 pve_folder_path · 11 pve_network. Reordering the base
`vars` list silently mis-patches — update both together.

## EnvironmentConfig data keys
`node, datastore, folderPath, network, template, annotation,
tfvarsSecretName, tfvarsSecretKey, providerConfigName, providerConfigKind`.
See `examples/environmentconfig.yaml`. Note there is **no** `unverifiedSsl`
key (the Proxmox module has no `unverified_ssl` var, unlike vsphere-vm).

## Local render
`crossplane render` does NOT resolve EnvironmentConfigs from a cluster — pass
the example via `--extra-resources`, or the env-sourced vars come out empty:
```bash
crossplane render examples/xr.yaml apis/composition.yaml examples/functions.yaml \
  --extra-resources examples/environmentconfig.yaml --include-function-results
# or: CONFIG=machinery/proxmox-vm XR=xr.yaml task render
```

## Registry note (don't "fix" this)
`provider-opentofu` is published **only** on `xpkg.upbound.io/upbound/...`, not
on the canonical `xpkg.crossplane.io` mirror — so its `dependsOn` and
`examples/provider.yaml` intentionally use the Upbound path. The two Functions
are on `xpkg.crossplane.io` per the repo convention.

## Version-pin coupling
The Terraform module ref `proxmox-vm.git?ref=3.0.2-rc08-20260407` is pinned in
the Composition base (`forProvider.module`). Bumping the VM behaviour means
bumping that ref, not the package version alone.
