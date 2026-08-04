# packer-release

A Crossplane Configuration that builds a machine image **and proves it works**,
by composing two existing XRs rather than re-implementing either.

```
PackerRelease XR
  ├─ PackerBuild        (cicd/packer-build)   -> status.results[template-name]
  ├─ VMProvision        (machinery/vm-provision)
  │    ├─ VsphereVM     clone that template
  │    └─ AnsibleRun    run playbooks against the clone
  └─ Object             promote PipelineRun (govc) — opt-in, gated on tested
```

## Why a layer above packer-build

`packer-build` answers *did packer exit 0*, which is not the same question as
*is this image usable*. A template can build cleanly and still fail to boot,
come up without a network, or lack the user every later consumer logs in as.
The only way to find out is to clone it and drive it.

## Quick start

```yaml
apiVersion: resources.stuttgart-things.com/v1alpha1
kind: PackerRelease
metadata:
  name: ubuntu24-labul
  namespace: default
spec:
  environmentConfig: default
  build:
    osVersion: ubuntu24
    provisioning: base-os
    packerTemplate: ubuntu24-base-os.pkr.hcl
```

```
$ kubectl get packerrelease
NAME             PHASE     TEMPLATE                      BUILT   TESTED
ubuntu24-labul   Tested    ubuntu24-base-20260721-1131   True    true
```

See [`examples/`](examples/) for the full and build-only variants.

## Lifecycle

| Phase | Composed | XR Ready | Terminal |
|---|---|---|---|
| `Building` | PackerBuild | no | no |
| `BuildFailed` | PackerBuild | no | **yes** |
| `BuildIncomplete` | PackerBuild | no | **yes** |
| `Testing` | PackerBuild + test VM | no | no |
| `Tested` | PackerBuild | **yes** | yes, unless promotion is on |
| `TestSkipped` | PackerBuild | once the build succeeds | yes |
| `Promoting` | PackerBuild + promote run | no | no |
| `Promoted` | PackerBuild + promote run | **yes** | yes |
| `PromoteFailed` | PackerBuild + promote run | no | **yes** |
| `PromoteUnsupported` | PackerBuild | **yes** | **yes** |

Ready therefore means *built and verified* — and, with promotion enabled,
*published*.

The two terminal failure phases exist because nothing here retries. A failed
build leaves a PipelineRun in `Failed`, the gate never opens and no test VM is
created — correct behaviour, but for a while it reported as `Building`, so the
one column you would watch claimed work was still in flight. `BuildFailed`
says otherwise; `buildPipelineRunName` points at the logs.

`BuildIncomplete` is the narrower case the `packer-build >=v0.3.0` dependency
floor guards: the build genuinely succeeded, but its status carries no
`template-name`, so there is nothing to clone or promote and the gate can
never open.

### The gate

The test VM is composed only while all of these hold:

- `spec.test.enabled` (default true)
- the build reported `succeeded: "True"`
- the build reported a `template-name` result
- `status.tested` is not yet set

### The latch

`status.tested` is set once the test VM reports Ready, and never cleared.

It is what makes teardown terminal. Removing the test VM satisfies the gate
condition again — without the latch the next reconcile would rebuild it, and
the loop would never settle. Re-running a release means recreating the XR, not
clearing a field.

## Promotion

