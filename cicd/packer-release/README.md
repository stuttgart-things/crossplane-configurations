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
  stage-time `pipelineRevision` >= `v0.10.0` — the first tag containing
  `promote-packer-template.yaml`.
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
