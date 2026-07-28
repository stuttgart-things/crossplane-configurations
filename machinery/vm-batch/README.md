# vm-batch — Crossplane Configuration

A Crossplane v2 Configuration that provisions a **batch** of VMs — and,
optionally, Ansible-provisions all of them at once — from a single namespaced
`VMBatch` XR. Supports vSphere and Proxmox via the native single-VM
Configurations.

## Overview

The native single-VM Configurations [`proxmoxvm`](../proxmoxvm/)
(`NativeProxmoxVM`) and [`vspherevm`](../vspherevm/) (`NativeVsphereVM`) each
build **one** VM. `vm-batch` is the fan-out on top of them. Its Composition is a
[`function-kcl`](https://github.com/crossplane-contrib/function-kcl) pipeline:

- **`render`** — for every entry in `spec.vms[]`, emits `count` native VM XRs
  (`NativeProxmoxVM` when `spec.provider: proxmox`, `NativeVsphereVM` when
  `vsphere`). Then, gated on **every** VM being Ready with an IP, it emits a
  **single** `AnsibleRun` whose inventory lists **every** VM IP.
- **`patch-status`** — aggregates every VM IP, the ready-vs-total counts and
  VM/Ansible readiness onto the XR status.
- **`automatically-detect-ready-composed-resources`** (`function-auto-ready`).

```
                    ┌─ proxmox ─► NativeProxmoxVM × N  (proxmoxvm)
VMBatch  (this) ────┤                                              ─┐
   vms[] + count    └─ vsphere ─► NativeVsphereVM  × N  (vspherevm) ─┤
                                                                     ▼
                              one AnsibleRun, inventory = all VM IPs (ansible-run)
```

> `vm-batch` composes three other Configurations — all declared as `dependsOn`
> (`proxmoxvm`, `vspherevm`, `ansible-run`), so the package manager pulls them
> (and transitively their providers/functions) on install.

## Why a batch Configuration

The native modules build a single VM (with an optional single-VM Ansible run).
`vm-batch` adds the two things they cannot do alone:

1. **Multiple VMs from one XR** — a list of distinct VM definitions, each with
   an optional `count` of identical replicas.
2. **One Ansible play across the whole fleet** — a single `AnsibleRun` whose
   inventory is auto-built from every VM's IP (`all+["ip1","ip2",…]`), rather
   than one run per VM.

`vm-batch` carries **no placement of its own**: it passes
`spec.environmentConfig` through to every emitted VM, and each native VM
resolves its own topology (node/datastore/network, template, MOIDs, …) from the
matching EnvironmentConfig. Name one environment and the whole fleet lands in it.

## Parameters

### Common

| Parameter | Required | Default | Description |
|---|---|---|---|
| `provider` | yes | - | `proxmox` (emits `NativeProxmoxVM`) or `vsphere` (emits `NativeVsphereVM`) |
| `vms[]` | yes | - | List of VM definitions (at least one) |
| `vms[].name` | yes | - | Base VM name; each replica becomes `<name>-<index>` (see [Replica naming](#replica-naming)) |
| `vms[].count` | no | `"1"` | Number of identical replicas of this entry (digits only, ≥ 1) |
| `vms[].vm` | no | - | Per-entry `spec.vm` overrides (free-form; merged over `defaults`) |
| `environmentConfig` | no | `default` | Passed through to each VM as its `spec.environmentConfig` |
| `providerConfigRef` | no | - | Passed through to each VM (`{name, kind}`) |
| `defaults` | no | - | Shared `spec.vm` fields applied to every entry (a per-entry `vm` overrides them) |
| `cloudInit` | no | - | **Proxmox only:** passed through to each VM as `spec.cloudInit` |

`defaults` and `vms[].vm` are **free-form** and mirror the chosen native
module's own `spec.vm` — use its field names. Note the provider split: **Proxmox
uses `memory`, vSphere uses `ram`**. See [`proxmoxvm`](../proxmoxvm/) /
[`vspherevm`](../vspherevm/) for the full field sets.

> **The split fails silently.** Free-form applies only to `VMBatch` — the native
> XRDs are structural, so a key they do not declare is **pruned by the API server
> with no error and no event**, and the VM builds at that field's XRD default
> (`ram` on a Proxmox batch → 4096 MiB, not what you set). If a size does not
> take, inspect the composed child XR (`kubectl get nativeproxmoxvm <batch>-<replica> -o yaml`).

### Replica naming

Every replica is `<name>-<0-based index>` — **including at `count: "1"`**, which
yields `<name>-0`. The Kubernetes object name of each composed native XR is
additionally batch-name-prefixed (`<batch>-<name>-<index>`) so two batches in a
namespace never collide.

The index suffix is constant *by design*. If the name shape varied with the count
(bare `<name>` at 1, suffixed above), then changing `count` between 1 and 2 would
**rename** the child XR — the old name leaves the desired resource set, so
Crossplane deletes it and builds fresh VMs. Scaling a batch would destroy the
machines it is meant to be growing. With a constant suffix, scale-up is purely
additive and scale-down removes only the trailing replicas.

**The hypervisor object and the guest hostname get different names**, and that is
deliberate — the batch prefix is what keeps two batches in a namespace from
colliding, so it belongs on the object, not inside the guest:

| | hypervisor VM object | guest hostname |
|---|---|---|
| `proxmox` | `<batch>-<name>-<index>` | `<name>-<index>` |
| `vsphere` | `<batch>-<name>-<index>` | `<name>-<index>` |

Both come from the child XR: the object from its `metadata.name`, the guest from
the `spec.vm.name` vm-batch passes down. Requires **vspherevm ≥ v0.8.0**, which
is the floor in `dependsOn` — v0.7.1 and earlier ignored `spec.vm.name`
([#195](https://github.com/stuttgart-things/crossplane-configurations/issues/195))
and leaked the batch prefix into the vSphere guest hostname.

### Ansible (`spec.ansible`)

| Parameter | Default | Description |
|---|---|---|
| `enabled` | `false` | Enable one Ansible run across the whole batch |
| `playbooks` | `["sthings.baseos.setup"]` | Playbooks to run |
| `varsFile` | - | Ansible vars (`key+-value` form) |
| `varsInventory` | auto (all VM IPs) | Inventory; auto-built from every VM IP when omitted |
| `credentialsSecretName` | `ansible-credentials` | Ansible credentials Secret |
| `pipelineNamespace` | `tekton-ci` | Tekton namespace |
| `crossplaneProviderConfig` | `in-cluster` | provider-kubernetes config used to wrap the PipelineRun |
| `wrapInCrossplane` | `true` | Wrap the PipelineRun in a Crossplane Object |
| `gitRepoUrl` / `ansibleWorkingImage` | pinned defaults | Pipeline source / runner image |
| `extraCollections` / `extraRoles` | - | Extra Ansible collections / roles |

The `AnsibleRun` is emitted only once **all** VMs are Ready and each has
reported an IP — so it never appears in offline `crossplane render`, and it is
**sticky** (re-emitted verbatim once created so an involuntary VM-IP blip
cannot delete and re-run the play). Setting `ansible.enabled: false` still
removes it.

> **The batch Ansible run is create-only.** The inventory is frozen when the run
> is first emitted and the sticky path never re-reads the VM set, so **replicas
> added to the batch later are built but never Ansible-provisioned**. Widening
> the stickiness would not fix it — `kcl-tekton-pr` excludes `Update` from its
> managementPolicies, so a rewritten `AnsibleRun` spec never reaches Tekton; a
> second play needs a differently named `AnsibleRun`, which is out of scope for
> v0.1.0. Provision late additions with their own `VMBatch`, or delete and
> recreate the batch.

## Status

- `status.share.ip` — every VM IP in the batch
- `status.share.provider` — which provider was used
- `status.share.ansibleReady` / `ansibleSucceeded` / `ansibleCompletionTime` — Ansible completion latch
- `status.vmReady` — **all** VMs Ready
- `status.ansibleReady` — Ansible completed (`true` when Ansible is disabled)
- `status.vmCount` / `status.readyCount` — total expected VMs vs. currently Ready

## Usage

- [`examples/xr-min.yaml`](examples/xr-min.yaml) — two minimal Proxmox VMs, no Ansible.
- [`examples/xr.yaml`](examples/xr.yaml) — a Proxmox fleet (`web`×2 + `db`) with shared Ansible base-OS provisioning.
- [`examples/xr-max.yaml`](examples/xr-max.yaml) — a vSphere batch (`app`×3 + `bastion`) with every field set.

## Cluster preconditions

1. **Composed Configurations** installed: `proxmoxvm`, `vspherevm`,
   `ansible-run` (pulled automatically via `dependsOn`).
2. **Native VM provider + `ClusterProviderConfig`** for the chosen provider —
   see the [`proxmoxvm`](../proxmoxvm/) / [`vspherevm`](../vspherevm/) READMEs.
3. **`EnvironmentConfig`(s)** for the target environment(s) — carried by the
   native modules, selected by `spec.environmentConfig`.
4. **Ansible** (if enabled) — the Tekton stack, the credentials Secret, and the
   provider-kubernetes config named by `ansible.crossplaneProviderConfig`.

## Install

```bash
kubectl apply -f examples/functions.yaml
kubectl apply -f examples/configuration.yaml
```

## Development

```bash
crossplane render examples/xr.yaml apis/composition.yaml examples/functions.yaml \
  --include-function-results
# or: CONFIG=machinery/vm-batch XR=xr.yaml task render
```

`crossplane render` is single-level: it shows the emitted `NativeProxmoxVM` /
`NativeVsphereVM` XRs — it does **not** recurse into those Configurations' own
Compositions. The `AnsibleRun` is gated on all VMs being Ready with IPs, so it
does not appear in offline render. No EnvironmentConfig / `--extra-resources`
needed (vm-batch carries no placement; the native modules resolve it).

## Files

- `crossplane.yaml` — package metadata + dependencies
- `apis/definition.yaml` — XRD (`VMBatch`, v1alpha1, namespaced)
- `apis/composition.yaml` — `function-kcl` fan-out + status patch + `function-auto-ready`
- `examples/xr-min.yaml` / `xr.yaml` / `xr-max.yaml` — example XRs
- `examples/functions.yaml` — required Crossplane Functions
- `examples/configuration.yaml` — install manifest (OCI ref)

## License

Apache-2.0
