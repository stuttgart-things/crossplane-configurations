# Namespace - Crossplane Composition

A Crossplane v2 Configuration that manages a Kubernetes Namespace on a target cluster from a namespaced `ManagedNamespace` XR, with optional governance addons (ResourceQuota, LimitRange, default-deny NetworkPolicy, RoleBindings).

## Overview

This composition turns a single `ManagedNamespace` XR into a fully-managed Namespace plus opt-in policy resources. It uses `function-go-templating` to conditionally render only the addons declared on the XR, so a minimum XR creates a bare Namespace and nothing else.

The Configuration **owns** the Namespace (`managementPolicies: ['*']`): labels, annotations and addons on the XR are authoritative, and deleting the XR deletes the Namespace. If you need adopt-only semantics for a pre-existing Namespace, use a different Configuration — or compose this one alongside an Observe-only Object.

## Features

- **Namespace metadata**: arbitrary labels and annotations as maps
- **ResourceQuota**: free-form `hard` quota map (any corev1 quota key)
- **LimitRange**: mirrors corev1 `LimitRangeSpec.limits` (default / defaultRequest / max / min / maxLimitRequestRatio per `type`)
- **Default-deny NetworkPolicy**: one-flag baseline (`networkPolicy.denyAll: true`) — Ingress + Egress
- **RoleBindings**: bind any ClusterRole/Role to any combination of User / Group / ServiceAccount subjects

## Usage

### Minimum

```yaml
apiVersion: resources.stuttgart-things.com/v1alpha1
kind: ManagedNamespace
metadata:
  name: min
  namespace: default
spec:
  providerConfigRef: in-cluster
  name: demo-min
```

Creates a bare `demo-min` Namespace on the target cluster.

### Realistic team namespace

```yaml
apiVersion: resources.stuttgart-things.com/v1alpha1
kind: ManagedNamespace
metadata:
  name: team-alpha
  namespace: default
spec:
  providerConfigRef: in-cluster
  name: team-alpha
  labels:
    team: alpha
    env: dev
  annotations:
    owner: alpha-team@stuttgart-things.com
  roleBindings:
    - name: team-alpha-view
      roleRef:
        kind: ClusterRole
        name: view
      subjects:
        - kind: Group
          name: team-alpha
          apiGroup: rbac.authorization.k8s.io
```

### Full governance baseline

See [`examples/xr-max.yaml`](examples/xr-max.yaml) — quota, limit-range, deny-all NP and multiple RoleBindings on a single XR.

## Cluster preconditions

The Configuration assumes the following are already present on the target cluster:

- A `ClusterProviderConfig` for `provider-kubernetes` whose name matches `spec.providerConfigRef` on the XR (the examples reference `in-cluster`).

## Development

### Render the Composition

Test the template rendering without applying to the cluster:

```bash
crossplane render examples/xr.yaml apis/composition.yaml examples/functions.yaml --include-function-results
```

Or via the repo Taskfile (auto-discovers Configurations and example XRs):

```bash
task render
```

### Trace resource status

```bash
crossplane beta trace managednamespace.resources.stuttgart-things.com team-alpha -n default
```

### View resource tree

```bash
kubectl tree ManagedNamespace team-alpha
```

## Files

- `crossplane.yaml` — package metadata + dependencies
- `apis/definition.yaml` — XRD (`ManagedNamespace`, v1alpha1, namespaced)
- `apis/composition.yaml` — Composition pipeline (go-templating + auto-ready)
- `examples/xr-min.yaml` — only XRD-required fields
- `examples/xr.yaml` — realistic team-namespace example
- `examples/xr-max.yaml` — every spec field exercised
- `examples/functions.yaml` — required Crossplane Functions
- `examples/configuration.yaml` — install manifest (OCI ref)

<!-- ci: smoke-test the push-preview preview job (#289) -->
