# Volume Claim - Crossplane Composition

A Crossplane v2 Configuration that provisions a Kubernetes `PersistentVolumeClaim` on a target cluster from a namespaced `VolumeClaim` XR.

## Overview

This composition wraps a PVC as a single resource managed via `provider-kubernetes` (namespaced `kubernetes.m.crossplane.io/v1alpha1` Object). The XR exposes the common PVC fields (`storageClassName`, `storage`, `accessModes`, `volumeMode`, `labels`, `annotations`) plus the less-used but useful ones (`selector` for binding to pre-provisioned PVs, `dataSource` for snapshot restore / PVC cloning).

## Features

- **Default StorageClass fallback**: leave `storageClassName` unset and the field is omitted from the PVC; Kubernetes uses the cluster's default StorageClass.
- **Sized + access-controlled**: `storage` (default `10Gi`), `accessModes` (default `[ReadWriteOnce]`), `volumeMode` (default `Filesystem`).
- **Labels + annotations** as freeform maps on the PVC.
- **`selector`** with `matchLabels` and `matchExpressions` for binding to pre-provisioned PVs.
- **`dataSource`** for VolumeSnapshot restore or PVC cloning.

## Usage

### Minimum

```yaml
apiVersion: resources.stuttgart-things.com/v1alpha1
kind: VolumeClaim
metadata:
  name: min
  namespace: default
spec:
  providerConfigRef: in-cluster
  pvcName: demo-min
```

Creates a 10Gi `ReadWriteOnce` PVC named `demo-min` in `default`, bound by the cluster's default StorageClass.

### Realistic

```yaml
apiVersion: resources.stuttgart-things.com/v1alpha1
kind: VolumeClaim
metadata:
  name: app-data
  namespace: default
spec:
  providerConfigRef: in-cluster
  pvcName: app-data
  storageClassName: standard
  storage: 20Gi
  labels:
    app: my-app
    tier: data
```

### Snapshot restore + pre-provisioned PV selector

See [`examples/xr-max.yaml`](examples/xr-max.yaml) — `dataSource` referencing a `VolumeSnapshot` plus `selector.matchLabels`/`matchExpressions` for binding to a specific PV.

## Cluster preconditions

The Configuration assumes the following are already present on the target cluster:

- A `ClusterProviderConfig` for `provider-kubernetes` whose name matches `spec.providerConfigRef` on the XR (the examples reference `in-cluster`).
- A StorageClass (the cluster default, or whichever name is passed via `spec.storageClassName`).

## Development

### Render the Composition

```bash
crossplane render examples/xr.yaml apis/composition.yaml examples/functions.yaml --include-function-results
```

Or via the repo Taskfile:

```bash
task render
# or non-interactive:
CONFIG=k8s/volume-claim XR=xr.yaml task render
```

### Trace resource status

```bash
crossplane beta trace volumeclaim.resources.stuttgart-things.com app-data -n default
```

## Files

- `crossplane.yaml` — package metadata + dependencies
- `apis/definition.yaml` — XRD (`VolumeClaim`, v1alpha1, namespaced)
- `apis/composition.yaml` — Composition pipeline (go-templating + auto-ready)
- `examples/xr-min.yaml` — only XRD-required fields; exercises default-SC fallback
- `examples/xr.yaml` — realistic named-SC example
- `examples/xr-max.yaml` — every spec field exercised (selector + dataSource)
- `examples/functions.yaml` — required Crossplane Functions
- `examples/configuration.yaml` — install manifest (OCI ref)
