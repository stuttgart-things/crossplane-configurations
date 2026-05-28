# Cloud Config - Crossplane Composition

A Crossplane composition that automates the creation of cloud-init configurations for virtual machines. This configuration transforms a `CloudInit` custom resource into Kubernetes secrets containing properly formatted cloud-init userdata.

## Overview

This composition provides a declarative way to define VM cloud configurations using Crossplane. It leverages the `function-go-templating` function to render cloud-init YAML from structured XRD specifications.

## Features

- **User Management**: Define users, SSH keys, groups, and sudo privileges
- **Package Management**: Specify packages to install and manage updates/upgrades
- **File Management**: Write custom files with specific permissions and ownership
- **Network Configuration**: Optional network configuration support
- **Boot Commands**: Execute commands during boot or runtime
- **SSH Configuration**: Flexible SSH password authentication and root access control

## Usage

### Create a CloudInit namespaced XR

```yaml
apiVersion: resources.stuttgart-things.com/v1alpha1
kind: CloudInit
metadata:
  name: dev2-vm
  namespace: default
spec:
  providerConfigRef: in-cluster
  vmName: dev2
  namespace: default
  hostname: dev2
  domain: example.com
  timezone: UTC
  users:
    - name: ubuntu
      sshAuthorizedKeys:
        - ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAAB...
      sudo: "ALL=(ALL) NOPASSWD:ALL"
      groups: sudo,docker
      shell: /bin/bash
      lockPasswd: true
  packages:
    - curl
    - wget
    - git
  runcmd:
    - systemctl enable docker
  packageUpdate: true
```

The composition manages two resources via `provider-kubernetes`:

1. The **target namespace** (`spec.namespace`, default `vms`) — created with `managementPolicies: [Observe, Create]`, so a pre-existing namespace is adopted unchanged and is never modified or deleted by Crossplane.
2. A **Kubernetes Secret** in that namespace containing the rendered cloud-init userdata (and optional networkdata).

## Cluster preconditions

The Configuration assumes the following are already present on the target cluster:

- A `ClusterProviderConfig` for `provider-kubernetes` whose name matches `spec.providerConfigRef` on the XR (the examples reference `in-cluster`).

## Development

### Render the Composition

Test the template rendering without applying to the cluster:

```bash
crossplane render examples/xr.yaml apis/composition.yaml examples/functions.yaml --include-function-results
```

### Trace Resource Status

Monitor the composition status and track created resources:

```bash
crossplane beta trace cloudinit.resources.stuttgart-things.com dev2-vm -n default
```

### View Resource Tree

Display the hierarchical relationship of created resources:

```bash
kubectl tree CloudInit dev2-vm
```

## Files

- `apis/composition.yaml` - Crossplane Composition definition
- `examples/xr.yaml` - Example CloudInit namespaced XR
- `examples/functions.yaml` - Function definitions (if applicable)
