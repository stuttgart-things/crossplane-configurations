# CLAUDE.md — `ansible-run` Configuration

Context for working on this Crossplane Configuration. Repo-wide conventions
(Crossplane v2, registries, function-name rules, `task` workflows) live in
the **root `CLAUDE.md`** — read that too. This file focuses on `ansible-run`.

## What it does
Turns a namespaced `AnsibleRun` XR (group `resources.stuttgart-things.com`,
`v1alpha1`) into a Tekton `PipelineRun` on a target cluster. The PipelineRun
is rendered by the external KCL module
`oci://ghcr.io/stuttgart-things/kcl-tekton-pr` and wrapped in a
`kubernetes.m.crossplane.io/v1alpha1` Object applied via provider-kubernetes.

## Composition pipeline (`apis/composition.yaml`)
1. **`load-environment`** (`function-environment-configs`) — selects an
   `EnvironmentConfig` by label `resources.stuttgart-things.com/environment`
   (value from `spec.environmentConfig`, `fromFieldPathPolicy: Optional`) and
   merges its `data` into the pipeline context. Shared per-environment
   defaults; XR spec always wins.
2. **`render-pipelinerun`** (`function-kcl`, OCI `kcl-tekton-pr:<pin>`) —
   renders the PipelineRun + wrapping Object.
3. **`derive-status`** (`function-kcl`, **inline** KCL) — reads the observed
   Object and patches the XR status (see below).
4. **`ready`** (`function-auto-ready`) — bubbles Object readiness to the XR.

## The `derive-status` step (the inline KCL)
`function-kcl` contract used here:
- `option("params").ocds` — observed composed resources, a map keyed by
  composition-resource-name; each entry has `.Resource` (the live object).
  We scan it for the entry whose `.Resource.kind == "Object"`.
- The wrapped Object mirrors the live PipelineRun at
  **`.Resource.status.atProvider.manifest`** — so the PipelineRun status is
  `…manifest.status` (`.conditions[type==Succeeded]`, `.startTime`,
  `.completionTime`, `.childReferences`).
- Set XR status by mutating the **desired XR**: `dxr = option("params").dxr`,
  then return `items = [ dxr | {status = (dxr.status or {}) | patch} ]`.
  function-kcl recognises it as the composite by apiVersion+kind and merges
  the status. Returning only `dxr` is safe — function-kcl carries forward the
  desired Object from the previous step.

XRD status fields populated (`apis/definition.yaml` → `status`):
`installed`, `pipelineRunName`, `succeeded` (True/False/Unknown), `reason`,
`message`, `startTime`, `completionTime`, `taskRuns[]` ({name, pipelineTaskName}).

## Readiness
`spec.deriveReadiness` (default `true`) → the KCL module adds
`spec.readiness.policy: DeriveFromCelQuery` on the Object so it is Ready only
once the PipelineRun's `Succeeded` condition is True; `function-auto-ready`
then propagates that to the XR.

## Local verify
```bash
# Full pipeline (needs Docker + crossplane CLI; pulls the real functions
# and the pinned kcl-tekton-pr OCI module — the pin MUST be published):
crossplane render examples/xr.yaml apis/composition.yaml examples/functions.yaml

# Or via the repo task runner:
task render   # then pick this configuration
```
To iterate on the inline `derive-status` KCL without re-rendering the whole
pipeline, copy it into a `.k` file and feed a synthetic `-D params='{...}'`
containing an `ocds` map with a fake Object `status.atProvider.manifest`.

## Version-pin coupling (read before bumping)
`render-pipelinerun` pins `kcl-tekton-pr:<version>`. That tag must exist in
ghcr (`kcl mod push` from `stage-time/kcl/ansible`) **before** `verify` can
pass — otherwise it fails with
`failed to resolve <v>: …kcl-tekton-pr:<v>: not found`. Order: publish module
→ bump the pin here.

## Pre-commit gotcha
`detect-secrets` scans changed files. The README contains an example
`ansibleCredentialsSecretName: ansible-credentials` (a Secret *name*, not a
credential) recorded in `.secrets.baseline`. If you edit files and the hook
re-flags or shifts a line, run `detect-secrets scan --baseline .secrets.baseline`
(pin `detect-secrets==1.5.0` to match the hook) and `git add .secrets.baseline`.
