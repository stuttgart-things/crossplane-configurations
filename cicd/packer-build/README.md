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
  name: packer-build-ubuntu26-labul
  namespace: default
spec:
  pipelineRunName: build-ubuntu26-labul-proxmox-base-os
  osVersion: ubuntu26
  provisioning: base-os
  packerTemplate: ubuntu26-base-os.pkr.hcl
  vaultSecretName: vault-infra
```

With the shipped EnvironmentConfig that builds
`packer/builds/ubuntu26-labul-proxmox-base-os` from the stuttgart-things repo,
using the `v0.13.6` pipeline from stage-time.

Two labs build images: **LabUL on Proxmox** (the EnvironmentConfig default)
and **LabDA on vSphere** (`lab: labda`, `hypervisor: vsphere`,
`vaultSecretName: vault-labda` on the XR, see `examples/xr-max.yaml`). LabUL
vSphere is gone.

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

## Setup on a target cluster

### 0. Check what the cluster already provides

```bash
# Crossplane + provider-kubernetes, and a ClusterProviderConfig whose name
# matches spec.crossplaneProviderConfig (default: in-cluster)
kubectl get providers.pkg.crossplane.io
kubectl get clusterproviderconfigs.kubernetes.m.crossplane.io

# The three Functions this Composition's pipeline uses
kubectl get functions.pkg.crossplane.io | grep -E 'kcl|auto-ready|environment-configs'

# Tekton, and the StorageClass the EnvironmentConfig names
kubectl get deploy -n tekton-pipelines
kubectl get sc

# RBAC: provider-kubernetes must be able to create PipelineRuns
SA=$(kubectl get sa -n crossplane-system -o name | grep provider-kubernetes | head -1 | cut -d/ -f2)
kubectl auth can-i create pipelineruns.tekton.dev -n tekton-ci \
  --as="system:serviceaccount:crossplane-system:${SA}"
```

Everything above is usually already in place on a stuttgart-things cluster.
The steps below are the parts specific to this Configuration.

### 1. Install the Configuration

```bash
kubectl apply -f examples/configuration.yaml
kubectl get configuration.pkg.crossplane.io packer-build   # wait for HEALTHY=True
```

Verify the API landed:

```bash
kubectl get xrd packerbuilds.resources.stuttgart-things.com   # ESTABLISHED=True
kubectl get composition packer-build
```

### 2. Apply the EnvironmentConfig

```bash
kubectl apply -f examples/environmentconfig.yaml
```

Adjust `storageClass` first if the cluster's differs (e.g. `local-path`,
`standard`). The shipped `lab` / `hypervisor` build for LabUL Proxmox; a
LabDA vSphere build overrides both on the XR.

### 3. Create the Vault CA ConfigMap

Only needed when the Vault endpoint uses a private CA — which both the infra
Vault (LabUL Proxmox builds) and the LabDA Vault do. Bundle every CA the
cluster's builds need into one ConfigMap. Vault serves its own CA
unauthenticated:

```bash
# once per Vault the cluster's builds use (VAULT_ADDR from the vault Secret)
curl -sk "$VAULT_ADDR/v1/pki/ca/pem" -o <lab>-vault-ca.crt

# Verify the CA actually validates the endpoint BEFORE trusting it --
# note the absence of -k here. Using -k at this step hides exactly the
# failure this ConfigMap exists to prevent.
curl -s --cacert <lab>-vault-ca.crt -o /dev/null -w '%{http_code}\n' \
  "$VAULT_ADDR/v1/sys/health"   # expect 200

kubectl create configmap packer-ca-certs -n tekton-ci \
  --from-file=<lab>-vault-ca.crt   # repeat --from-file per CA
```

The ConfigMap name must match `caCertsConfigMapName` in the EnvironmentConfig.

### 4. Create the Vault Secret

The Secret carries `VAULT_ADDR` plus **either** `VAULT_TOKEN` **or** an
AppRole (`VAULT_ROLE_ID` + `VAULT_SECRET_ID`). packer's `vault()` only reads
`VAULT_TOKEN`, but from stage-time **v0.12.0** `execute-packer` performs the
AppRole login itself when the Secret has no token, and revokes the token on
exit. `vault-infra` (LabUL Proxmox) is AppRole-only on purpose. On a
`pipelineRevision` below v0.12.0 an AppRole-only Secret fails with
`Must set VAULT_TOKEN env var in order to use vault template function`.

Template: `templates/packer-vault-secret.yaml` in
[stage-time](https://github.com/stuttgart-things/stage-time) — an
`InlineSopsSecret`, so you encrypt locally and the plaintext token never
leaves your machine. The Secret name must match `vaultSecretName` on the XR
(default `vault`).

```bash
# 1. encrypt locally (recipient key differs per cluster -- read it off an
#    existing InlineSopsSecret rather than hardcoding one)
sops --encrypt --age <recipient> secret.yaml
# 2. paste the output into spec.encryptedYAML of the template, then apply it
kubectl apply -f packer-vault-secret.yaml
```

Delete the `InlineSopsSecret` CR, not the derived Secret — the operator holds
a finalizer and will recreate the Secret.

### 5. Create the git basic-auth Secret

Needed because the default template repo is private, and an `https://` remote
cannot be authenticated by an SSH-key workspace.

