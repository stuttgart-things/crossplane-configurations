# github-runner-set

A Crossplane v2 Configuration that deploys one **GitHub Actions runner scale set** (ARC chart `gha-runner-scale-set`) on a target cluster from a namespaced `GithubRunnerSet` XR. The cluster needs the ARC controller first — see [github-arc-controller](../github-arc-controller/), which also explains why these are two Configurations.

## Overview

The Composition (`function-go-templating` + `function-auto-ready`) emits exactly one `helm.m.crossplane.io/v1beta1` `Release`:

| | |
|---|---|
| chart | `oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set` (public, no pull secret) |
| version | `spec.version`, default `0.15.0` — must equal the controller's |
| scale set name | `spec.runnerScaleSetName`, default `ghr-{repository\|organization}-{clusterName}` — also the Helm release name and the **`runs-on:` label** |
| registration URL | `spec.githubConfigUrl`, else `https://github.com/{organization}/{repository}`, else `https://github.com/{organization}` (org-level runners) |
| namespace | `spec.namespace`, default `arc-runners`, created by Helm |
| credential | `spec.githubConfigSecret` — name of an existing Secret, see [Credentials](#credentials) |
| runner image | `ghcr.io/actions/actions-runner:2.338.0` (`spec.runnerImage`) |
| container mode | `kubernetes` with an ephemeral work volume (`spec.containerMode`) |
| provider config | `ClusterProviderConfig` `spec.helmProviderConfigRef`, default `{clusterName}-helm` |

The XR reports Ready once the Release does and publishes `status.runnerScaleSetName` (shown as `RUNS-ON` by `kubectl get githubrunnersets`) and `status.githubConfigUrl`.

## Credentials

The XR never carries a token. `spec.githubConfigSecret` is the **name** of a Secret that already exists in `spec.namespace` on the target cluster, and the chart receives that name only (`githubConfigSecret: <name>`) — so the credential is in neither the XR, the managed resource, nor the Helm release values Helm stores in its release Secret.

The Secret must carry the keys the ARC listener expects:

| auth | keys |
|---|---|
| PAT | `github_token` |
| GitHub App | `github_app_id`, `github_app_installation_id`, `github_app_private_key` |

Provide it with External Secrets, e.g. from OpenBao/Vault:

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: github-arc
  namespace: arc-runners
spec:
  refreshInterval: 1h
  secretStoreRef:
    kind: ClusterSecretStore
    name: openbao                  # the cluster's store
  target:
    name: github-arc               # = spec.githubConfigSecret
  data:
    - secretKey: github_token
      remoteRef:
        key: cicd/github-arc       # your KV path
        property: token
```

The namespace has to exist before the ExternalSecret can; create it alongside (Helm would create it too, but only once the Release installs).

## Usage

### Minimum — organization-level runners

```yaml
apiVersion: resources.stuttgart-things.com/v1alpha1
kind: GithubRunnerSet
metadata:
  name: stuttgart-things-org
  namespace: crossplane-system
spec:
  clusterName: sthings-runner
  githubConfigSecret: github-arc   # runs-on: ghr-stuttgart-things-sthings-runner
```

### One repository (LabDA sthings-runner shape)

```yaml
apiVersion: resources.stuttgart-things.com/v1alpha1
kind: GithubRunnerSet
metadata:
  name: ansible-in-cluster
  namespace: crossplane-system
spec:
  clusterName: in-cluster          # keeps runs-on: ghr-ansible-in-cluster
  helmProviderConfigRef: in-cluster
  repository: ansible
  githubConfigSecret: github-arc
  containerMode:
    type: kubernetes
    storageClassName: openebs-hostpath
    storage: 50Mi
```

Every field: [`examples/xr-max.yaml`](examples/xr-max.yaml). `spec.values` is merged last; maps merge, **lists are replaced** — setting `values.template` replaces the runner container the composition builds.

### Container modes

| `containerMode.type` | what the chart does |
|---|---|
| `kubernetes` (default) | job containers run as separate pods via the container hooks, on an ephemeral work PVC (`storageClassName`, `storage`). `requireJobContainer: false` (default) lets jobs without `container:` run in the runner pod itself. |
| `kubernetes-novolume` | as above, without the work PVC. |
| `dind` | Docker-in-Docker sidecar — privileged. |
| `none` | no container mode; the runner pod is just the composed template (plus `values`). |

## Upgrading

Bump `spec.version` on every `GithubRunnerSet` together with the controller (see [github-arc-controller → Upgrading](../github-arc-controller/README.md#upgrading), including the manual CRD step). Keep `runnerImage.tag` current: GitHub deprecates runner versions server-side, and a deprecated runner exits with code 7 on every start, which ARC reports as phase `Outdated` and retries forever. Latest: https://github.com/actions/runner/releases/latest

## Migrating from `stuttgart-things/crossplane` `github-runner`

The old package's `GithubRunner` claims map 1:1:

| old (`resources.stuttgart-things.com/v1alpha1` `GithubRunner`) | new (`GithubRunnerSet`) |
|---|---|
| `clusterConfig: in-cluster` | `clusterName: in-cluster` + `helmProviderConfigRef: in-cluster` (keeps the `ghr-<repo>-in-cluster` name) |
| `group` | `organization` |
| `repository` | `repository` |
| `version` | `version` |
| `runnerImageTag` | `runnerImage.tag` |
| `githubTokenSecret{name,namespace,key}` (token copied into the release values) | `githubConfigSecret` (Secret name in the runner namespace, key `github_token`) |
| `deploymentNamespace` (default `arc-systems`) | `namespace` (default `arc-runners`; set `arc-systems` to stay put) |
| hard-coded kubernetes mode, `openebs-hostpath`, 50Mi | `containerMode` |

The Helm release name stays `ghr-<repo>-<clusterName>`, so with `namespace: arc-systems` provider-helm should pick up the existing release (not yet exercised on a live cluster). As for the controller: set `deletionPolicy: Orphan` on the old Release object before deleting the old claim, so the two MRs never manage the same release. Moving to `arc-runners` instead is a reinstall (new release in the new namespace, the old one uninstalled with its claim) — runners re-register under the same name.

## Cluster preconditions

- The ARC controller on the target cluster, same chart version ([github-arc-controller](../github-arc-controller/)). The chart discovers its ServiceAccount by label at install time; set `spec.controllerServiceAccount` when there is more than one controller.
- The credential Secret in `spec.namespace` (External Secrets, see above).
- `provider-helm` with a `ClusterProviderConfig` named `{clusterName}-helm` or `spec.helmProviderConfigRef`.

## Development

```bash
crossplane render examples/xr.yaml apis/composition.yaml examples/functions.yaml --include-function-results
task render                      # repo-wide picker
CONFIG=cicd/github-runner-set task render-golden
```

```bash
kubectl get githubrunnersets -A          # RUNS-ON column = the label for workflows
crossplane resource trace githubrunnerset.resources.stuttgart-things.com ansible-in-cluster -n crossplane-system
```

## Files

- `crossplane.yaml` — package metadata + dependencies
- `apis/definition.yaml` — XRD (`GithubRunnerSet`, v1alpha1, namespaced)
- `apis/composition.yaml` — Composition pipeline (go-templating + auto-ready)
- `examples/xr-min.yaml` — only XRD-required fields (org-level runners)
- `examples/xr.yaml` — one repository, the LabDA sthings-runner shape
- `examples/xr-max.yaml` — every spec field exercised (GitHub App secret, explicit name, values)
- `examples/functions.yaml` — required Crossplane Functions
- `examples/configuration.yaml` — install manifest (OCI ref)
