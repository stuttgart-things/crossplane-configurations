# CLAUDE.md — `vsphere-vm` Configuration

Context for working on this Crossplane Configuration. Repo-wide conventions
(Crossplane v2, registries, function-name rules, `task` workflows) live in the
**root `CLAUDE.md`** — read that too. This file focuses on `vsphere-vm`.

## What it does
Turns a namespaced `VsphereVM` XR (group `resources.stuttgart-things.com`,
`v1alpha1`) into an OpenTofu `Workspace` (`opentofu.m.upbound.io/v1beta1`,
provider-opentofu) that runs the Terraform module
`github.com/stuttgart-things/vsphere-vm` to create a VM on VMware vSphere. The
VM IP is surfaced on `status.share.ip`.

## Composition pipeline (`apis/composition.yaml`)
1. **`load-environment`** (`function-environment-configs`) — selects an
   `EnvironmentConfig` by the **config-scoped** label
   `vsphere-vm.resources.stuttgart-things.com/environment` (value from
   `spec.environmentConfig`, `fromFieldPathPolicy: Optional`) and loads its
   `data` into the pipeline environment.

   The label key is intentionally namespaced to this Configuration. A
   generic shared key (`resources.stuttgart-things.com/environment`, as used
   by `ansible-run`) collides across Configurations: if two configs'
   EnvironmentConfigs share `environment=default`, the Selector matches both
   and the step fails with `expected exactly one required resource, got 2`.
   NOTE: `ansible-run` still uses the generic key — the two only coexist
   safely because vsphere-vm uses its own key. If a third config reuses the
   generic key, `ansible-run` will hit this collision; the fleet-wide fix is
   to config-scope every config's label.
2. **`patch-and-transform`** (`function-patch-and-transform`) — renders the
   `Workspace`, patching its `forProvider.vars[]` from the XR + environment.

## Override semantics (the important bit)
Each shared-infra var has **two** patches in order:
1. `FromEnvironmentFieldPath <key>` (Optional) — sets the env default.
2. `FromCompositeFieldPath spec.vm.<field>` (Optional) — overrides it **only
   when the XR sets the field**.

Precedence is therefore **XR spec → EnvironmentConfig → Workspace base value**.
For this to work the XRD must **not** default the env-sourced fields
(folderPath, datacenter, datastore, resourcePool, network, template,
annotation, unverifiedSsl, tfvars.*, providerRef.*) — an XRD default would make
the field always present on-cluster, so the Optional XR patch would always fire
and the EnvironmentConfig value would never win. Only the purely per-VM fields
(count, ram, disk, cpu, firmware, bootstrap) carry XRD defaults.

`vars[]` indices are positional and must stay aligned with the base list:
0 count · 1 name · 2 ram · 3 disk · 4 cpu · 5 firmware · 6 folderPath ·
7 datacenter · 8 datastore · 9 resourcePool · 10 network · 11 template ·
12 bootstrap · 13 annotation · 14 unverifiedSsl. Reordering the base `vars`
list silently mis-patches — update both together.

## EnvironmentConfig data keys
`folderPath, datacenter, datastore, resourcePool, network, template,
annotation, unverifiedSsl, tfvarsSecretName, tfvarsSecretKey,
providerConfigName, providerConfigKind`. See `examples/environmentconfig.yaml`.

## Local render
`crossplane render` does NOT resolve EnvironmentConfigs from a cluster — pass
the example via `--extra-resources`, or the env-sourced vars come out empty:
```bash
crossplane render examples/xr.yaml apis/composition.yaml examples/functions.yaml \
  --extra-resources examples/environmentconfig.yaml --include-function-results
# or: CONFIG=machinery/vsphere-vm XR=xr.yaml task render
```

## Registry note (don't "fix" this)
`provider-opentofu` is published **only** on `xpkg.upbound.io/upbound/...`, not
on the canonical `xpkg.crossplane.io` mirror — so its `dependsOn` and
`examples/provider.yaml` intentionally use the Upbound path. The two Functions
are on `xpkg.crossplane.io` per the repo convention.

## Version-pin coupling
The Terraform module ref `vsphere-vm.git?ref=v2.12.0-1.0.0` is pinned in the
Composition base (`forProvider.module`). Bumping the VM behaviour means
bumping that ref, not the package version alone.
