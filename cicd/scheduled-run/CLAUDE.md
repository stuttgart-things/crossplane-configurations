# CLAUDE.md — `scheduled-run` Configuration

Context for working on this Crossplane Configuration. Repo-wide conventions
(Crossplane v2, registries, function-name rules, `task` workflows) live in the
**root `CLAUDE.md`** — read that too. This file focuses on `scheduled-run`.

## What it does
Turns a namespaced `ScheduledRun` XR (group `resources.stuttgart-things.com`,
`v1alpha1`) into a Kubernetes `CronJob` that (re)creates an arbitrary target
manifest — usually another XR (`AnsibleRun`, `TofuRun`) — on every tick. The
CronJob, its RBAC, ServiceAccount and a ConfigMap holding the target manifest
are wrapped in `kubernetes.m.crossplane.io/v1alpha1` Objects applied via
provider-kubernetes.

## The core problem this solves
A Crossplane XR is declarative and one-shot: re-applying the same `AnsibleRun`
is a no-op, and even a freshly-*named* one still renders a PipelineRun named
from `spec.pipelineRunName` (identical every tick → collision). So the CronJob
cannot just `kubectl apply` the manifest — it must inject **per-tick
uniqueness**. `spec.uniqueFields` (dot-paths, default `[metadata.name]`) get a
UTC-timestamp suffix in the job before `kubectl create`.

## Inline KCL, not an OCI module
`render-scheduler` renders everything with **inline** `function-kcl`, like
`cluster-backup` and unlike `ansible-run` (which pins `kcl-tekton-pr`).
Deliberate: an external module reintroduces the publish-before-pin coupling
(the tag must exist in ghcr before `verify` passes), and this renders plain
core/batch/rbac objects no other consumer shares.

## Composition pipeline (`apis/composition.yaml`)
1. **`load-environment`** (`function-environment-configs`) — config-scoped
   label `scheduled-run.resources.stuttgart-things.com/environment`, Optional.
   Supplies `schedule`, `image`, `namespace`, `targetNamespace`.
2. **`render-scheduler`** (`function-kcl`, inline) — ServiceAccount, RBAC,
   ConfigMap (target manifest via `yaml.encode`), CronJob. Wraps each in an
   Object. RBAC group is parsed from the manifest's `apiVersion`; namespaced
   scope → Role in `targetNamespace`, cluster scope → ClusterRole named
   `<sa>-run-<xrName>` (XR-name-suffixed to avoid cluster-wide collisions).
3. **`derive-status`** (`function-kcl`, inline) — reads the observed CronJob
   Object and surfaces `cronJobName`, `lastScheduleTime`, `lastSuccessfulTime`,
   `targetKind`.
4. **`ready`** (`function-auto-ready`).

## The CronJob script (inside the render KCL)
Single container, `alpine/k8s` (kubectl + yq + busybox shell). Per tick:
`SUFFIX=$(date -u +%Y%m%d%H%M%S)` → suffix each `uniqueFields` path with yq →
stamp owner label → set/strip `metadata.namespace` by scope → `kubectl create`
→ prune owned instances beyond `keepHistory`.

**Two KCL-string escaping rules — both load-bearing (see root CLAUDE.md gotchas):**
- `$` followed by `{` is **KCL interpolation**. The shell script therefore uses
  bare `$VAR`, never `${VAR}`. Only `$(...)` command substitution and `$((...))`
  arithmetic are safe (no `{`). Regressing a `$VAR` to `${VAR}` makes KCL try to
  interpolate a shell variable and the render fails.
- A literal `\"` in the emitted script is written `\\"` in the KCL triple-quoted
  string (`\\`→`\`, `"`→`"`). Same for `\\n` in `printf` (→ literal `\n`).

**Writable scratch:** the ConfigMap mount at `/target` is read-only and the
script edits the manifest, so there is an `emptyDir` at `/work`; the script
`cp`s the manifest there first. Dropping the emptyDir makes `cd /work` abort
under `set -eu`.

## What it does NOT own
The instances the CronJob creates are imperative (`kubectl create`), so they are
not Crossplane-managed. Deleting the XR leaves them; `keepHistory` bounds them
while running; teardown = suspend/delete + manual sweep by the owner label.

## Local verify (no crossplane CLI needed for the KCL)
Full pipeline needs the crossplane CLI + docker:
```bash
crossplane render examples/xr.yaml apis/composition.yaml examples/functions.yaml
task render   # or CONFIG=cicd/scheduled-run XR=xr.yaml task render
```
To iterate on the inline KCL alone, extract the `render-scheduler` /
`derive-status` `spec.source` into a `.k` file and feed synthetic
`option("params")` via a `-Y settings.yaml` with `kcl_options: [{key: params,
value: {oxr: <the XR>, ctx: {...}, ocds: {...}}}]`, then `kcl run render.k -Y
settings.yaml`. This is how the templating was validated without the CLI.

## Version-pin coupling
None on an external KCL module (inline). The only pins are the `dependsOn`
functions in `crossplane.yaml` and their short-name CRs in
`examples/functions.yaml` (`xpkg.upbound.io`, deliberately — see root CLAUDE.md).
