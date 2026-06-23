# CLAUDE.md — `packer-build` Configuration

Context for working on this Crossplane Configuration. Repo-wide conventions
(Crossplane v2, registries, function-name rules, `task` workflows) live in the
**root `CLAUDE.md`** — read that too. This file focuses on `packer-build`.

## What it does
Turns a namespaced `PackerBuild` XR (group `resources.stuttgart-things.com`,
`v1alpha1`) into a Tekton `PipelineRun` (`build-packer-template`) that runs
Packer on a target cluster to build a VM template / golden image. The
PipelineRun is rendered by the external KCL module
`oci://ghcr.io/stuttgart-things/kcl-tekton-pr-packer` and wrapped in a
`kubernetes.m.crossplane.io/v1alpha1` Object applied via provider-kubernetes.

Packer sibling of [`cicd/ansible-run`](../ansible-run); the Composition is a
near-clone (load-environment → render-pipelinerun → derive-status → ready).

## Key differences vs ansible-run
- **Two repos, separated**: `pipelineRepoUrl`/`pipelineRevision`/`pipelinePath`
  (pipeline def, in stage-time) vs `gitRepoUrl`/`gitRevision`/
  `gitWorkspaceSubdirectory` (template source, in stuttgart-things/
  stuttgart-things). `osVersion`+`lab`+`provisioning` compose the subdirectory
  (`packer/builds/<os>-<lab>-vsphere-<prov>`) when it isn't explicit.
- **`templateName` status**: the `build-packer-template` pipeline declares a
  `template-name` result (wired from the execute-packer task). `derive-status`
  reads `…manifest.status.results[name==template-name].value` and surfaces it.
  This is the contract a downstream promote/rename step depends on.
- **Credentials**: `packer-credentials` (builder creds) + `vault` (approle/
  token), not ansible-credentials.

## The `derive-status` step
Same function-kcl contract as ansible-run: scan `option("params").ocds` for the
entry whose `.Resource.kind == "Object"`, read the live PipelineRun at
`.Resource.status.atProvider.manifest`, set XR status by mutating the desired
XR (`dxr`) and returning `items = [ dxr | {status = …} ]`.

## Readiness
`spec.deriveReadiness` (default `true`) → the KCL module adds
`spec.readiness.policy: DeriveFromCelQuery` on the Object so it is Ready only
once the PipelineRun's `Succeeded` condition is True; `function-auto-ready`
propagates that to the XR.

## Version-pin coupling (read before bumping)
`render-pipelinerun` pins `kcl-tekton-pr-packer:<version>`. That tag must exist
in ghcr (`kcl mod push` from `stage-time/kcl/packer`) BEFORE `verify` can pass.
Order: publish module → bump the pin here.

## Local verify
```bash
crossplane render examples/xr.yaml apis/composition.yaml examples/functions.yaml \
  --extra-resources examples/environmentconfig.yaml
```
