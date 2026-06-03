# harvester-vm — Crossplane Configuration

A Crossplane v2 Configuration that provisions a Harvester / KubeVirt virtual machine from a namespaced `HarvesterVM` XR.

## Overview

The Composition pipeline first loads shared per-environment infrastructure defaults via [`function-environment-configs`](https://github.com/crossplane-contrib/function-environment-configs), then:

1. renders a [`VolumeClaim`](../../k8s/volume-claim/) composite (the VM's root disk PVC) and a [`CloudInit`](../../k8s/cloud-config/) composite (the bootstrap Secret `<vmName>-cloud-init`) via [`function-patch-and-transform`](https://github.com/crossplane-contrib/function-patch-and-transform), then
2. renders a KubeVirt `VirtualMachine` (`kubevirt.io/v1`) as a [provider-kubernetes](https://github.com/crossplane-contrib/provider-kubernetes) `Object` via [`function-go-templating`](https://github.com/crossplane-contrib/function-go-templating), wiring in the PVC and the cloud-init Secret, plus an Observe-only `Object` on the resulting `VirtualMachineInstance` whose reported IP is surfaced on `status.share.ip`, and — when `spec.ansible.enabled` is set — an [`AnsibleRun`](../../cicd/ansible-run/) (Tekton PipelineRun) gated on that IP for post-create base-OS provisioning.

Per-VM settings (vm name, cpu, memory, disk, cloud-init users/packages, …) come from the XR spec. Shared infrastructure settings (provider config, storage class, target namespace, Multus network, base image) default from an `EnvironmentConfig` and are overridable per XR.

> This Configuration composes the repo's `volume-claim` and `cloud-config` Configurations — both are declared as `dependsOn` and must be installed on the cluster.

## Features

- **Composed building blocks** — reuses `volume-claim` (PVC) and `cloud-config` (cloud-init Secret) rather than re-implementing them.
- **Shared defaults via EnvironmentConfig** — `spec.environmentConfig` (default `default`) selects a Crossplane `EnvironmentConfig` (matched by the config-scoped label `harvester-vm.resources.stuttgart-things.com/environment`) whose `data` supplies per-environment defaults for `providerConfigRef`, `storageClassName`, `namespace`, `networkName` and `imageId`. Anything set explicitly on the XR spec wins; if no EnvironmentConfig matches, XR spec / built-in defaults apply. The label key is namespaced to this Configuration so it never collides with another Configuration's EnvironmentConfig. See [`examples/environmentconfig.yaml`](examples/environmentconfig.yaml).
- **Harvester image wiring** — `imageId` is rendered into the PVC annotation `harvesterhci.io/imageId`.
- **VM IP on status** — an Observe-only provider-kubernetes `Object` reads the `VirtualMachineInstance` and the Composition lifts its agent-reported IP onto `status.share.ip` (and the VMI `phase` onto `status.vm.ready`). This matches the `status.share.ip` convention of the OpenTofu-backed VM Configurations, so a `HarvesterVM` can feed the same shared provisioning / Ansible layer. **Requires the in-guest QEMU guest agent** — `cloudInit.packages` defaults to `[qemu-guest-agent]` for this reason; on a bridge/Multus network the VMI reports no IP without it.
- **Optional Ansible base-OS provisioning** — set `spec.ansible.enabled: true` to emit an `AnsibleRun` (Tekton PipelineRun) **after** the VM is Running and its IP is known; the IP auto-populates the Ansible inventory (`all+["<ip>"]`). Disabled by default — a plain `HarvesterVM` creates only the VM. Reuses the same IP gate as the OpenTofu path. Composes the repo's [`ansible-run`](../../cicd/ansible-run/) Configuration (declared as `dependsOn`).

## Status

| Field | Description |
|---|---|
| `status.share.ip` | VM IP address(es) as reported by the QEMU guest agent on the VMI. `[]` until the guest boots, leases an address and the agent reports. |
| `status.vm.ready` | `true` once the VMI `phase` is `Running`. |
| `status.ansibleReady` | `true` when the emitted `AnsibleRun`'s PipelineRun `Succeeded`. Always `true` when Ansible is disabled. |
| `status.vm.name` / `status.volume.*` / `status.cloudInit.*` | VM name and composed-resource readiness. |

> The IP only appears when `qemu-guest-agent` is installed **and** running in the guest. Verify with `kubectl get vmi -n <ns> <vmName> -o jsonpath='{.status.interfaces[0].infoSource}'` — the output must contain `guest-agent`.

## Parameters

| Parameter | Source | Default | Description |
|---|---|---|---|
| `volume.pvcName` | XR (required) | - | Root disk PVC name |
| `volume.storage` | XR | `10Gi` | Disk size |
| `volume.accessModes` | XR | `[ReadWriteOnce]` | PVC access modes |
| `volume.volumeMode` | XR | `Filesystem` | PVC volume mode |
| `volume.storageClassName` | XR or EnvironmentConfig | - | Storage class |
| `volume.namespace` | XR or EnvironmentConfig | - | Target namespace |
| `volume.imageId` | XR or EnvironmentConfig | - | Harvester image id (PVC annotation `harvesterhci.io/imageId`) |
| `cloudInit.vmName` | XR (required) | - | VM / cloud-init name (Secret is `<vmName>-cloud-init`) |
| `cloudInit.hostname` | XR (required) | - | VM hostname |
| `cloudInit.namespace` | XR or EnvironmentConfig | - | Target namespace |
| `cloudInit.packages` | XR | `[qemu-guest-agent]` | Cloud-init packages. Keep `qemu-guest-agent` if overriding, or `status.share.ip` stays empty. |
| `cloudInit.*` | XR | various | domain, users, runcmd, … (see XRD) |
| `vm.cpu.cores` | XR (required) | - | vCPU cores |
| `vm.resources.memory` / `vm.resources.cpu` | XR (required) | - | Memory / CPU limits |
| `vm.networks` | XR | from EnvironmentConfig `networkName` | Multus networks; one `default` interface to `networkName` when omitted |
| `vm.*` | XR | various | os, runStrategy, machineType, … (see XRD) |
| `providerConfigRef` | XR or EnvironmentConfig | - | provider-kubernetes ClusterProviderConfig name |
| `environmentConfig` | XR | `default` | EnvironmentConfig selector label value |
| `ansible.enabled` | XR | `false` | Run base-OS Ansible provisioning after the VM has an IP |
| `ansible.playbooks` | XR | `[sthings.baseos.setup]` | Playbooks to run |
| `ansible.varsInventory` | XR | auto (VM IP) | Inventory; auto-populated with the VM IP when omitted |
| `ansible.varsFile` | XR | - | Ansible vars (`key+-value` form) |
| `ansible.credentialsSecretName` | XR | `ansible-credentials` | Ansible credentials Secret |
| `ansible.pipelineNamespace` | XR | `tekton-ci` | Tekton namespace |
| `ansible.crossplaneProviderConfig` | XR | `dev` | provider-kubernetes config wrapping the PipelineRun |
| `ansible.*` | XR | various | extraCollections, extraRoles, wrapInCrossplane, … (see XRD) |

> Override precedence: **XR spec → EnvironmentConfig → built-in default.**

## Usage

### Minimum

See [`examples/xr-min.yaml`](examples/xr-min.yaml) — only the required fields; all placement and the base image come from the EnvironmentConfig.

### Realistic

See [`examples/xr.yaml`](examples/xr.yaml) — VM sized per spec, infra inherited from the EnvironmentConfig.

### Maximum

See [`examples/xr-max.yaml`](examples/xr-max.yaml) — every field set explicitly, overriding all EnvironmentConfig defaults.

## Cluster preconditions

### 1. provider-kubernetes + ClusterProviderConfig

```bash
kubectl apply -f examples/provider.yaml
kubectl apply -f examples/cluster-provider-config.yaml
```

The `ClusterProviderConfig` name must match `providerConfigRef` / EnvironmentConfig `providerConfigRef` (default `default`). `InjectedIdentity` assumes Crossplane runs on the same Harvester / KubeVirt cluster the VMs are created on; target a remote cluster by switching to a kubeconfig Secret.

### 2. Dependency Configurations

`harvester-vm` composes `volume-claim`, `cloud-config` and (for Ansible) `ansible-run` — all declared as `dependsOn`, so the package manager pulls them automatically when this Configuration is installed:

```bash
kubectl apply -f ../../k8s/volume-claim/examples/configuration.yaml
kubectl apply -f ../../k8s/cloud-config/examples/configuration.yaml
kubectl apply -f ../../cicd/ansible-run/examples/configuration.yaml   # only when ansible.enabled
```

> Ansible is **opt-in** (`spec.ansible.enabled: false` by default). When you enable it, the `ansible-run` preconditions also apply — the Tekton stack, the credentials Secret (`ansible.credentialsSecretName`), and the provider-kubernetes config named by `ansible.crossplaneProviderConfig`. See the [`ansible-run` README](../../cicd/ansible-run/).

### 3. EnvironmentConfig (shared defaults)

Edit [`examples/environmentconfig.yaml`](examples/environmentconfig.yaml) for your Harvester environment (the shipped values are placeholders), then:

```bash
kubectl apply -f examples/environmentconfig.yaml
```

## Install

```bash
kubectl apply -f examples/functions.yaml
kubectl apply -f examples/configuration.yaml
```

## Development

### Render the Composition

`crossplane render` does not resolve EnvironmentConfigs from a cluster, so pass the example one as an extra resource:

```bash
crossplane render examples/xr.yaml apis/composition.yaml examples/functions.yaml \
  --extra-resources examples/environmentconfig.yaml \
  --include-function-results
```

Or via the repo Taskfile:

```bash
CONFIG=machinery/harvester-vm XR=xr.yaml task render
```

`crossplane render` has no live `VirtualMachineInstance`, so the Observe-only VMI `Object` yields nothing and `status.share.ip` renders `[]` — expected offline.

### Trace resource status

```bash
crossplane beta trace harvestervm.resources.stuttgart-things.com dev9 -n default
```

## Files

- `crossplane.yaml` — package metadata + dependencies
- `apis/definition.yaml` — XRD (`HarvesterVM`, v1alpha1, namespaced)
- `apis/composition.yaml` — Composition pipeline (function-environment-configs + function-patch-and-transform + function-go-templating)
- `examples/xr-min.yaml` — only required fields; everything else from the EnvironmentConfig
- `examples/xr.yaml` — realistic single VM
- `examples/xr-max.yaml` — every spec field exercised
- `examples/environmentconfig.yaml` — shared per-environment defaults (placeholders)
- `examples/functions.yaml` — required Crossplane Functions
- `examples/configuration.yaml` — install manifest (OCI ref)
- `examples/provider.yaml` — provider-kubernetes install
- `examples/cluster-provider-config.yaml` — provider-kubernetes ClusterProviderConfig

## License

Apache-2.0
