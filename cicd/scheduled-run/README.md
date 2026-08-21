# Scheduled Run - Crossplane Composition

A Crossplane v2 Configuration that runs an **arbitrary Kubernetes object on a cron schedule** by composing a `CronJob` (via [`provider-kubernetes`](https://github.com/crossplane-contrib/provider-kubernetes)) from a namespaced `ScheduledRun` XR. The scheduled object is typically another XR — an [`AnsibleRun`](../ansible-run/), a `TofuRun`, etc. — but any manifest works.

## Why this exists

A Crossplane XR is **declarative and one-shot**. An `AnsibleRun` renders a Tekton `PipelineRun` and then stops; re-applying the same XR does nothing, because the object already exists and matches. Crossplane has no built-in notion of "run this every night". `ScheduledRun` is that missing piece, kept **generic** — it schedules any manifest, not one hard-wired kind.

## The one hard part: making a declarative XR re-run

Re-creating the same `AnsibleRun` is a no-op. Even a freshly-*named* `AnsibleRun` still renders a `PipelineRun` whose name is `spec.pipelineRunName` — identical every tick, so the wrapped Object collides.

`spec.uniqueFields` names the dot-paths inside `spec.manifest` that get a **per-tick UTC-timestamp suffix** before the object is created:

```yaml
uniqueFields:
  - metadata.name
  - spec.pipelineRunName
  - spec.crossplaneObjectName
```

Each tick then produces a genuinely new run. A field whose value is absent is skipped, so a manifest that uses `metadata.generateName` instead of a fixed name can leave `uniqueFields` empty and let the API server generate the suffix.

## Shape

`ScheduledRun` composes, through `provider-kubernetes`:

- a **ServiceAccount** (in `spec.namespace`),
- a **Role/RoleBinding** (or **ClusterRole/ClusterRoleBinding** for `targetScope: Cluster`) scoped to the target's **own API group only** — `create, get, list, delete`,
- a **ConfigMap** holding `spec.manifest`,
- a **CronJob** (`spec.schedule`) whose single container (kubectl + yq, `alpine/k8s`) on each tick:
  1. suffixes every `uniqueFields` path with the tick timestamp,
  2. stamps an owner label (`scheduled-run.resources.stuttgart-things.com/owner=<xr-name>`),
  3. sets `metadata.namespace` to `targetNamespace` (namespaced scope) or strips it (cluster scope),
  4. `kubectl create`s the manifest,
  5. prunes older owned instances down to `spec.keepHistory`.

The CronJob's live `lastScheduleTime` / `lastSuccessfulTime` are surfaced onto the XR status — the latter, not XR readiness, is what tells you a tick has actually fired.

## Features

- **Schedules any manifest** — `spec.manifest` is opaque to Crossplane. Point it at an `AnsibleRun`, a `TofuRun`, a plain `Job`, anything.
- **Per-tick uniqueness** — `spec.uniqueFields` (default `[metadata.name]`) makes a declarative XR actually re-run rather than no-op re-apply.
- **Least-privilege RBAC, auto-derived** — the generated Role/ClusterRole is scoped to the target manifest's API group (parsed from its `apiVersion`), not `*`.
- **Bounded history** — `spec.keepHistory` (default 3) deletes older owned instances after each create; `0` disables pruning.
- **Namespaced or cluster-scoped targets** — `spec.targetScope` toggles a namespaced Role vs a ClusterRole and whether `metadata.namespace` is set or stripped.
- **Shared defaults via EnvironmentConfig** — `spec.environmentConfig` (default `default`) selects an `EnvironmentConfig` (config-scoped label `scheduled-run.resources.stuttgart-things.com/environment`) supplying `schedule`, `image`, `namespace`, `targetNamespace`. XR spec always wins.
- **Standard CronJob knobs** — `concurrencyPolicy` (default `Forbid`), `suspend`, `successfulJobsHistoryLimit`, `failedJobsHistoryLimit`.

## Usage

### Minimum

```yaml
apiVersion: resources.stuttgart-things.com/v1alpha1
kind: ScheduledRun
metadata:
  name: scheduled-run-min
  namespace: default
spec:
  schedule: "0 2 * * *"
  manifest:
    apiVersion: resources.stuttgart-things.com/v1alpha1
    kind: AnsibleRun
    metadata:
      name: nightly-baseos
    spec:
      pipelineRunName: nightly-baseos
      ansiblePlaybooks:
        - sthings.baseos.setup
      crossplaneObjectName: nightly-baseos
      crossplaneProviderConfig: in-cluster
```

### Realistic

See [`examples/xr.yaml`](examples/xr.yaml) — schedules an `AnsibleRun` every night with the three unique fields set and `keepHistory: 5`.

### Maximum

See [`examples/xr-max.yaml`](examples/xr-max.yaml) — every spec field set, scheduling a plain `Job`.

## What it does NOT own

The instances the CronJob creates are made **imperatively** (`kubectl create`), so they are **not** Crossplane-managed. Deleting the `ScheduledRun` removes the CronJob, ConfigMap, ServiceAccount and RBAC, but **leaves already-created instances** behind. `keepHistory` bounds them while the schedule runs; a clean teardown is `spec.suspend: true` (or delete the XR) followed by a manual sweep of the owner-labelled instances:

```bash
kubectl delete ansiblerun.resources.stuttgart-things.com \
  -n tekton-ci -l scheduled-run.resources.stuttgart-things.com/owner=nightly-baseos
```

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

### 2. Namespaces

`spec.namespace` (CronJob + ServiceAccount, default `crossplane-system`) and `spec.targetNamespace` (where the manifest is created) must already exist — use the [`namespace`](../../k8s/namespace/) Configuration or an existing one.

## Development

### Render the Composition

```bash
crossplane render examples/xr.yaml apis/composition.yaml examples/functions.yaml --include-function-results
```

Or via the repo Taskfile:

```bash
task render
# or non-interactive:
CONFIG=cicd/scheduled-run XR=xr.yaml task render
```

### Trace resource status

> Needs the Crossplane CLI **v2.3.0 or newer**. Before the beta subcommands were
> promoted this was `crossplane beta trace`; that spelling no longer exists.
> The `sthings.baseos` binaries role ships v2.4.1.

```bash
crossplane resource trace scheduledrun.resources.stuttgart-things.com nightly-baseos -n default
```

## Files

- `crossplane.yaml` — package metadata + dependencies
- `apis/definition.yaml` — XRD (`ScheduledRun`, v1alpha1, namespaced)
- `apis/composition.yaml` — Composition pipeline (inline `function-kcl` + `function-auto-ready`)
- `examples/xr-min.yaml` — only XRD-required fields (manifest + schedule)
- `examples/xr.yaml` — realistic nightly `AnsibleRun` with unique fields
- `examples/xr-max.yaml` — every spec field exercised (plain `Job` target)
- `examples/functions.yaml` — required Crossplane Functions
- `examples/environmentconfig.yaml` — shared per-environment defaults
- `examples/configuration.yaml` — install manifest (OCI ref)
