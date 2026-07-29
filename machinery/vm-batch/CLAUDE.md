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
  The floor is the ONLY tolerance in `_count` — a non-numeric `count` panics
  `int()` and fails the **whole** Composition, not just its entry, so the XRD
  pins `count` to `^[1-9][0-9]*$` rather than letting the lambda cope.
- **Replica naming — the suffix is ALWAYS applied.** `<name>-<i>` (0-based) even
  at `count: "1"`, which yields `<name>-0`. **Do not "tidy" this into a bare
  `<name>` for the single-replica case.** That was the original shape and it is a
  data-loss bug: the name would then depend on the count, so crossing the 1↔2
  boundary RENAMES the child XR, the old name drops out of the desired set, and
  Crossplane deletes the running VM. Scaling a batch would destroy the machines
  it is growing. Verified with `crossplane render --observed-resources`: with
  `vm-batch-min-one` observed, `count: "2"` emitted only `…-one-0` / `…-one-1`.
  A constant suffix makes scale-up additive and scale-down trailing-only.
  The composed native XR's `metadata.name` is additionally batch-prefixed
  (`<batch>-<name>-<i>`) for cross-batch uniqueness. `len(_vmXRs)` is the
  expected VM count used everywhere.
- **Two names per replica, on purpose.** The child XR's `metadata.name` is
  batch-prefixed (`<batch>-<name>-<i>`); `spec.vm.name` carries the un-prefixed
  `<name>-<i>` and both native modules use it for the GUEST hostname. The prefix
  exists to keep two batches in one namespace from colliding — it has no business
  inside the guest.
  **This needs vspherevm >= v0.8.0 AND proxmoxvm >= v0.7.0** (the `dependsOn`
  floors); lower either and the split silently comes back.
  - vspherevm < v0.8.0 declared `spec.vm.name` in the XRD but never read it —
    issue #195, fixed by `_hostname = _vm?.name or _name` feeding the cloud-init
    guestinfo `local-hostname` (`instance-id` stays on `metadata.name`; do not
    "align" it). `forProvider.name` stays the prefixed XR name because vSphere
    inventory names must be unique.
  - proxmoxvm < v0.7.0 had no working lever at all: v0.6.0 shipped a NoCloud
    SMBIOS seed that PVE's own generated user-data (`hostname: <VM name>`) always
    overrode. v0.7.0 sets `forProvider.name` to the hostname instead, so on
    **Proxmox the hypervisor VM name is the un-prefixed one** — the asymmetry
    with vSphere in the README table is deliberate, not drift.
- **`defaults` / `vms[].vm` are free-form passthrough** (`x-kubernetes-
  preserve-unknown-fields: true`) that mirror the native module's `spec.vm`.
  This is deliberate — vm-batch does NOT re-declare or normalize the native
  fields, so it never drifts from them. Consequence: the provider split leaks
  (Proxmox `memory` vs vSphere `ram`); documented in the XRD + README. Note the
  failure mode is SILENT: free-form stops at the `VMBatch` boundary, the native
  XRDs are structural, so a key they do not declare is pruned by the API server
  with no error/event and the VM builds at that field's XRD default. When
  debugging "my size didn't apply", read the composed child XR, not the VMBatch.
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

**Known limitation — the run is CREATE-ONLY.** The sticky branch keys on
`prevSpec.pipelineRunName != ""` alone and never consults the VM set, so the
inventory is frozen at first emission and replicas added later are built but
never provisioned (verified: growing a 3-VM batch to 4 with the `AnsibleRun`
observed re-emits the 3-IP inventory unchanged). Making the stickiness IP-set-
aware does NOT fix this — `kcl-tekton-pr` excludes `Update` from its
managementPolicies, so a rewritten `AnsibleRun` spec never reaches Tekton. The
real fix is a differently *named* `AnsibleRun` per inventory (name keyed on a
hash of the IP set), which trades the frozen inventory for run churn on every
IP change — needs a deliberate decision, deferred past v0.1.0.

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
