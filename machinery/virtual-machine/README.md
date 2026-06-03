# virtual-machine — Crossplane Configuration

A Crossplane v2 Configuration providing a high-level, opinionated VM API. A
namespaced `XVirtualMachine` XR asks for a VM by **intent** — t-shirt size,
provider, environment and OS — and the Composition resolves the per-environment
datacenter topology and emits a single `VMProvision`, which routes to the
matching provider VM (and optional Ansible base-OS provisioning).

## Overview

This is the top of a three-layer stack:

```
XVirtualMachine  (this)  ──►  VMProvision  (vm-provision)  ──►  VsphereVM / ProxmoxVM (+ AnsibleRun)
   size/provider/env             router/aggregator                leaf OpenTofu Workspaces
```

The Composition pipeline:

1. **`load-environment`** ([`function-environment-configs`](https://github.com/crossplane-contrib/function-environment-configs)) — loads the per-environment datacenter topology, selected by the config-scoped label `virtual-machine.resources.stuttgart-things.com/environment` (value from `spec.environment`). Each `EnvironmentConfig` holds **both** a `vsphere` and a `proxmox` sub-block under `data`.
2. **`render`** ([`function-kcl`](https://github.com/crossplane-contrib/function-kcl)) — maps the t-shirt `size` to cpu/ram/disk, picks the provider sub-block by `spec.provider`, maps `spec.os` to a template name, and emits a `VMProvision`.
3. **`patch-status`** (`function-kcl`) — surfaces VM IP / provider / size / environment and VM/Ansible readiness onto the XR status.
4. **`automatically-detect-ready-composed-resources`** ([`function-auto-ready`](https://github.com/crossplane-contrib/function-auto-ready)).

## Parameters

| Parameter | Source | Default | Description |
|---|---|---|---|
| `size` | XR (required) | - | T-shirt size: `small`/`medium`/`large`/`xlarge` → cpu/ram/disk |
| `provider` | XR (required) | - | `vsphere` or `proxmox` — picks the EnvironmentConfig sub-block |
| `environment` | XR (required) | - | `labul`/`labda` — selects the EnvironmentConfig |
| `os` | XR | `ubuntu24` | `ubuntu24`/`ubuntu22` — keys the per-provider `templates` map |
| `count` | XR | `"1"` | Number of VMs |
| `ansible` | XR | `true` | Run base-OS Ansible provisioning after VM creation |
| `providerRef.name` | XR | `default` | OpenTofu provider config name |
| `providerRef.kind` | XR | `ClusterProviderConfig` | `ProviderConfig` or `ClusterProviderConfig` |

### T-shirt size map

| size | cpu | ram (MB) | disk |
|---|---|---|---|
| small | 2 | 2048 | 32 |
| medium | 4 | 4096 | 64 |
| large | 8 | 8192 | 128 |
| xlarge | 16 | 16384 | 256 |

Proxmox disks get a `G` suffix appended automatically (`128` → `128G`); vSphere
uses the bare number.

## Usage

- **Minimum** — [`examples/xr-min.yaml`](examples/xr-min.yaml): just `size`, `provider`, `environment`; `os`/`count`/`ansible` default, topology from the EnvironmentConfig.
- **Realistic** — [`examples/xr.yaml`](examples/xr.yaml): a medium vSphere VM in LabUL on Ubuntu 24 with Ansible.
- **Maximum** — [`examples/xr-max.yaml`](examples/xr-max.yaml): every field set; a large Proxmox VM, Ansible disabled.

## Cluster preconditions

### 1. The composed Configurations

`vm-provision` (and transitively `vsphere-vm`, `proxmox-vm`, `ansible-run`) is
declared as `dependsOn`, so installing `virtual-machine` pulls it. Each carries
its own preconditions — an OpenTofu `ClusterProviderConfig`, the tfvars
Secret(s), and (for Ansible) the Tekton stack + credentials Secret. See those
Configurations' READMEs.

### 2. OpenTofu ClusterProviderConfig

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

One `EnvironmentConfig` per environment, holding both provider sub-blocks. The
example describes LabUL.

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
- `examples/environmentconfig.yaml` — per-environment datacenter topology (LabUL, both providers)
- `examples/functions.yaml` — required Crossplane Functions
- `examples/configuration.yaml` — install manifest (OCI ref)
- `examples/cluster-provider-config.yaml` — OpenTofu ClusterProviderConfig (Terraform K8s backend)

## License

Apache-2.0
