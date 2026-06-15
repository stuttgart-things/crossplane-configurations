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
  `int("")`.

## Composition pipeline (`apis/composition.yaml`)
1. **`load-environment`** (`function-environment-configs`) — config-scoped label
   `proxmoxvm.resources.stuttgart-things.com/environment` (value from
   `spec.environmentConfig`, Optional). The label key is namespaced to this
   Configuration — a generic key collides (`expected exactly one required
   resource, got 2`).
2. **`render`** (`function-kcl`) — emits the `EnvironmentVM`; cloud-init via the
   `initialization` block (ipConfig / userAccount / dns) replaces the Telmate
   remote-exec hostname/machine-id/reboot. The guest hostname is set from the VM
   name by Proxmox cloud-init. Also emits an optional `AnsibleRun` gated on the
   VM being Ready with an IP.
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
providerConfigKind`, plus an optional `ansible` sub-block. See
`examples/environmentconfig.yaml` (values are LabUL placeholders — set the real
`templateVmId` before cluster use).

## Credentials
The `ClusterProviderConfig` references a Secret whose `credentials` JSON maps to
the bpg provider config (`internal/clients/proxmoxbpg.go::buildConfiguration`):
top-level `endpoint, username, password, api_token, auth_ticket,
csrf_prevention_token, insecure, tmp_dir, random_vm_id*`; SSH block
`ssh_username / ssh_password / ssh_private_key`. bpg may need SSH to the node
for cloud-init snippet uploads / some disk paths — validate per flow.

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
