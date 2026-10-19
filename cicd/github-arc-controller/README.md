# github-arc-controller

A Crossplane v2 Configuration that installs the **GitHub Actions Runner Controller** (ARC, chart `gha-runner-scale-set-controller`) on a target cluster from a namespaced `GithubArcController` XR.

## Overview

The Composition (`function-go-templating` + `function-auto-ready`) emits exactly one `helm.m.crossplane.io/v1beta1` `Release`:

| | |
|---|---|
| chart | `oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set-controller` (public, no pull secret) |
| version | `spec.version`, default `0.15.0` |
| release name | `spec.releaseName`, default `github-controller` (set as the Release's `crossplane.io/external-name`) |
| namespace | `spec.namespace`, default `arc-systems`, created by Helm |
| provider config | `ClusterProviderConfig` `spec.helmProviderConfigRef`, default `{clusterName}-helm` |
| CRDs | installed by the chart (`skipCRDs: false`) — first install only, see [Upgrading](#upgrading) |

The XR reports Ready once the Release does, and publishes `status.chartVersion`, `status.releaseName` and `status.namespace`.

## Controller and runner sets: two Configurations

ARC is two Helm charts with two lifecycles: **one controller per cluster**, and **any number of runner scale sets** (LabDA's sthings-runner carries 18). This Configuration is the controller; each scale set is a `GithubRunnerSet` XR from the sibling [github-runner-set](../github-runner-set/) Configuration. One XR holding the controller plus a list of runner sets would turn adding a repository into an edit of a shared object, and deleting it would take every runner on the cluster with it. The runner set does not `dependsOn` this package: what it needs is the controller running on the *target* cluster, not this XRD on the control plane.

## Usage

### Minimum

```yaml
apiVersion: resources.stuttgart-things.com/v1alpha1
kind: GithubArcController
metadata:
  name: sthings-runner
  namespace: crossplane-system
spec:
  clusterName: sthings-runner     # -> ClusterProviderConfig sthings-runner-helm
```

### Crossplane on the runner cluster itself (LabDA sthings-runner)

```yaml
apiVersion: resources.stuttgart-things.com/v1alpha1
kind: GithubArcController
metadata:
  name: gha-controller
  namespace: crossplane-system
spec:
  clusterName: in-cluster
  helmProviderConfigRef: in-cluster
  releaseName: github-controller
  version: "0.15.0"               # lockstep with every GithubRunnerSet
```

Every field: [`examples/xr-max.yaml`](examples/xr-max.yaml). `spec.values` is merged last and overrides the modelled fields (`replicaCount`, `logLevel`).

## Upgrading

**Bump the controller and every runner set together** — ARC releases both charts with the same version and supports only matching pairs.

**Helm never upgrades CRDs.** Files under a chart's `crds/` are applied on the first install only, so a `spec.version` bump leaves the ARC CRDs at the old version. The API server then silently prunes fields the new controller writes (e.g. `EphemeralRunnerSet.status.phase`), outdated EphemeralRunners are never garbage-collected, and the controller rewrites status in a hot loop — on sthings-runner this produced 86k orphaned objects and a 1.1 GB etcd (2026-08-25). Tell-tale log line: `unknown field "status.phase"`. Apply the matching CRDs after every bump:

```bash
ARC_VERSION=0.15.0   # = spec.version
BASE=https://raw.githubusercontent.com/actions/actions-runner-controller/gha-runner-scale-set-${ARC_VERSION}/charts/gha-runner-scale-set-controller/crds
for crd in ephemeralrunners ephemeralrunnersets autoscalingrunnersets autoscalinglisteners; do
  # server-side: the files are >600KB, client-side apply hits the annotation limit
  kubectl apply --server-side --force-conflicts -f "${BASE}/actions.github.com_${crd}.yaml"
done
```

## Migrating from `stuttgart-things/crossplane` `github-controller`

The old package (`ghcr.io/stuttgart-things/crossplane/github-controller`) created the release `github-controller` in `arc-systems`; this XR's defaults name the same release, so provider-helm should pick up that release instead of installing a second controller (not yet exercised on a live cluster). Two Release MRs must never manage the same release, so take the old one out without uninstalling:

1. Set `deletionPolicy: Orphan` on the old `releases.helm.crossplane.io` object, then delete the old claim/XR.
2. Apply the `GithubArcController` (same `version` as before; bump afterwards).

Old field → new field: `clusterName` (provider config) → `helmProviderConfigRef`; `deploymentNamespace` → `namespace`; `version` unchanged.

## Cluster preconditions

- `provider-helm` with a `ClusterProviderConfig` named `{clusterName}-helm` or `spec.helmProviderConfigRef`, whose identity can install cluster-scoped resources (CRDs, ClusterRoles) on the target.

## Development

```bash
crossplane render examples/xr.yaml apis/composition.yaml examples/functions.yaml --include-function-results
task render                      # repo-wide picker
CONFIG=cicd/github-arc-controller task render-golden
```

```bash
crossplane resource trace githubarccontroller.resources.stuttgart-things.com gha-controller -n crossplane-system
```

## Files

- `crossplane.yaml` — package metadata + dependencies
- `apis/definition.yaml` — XRD (`GithubArcController`, v1alpha1, namespaced)
- `apis/composition.yaml` — Composition pipeline (go-templating + auto-ready)
- `examples/xr-min.yaml` — only XRD-required fields
- `examples/xr.yaml` — the LabDA sthings-runner shape
- `examples/xr-max.yaml` — every spec field exercised
- `examples/functions.yaml` — required Crossplane Functions
- `examples/configuration.yaml` — install manifest (OCI ref)
