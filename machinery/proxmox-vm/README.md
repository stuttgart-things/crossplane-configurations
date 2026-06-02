# proxmox-vm — Crossplane Configuration

A Crossplane v2 Configuration that provisions a Proxmox VE virtual machine from a namespaced `ProxmoxVM` XR.

## Overview

The Composition pipeline first loads shared per-environment infrastructure defaults via [`function-environment-configs`](https://github.com/crossplane-contrib/function-environment-configs), then renders an OpenTofu `Workspace` (`opentofu.m.upbound.io/v1beta1`, [provider-opentofu](https://marketplace.upbound.io/providers/upbound/provider-opentofu)) via [`function-patch-and-transform`](https://github.com/crossplane-contrib/function-patch-and-transform). The Workspace runs the Terraform module [`github.com/stuttgart-things/proxmox-vm`](https://github.com/stuttgart-things/proxmox-vm), which creates the VM on Proxmox VE. The VM's IP address is surfaced on `status.share.ip`.

Per-VM settings (name, cpu, ram, disk, count, firmware) come from the XR spec. Shared infrastructure settings (node, datastore, network, folder, template, tfvars Secret, provider config) default from an `EnvironmentConfig` and are overridable per XR.

## Features

- **VM provisioning via OpenTofu** — the `Workspace` runs the pinned Terraform module `git::https://github.com/stuttgart-things/proxmox-vm.git?ref=3.0.2-rc08-20260407` (pin lives in the Composition base).
- **Shared defaults via EnvironmentConfig** — `spec.environmentConfig` (default `default`) selects a Crossplane `EnvironmentConfig` (matched by the config-scoped label `proxmox-vm.resources.stuttgart-things.com/environment`) whose `data` supplies per-environment defaults for `node`, `datastore`, `folderPath`, `network`, `template`, `annotation`, `tfvarsSecretName`, `tfvarsSecretKey`, `providerConfigName` and `providerConfigKind`. Anything set explicitly on the XR spec wins; if no EnvironmentConfig matches, XR spec / built-in Workspace defaults apply. The label key is namespaced to this Configuration so it never collides with another Configuration's EnvironmentConfig. See [`examples/environmentconfig.yaml`](examples/environmentconfig.yaml).
- **tfvars from a Secret** — the Proxmox credentials live in a Kubernetes Secret (`tfvars.secretName` / EnvironmentConfig `tfvarsSecretName`, default `proxmox-tfvars`), read by the Workspace as a `terraform.tfvars` var file.
- **IP surfaced on the XR** — the Workspace output `ip` is copied to `status.share.ip`.

## Parameters

| Parameter | Source | Default | Description |
|---|---|---|---|
| `vm.name` | XR (required) | - | VM name |
| `vm.count` | XR | `"1"` | Number of VMs |
| `vm.cpu` | XR | `"4"` | vCPUs |
| `vm.ram` | XR | `"4096"` | Memory (MB) |
| `vm.disk` | XR | `"32G"` | Disk size |
| `vm.firmware` | XR | `"seabios"` | Firmware type |
| `vm.template` | XR or EnvironmentConfig | - | VM template name |
| `vm.annotation` | XR or EnvironmentConfig | Workspace base value | VM notes/annotation |
| `proxmox.node` | XR or EnvironmentConfig | - | Proxmox cluster node |
| `proxmox.datastore` | XR or EnvironmentConfig | - | Proxmox datastore |
| `proxmox.folderPath` | XR or EnvironmentConfig | - | VM folder path |
| `proxmox.network` | XR or EnvironmentConfig | - | Proxmox network bridge |
| `tfvars.secretName` | XR or EnvironmentConfig | `proxmox-tfvars` | tfvars Secret name |
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

### 2. tfvars Secret (Proxmox credentials)

Created in the **same namespace as the ProxmoxVM XR** (typically `default`):

```bash
kubectl create secret generic proxmox-tfvars \
  -n default \
  --from-literal=terraform.tfvars="$(cat <<EOF
pve_api_url = "<proxmox-api-url>"
pve_api_user = "<user>@<realm>"
pve_api_password = "<password>"
vm_ssh_user = "<ssh-user>"
vm_ssh_password = "<ssh-password>"
EOF
)"
```

#### Or: source it from Vault via External Secrets Operator

If the cluster runs [ESO](https://external-secrets.io/) and the Proxmox creds live in Vault, use [`examples/external-secret.yaml`](examples/external-secret.yaml) instead of `kubectl create secret`. It defines a `ClusterSecretStore` (Vault KV v2, Kubernetes auth) and an `ExternalSecret` that templates the `terraform.tfvars` key from the Vault keys into `proxmox-tfvars`. Adjust the Vault address, KV mount/path, auth role and CA bundle for your environment (see the comments in the file). The Vault role must bind the ESO ServiceAccount and grant read on the KV path; the listener CA is fetched with `curl -sk "$VAULT_ADDR/v1/pki/ca/pem" | base64 -w0`.

```bash
kubectl apply -f examples/external-secret.yaml
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
CONFIG=machinery/proxmox-vm XR=xr.yaml task render
```

### Trace resource status

```bash
crossplane beta trace proxmoxvm.resources.stuttgart-things.com opentofu-test1 -n default
```

## Files

- `crossplane.yaml` — package metadata + dependencies
- `apis/definition.yaml` — XRD (`ProxmoxVM`, v1alpha1, namespaced)
- `apis/composition.yaml` — Composition pipeline (function-environment-configs + function-patch-and-transform)
- `examples/xr-min.yaml` — only required fields; everything else from the EnvironmentConfig
- `examples/xr.yaml` — realistic single VM
- `examples/xr-max.yaml` — every spec field exercised
- `examples/environmentconfig.yaml` — shared per-environment defaults (LabUL)
- `examples/functions.yaml` — required Crossplane Functions
- `examples/configuration.yaml` — install manifest (OCI ref)
- `examples/provider.yaml` — provider-opentofu install
- `examples/external-secret.yaml` — optional ESO `ClusterSecretStore` + `ExternalSecret` sourcing `proxmox-tfvars` from Vault
- `examples/cluster-provider-config.yaml` — OpenTofu ClusterProviderConfig (Terraform K8s backend)
- `examples/deployment-runtime-config.yaml` — optional provider poll/reconcile tuning

## License

Apache-2.0
