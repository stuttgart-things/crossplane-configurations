# CLAUDE.md — `packer-build` Configuration

Context for working on this Crossplane Configuration. Repo-wide conventions
(Crossplane v2, registries, function-name rules, `task` workflows) live in
the **root `CLAUDE.md`** — read that too. This file focuses on `packer-build`.

## What it does
Turns a namespaced `PackerBuild` XR (group `resources.stuttgart-things.com`,
`v1alpha1`) into a Tekton `PipelineRun` on a target cluster. The PipelineRun
is rendered by the external KCL module
`oci://ghcr.io/stuttgart-things/kcl-tekton-pr-packer` and wrapped in a
`kubernetes.m.crossplane.io/v1alpha1` Object applied via provider-kubernetes.

The packer sibling of [`ansible-run`](../ansible-run). Structurally near
identical — the four composition steps and the whole `derive-status` block are
the same, because they read only generic PipelineRun status.

## The one real difference: two git repos
`ansible-run` has a single `gitRepoUrl`. Packer needs two, because the
templates do not live with the pipeline:

| Field group | Points at | Used as |
|---|---|---|
| `pipelineRepoUrl` / `pipelineRevision` / `pipelinePath` | stage-time | the PipelineRun's `pipelineRef` git resolver |
| `gitRepoUrl` / `gitRevision` / `gitWorkspaceSubdirectory` | stuttgart-things | pipeline PARAMS telling the clone task which `.pkr.hcl` to fetch |

Conflating them is the most common mistake when porting from `ansible-run`.

`pipelineRevision` must stay a **pinned tag**. The git resolver fetches at
render time, so `main` silently changes what runs whenever stage-time moves.

## Build-directory composition
`gitWorkspaceSubdirectory` can be given explicitly, or composed from
`osVersion`-`lab`-`hypervisor`-`provisioning` as
`packer/builds/<os>-<lab>-<hypervisor>-<provisioning>`.

Split by what varies: `lab` and `hypervisor` are ENVIRONMENT properties and
belong in the EnvironmentConfig; `osVersion` and `provisioning` identify an
individual build and belong on the XR.

Note `hypervisor` was hardcoded to `vsphere` in the KCL module until 0.3.0 —
proxmox build dirs were unreachable without an explicit
`gitWorkspaceSubdirectory`.

## XRD defaults vs EnvironmentConfig (the trap)
An XRD `default:` is applied by the API server **before** the Composition
runs, so it is indistinguishable from an explicit XR value and **masks the
EnvironmentConfig entirely**. Every field the EnvironmentConfig should be able
to supply therefore has NO XRD default, on purpose:

`namespace`, `storageClass`, `pipelineRepoUrl`, `pipelineRevision`,
`pipelinePath`, `gitRepoUrl`, `gitBasicAuthSecretName`, `caCertsConfigMapName`,
`lab`, `hypervisor`, `packerWorkingImage`, `credentialsSecretName`

Precedence is: explicit XR value > EnvironmentConfig > KCL module default.
Do not "tidy up" by adding defaults to these.

## The environment label is config-scoped
The Selector matches
`packer-build.resources.stuttgart-things.com/environment`, not a generic
shared key. `ansible-run` ships an EnvironmentConfig also labelled `default`;
function-environment-configs requires EXACTLY ONE match, so a shared key would
break both with `expected exactly one required resource, got 2`.

## Runtime preconditions that bite
These are build-time-only failures — `packer validate` does NOT catch them:

- **`packerWorkingImage` must carry the full stack**: packer, `xorriso`,
  `ansible-playbook`, and the hashi_vault python deps (`hvac`). A plain alpine
  packer image dies at `could not find a supported CD ISO creation command`
  (any template using `cd_files`) or on the `community.hashi_vault` lookup.
  Installing the packer ansible *plugin* does not help — it is a shim that
  shells out to the ansible-playbook *binary*.
- **`vaultSecretName` must hold `VAULT_TOKEN`**, not just AppRole creds.
  Packer's `vault()` builds a client from `VAULT_ADDR` + `VAULT_TOKEN` and
  never performs an AppRole login; `VAULT_ROLE_ID`/`VAULT_SECRET_ID` are
  injected but inert. Symptom:
  `Must set VAULT_TOKEN env var in order to use vault template function`.
- **A private-CA Vault endpoint needs `caCertsConfigMapName`**, or the run
  dies with `x509: certificate signed by unknown authority`. The CA is
  downloadable unauthenticated from Vault itself at `/v1/pki/ca/pem`. The KCL
  module emitted no `caCerts` workspace at all before 0.4.0, so the XR path
  could not build against the LabUL Vault.
- **`packerTemplate: "."`** (the default) fails on build dirs holding more
  than one template: `packer init` reports `Duplicate local definition`.
  Several stuttgart-things dirs ship byte-identical duplicates, so naming a
  single file is usually required.

## Version-pin coupling (read before bumping)
`render-pipelinerun` pins `kcl-tekton-pr-packer:<version>`. That tag must
exist in ghcr **before** `verify` can pass — otherwise it fails with
`failed to resolve <v>: …kcl-tekton-pr-packer:<v>: not found`. The module is
published MANUALLY (no CI publishes it). Order: bump
`stage-time/kcl/packer/kcl.mod` → `kcl mod push` → bump the pin here.

## Local verify
```bash
crossplane render examples/xr.yaml apis/composition.yaml examples/functions.yaml \
  --extra-resources examples/environmentconfig.yaml

# Or via the repo task runner:
task render   # then pick this configuration
```
`crossplane render` does NOT apply XRD defaults, so examples set
`wrapInCrossplane: true` explicitly (the KCL module's own default is `false`).
