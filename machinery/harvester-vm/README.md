# harvester-vm — Crossplane Configuration

A Crossplane v2 Configuration that provisions a Harvester / KubeVirt virtual machine from a namespaced `HarvesterVM` XR.

## Overview

The Composition pipeline first loads shared per-environment infrastructure defaults via [`function-environment-configs`](https://github.com/crossplane-contrib/function-environment-configs), then:

1. renders a [`VolumeClaim`](../../k8s/volume-claim/) composite (the VM's root disk PVC) and a [`CloudInit`](../../k8s/cloud-config/) composite (the bootstrap Secret `<vmName>-cloud-init`) via [`function-patch-and-transform`](https://github.com/crossplane-contrib/function-patch-and-transform), then
2. renders a KubeVirt `VirtualMachine` (`kubevirt.io/v1`) as a [provider-kubernetes](https://github.com/crossplane-contrib/provider-kubernetes) `Object` via [`function-go-templating`](https://github.com/crossplane-contrib/function-go-templating), wiring in the PVC and the cloud-init Secret.

Per-VM settings (vm name, cpu, memory, disk, cloud-init users/packages, …) come from the XR spec. Shared infrastructure settings (provider config, storage class, target namespace, Multus network, base image) default from an `EnvironmentConfig` and are overridable per XR.

> This Configuration composes the repo's `volume-claim` and `cloud-config` Configurations — both are declared as `dependsOn` and must be installed on the cluster.

## Features

- **Composed building blocks** — reuses `volume-claim` (PVC) and `cloud-config` (cloud-init Secret) rather than re-implementing them.
- **Shared defaults via EnvironmentConfig** — `spec.environmentConfig` (default `default`) selects a Crossplane `EnvironmentConfig` (matched by the config-scoped label `harvester-vm.resources.stuttgart-things.com/environment`) whose `data` supplies per-environment defaults for `providerConfigRef`, `storageClassName`, `namespace`, `networkName` and `imageId`. Anything set explicitly on the XR spec wins; if no EnvironmentConfig matches, XR spec / built-in defaults apply. The label key is namespaced to this Configuration so it never collides with another Configuration's EnvironmentConfig. See [`examples/environmentconfig.yaml`](examples/environmentconfig.yaml).
- **Harvester image wiring** — `imageId` is rendered into the PVC annotation `harvesterhci.io/imageId`.

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
| `cloudInit.*` | XR | various | domain, users, packages, runcmd, … (see XRD) |
| `vm.cpu.cores` | XR (required) | - | vCPU cores |
| `vm.resources.memory` / `vm.resources.cpu` | XR (required) | - | Memory / CPU limits |
| `vm.networks` | XR | from EnvironmentConfig `networkName` | Multus networks; one `default` interface to `networkName` when omitted |
| `vm.*` | XR | various | os, runStrategy, machineType, … (see XRD) |
| `providerConfigRef` | XR or EnvironmentConfig | - | provider-kubernetes ClusterProviderConfig name |
| `environmentConfig` | XR | `default` | EnvironmentConfig selector label value |

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

`harvester-vm` composes `volume-claim` and `cloud-config` — install them (declared as `dependsOn`, so the package manager pulls them automatically when this Configuration is installed):

```bash
kubectl apply -f ../../k8s/volume-claim/examples/configuration.yaml
kubectl apply -f ../../k8s/cloud-config/examples/configuration.yaml
```

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
