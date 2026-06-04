# vm-provision — Crossplane Configuration

A Crossplane v2 Configuration that provides unified VM provisioning from a single namespaced `VMProvision` XR, with optional Ansible base-OS automation. Supports vSphere and Proxmox via one API.

## Overview

The Composition is a [`function-kcl`](https://github.com/crossplane-contrib/function-kcl) router that composes the repo's [`vsphere-vm`](../vsphere-vm/), [`proxmox-vm`](../proxmox-vm/) and [`ansible-run`](../../cicd/ansible-run/) Configurations:

- `spec.provider: vsphere` emits a `VsphereVM`; `proxmox` emits a `ProxmoxVM` (both OpenTofu `Workspace`-backed).
- When `spec.ansible.enabled` is `true`, an `AnsibleRun` (Tekton `PipelineRun`) is emitted **after** the VM is Ready and its IP is known — the VM IP auto-populates the Ansible inventory.
- The VM IP, provider, and VM/Ansible readiness are surfaced on the XR status.

> This Configuration composes three others — all declared as `dependsOn` (`vsphere-vm`, `proxmox-vm`, `ansible-run`), so the package manager pulls them (and transitively their providers/functions) on install.

## Parameters

### Common

| Parameter | Required | Default | Description |
|---|---|---|---|
| `provider` | yes | - | `vsphere` or `proxmox` |
| `vm.name` | yes | - | VM name |
| `vm.template` | yes | - | VM template |
| `vm.count` | no | `"1"` | Number of VMs |
| `vm.cpu` | no | `"4"` | vCPUs |
| `vm.ram` | no | `"4096"` | Memory (MB) |
| `vm.disk` | no | `"64"` | Disk size (Proxmox gets a `G` suffix auto-appended) |
| `vm.firmware` | no | `bios` (vSphere) / `seabios` (Proxmox) | Firmware |
| `tfvars.secretName` | yes | - | tfvars Secret name |
| `connectionSecret.name` | yes | - | Output connection Secret name |
| `providerRef.name` | yes | `default` | OpenTofu provider config name |
| `providerRef.kind` | no | `ClusterProviderConfig` | `ProviderConfig` or `ClusterProviderConfig` |

### vSphere (`spec.vsphere`)

`folderPath`, `datacenter`, `datastore`, `resourcePool`, `network`, `unverifiedSsl` (default `"true"`).

### Proxmox (`spec.proxmox`)

`node`, `datastore`, `network`, `folderPath` (optional).

### Ansible (`spec.ansible`)

| Parameter | Default | Description |
|---|---|---|
| `enabled` | `false` | Enable Ansible provisioning after VM creation |
| `playbooks` | `["sthings.baseos.setup"]` | Playbooks to run |
| `varsFile` | - | Ansible vars (`key+-value` form) |
| `varsInventory` | auto (VM IP) | Inventory; auto-populated with the VM IP when omitted |
| `credentialsSecretName` | `ansible-credentials` | Ansible credentials Secret |
| `pipelineNamespace` | `tekton-ci` | Tekton namespace |
| `crossplaneProviderConfig` | `in-cluster` | provider-kubernetes config used to wrap the PipelineRun |
| `wrapInCrossplane` | `true` | Wrap the PipelineRun in a Crossplane Object |
| `extraCollections` / `extraRoles` | - | Extra Ansible collections / roles |

## Status

- `status.share.ip` — VM IP address(es)
- `status.share.provider` — which provider was used
- `status.vmReady` — VM Ready
- `status.ansibleReady` — Ansible completed (`true` when Ansible is disabled)

## Usage

- [`examples/xr-min.yaml`](examples/xr-min.yaml) — minimal Proxmox VM, no Ansible.
- [`examples/xr.yaml`](examples/xr.yaml) — vSphere VM with Ansible base-OS provisioning.
- [`examples/xr-max.yaml`](examples/xr-max.yaml) — Proxmox VM with every field set.

## Cluster preconditions

1. **Composed Configurations** installed: `vsphere-vm`, `proxmox-vm`, `ansible-run` (pulled automatically via `dependsOn`).
2. **OpenTofu `ClusterProviderConfig`** — see [`examples/cluster-provider-config.yaml`](examples/cluster-provider-config.yaml); name must match `providerRef.name`.
3. **tfvars Secret(s)** — Proxmox/vSphere credentials in the XR's namespace (see the `proxmox-vm` / `vsphere-vm` READMEs, incl. the ESO option).
4. **Ansible** (if enabled) — the Tekton stack, the credentials Secret, and the provider-kubernetes config named by `ansible.crossplaneProviderConfig`.

## Install

```bash
kubectl apply -f examples/functions.yaml
kubectl apply -f examples/configuration.yaml
```

## Development

```bash
crossplane render examples/xr.yaml apis/composition.yaml examples/functions.yaml \
  --include-function-results
# or: CONFIG=machinery/vm-provision XR=xr.yaml task render
```

`crossplane render` is single-level: it shows the emitted `VsphereVM` / `ProxmoxVM` (and, on a live cluster with a Ready VM, the `AnsibleRun`) — it does **not** recurse into those Configurations' own Compositions. The `AnsibleRun` is gated on VM readiness + IP, so it does not appear in offline render.

## Files

- `crossplane.yaml` — package metadata + dependencies
- `apis/definition.yaml` — XRD (`VMProvision`, v1alpha1, namespaced)
- `apis/composition.yaml` — `function-kcl` router + status patch + `function-auto-ready`
- `examples/xr-min.yaml` / `xr.yaml` / `xr-max.yaml` — example XRs
- `examples/functions.yaml` — required Crossplane Functions
- `examples/configuration.yaml` — install manifest (OCI ref)
- `examples/cluster-provider-config.yaml` — OpenTofu ClusterProviderConfig

## License

Apache-2.0
