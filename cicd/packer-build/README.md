# packer-build

A Crossplane Configuration that turns a namespaced `PackerBuild` XR into a
Tekton `PipelineRun` that builds a machine image with HashiCorp Packer.

The packer sibling of [`ansible-run`](../ansible-run).

```
PackerBuild XR
  └─ function-environment-configs   shared per-env defaults
  └─ function-kcl                   kcl-tekton-pr-packer -> PipelineRun
       └─ wrapped in a provider-kubernetes Object
  └─ function-kcl (derive-status)   PipelineRun status -> XR status
  └─ function-auto-ready            XR Ready once the build succeeds
```

## Quick start

```yaml
apiVersion: resources.stuttgart-things.com/v1alpha1
kind: PackerBuild
metadata:
  name: packer-build-ubuntu24-labul
  namespace: default
spec:
  pipelineRunName: build-ubuntu24-labul-base-os
  osVersion: ubuntu24
  provisioning: base-os
  packerTemplate: ubuntu24-base-os.pkr.hcl
```

With the shipped EnvironmentConfig that builds
`packer/builds/ubuntu24-labul-vsphere-base-os` from the stuttgart-things repo,
using the `v0.9.0` pipeline from stage-time.

See [`examples/`](examples/) for minimal and full XRs.

## Two git repos, not one

This is the main thing to understand coming from `ansible-run`:

| Field group | Repo | Role |
|---|---|---|
| `pipelineRepoUrl` / `pipelineRevision` / `pipelinePath` | stage-time | where the pipeline definition lives |
| `gitRepoUrl` / `gitRevision` / `gitWorkspaceSubdirectory` | stuttgart-things | where the `.pkr.hcl` templates live |

`pipelineRevision` should be a **pinned tag**. The git resolver fetches it at
render time, so a branch silently changes what runs.

## Choosing the build directory

Either set `gitWorkspaceSubdirectory` outright, or let it be composed:

```
packer/builds/<osVersion>-<lab>-<hypervisor>-<provisioning>
```

`lab` and `hypervisor` are environment properties (put them in the
EnvironmentConfig); `osVersion` and `provisioning` identify the build (put
them on the XR).

## Preconditions on the target cluster

- A `ClusterProviderConfig` for provider-kubernetes matching
  `spec.crossplaneProviderConfig` (default `in-cluster`).
- In the pipeline namespace (default `tekton-ci`):
  - a **`vault` Secret** with `VAULT_ADDR` and `VAULT_TOKEN`
  - a **basic-auth Secret** for cloning the private template repo
  - a **ConfigMap holding the Vault CA** if that endpoint uses a private CA
- RBAC letting provider-kubernetes manage `tekton.dev` resources plus PVCs,
  Secrets and ServiceAccounts in that namespace.

## Gotchas that only surface at build time

`packer validate` catches none of these:

| Symptom | Cause |
|---|---|
| `could not find a supported CD ISO creation command` | working image lacks `xorriso` (needed by any template using `cd_files`) |
| `exec: "ansible-playbook": executable file not found` | working image lacks ansible — the packer ansible *plugin* is only a shim around the binary |
| `Failed to import the required Python library (hvac)` | working image lacks the hashi_vault python deps |
| `Must set VAULT_TOKEN env var…` | the vault Secret has AppRole creds only; packer's `vault()` never performs an AppRole login |
| `x509: certificate signed by unknown authority` | Vault behind a private CA and no `caCertsConfigMapName`; fetch the CA from Vault itself at `/v1/pki/ca/pem` |
| `Duplicate local definition` | `packerTemplate: "."` on a dir with more than one template — name a single file |

`ghcr.io/stuttgart-things/sthings-packer:1.15.1` carries the full stack and is
the image these examples assume.

## Status

The XR surfaces the live PipelineRun:

```
$ kubectl get packerbuild packer-build-ubuntu24-labul
NAME                          SYNCED   READY   SUCCEEDED
packer-build-ubuntu24-labul   True     True    True
```

plus `pipelineRunName`, `reason`, `message`, `startTime`, `completionTime` and
the child `taskRuns`. With `deriveReadiness: true` (default) the XR is Ready
only once the build actually succeeds — a real vSphere base-OS build takes
roughly 20 minutes.

## Local render

```bash
crossplane render examples/xr.yaml apis/composition.yaml examples/functions.yaml \
  --extra-resources examples/environmentconfig.yaml
```