Off by default. `spec.promote.enabled: true` adds a third composed resource: a
`provider-kubernetes` Object wrapping a
[`promote-packer-template`](https://github.com/stuttgart-things/stage-time/blob/main/pipelines/promote-packer-template.yaml)
PipelineRun, which uses govc to rename the current golden image aside and the
fresh build into its place.

**Both providers promote, by different mechanisms.** The paragraph above
describes the vSphere path. Proxmox is not a rename at all — see
[Promotion on Proxmox](#promotion-on-proxmox) below. A provider with neither
pipeline is **rejected at admission** (an XRD CEL rule), and the Composition
additionally withholds the Object — such an XR reports the terminal phase
`PromoteUnsupported` rather than firing the vSphere promoter at a template it
cannot address.

```yaml
  promote:
    enabled: true
    goldenName: sthings-u24
```

Everything else — build and golden folders, datacenter, CA bundle, Vault path,
the pipeline pin — is environment, and lives in the EnvironmentConfig.
`goldenName` does not: which image a build supersedes is a per-release
decision, and it is not derivable from `osVersion` (the `ubuntu24` →
`sthings-u24` mapping is a naming convention, not a rule).

Exactly one previous generation is kept, as `<goldenName>-previous`, so a bad
promotion is one rename away from rollback. `status.previousTemplate` holds its
full inventory path.

**It is gated on `status.tested`, not on `spec.test.enabled`.** Since only a
test VM reaching Ready ever sets that latch, promotion with the smoke test
disabled does not skip the gate — it means the golden image is never touched.

### Promotion on Proxmox

Nothing is renamed, because renaming would reach nobody. `bpg`'s
`VirtualMachine` exposes `spec.forProvider.clone` with `vmId` and nothing else —
no name, path, ref or selector. What consumers actually resolve is
`templateVmId` in the `proxmoxvm` capability chart, so promotion **changes that
value**, delivered as a pull request against the config repo by
[`promote-proxmox-template`](https://github.com/stuttgart-things/stage-time/blob/main/pipelines/promote-proxmox-template.yaml).

```yaml
  promote:
    enabled: true
    proxmox:
      valuePath: .environments.labul.templateVmId
```

**Merging the pull request is the promotion.** Until then the fleet keeps
cloning the previous VMID. `status.phase: Promoted` therefore means something
weaker here than on vSphere — read `status.promotionPullRequest`.

Consequences, all of them simplifications: no PVE API call (so no node access,
unavailable in LabUL anyway, and no Proxmox API token), no `-previous`
generation to retain, and rollback is putting the old VMID back.

`valuePath` has no default and is not derived from `lab`: it names an
environment block, and a guess would promote a different lab's pointer *while
looking like it worked*. Set `proxmox.dryRun: "true"` for a first run — it
prints the diff and opens nothing, which is how you prove the path matches
before a release ever touches the config repo.

Re-running is safe. The branch name is deterministic, so a second run updates
its own open pull request instead of opening another. Requires stage-time
**>= v0.13.2**: v0.13.0 and v0.13.1 could open a promotion but not re-run one
(the push died on `stale info`), and since a `PackerRelease` reconciles
repeatedly, that turned every promotion after the first into `PromoteFailed`.

#### The write credential

The pipeline pushes a branch and opens a pull request, so it needs a token with
write access to the config repo. The read-only clone credential the build
pipelines use is **not** sufficient, and `sops-git-credentials` must not be
widened — it is deliberately read-only and every cluster uses it.

| | |
|---|---|
| Secret | `config-repo-pr-token` in the pipeline namespace (`tekton-ci`) |
| Key | `token` |
| Type | fine-grained PAT, **resource owner `stuttgart-things`** |
| Repository access | only `stuttgart-things/stuttgart-things` |
| Permissions | `Contents: Read and write`, `Pull requests: Read and write` |

**It expires.** GitHub caps fine-grained PAT lifetimes, and a lapsed token fails
the promotion *after* the build and the smoke test have already run — an
expensive place to discover it, and the failure names an HTTP 401 rather than an
expiry. Put it on the same rotation list as the other credentials.

**It can also push `main`, and that cannot be scoped away.** GitHub has no
permission for "may push branches but not the default branch"; the enforcement
mechanism would be branch protection, which is unavailable on a private repo on
the free plan (`/rulesets` and `/branches/main/protection` both answer 403). The
pipeline never does — one deterministically named branch, an explicit lease,
then a PR — but that is behaviour, not enforcement. If that is too broad, run
with `dryRun: "true"` and a read-only credential: the pipeline then prints the
exact diff and a human commits it, which still removes the hand-copied VMID that
[#2442](https://github.com/stuttgart-things/stuttgart-things/issues/2442)
complains about.

Note also that this is a credential *for* the config repo, stored *in* it
(sops-encrypted). Whoever can push there can add a blob a cluster will decrypt,
and this blob grants push — a concrete argument for pinning the
`sops-git-secrets` chart's `git.revision` to a tag rather than following `main`.

Creating it and storing it, without the plaintext ever reaching a shell history,
a log or a process argument. It verifies `permissions.push` first and writes
**nothing** unless that is true:

```bash
read -rsp 'PAT: ' T; echo
T=$(printf '%s' "$T" | tr -d '[:space:]')
cd ~/projects/stuttgart-things
R=$(printf 'header = "Authorization: Bearer %s"\n' "$T" \
     | curl -s -K - https://api.github.com/repos/stuttgart-things/stuttgart-things)
PUSH=$(printf '%s' "$R" | jq -r '.permissions.push // false')
echo "repo: $(printf '%s' "$R" | jq -r '.full_name // .message')   push: $PUSH"
if [ "$PUSH" = "true" ]; then
  printf 'apiVersion: v1\nkind: Secret\nmetadata:\n  name: config-repo-pr-token\n  namespace: tekton-ci\ntype: Opaque\nstringData:\n  token: %s\n' "$T" > /tmp/pat.yaml
  chmod 600 /tmp/pat.yaml
  sops --config secrets/.sops.yaml \
       --filename-override secrets/config-repo-pr-token.enc.yaml \
       --encrypt /tmp/pat.yaml > secrets/config-repo-pr-token.enc.yaml
  shred -u /tmp/pat.yaml
  echo "OK -> secrets/config-repo-pr-token.enc.yaml"
else
  echo "ABORT -- nothing written"
fi
unset T
```

`printf` is a bash builtin and `curl` reads the header from stdin (`-K -`), so
the token never becomes a process argument. Commit the blob and add it to the
`sops-git-secrets` chart's `secrets:` list so it has an owner; the operator then
materialises the Secret in `tekton-ci`.

An earlier version of this snippet checked only the HTTP status. A read-only
token answers `200` just as happily, so it would have been stored and the
failure deferred to the first real promotion — hence the `permissions.push`
check.
That combination reports `TestSkipped` and composes nothing.

**Promotion is the one thing here that outlives the XR.** The test VM is
garbage-collected with its owner; a renamed template is not. Deleting a
`PackerRelease` after a successful promotion leaves the golden image pointing
at the new build.

Consequently the promote Object is *not* torn down the way the test VM is.
There is no "already promoted" clause in its gate: the PipelineRun is the
record of which template was promoted and what it superseded, and removing the
Object would delete it. Re-rendering costs nothing — a PipelineRun is immutable
and the name is fixed, so the provider converges on the existing one.

### Teardown without a cleanup step

The test VM disappears because the Composition stops rendering it, not because
anything deletes it. A `finally`-style teardown only runs while the thing
driving it is still alive; a composed resource is removed by the same garbage
collector that owns it, including when the whole `PackerRelease` is deleted
mid-test.

**A failed smoke test leaves the VM up.** A test VM that never reaches Ready
keeps the gate open, so the evidence survives for inspection. That is
deliberate — clean up by deleting the `PackerRelease`.

## Three EnvironmentConfigs, one per layer

Nothing is duplicated between them; set each field in exactly one place.

| EnvironmentConfig | Selector label | Owns |
|---|---|---|
| `packer-release` | `packer-release.resources.stuttgart-things.com/environment` | test-VM placement, tfvars Secret, OpenTofu provider config, Ansible playbooks, the `promote` block |
| `packer-build` | `packer-build.resources.stuttgart-things.com/environment` | repos, pipeline revision, CA ConfigMap, credentials, working image, lab, hypervisor |
| `vsphere-vm` | `vsphere-vm.resources.stuttgart-things.com/environment` | placement fallbacks for any field the two above leave unset |

Each key is namespaced to its Configuration because
`function-environment-configs` requires **exactly one** Selector match, and
several sibling Configurations ship an EnvironmentConfig whose value is also
`default`. A shared key breaks all of them at once with
`expected exactly one required resource, got 2`.

### Overriding the build pipeline pin for one release

`build.pipelineRevision` overrides the `packer-build` EnvironmentConfig's
stage-time tag for this release only. Leave it unset and the environment
decides, which is what a release normally wants.

It exists because it pairs with `build.vaultSecretName`, and that pairing is
load-bearing. A Vault Secret carrying an **AppRole instead of a token** — the
infra Vault, `vaultSecretName: vault-infra` — only works from stage-time
**>= v0.12.0**, where `execute-packer` performs the login itself. On an older
pin the build dies on `Must set VAULT_TOKEN env var in order to use vault
template function`. Before v0.4.0 a release could name the Vault but not the
tag able to read it, which made Proxmox releases unexpressible: their
credentials are only on the infra Vault, so they need both settings at once.

Not the lever for moving the fleet forward — bumping the EnvironmentConfig is
that change, and it is a fleet decision affecting every build including the
vSphere golden ones. This is for one release that needs a different pin than
its environment.

## Preconditions

`dependsOn` pulls `packer-build` and `vm-provision` (and transitively
`vsphere-vm`, `proxmox-vm`, `ansible-run`).

**`packer-build` must be >= v0.3.0.** The gate reads the `template-name`
PipelineRun result off its status, which older versions do not surface.
Against an older `packer-build` the gate never opens behind a successful
build; the XR reports `phase: BuildIncomplete`, which is the only signal
that anything is wrong — no condition or event says so.

On the target cluster, additionally:

- Everything `packer-build` needs — see [its README](../packer-build/README.md):
  the `vault` Secret, the git basic-auth Secret and the CA ConfigMap in the
  pipeline namespace.
- For promotion only: a provider-**kubernetes** `ClusterProviderConfig`
  (`kubectl get clusterproviderconfigs.kubernetes.m.crossplane.io`), and a
  stage-time pin containing the pipeline for the provider in use — `>= v0.10.0`
  for `promote-packer-template.yaml` (vSphere), `>= v0.13.2` for
  `promote-proxmox-template.yaml` (Proxmox). The two pins are separate keys:
  `promote.pipelineRevision` is the vSphere one and predates Proxmox, so
  applying it there pins a tag that does not contain the file — use
  `promote.proxmox.pipelineRevision`.
- For a **Proxmox smoke test**: a tfvars Secret for the Proxmox hypervisor, and
  it must be named explicitly. `test.tfvars.secretName` falls back to the
  EnvironmentConfig's `tfvarsSecretName` *before* the provider-derived
  `proxmox-tfvars`, so on a cluster whose `packer-release` EnvironmentConfig
  names `vsphere-tfvars` a Proxmox test silently picks up vSphere credentials.
  Note the two Proxmox paths do not share one Secret: this feeds
  `stuttgart-things/proxmox-vm`, built on **Telmate** (`pm_api_url` including
  `/api2/json`), while `NativeProxmoxVM` uses bpg's JSON credentials with a bare
  endpoint.
- For **Proxmox** promotion additionally: the `config-repo-pr-token` Secret in
  the pipeline namespace, a fine-grained PAT with `Contents` + `Pull requests`
  write on the config repo. It **expires** — see
  [The write credential](#the-write-credential) for the scope, the expiry
  consequences and a creation snippet that verifies write access before storing
  anything.
- An OpenTofu `ClusterProviderConfig`. Its name is a per-cluster choice — check
  with `kubectl get clusterproviderconfigs.opentofu.m.upbound.io` and set
  `providerConfigName` in the EnvironmentConfig to match. This is **not** the
  provider-kubernetes config that `packer-build` and `ansible-run` call
  `crossplaneProviderConfig`; mixing them up produces a
  not-found-shaped failure with nothing pointing at the cause.
- A tfvars Secret in the XR's namespace holding the hypervisor credentials:

  ```bash
  kubectl create secret generic vsphere-tfvars -n default \
    --from-literal=terraform.tfvars="$(cat <<'TFVARS'
  vsphere_server   = "<vcenter>"
  vsphere_user     = "<user>"
  vsphere_password = "<password>"
  vm_ssh_user      = "<ssh-user>"
  vm_ssh_password  = "<ssh-password>"
  TFVARS
  )"
  ```

  `vm_ssh_user`/`vm_ssh_password` are not optional: the `vsphere-vm` Terraform
  module runs `remote-exec` provisioners, so a template the credentials cannot
  log into fails the test VM outright — which is arguably the smoke test doing
  its job.

## Gotchas

| Symptom | Cause |
|---|---|
| `phase: BuildIncomplete` | `packer-build` older than v0.3.0 — no `template-name` on its status |
| `phase: BuildFailed` | the build PipelineRun failed; nothing retries. `kubectl logs -n <ns> -l tekton.dev/pipelineRun=<buildPipelineRunName> -c step-packer-action` |
| `phase: PromoteFailed` | the govc run failed. It refuses to touch a half-promoted inventory, so check the golden folder for a stray `<goldenName>-previous` before rerunning |
| Stuck in `Promoting` behind a PipelineRun that already succeeded | the promote Object lost its `readiness` CEL query. Without it provider-kubernetes treats creation as completion, stops re-observing, and `status.atProvider.manifest` freezes before the results exist |
| `Building` forever, no PipelineRun, `PackerBuild` unsynced on `packer-build-run` | two releases in one namespace colliding on the Object name. `packer-build`'s KCL module defaults it to a literal, so this Configuration sets `crossplaneObjectName` per release — a bare `PackerBuild` still has the limit |
| `promote.enabled: true` but nothing happens | the smoke test is off, so the `tested` latch never closes — the phase is `TestSkipped`, not `Promoting` |
| `error fetching virtual machine: vm '<name>' not found`, looping | the template does not exist, or this vCenter account cannot see it. Check with `govc find /<dc> -type m -name '<name>'` |
| Test VM never boots after a clean build | firmware mismatch — `spec.test.firmware` must match what the template was built with |
| Test VM up but Ansible fails to connect | the template lacks the user in `vm_ssh_user`; a base-OS build creates it, a vanilla OS image does not |
| Test VM still around after a pass | the latch never got set — check `status.tested` and the test VM's Ready condition |

## Local render

```bash
crossplane render examples/xr.yaml apis/composition.yaml examples/functions.yaml \
  --extra-resources examples/environmentconfig.yaml
```

To exercise the gate offline, supply an observed `PackerBuild` carrying a
`template-name` result via `--observed-resources`; the four phases are
reachable that way without touching a cluster.
