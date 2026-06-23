# packer-build

A Crossplane Configuration that turns a namespaced `PackerBuild` XR
(group `resources.stuttgart-things.com`, `v1alpha1`) into a Tekton
`PipelineRun` that runs Packer (`build-packer-template`) on a target cluster
to build a VM template / golden image.

It is the Packer sibling of [`cicd/ansible-run`](../ansible-run). The
PipelineRun is rendered by the external KCL module
`oci://ghcr.io/stuttgart-things/kcl-tekton-pr-packer` and wrapped in a
`kubernetes.m.crossplane.io/v1alpha1` Object applied via provider-kubernetes.

## Composition pipeline (`apis/composition.yaml`)

1. **`load-environment`** (`function-environment-configs`) — selects an
   `EnvironmentConfig` by the config-scoped label
   `packer-build.resources.stuttgart-things.com/environment` (value from
   `spec.environmentConfig`, `fromFieldPathPolicy: Optional`) and merges its
   `data` into the pipeline context. Shared per-environment defaults; XR spec
   always wins.
2. **`render-pipelinerun`** (`function-kcl`, OCI `kcl-tekton-pr-packer:<pin>`) —
   renders the PipelineRun + wrapping Object.
3. **`derive-status`** (`function-kcl`, inline KCL) — reads the observed Object
   and patches the XR status, including the built `templateName` (read from the
   PipelineRun's `template-name` result).
4. **`ready`** (`function-auto-ready`) — bubbles Object readiness to the XR.

## Two repos, on purpose

Packer keeps the **pipeline definition** and the **template source** as
separate refs (the ansible Configuration conflates them):

- `pipelineRepoUrl` / `pipelineRevision` / `pipelinePath` — where
  `build-packer-template.yaml` lives (`stage-time`).
- `gitRepoUrl` / `gitRevision` / `gitWorkspaceSubdirectory` — where the
  `*.pkr.hcl` templates live (`stuttgart-things/stuttgart-things`, under
  `packer/builds/<os>-<lab>-vsphere-<provisioning>/`).

`osVersion` + `lab` + `provisioning` compose `gitWorkspaceSubdirectory`
automatically when it isn't set explicitly.

## Cluster preconditions

On the target cluster (referenced by `spec.crossplaneProviderConfig`):

- A `ClusterProviderConfig` for `provider-kubernetes` whose name matches
  `spec.crossplaneProviderConfig`.
- The pipeline namespace (`spec.namespace`, default `tekton-ci`) with:
  - a `packer-credentials` Secret (builder credentials, e.g.
    `PKR_VAR_vsphere_password`), and
  - a `vault` Secret (Vault `VAULT_ADDR`/`VAULT_ROLE_ID`/`VAULT_SECRET_ID`/
    `VAULT_TOKEN`).
- RBAC permitting the provider-kubernetes service account to manage
  `tekton.dev` resources (PipelineRun/Pipeline/Task) plus PVCs, Secrets and
  ServiceAccounts in the pipeline namespace.

```bash
kubectl create secret generic packer-credentials \
  --from-literal=PKR_VAR_vsphere_password=<PASSWORD> \
  -n tekton-ci
```

## Status

`apis/definition.yaml` → `status`: `installed`, `pipelineRunName`,
`templateName`, `succeeded` (True/False/Unknown), `reason`, `message`,
`startTime`, `completionTime`, `taskRuns[]` ({name, pipelineTaskName}).

## Local verify

```bash
# Full pipeline (needs Docker + crossplane CLI; pulls the real functions and
# the pinned kcl-tekton-pr-packer OCI module — the pin MUST be published):
crossplane render examples/xr.yaml apis/composition.yaml examples/functions.yaml \
  --extra-resources examples/environmentconfig.yaml

# Or via the repo task runner:
task render   # then pick this configuration
```

## Version-pin coupling (read before bumping)

`render-pipelinerun` pins `kcl-tekton-pr-packer:<version>`. That tag must exist
in ghcr (`kcl mod push` from `stage-time/kcl/packer`) **before** `verify` can
pass — otherwise it fails with
`failed to resolve <v>: …kcl-tekton-pr-packer:<v>: not found`. Order: publish
module → bump the pin here.
