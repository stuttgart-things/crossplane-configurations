# virtual-machine — Crossplane Configuration

A Crossplane v2 Configuration providing a high-level, opinionated VM API. A
namespaced `XVirtualMachine` XR asks for a VM by **intent** — t-shirt size,
provider, environment and OS — and the Composition resolves the per-environment
topology and emits the matching backend (and optional Ansible base-OS
provisioning).

## Overview

`XVirtualMachine` routes by `spec.provider`:

```
                          ┌─ vsphere/proxmox ─► VMProvision (vm-provision) ─► VsphereVM / ProxmoxVM (+ AnsibleRun)
XVirtualMachine  (this) ──┤                                                      leaf OpenTofu Workspaces
   size/provider/env      └─ harvester ───────► HarvesterVM  (harvester-vm)  ─► KubeVirt VM (+ AnsibleRun)
```

The Composition pipeline:

1. **`load-environment`** ([`function-environment-configs`](https://github.com/crossplane-contrib/function-environment-configs)) — loads the per-environment topology, selected by the config-scoped label `virtual-machine.resources.stuttgart-things.com/environment` (value from `spec.environment`). Each `EnvironmentConfig` holds a `vsphere`, a `proxmox` and a `harvester` sub-block under `data`, plus a cross-provider `data.ansible` block: `crossplaneProviderConfig` (the provider-kubernetes config that runs the Ansible PipelineRun, falls back to `in-cluster`) and `ansibleWorkingImage` (the runner image whose baked-in collections define the playbook set, falls back to `…/sthings-ansible:13.5.0-test`).
2. **`render`** ([`function-kcl`](https://github.com/crossplane-contrib/function-kcl)) — maps the t-shirt `size` to cpu/ram/disk and picks the sub-block by `spec.provider`. For `vsphere`/`proxmox` it maps `spec.os` to a template name and emits a `VMProvision`; for `harvester` it maps `spec.os` to a Harvester `imageId` and emits a `HarvesterVM` directly.
3. **`patch-status`** (`function-kcl`) — surfaces VM IP / provider / size / environment and VM/Ansible readiness onto the XR status (reads either a `VMProvision` or a `HarvesterVM`).
4. **`automatically-detect-ready-composed-resources`** ([`function-auto-ready`](https://github.com/crossplane-contrib/function-auto-ready)).

## Parameters

| Parameter | Source | Default | Description |
|---|---|---|---|
| `size` | XR (required) | - | T-shirt size: `small`/`medium`/`large`/`xlarge` → cpu/ram/disk |
| `provider` | XR (required) | - | `vsphere`/`proxmox` (→ `VMProvision`) or `harvester` (→ `HarvesterVM`) — picks the EnvironmentConfig sub-block |
| `environment` | XR (required) | - | `labul`/`labda`/`harvester` — selects the EnvironmentConfig |
| `os` | XR | `ubuntu24` | **Golden** (`sthings-u26` / `sthings-u26-k3s` / `sthings-rocky9` / `sthings-leap`) or **vanilla** (`ubuntu24` = the `u26-dev` cloud image / `rocky9-dev`). Keys the per-provider `templates` map (vsphere/proxmox) or the `images` map → `{imageId, storageClassName}` (harvester). **`ansible: true` requires a golden image** — see the `ansible` row. |
| `count` | XR | `"1"` | Number of VMs (vsphere/proxmox only; `harvester` is always a single VM) |
| `ansible` | XR | `true` | Run base-OS Ansible provisioning after VM creation. **Only works on a golden `sthings-*` image** (see the note below) — the Ansible runner authenticates over SSH with the `sthings` user + password from the `ansible-credentials` Secret, which only the golden images bake in. On a vanilla image (`ubuntu24`/`rocky9-dev`) the run fails with `Permission denied (publickey)`. Set `ansible: false` for vanilla images. |
| `providerRef.name` | XR | `default` | OpenTofu provider config name (vsphere/proxmox; `harvester` uses the sub-block's `providerConfigRef`) |
| `providerRef.kind` | XR | `ClusterProviderConfig` | `ProviderConfig` or `ClusterProviderConfig` |

> **`ansible: true` needs a golden image.** The base-OS provisioning runs as a Tekton PipelineRun that SSHes into the new VM as `sthings`, authenticating with `ANSIBLE_USER`/`ANSIBLE_PASSWORD` from the `ansible-credentials` Secret (no SSH key). Only the **golden** `sthings-*` images (built by `harvester/packer/golden/*`) bake that `sthings` user + matching password, and the Composition emits `ssh_pwauth: true` on the cloud-init so password auth is enabled. The **vanilla** images (`ubuntu24`/`u26-dev`, `rocky9-dev`) have no baked password — the cloud-init creates `sthings` with only the public SSH key — so `ansible: true` there fails with `sthings@<ip>: Permission denied (publickey)`. Use a golden image when `ansible: true`, or set `ansible: false` for vanilla images (key-only access).

### T-shirt size map

| size | cpu | ram (MB) | disk |
|---|---|---|---|
| small | 2 | 2048 | 32 |
| medium | 4 | 4096 | 64 |
| large | 8 | 8192 | 128 |
| xlarge | 16 | 16384 | 256 |

Per provider the size maps differently: Proxmox disks get a `G` suffix
(`128` → `128G`); vSphere uses the bare number; **Harvester** uses `<disk>Gi`
for the PVC, `<ram>Mi` for memory, and `cpu` for vCPU cores.

### Harvester sub-block (`data.harvester`)

For `provider: harvester` the EnvironmentConfig `harvester` sub-block supplies:
`images` (os → Harvester `imageId`), `providerConfigRef`, `storageClassName`,
`namespace`, `networkName`, and a default cloud-init `user` + `sshKey` (so
Ansible can reach the VM). `qemu-guest-agent` — required for the VM to report
its IP — comes from the `HarvesterVM` XRD default, so it is not set here.

## Usage

- **Minimum** — [`examples/xr-min.yaml`](examples/xr-min.yaml): just `size`, `provider`, `environment`; `os`/`count`/`ansible` default, topology from the EnvironmentConfig.
- **Realistic** — [`examples/xr.yaml`](examples/xr.yaml): a medium vSphere VM in LabUL on Ubuntu 24 with Ansible.
- **Maximum** — [`examples/xr-max.yaml`](examples/xr-max.yaml): every field set; a large Proxmox VM, Ansible disabled.
- **Harvester** — [`examples/xr-harvester.yaml`](examples/xr-harvester.yaml): a medium Harvester / KubeVirt VM in LabUL on Ubuntu 24 with Ansible.

## Cluster preconditions

### 1. The composed Configurations

`vm-provision` (and transitively `vsphere-vm`, `proxmox-vm`, `ansible-run`) and
`harvester-vm` (and transitively `volume-claim`, `cloud-config`, `ansible-run`)
are declared as `dependsOn`, so installing `virtual-machine` pulls them. Each
carries its own preconditions. **vsphere/proxmox** (steps 2–3): an OpenTofu
`ClusterProviderConfig` + the tfvars Secret(s). **harvester**: a
provider-kubernetes `ClusterProviderConfig` matching the `harvester` sub-block's
`providerConfigRef` (see the `harvester-vm` README). Ansible (any provider)
needs the Tekton stack + credentials Secret.

### 2. OpenTofu ClusterProviderConfig (vsphere/proxmox)

```bash
kubectl apply -f examples/cluster-provider-config.yaml
```

The name must match the XR's `spec.providerRef.name` (default `default`).

### 3. tfvars Secret(s)

The `VMProvision` references a tfvars Secret per provider (`vsphere-tfvars` /
`proxmox-tfvars`, from the EnvironmentConfig sub-block), created in the XR's
namespace. See the `vsphere-vm` / `proxmox-vm` READMEs (including the optional
ESO-from-Vault path).

### 4. EnvironmentConfig (datacenter topology)

```bash
kubectl apply -f examples/environmentconfig.yaml
```

One `EnvironmentConfig` per environment, holding the `vsphere`, `proxmox` and
`harvester` sub-blocks. The example describes LabUL (the `harvester` values are
placeholders — substitute your Harvester environment).

> **Shortcut (vSphere + ESO):** [`examples/deploy-vsphere/`](examples/deploy-vsphere/) is an apply-ordered folder covering all of the above — a dedicated `vsphere-vms` namespace, the Configuration install, ClusterProviderConfig, a vSphere-only LabUL EnvironmentConfig, an ESO `ClusterSecretStore` + `ExternalSecret` for `vsphere-tfvars` (no `kubectl create secret`), and the XR. Files are numbered `0-platform-*` … `8-user-xr.yaml` (incl. the Ansible provider-kubernetes config + credentials), so `kubectl apply -f examples/deploy-vsphere/` runs them in dependency order. See its [README](examples/deploy-vsphere/README.md).

> **Shortcut (Harvester + ESO):** [`examples/deploy-harvester/`](examples/deploy-harvester/) is the Harvester sibling — a dedicated `harvester-vms` namespace, the Configuration install, a provider-kubernetes ClusterProviderConfig for the VM (`harvester`, with a remote-cluster `2b` alternative), a Harvester-only LabUL EnvironmentConfig, the Ansible provider-kubernetes config + an ESO `ClusterSecretStore`/`ExternalSecret` for `ansible-credentials`, and the XR. No tfvars (KubeVirt, not OpenTofu). Files are numbered `0-platform-*` … `7-user-xr.yaml`, so `kubectl apply -f examples/deploy-harvester/` runs them in dependency order. See its [README](examples/deploy-harvester/README.md).

## Install

```bash
kubectl apply -f examples/functions.yaml
kubectl apply -f examples/configuration.yaml
```

## Development

### Render the Composition

`crossplane render` does not resolve EnvironmentConfigs from a cluster, so pass
the example one as an extra resource:

```bash
crossplane render examples/xr.yaml apis/composition.yaml examples/functions.yaml \
  --extra-resources examples/environmentconfig.yaml \
  --include-function-results
```

Or via the repo Taskfile:

```bash
task render
# or non-interactive:
CONFIG=machinery/virtual-machine XR=xr.yaml task render
```

The emitted `VMProvision` is inert under render (no live VM status), so
`status.share.ip` stays `[]` and readiness stays false — expected.

### Trace resource status

```bash
crossplane beta trace xvirtualmachine.resources.stuttgart-things.com vm-standard -n default
```

## Files

- `crossplane.yaml` — package metadata + dependencies
- `apis/definition.yaml` — XRD (`XVirtualMachine`, v1alpha1, namespaced)
- `apis/composition.yaml` — Composition pipeline (env-configs + kcl + auto-ready)
- `examples/xr-min.yaml` — only required fields
- `examples/xr.yaml` — realistic single VM
- `examples/xr-max.yaml` — every spec field exercised
- `examples/xr-harvester.yaml` — `provider: harvester` → a HarvesterVM
- `examples/environmentconfig.yaml` — per-environment topology (LabUL; vsphere/proxmox/harvester sub-blocks)
- `examples/functions.yaml` — required Crossplane Functions
- `examples/configuration.yaml` — install manifest (OCI ref)
- `examples/cluster-provider-config.yaml` — OpenTofu ClusterProviderConfig (Terraform K8s backend)
- `examples/deploy-vsphere/` — apply-ordered folder (`0-platform-*` … `8-user-xr.yaml`): dedicated namespaces + install + OpenTofu & provider-kubernetes provider configs + EnvironmentConfig + ESO (ClusterSecretStore/ExternalSecrets for tfvars & ansible-credentials) + XR for a vSphere VM with provisioning
- `examples/deploy-harvester/` — Harvester sibling (`0-platform-*` … `7-user-xr.yaml`): dedicated namespaces + install + provider-kubernetes provider configs (VM `harvester` + Ansible `in-cluster`) + Harvester EnvironmentConfig + ESO (ClusterSecretStore/ExternalSecret for ansible-credentials) + XR for a Harvester/KubeVirt VM with provisioning

## License

Apache-2.0
