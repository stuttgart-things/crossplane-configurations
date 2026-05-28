# Ansible Run - Crossplane Composition

A Crossplane v2 Configuration that triggers an Ansible playbook on a target cluster by emitting a [Tekton](https://tekton.dev/) `PipelineRun` from a namespaced `AnsibleRun` XR.

## Overview

The Composition pipeline renders a Tekton `PipelineRun` via [`function-kcl`](https://github.com/crossplane-contrib/function-kcl) using the KCL module [`oci://ghcr.io/stuttgart-things/kcl-tekton-pr`](https://github.com/stuttgart-things/kcl-tekton-pr). The KCL module wraps the `PipelineRun` in a namespaced `kubernetes.m.crossplane.io/v1alpha1` Object, so `provider-kubernetes` applies it on the cluster identified by `spec.crossplaneProviderConfig`. `function-auto-ready` then bubbles the Object's readiness up to the XR.

The actual playbook execution lives in the upstream Tekton pipeline (`spec.gitRepoUrl` + `spec.gitPath`, defaults to [`stage-time`](https://github.com/stuttgart-things/stage-time) → `pipelines/execute-ansible-playbooks-from-collections.yaml`); this Configuration just owns the wrapper.

## Features

- **PipelineRun rendering via KCL** — the [`kcl-tekton-pr`](https://github.com/stuttgart-things/kcl-tekton-pr) module is pinned in the Composition (`oci://ghcr.io/stuttgart-things/kcl-tekton-pr:0.4.6`) and consumes the XR spec verbatim.
- **Playbooks + extra roles/collections** — `ansiblePlaybooks`, `ansibleExtraRoles`, `ansibleExtraCollections` are passed straight through to the pipeline.
- **Per-host vars + inventory** — `ansibleVarsFile` and `ansibleVarsInventory` use the upstream pipeline's `key+-value` / `host+[...]` syntax.
- **Credentials from a Secret** — `ansibleCredentialsSecretName` / `…UserKey` / `…PasswordKey` reference a Secret pre-created in `spec.namespace`.
- **Target-cluster routing** — `spec.crossplaneProviderConfig` selects the `ClusterProviderConfig` the wrapped Object is applied through; `spec.targetCluster.scope` toggles Namespaced vs Cluster.
- **Workspace StorageClass override** — `spec.storageClass` sets the PVC StorageClass for the PipelineRun workspace. Unset → KCL module default (`openebs-hostpath`). Override on clusters whose default SC differs (e.g. `local-path`, `longhorn`).

## Usage

### Minimum

```yaml
apiVersion: resources.stuttgart-things.com/v1alpha1
kind: AnsibleRun
metadata:
  name: ansible-run-min
  namespace: default
spec:
  pipelineRunName: run-ansible-min
  namespace: tekton-ci
  gitRepoUrl: https://github.com/stuttgart-things/stage-time.git
  ansiblePlaybooks:
    - sthings.baseos.setup
  crossplaneObjectName: run-ansible-min
  crossplaneProviderConfig: in-cluster
```

Falls back to XRD defaults for storage, working image, credentials Secret name, and inventory toggles.

### Realistic

See [`examples/xr.yaml`](examples/xr.yaml) — adds extra roles/collections, a vars file, and an inventory mapping `all` to a single host.

### Maximum

See [`examples/xr-max.yaml`](examples/xr-max.yaml) — every XRD spec field set, including multiple playbooks, multi-host inventory, and an explicit `targetCluster` block.

## Cluster preconditions

The Configuration assumes the following are present on the target cluster:

### 1. ClusterProviderConfig for provider-kubernetes

```bash
kubectl apply -f - <<EOF
---
apiVersion: kubernetes.m.crossplane.io/v1alpha1
kind: ClusterProviderConfig
metadata:
  name: in-cluster
spec:
  credentials:
    source: InjectedIdentity
EOF
```

The name must match `spec.crossplaneProviderConfig` on the XR.

### 2. Pipeline namespace + credentials Secret

```bash
kubectl create namespace tekton-ci

kubectl apply -f - <<EOF
---
apiVersion: v1
kind: Secret
metadata:
  name: ansible-credentials   # pragma: allowlist secret
  namespace: tekton-ci
type: Opaque
stringData:
  ANSIBLE_USER: sthings        # pragma: allowlist secret
  ANSIBLE_PASSWORD: ""         # pragma: allowlist secret
EOF
```

### 3. RBAC for the provider-kubernetes ServiceAccount

The `provider-kubernetes` ServiceAccount needs permissions to manage Tekton resources and the supporting Kubernetes objects in the pipeline namespace. Find the correct ServiceAccount name with `kubectl get sa -n crossplane-system | grep provider-kubernetes` and substitute it below.

```bash
kubectl apply -f - <<EOF
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: provider-kubernetes-tekton
rules:
  - apiGroups: ["tekton.dev"]
    resources: ["pipelineruns", "pipelines", "tasks", "taskruns"]
    verbs: ["*"]
  - apiGroups: [""]
    resources: ["persistentvolumeclaims", "secrets", "serviceaccounts"]
    verbs: ["*"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: provider-kubernetes-tekton
subjects:
  - kind: ServiceAccount
    name: <provider-kubernetes-service-account>
    namespace: crossplane-system
roleRef:
  kind: ClusterRole
  name: provider-kubernetes-tekton
  apiGroup: rbac.authorization.k8s.io
EOF
```

## Development

### Render the Composition

```bash
crossplane render examples/xr.yaml apis/composition.yaml examples/functions.yaml --include-function-results
```

Or via the repo Taskfile:

```bash
task render
# or non-interactive:
CONFIG=cicd/ansible-run XR=xr.yaml task render
```

### Render the KCL module directly

```bash
kcl run oci://ghcr.io/stuttgart-things/kcl-tekton-pr --tag 0.4.6 -D params='{
  "oxr": {
    "spec": {
      "pipelineRunName": "run-ansible-test",
      "namespace": "tekton-ci",
      "ansibleCredentialsSecretName": "ansible-credentials",
      "ansiblePlaybooks": ["sthings.baseos.setup"],
      "ansibleVarsFile": [
        "manage_filesystem+-true",
        "update_packages+-true",
        "ansible_become+-true",
        "ansible_become_method+-sudo"
      ],
      "ansibleVarsInventory": ["all+[\"10.31.102.107\"]"],
      "wrapInCrossplane": true,
      "crossplaneObjectName": "run-ansible-test",
      "crossplaneNamespace": "default",
      "crossplaneProviderConfig": "in-cluster"
    }
  }
}' --format yaml
```

### Trace resource status

```bash
crossplane beta trace ansiblerun.resources.stuttgart-things.com ansible-run-baseos -n default
```

## Files

- `crossplane.yaml` — package metadata + dependencies
- `apis/definition.yaml` — XRD (`AnsibleRun`, v1alpha1, namespaced)
- `apis/composition.yaml` — Composition pipeline (function-kcl + function-auto-ready)
- `examples/xr-min.yaml` — only XRD-required fields + ClusterProviderConfig target
- `examples/xr.yaml` — realistic baseos playbook with vars + inventory
- `examples/xr-max.yaml` — every spec field exercised (multi-playbook, multi-host)
- `examples/functions.yaml` — required Crossplane Functions
- `examples/configuration.yaml` — install manifest (OCI ref)
