# CLAUDE.md — `harvester-vm` Configuration

Context for working on this Crossplane Configuration. Repo-wide conventions
(Crossplane v2, registries, function-name rules, `task` workflows) live in the
**root `CLAUDE.md`** — read that too. This file focuses on `harvester-vm`.

## What it does
Turns a namespaced `HarvesterVM` XR (group `resources.stuttgart-things.com`,
`v1alpha1`) into a Harvester / KubeVirt `VirtualMachine`. Unlike the
OpenTofu-backed `proxmox-vm` / `vsphere-vm`, this Configuration is **composed**:
it creates a `VolumeClaim` and a `CloudInit` composite (from this repo's
`volume-claim` and `cloud-config` Configurations) and then a KubeVirt
`VirtualMachine` via provider-kubernetes.

## Composition pipeline (`apis/composition.yaml`)
1. **`load-environment`** (`function-environment-configs`) — selects an
   `EnvironmentConfig` by the **config-scoped** label
   `harvester-vm.resources.stuttgart-things.com/environment` (value from
   `spec.environmentConfig`, `fromFieldPathPolicy: Optional`) and loads its
   `data` into the pipeline environment. The label key is intentionally
   namespaced to this Configuration — a generic shared key collides across
   Configurations and `function-environment-configs` requires exactly one
   Selector match (`expected exactly one required resource, got 2`).
2. **`create-volume-claim`** (`function-patch-and-transform`) — renders a
   `VolumeClaim` composite (root disk PVC). The `imageId` becomes the PVC
   annotation `harvesterhci.io/imageId`.
3. **`create-cloud-init`** (`function-patch-and-transform`) — renders a
   `CloudInit` composite; `cloud-config` writes a Secret named
   `<vmName>-cloud-init`, which step 4's VM mounts as the cloudinit disk.
4. **`create-vm`** (`function-go-templating`) — renders the KubeVirt
   `VirtualMachine` as a `kubernetes.m.crossplane.io/v1alpha1` `Object`
   (`managementPolicies: ['*']`, ClusterProviderConfig). The same step also
   emits an **Observe-only** `Object` (`managementPolicies: ['Observe']`) on the
   resulting `VirtualMachineInstance` (resource name `<vmName>-vmi`, same
   name/namespace as the VM) and reads its agent-reported IP back onto
   `status.share.ip` (+ VMI `phase` → `status.vm.ready`).

5. **Optional Ansible (same `create-vm` step)** — when `spec.ansible.enabled`
   is true (default false) AND the VMI `phase == Running` with a resolved IP,
   the step also emits an `AnsibleRun` (`<vmName>-ansible-run`, from the
   `ansible-run` Configuration) whose `ansibleVarsInventory` auto-populates with
   the VM IP (`all+["<ip>"]`). `status.ansibleReady` reads the AnsibleRun's
   `status.succeeded == "True"` back from observed; it is `true` outright when
   Ansible is disabled. The gate (Running + IP) means the AnsibleRun never
   appears in offline `crossplane render` — same as vm-provision's tofu path.

## VM IP surfacing (`status.share.ip`)
The IP lives on the **VMI**, not the VM, at `status.interfaces[].ipAddress`,
and is only populated by the **in-guest QEMU guest agent**. So:
- `cloudInit.packages` defaults to `[qemu-guest-agent]` in the XRD. Overriding
  the list without the agent ⇒ no IP on a bridge/Multus network.
- The IP is read from the observed VMI `Object`'s
  `status.atProvider.manifest.status.interfaces[0].ipAddress` via Sprig `dig`
  (nil-safe). It stays `[]` on the first reconcile, offline `render`, and until
  the guest boots + the agent reports — by design, no template error.
- `status.share.ip` deliberately mirrors the OpenTofu leaves' convention so a
  `HarvesterVM` can later feed the shared `vm-provision` / Ansible layer.

## Override semantics (the important bit)
Same pattern as `proxmox-vm`: each env-sourced field has **two** patches in
order — `FromEnvironmentFieldPath <key>` (Optional) sets the env default, then
`FromCompositeFieldPath spec.<...>` (Optional) overrides it **only when the XR
sets the field**. Precedence: **XR spec → EnvironmentConfig → built-in default**.

For this to work the XRD must **not** default the env-sourced fields
(`providerConfigRef`, `volume.storageClassName`, `volume.namespace`,
`volume.imageId`, `cloudInit.namespace`) — an XRD default would make the field
always present, so the Optional XR patch would always fire and the
EnvironmentConfig value would never win. Only purely per-VM fields
(`volume.storage/accessModes/volumeMode`, the `cloudInit.*` booleans/domain,
`vm.*`) carry XRD defaults.

The go-templating VM step reads the environment from
`index .context "apiextensions.crossplane.io/environment"` (guarded with
`| default dict`) and applies the same precedence inline via Sprig
`default` chains (e.g. `$xr.spec.cloudInit.namespace | default $env.namespace
| default "vms"`). The `imageId` annotation merge relies on patch order:
the passthrough `spec.volume.annotations` map is set first, then the
`spec.annotations[harvesterhci.io/imageId]` key is merged in.

## EnvironmentConfig data keys
`providerConfigRef, storageClassName, namespace, networkName, imageId`.
See `examples/environmentconfig.yaml` (values are PLACEHOLDERS — there is no
live Harvester environment baked in yet; replace before cluster use).

## Composes other Configurations
`dependsOn` includes `volume-claim` (>=v0.1.0) and `cloud-config` (>=v0.5.4)
from this repo. The Composition references their XR kinds `VolumeClaim` and
`CloudInit` directly. If their XRD spec field names change, the
patch-and-transform steps here must change with them — in particular the
`cloud-config` Secret name convention `<vmName>-cloud-init`, which the VM
step hardcodes as the cloudinit disk `secretRef`.

## Local render
`crossplane render` does NOT resolve EnvironmentConfigs from a cluster — pass
the example via `--extra-resources`, or the env-sourced fields come out empty:
```bash
crossplane render examples/xr.yaml apis/composition.yaml examples/functions.yaml \
  --extra-resources examples/environmentconfig.yaml --include-function-results
# or: CONFIG=machinery/harvester-vm XR=xr.yaml task render
```
Note `render` does not apply XRD defaults either, so fields like
`volume.storage` only appear when set explicitly on the XR.
