# vsphere-vm — Crossplane Configuration

A Crossplane v2 Configuration that provisions a VMware vSphere virtual machine from a namespaced `VsphereVM` XR.

## Overview

The Composition pipeline first loads shared per-environment infrastructure defaults via [`function-environment-configs`](https://github.com/crossplane-contrib/function-environment-configs), then renders an OpenTofu `Workspace` (`opentofu.m.upbound.io/v1beta1`, [provider-opentofu](https://marketplace.upbound.io/providers/upbound/provider-opentofu)) via [`function-patch-and-transform`](https://github.com/crossplane-contrib/function-patch-and-transform). The Workspace runs the Terraform module [`github.com/stuttgart-things/vsphere-vm`](https://github.com/stuttgart-things/vsphere-vm), which creates the VM on vSphere. The VM's IP address is surfaced on `status.share.ip`.

Per-VM settings (name, cpu, ram, disk, count, firmware, bootstrap) come from the XR spec. Shared infrastructure settings (datacenter, datastore, resource pool, network, folder, template, tfvars Secret, provider config) default from an `EnvironmentConfig` and are overridable per XR.

## Features

- **VM provisioning via OpenTofu** — the `Workspace` runs the pinned Terraform module `git::https://github.com/stuttgart-things/vsphere-vm.git?ref=v2.12.0-1.0.0` (pin lives in the Composition base).
- **Shared defaults via EnvironmentConfig** — `spec.environmentConfig` (default `default`) selects a Crossplane `EnvironmentConfig` (matched by the config-scoped label `vsphere-vm.resources.stuttgart-things.com/environment`) whose `data` supplies per-environment defaults for `folderPath`, `datacenter`, `datastore`, `resourcePool`, `network`, `template`, `annotation`, `unverifiedSsl`, `tfvarsSecretName`, `tfvarsSecretKey`, `providerConfigName` and `providerConfigKind`. Anything set explicitly on the XR spec wins; if no EnvironmentConfig matches, XR spec / built-in Workspace defaults apply. The label key is namespaced to this Configuration so it never collides with another Configuration's EnvironmentConfig. See [`examples/environmentconfig.yaml`](examples/environmentconfig.yaml).
- **tfvars from a Secret** — the vSphere credentials live in a Kubernetes Secret (`tfvars.secretName` / EnvironmentConfig `tfvarsSecretName`, default `vsphere-tfvars`), read by the Workspace as a `terraform.tfvars` var file.
- **IP surfaced on the XR** — the Workspace output `ip` is copied to `status.share.ip`.

## Parameters

| Parameter | Source | Default | Description |
|---|---|---|---|
| `vm.name` | XR (required) | - | VM name |
| `vm.count` | XR | `"1"` | Number of VMs |
| `vm.cpu` | XR | `"4"` | vCPUs |
| `vm.ram` | XR | `"4096"` | Memory (MB) |
| `vm.disk` | XR | `"64"` | Disk size (GB) |
| `vm.firmware` | XR | `"bios"` | `bios` or `efi` |
| `vm.bootstrap` | XR | `'["echo STUTTGART-THINGS"]'` | Bootstrap commands (JSON array) |
| `vm.folderPath` | XR or EnvironmentConfig | - | vSphere folder path |
| `vm.datacenter` | XR or EnvironmentConfig | - | vSphere datacenter |
| `vm.datastore` | XR or EnvironmentConfig | - | vSphere datastore |
| `vm.resourcePool` | XR or EnvironmentConfig | - | vSphere resource pool |
| `vm.network` | XR or EnvironmentConfig | - | vSphere network |
| `vm.template` | XR or EnvironmentConfig | - | VM template name |
| `vm.annotation` | XR or EnvironmentConfig | Workspace base value | VM annotation |
| `vm.unverifiedSsl` | XR or EnvironmentConfig | Workspace base value (`"true"`) | Skip SSL verification |
| `tfvars.secretName` | XR or EnvironmentConfig | `vsphere-tfvars` | tfvars Secret name |
| `tfvars.secretKey` | XR or EnvironmentConfig | `terraform.tfvars` | Key in the tfvars Secret |
| `connectionSecret.name` | XR (required) | - | Output connection Secret name |
| `providerRef.name` | XR or EnvironmentConfig | `default` | OpenTofu provider config name |
| `providerRef.kind` | XR or EnvironmentConfig | `ClusterProviderConfig` | `ProviderConfig` or `ClusterProviderConfig` |
| `environmentConfig` | XR | `default` | EnvironmentConfig selector label value |

> Override precedence: **XR spec → EnvironmentConfig → Workspace base value.**

## Usage

### Minimum

See [`examples/xr-min.yaml`](examples/xr-min.yaml) — just `vm.name`, `connectionSecret.name` and `environmentConfig`; all placement and credentials come from the EnvironmentConfig.

### Realistic

See [`examples/xr.yaml`](examples/xr.yaml) — VM sized per spec, infra inherited from the EnvironmentConfig.

### Maximum

See [`examples/xr-max.yaml`](examples/xr-max.yaml) — every field set explicitly, overriding all EnvironmentConfig defaults.

## Cluster preconditions

### 1. provider-opentofu + ClusterProviderConfig

```bash
kubectl apply -f examples/provider.yaml
kubectl apply -f examples/deployment-runtime-config.yaml   # optional: poll/reconcile tuning
kubectl apply -f examples/cluster-provider-config.yaml
```

The `ClusterProviderConfig` name must match `providerRef.name` / EnvironmentConfig `providerConfigName` (default `default`).

### 2. tfvars Secret (vSphere credentials)

Created in the **same namespace as the VsphereVM XR** (typically `default`):

```bash
kubectl create secret generic vsphere-tfvars \
  -n default \
  --from-literal=terraform.tfvars="$(cat <<EOF
vsphere_user = "<your-user>"
vsphere_password = "<your-password>"
vm_ssh_user = "<ssh-user>"
vm_ssh_password = "<ssh-password>"
vsphere_server = "<vcenter-server>"
EOF
)"
```

### 3. RBAC for the provider-opentofu ServiceAccount

The provider ServiceAccount needs permission to read the tfvars Secret. Find the SA name with `kubectl get sa -n crossplane-system | grep opentofu` and substitute below.

```bash
kubectl apply -f - <<EOF
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: provider-opentofu-secrets
rules:
  - apiGroups: [""]
    resources: ["secrets"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: provider-opentofu-secrets
subjects:
  - kind: ServiceAccount
    name: <provider-opentofu-service-account>
    namespace: crossplane-system
roleRef:
  kind: ClusterRole
  name: provider-opentofu-secrets
  apiGroup: rbac.authorization.k8s.io
EOF
```

### 4. EnvironmentConfig (shared defaults)

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
task render
# or non-interactive:
CONFIG=machinery/vsphere-vm XR=xr.yaml task render
```

### Trace resource status

```bash
crossplane beta trace vspherevm.resources.stuttgart-things.com opentofu-test1 -n default
```

## Files

- `crossplane.yaml` — package metadata + dependencies
- `apis/definition.yaml` — XRD (`VsphereVM`, v1alpha1, namespaced)
- `apis/composition.yaml` — Composition pipeline (function-environment-configs + function-patch-and-transform)
- `examples/xr-min.yaml` — only required fields; everything else from the EnvironmentConfig
- `examples/xr.yaml` — realistic single VM
- `examples/xr-max.yaml` — every spec field exercised
- `examples/environmentconfig.yaml` — shared per-environment defaults (LabUL)
- `examples/functions.yaml` — required Crossplane Functions
- `examples/configuration.yaml` — install manifest (OCI ref)
- `examples/provider.yaml` — provider-opentofu install
- `examples/cluster-provider-config.yaml` — OpenTofu ClusterProviderConfig (Terraform K8s backend)
- `examples/deployment-runtime-config.yaml` — optional provider poll/reconcile tuning

## License

Apache-2.0