Template: `templates/git-basicauth-secret.yaml` in
[stage-time](https://github.com/stuttgart-things/stage-time), rendered with
machineshop (`task render-scm-secret`):

```bash
machineshop render --source local \
  --template templates/git-basicauth-secret.yaml \
  --values "name=packer-git-basicauth, gitServer=github.com, \
            user=<user>, token=<pat>, \
            userName=<full name>, email=<mail>"
```

The Secret name must match `gitBasicAuthSecretName` in the EnvironmentConfig.

Two things the Secret must get right — both were broken in that template
until recently, and either alone stops the clone working:

- **`.gitconfig` needs `[credential] helper = store`.** Without it git never
  reads `.git-credentials` and falls through to an interactive helper, which
  does not exist inside a pipeline container.
- **Do not use a `url`/`insteadOf` rewrite with a trailing slash.** It never
  matches a real remote like `…/stuttgart-things.git`. The credential helper
  alone is enough.

Check a rendered Secret before trusting it:

```bash
# write .gitconfig and .git-credentials into a temp HOME, then:
printf 'protocol=https\nhost=github.com\n\n' | HOME=$TMP git credential fill
# expect username= and password= back, NOT "Missing or invalid credentials"
```

### 6. Run a build

```bash
kubectl apply -f examples/xr.yaml
kubectl get packerbuild -w
```

Watch it progress:

```bash
kubectl describe packerbuild packer-build-ubuntu26-labul
kubectl get pipelinerun -n tekton-ci
kubectl logs -n tekton-ci -l tekton.dev/pipelineRun=<name> \
  -c step-packer-action -f
```

A real base-OS build takes roughly 20 minutes. With
`deriveReadiness: true` (the default) the XR reports Ready only once the
build has actually succeeded.

## Gotchas that only surface at build time

`packer validate` catches none of these:

| Symptom | Cause |
|---|---|
| `could not find a supported CD ISO creation command` | working image lacks `xorriso` (needed by any template using `cd_files`) |
| `exec: "ansible-playbook": executable file not found` | working image lacks ansible — the packer ansible *plugin* is only a shim around the binary |
| `Failed to import the required Python library (hvac)` | working image lacks the hashi_vault python deps |
| `Must set VAULT_TOKEN env var…` | the vault Secret has AppRole creds only and `pipelineRevision` is below stage-time v0.12.0, which added the AppRole login |
| `x509: certificate signed by unknown authority` | Vault behind a private CA and no `caCertsConfigMapName`; fetch the CA from Vault itself at `/v1/pki/ca/pem` |
| `Duplicate local definition` | `packerTemplate: "."` on a dir with more than one template — name a single file |

`ghcr.io/stuttgart-things/sthings-packer:2.0.0` carries the full stack and is
the image these examples assume.

## Status

The XR surfaces the live PipelineRun:

```
$ kubectl get packerbuild packer-build-ubuntu26-labul
NAME                          SYNCED   READY   SUCCEEDED
packer-build-ubuntu26-labul   True     True    True
```

plus `pipelineRunName`, `reason`, `message`, `startTime`, `completionTime` and
the child `taskRuns`. With `deriveReadiness: true` (default) the XR is Ready
only once the build actually succeeds — a real base-OS build takes
roughly 20 minutes.

The template the build produced is on the status by name and, on Proxmox, by
VMID:

| Field | vSphere | Proxmox |
|---|---|---|
| `status.templateName` | template name (the artifact ID) | `template_name` from the build's packer manifest post-processor; absent if the build or the stage-time pin (< v0.13.6) does not report it |
| `status.templateVmId` | — | VMID (the artifact ID), what bpg clones and a promotion writes |

```bash
kubectl get packerbuild packer-build-ubuntu26-labul \
  -o jsonpath='{.status.templateName}{" "}{.status.templateVmId}'
# ubuntu26-base-20261001-1200 211
```

Which one is Proxmox follows `hypervisor` (XR, then EnvironmentConfig, then
`vsphere`), exactly as the build directory does.

`status.results` carries the raw PipelineRun results the two fields are taken
from: `template-name` (packer's artifact ID) and, from stage-time v0.13.6,
`template-display-name`.

Without this the name only exists inside the PipelineRun, so nothing composing
this XR could know which template to smoke-test or promote. Only string-valued
results are surfaced; Tekton also permits array- and object-typed results,
which the status schema cannot represent.

## Local render

```bash
crossplane render examples/xr.yaml apis/composition.yaml examples/functions.yaml \
  --extra-resources examples/environmentconfig.yaml
```
