# CLAUDE.md — `vm-provision` Configuration

Context for working on this Crossplane Configuration. Repo-wide conventions
(Crossplane v2, registries, function-name rules, `task` workflows) live in the
**root `CLAUDE.md`** — read that too. This file focuses on `vm-provision`.

## What it does
Turns a namespaced `VMProvision` XR (group `resources.stuttgart-things.com`,
`v1alpha1`) into either a `VsphereVM` or a `ProxmoxVM` (per `spec.provider`),
plus an optional `AnsibleRun`. It is a **router/aggregator** over three other
Configurations in this repo — `vsphere-vm`, `proxmox-vm`, `ansible-run` — not
a leaf that talks to providers directly. It declares no EnvironmentConfig; the
full topology is passed in by its caller (e.g. the `virtual-machine`
Configuration) or by the user.

## Composition pipeline (`apis/composition.yaml`)
1. **`render`** (`function-kcl`, inline) — branches on `spec.provider` and
   emits the matching VM sub-XR with the correct spec shape:
   - vSphere puts placement (`folderPath/datacenter/datastore/resourcePool/
     network`) **inside `spec.vm`** + `bootstrap`/`unverifiedSsl`.
   - Proxmox uses a separate `spec.proxmox` block and force-appends a `G`
     suffix to the disk if missing.
   Then, **gated on the VM being Ready and having an IP** (read from the sub-XR
   in `ocds`), it emits an `AnsibleRun` whose inventory auto-populates with the
   VM IP. The gate means the `AnsibleRun` never appears in offline
   `crossplane render` (no live status) — this is expected, not a bug.
2. **`patch-status`** (`function-kcl`, inline) — mirrors VM IP/provider and
   VM/Ansible readiness onto the XR status. `ansibleReady` is `True` when
   Ansible is disabled.
3. **`automatically-detect-ready-composed-resources`** (`function-auto-ready`).

## Sub-XR shape coupling (read before editing the KCL)
The emitted `VsphereVM` / `ProxmoxVM` / `AnsibleRun` specs must match the
**current** XRDs of `machinery/vsphere-vm`, `machinery/proxmox-vm` and
`cicd/ansible-run` in this repo. They were verified compatible at migration
time. If any of those XRDs change a field name, this KCL must change with it —
`crossplane render` here will still succeed (it only emits the sub-XRs as raw
resources; it does not validate them against the sub-XRDs), so a mismatch only
surfaces on a live cluster. Re-check against their `apis/definition.yaml` when
bumping any of the three.

## function-kcl contract
`option("params").oxr` (observed composite), `.ocds` (observed composed, a map
keyed by composition-resource-name; each entry has `.Resource`). Status is set
by returning the merged composite as an item (`oxr | {status = {...}}`).

## dependsOn
`function-kcl`, `function-auto-ready`, and the three composed Configurations
(`vsphere-vm`, `proxmox-vm`, `ansible-run`). The providers (provider-opentofu,
provider-kubernetes) are **not** listed — vm-provision creates no Workspace or
Object itself; those come transitively from the composed Configurations.

## Local render
```bash
crossplane render examples/xr.yaml apis/composition.yaml examples/functions.yaml
# or: CONFIG=machinery/vm-provision XR=xr.yaml task render
```
No EnvironmentConfig / `--extra-resources` needed. Note XRD defaults aren't
applied by render, so an unset `vm.disk` hits the KCL fallback (`32G`) rather
than the XRD default (`64`); on-cluster the XRD default wins.
