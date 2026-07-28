# CLAUDE.md — `vm-batch` Configuration

Context for working on this Crossplane Configuration. Repo-wide conventions
(Crossplane v2, registries, function-name rules, `task` workflows) live in the
**root `CLAUDE.md`** — read that too. This file focuses on `vm-batch`.

## What it does
Turns a namespaced `VMBatch` XR (group `resources.stuttgart-things.com`,
`v1alpha1`) into **many** native VM XRs plus, optionally, ONE shared
`AnsibleRun`. It is a **fan-out/aggregator** over the native single-VM
Configurations — `proxmoxvm` (`NativeProxmoxVM`), `vspherevm`
(`NativeVsphereVM`) — and `ansible-run`; it talks to no provider directly and
declares no EnvironmentConfig. It is the batch sibling of `vm-provision`, but it
fans out to the **native**-provider VM modules (not the OpenTofu `vsphere-vm` /
`proxmox-vm`), and it emits ONE Ansible run across the whole fleet rather than
one per VM.

## Composition pipeline (`apis/composition.yaml`)
1. **`render`** (`function-kcl`, inline) — for each `spec.vms[]` entry emits
   `count` native VM XRs; then, **gated on every VM being Ready with an IP**
   (read from `ocds`), emits a single `AnsibleRun` whose inventory lists all
   VM IPs. No `load-environment` step: `spec.environmentConfig` is passed
   THROUGH to each native VM, which does its own EnvironmentConfig resolution —
   so offline `crossplane render` needs no `--extra-resources`.
2. **`patch-status`** (`function-kcl`, inline) — aggregates all VM IPs, the
   ready-vs-total counts (`vmCount`/`readyCount`), `vmReady` (ALL ready) and
   `ansibleReady` (`True` when Ansible disabled).
3. **`automatically-detect-ready-composed-resources`** (`function-auto-ready`).

## KCL gotchas (read before editing the render step)
- **`spec.vm` merge = key-by-key override, NOT `|`.** KCL's dict union `|`
  ERRORS on a conflicting scalar key (`conflicting values on the attribute
  'cpu'`) instead of letting the right side win. The `_merge` lambda merges
  key-by-key so a per-entry `vm` overrides `defaults`. Don't "simplify" it back
  to `_defaults | entry.vm`.
- **Replica count via list multiplication.** KCL has no `range()`; replicas come
  from `[0] * n` and `for _i, _e in ([0] * n)` (the index is the first loop
  var). `int(count or "1")`, floored at 1, guards `int("")` on offline render.
- **Replica naming.** `count == 1` → `<name>`; `count > 1` → `<name>-<i>`
  (0-based). The composed native XR's `metadata.name` is batch-prefixed
  (`<batch>-<replica>`) for cross-batch uniqueness; the GUEST name
  (`spec.vm.name`) is the un-prefixed replica name. `len(_vmXRs)` is the
  expected VM count used everywhere.
- **`defaults` / `vms[].vm` are free-form passthrough** (`x-kubernetes-
  preserve-unknown-fields: true`) that mirror the native module's `spec.vm`.
  This is deliberate — vm-batch does NOT re-declare or normalize the native
  fields, so it never drifts from them. Consequence: the provider split leaks
  (Proxmox `memory` vs vSphere `ram`); documented in the XRD + README.
- **`cloudInit` passthrough is proxmox-only** (`NativeVsphereVM` has no such
  block) — the render KCL only attaches it when `provider == "proxmox"`.

## Sub-XR shape coupling
The emitted `NativeProxmoxVM` / `NativeVsphereVM` / `AnsibleRun` specs must match
the **current** XRDs of `machinery/proxmoxvm`, `machinery/vspherevm` and
`cicd/ansible-run`. `crossplane render` here only emits them as raw resources
(no validation against those XRDs), so a field-name drift only surfaces on a
live cluster. Because `defaults`/`vm` are passthrough, most native `spec.vm`
drift is transparent — but re-check the `AnsibleRun` spec and the
`spec.environmentConfig`/`providerConfigRef`/`cloudInit` wiring when bumping any
of the three.

## The single AnsibleRun + sticky gate (read before touching the ansible block)
One `AnsibleRun` provisions the whole fleet: inventory
`all+["ip1","ip2",…]` built from every VM IP. The gate is
`ansible.enabled and _allReady and len(_allIPs) >= _expected` — it waits for
ALL VMs (not the first), so the play runs against the complete fleet. Same
**STICKY** rule as the native modules: once the run exists it is re-emitted
VERBATIM from `ocds` (never rebuilt), so an involuntary VM-IP blip cannot delete
and re-run the play against live machines. `ansible.enabled: false` still
removes it. Don't rebuild-from-live inside the sticky branch.

## Local render / iterate
```bash
crossplane render examples/xr.yaml apis/composition.yaml examples/functions.yaml \
  --include-function-results
# or: CONFIG=machinery/vm-batch XR=xr.yaml task render
```
The emitted native VM XRs are inert under render (no live status), so the
`AnsibleRun` never appears and `status` stays empty/false — expected. To unit-
test the inline KCL without a cluster, feed a synthetic `-D params='{...}'` with
an `ocds` map of fake native-VM `status.share.ip` + `conditions[Ready=True]`
entries (that is how the fan-out, IP aggregation and ansible gate were
validated).

## dependsOn
`function-kcl`, `function-auto-ready`, and the three composed Configurations
(`proxmoxvm`, `vspherevm`, `ansible-run`). The native VM providers
(provider-proxmox-bpg, provider-vspherevm) and provider-kubernetes are **not**
listed — vm-batch creates no managed resource itself; those come transitively
from the composed Configurations.
