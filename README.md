# crossplane-configurations

A collection of [Crossplane](https://www.crossplane.io/) Configurations maintained by stuttgart-things.

Each subdirectory is a self-contained Crossplane Configuration package — an XRD (`CompositeResourceDefinition`) plus its `Composition`(s) and example namespaced XRs — that can be built and pushed as an OCI package and consumed by a Crossplane control plane.

## Configurations

| Category | Name | Version | Description | OCI |
|---|---|---|---|---|
| bootstrap | [cni](bootstrap/cni/) | v0.1.0 | Installs a CNI on a target cluster from a namespaced `Cni` XR — [cilium](https://cilium.io/) today (the provider is an enum, so more can follow). Emits a `provider-helm` Release for the chart plus a `provider-kubernetes` Object that observes the target's `RemoteCluster` to gate the install until the derived `{clusterName}-helm` ClusterProviderConfig exists — rendered by `function-kcl` (KCL module [`xplane-cni`](https://github.com/stuttgart-things/kcl/tree/main/crossplane/xplane-cni)). Settings that are cluster-shape facts rather than free-form Helm values (`kubeProxyReplacement`, `k8sServiceHost`) are named fields with defaults derived from `clusterName`, because getting them wrong deadlocks the cluster silently; `spec.values` stays open for the rest. `status.ready` is what [platform](bootstrap/platform/) gates its other components on. | [`ghcr.io/stuttgart-things/crossplane-configurations/cni`](https://github.com/stuttgart-things/crossplane-configurations/pkgs/container/crossplane-configurations%2Fcni) |
| bootstrap | [flux-apps](bootstrap/flux-apps/) | v0.1.2 | Deploys application [Flux](https://fluxcd.io/) Kustomizations onto a target cluster from a namespaced `FluxApps` XR. One `provider-kubernetes` Object per `spec.apps` entry, each wrapping a `kustomize.toolkit.fluxcd.io/v1` Kustomization that references an existing Flux source (typically bootstrapped by `flux-init`) — rendered by `function-kcl` (KCL module [`xplane-flux-apps`](https://github.com/stuttgart-things/kcl/tree/main/crossplane/xplane-flux-apps)). The generic, data-driven counterpart to the [`flux-app-kustomizations`](https://github.com/stuttgart-things/kcl/tree/main/flux/flux-app-kustomizations) module — hardcoded tekton/crossplane Kustomizations become `spec.apps` entries, with reconcile defaults from a `flux-apps-defaults` EnvironmentConfig. | [`ghcr.io/stuttgart-things/crossplane-configurations/flux-apps`](https://github.com/stuttgart-things/crossplane-configurations/pkgs/container/crossplane-configurations%2Fflux-apps) |
| bootstrap | [flux-init](bootstrap/flux-init/) | v0.3.0 | Bootstraps [Flux](https://fluxcd.io/) on a target cluster from a namespaced `FluxInit` XR via the [flux-operator](https://github.com/controlplaneio-fluxcd/flux-operator). Emits a `provider-helm` Release (operator chart), a `provider-kubernetes` `FluxInstance` Object, a `Usage` for teardown ordering, and one `OCIRepository`/`GitRepository` Object per source — rendered by `function-kcl` (KCL module [`xplane-flux-init`](https://github.com/stuttgart-things/kcl/tree/main/crossplane/xplane-flux-init)), with chart/distribution defaults from a `flux-defaults` EnvironmentConfig. | [`ghcr.io/stuttgart-things/crossplane-configurations/flux-init`](https://github.com/stuttgart-things/crossplane-configurations/pkgs/container/crossplane-configurations%2Fflux-init) |
| bootstrap | [ip-reservation](bootstrap/ip-reservation/) | v0.1.0 | Reserves IP addresses (and optional wildcard DNS) from [clusterbook](https://github.com/stuttgart-things/clusterbook) for a target cluster, from a namespaced `XIPReservation` XR. Derives the network from the target's `RemoteCluster.internalNetworkKey` (`provider-kubeconfig`), then emits an `IPReservation` (`provider-clusterbook`) wrapped in a `provider-kubernetes` Object — rendered by `function-kcl` (KCL module [`xplane-ip-reservation`](https://github.com/stuttgart-things/kcl/tree/main/crossplane/xplane-ip-reservation)). | [`ghcr.io/stuttgart-things/crossplane-configurations/ip-reservation`](https://github.com/stuttgart-things/crossplane-configurations/pkgs/container/crossplane-configurations%2Fip-reservation) |
| bootstrap | [platform](bootstrap/platform/) | v0.3.1 | Umbrella Configuration that assembles a cluster's platform from the bootstrap building blocks. A namespaced `Platform` XR carries the shared cluster identity (clusterName, provider config refs, namespace) once and composes one child XR per enabled component — today [cni](bootstrap/cni/) (`Cni`, opt-in, and a gate on the rest: a cluster with no CNI schedules nothing, so a Helm install aimed at it times out rather than failing fast), [flux-init](bootstrap/flux-init/) (`FluxInit`) and [flux-apps](bootstrap/flux-apps/) (`FluxApps`, one Kustomization per enabled component of each `spec.apps` entry), with further Configurations to follow — rendered by `function-kcl`. The XR status publishes the resolved shared contract (`status.shared`, including the `clusterType` discovered by a child) plus per-component readiness and outputs. | [`ghcr.io/stuttgart-things/crossplane-configurations/platform`](https://github.com/stuttgart-things/crossplane-configurations/pkgs/container/crossplane-configurations%2Fplatform) |
| bootstrap | [vault-auth](bootstrap/vault-auth/) | v0.1.0 | Bootstraps Vault Kubernetes auth backends (and optional `backend_config`) from a namespaced `VaultK8sAuth` XR. One OpenTofu `Workspace` (`provider-opentofu`) per `k8sAuths` entry runs the Vault Terraform provider, rendered by `function-kcl` (KCL module [`xplane-vault-auth`](https://github.com/stuttgart-things/kcl/tree/main/crossplane/xplane-vault-auth)). | [`ghcr.io/stuttgart-things/crossplane-configurations/vault-auth`](https://github.com/stuttgart-things/crossplane-configurations/pkgs/container/crossplane-configurations%2Fvault-auth) |
| bootstrap | [vault-config](bootstrap/vault-config/) | v0.1.0 | Deploys Vault secret-tooling (Secrets Store CSI Driver, Vault Secrets Operator, External Secrets Operator) plus the ServiceAccount/token/RBAC bootstrap for Vault Kubernetes auth from a namespaced `VaultConfig` XR, via namespaced `provider-helm`/`provider-kubernetes` (KCL module [`xplane-vault-config`](https://github.com/stuttgart-things/kcl/tree/main/crossplane/xplane-vault-config)). | [`ghcr.io/stuttgart-things/crossplane-configurations/vault-config`](https://github.com/stuttgart-things/crossplane-configurations/pkgs/container/crossplane-configurations%2Fvault-config) |
| bootstrap | [vault-pki-secrets](bootstrap/vault-pki-secrets/) | v0.1.4 | Provisions the out-of-band prerequisite Secrets (CA bundle `vault-pki-ca` + Vault token `vault-pki-token`) that a Vault-backed cert-manager `ClusterIssuer` references, from a namespaced `VaultPkiSecret` XR. The XR references two source Secrets (no inlined credentials); the Composition reads their token + CA values (`function-go-templating` ExtraResources) and pushes both Secrets onto a target cluster via `provider-kubernetes`, ensuring the target namespace exists first (`Observe, Create` — never deletes it), with names/namespace/target defaulting per-environment from an `EnvironmentConfig`. The `ClusterIssuer` itself stays owned by the `cert-manager-vault-pki` Argo CD ApplicationSet. Replaces the legacy Terraform `vault-cert-issuer` step. | [`ghcr.io/stuttgart-things/crossplane-configurations/vault-pki-secrets`](https://github.com/stuttgart-things/crossplane-configurations/pkgs/container/crossplane-configurations%2Fvault-pki-secrets) |
| cicd | [ansible-run](cicd/ansible-run/) | v0.2.0 | Triggers an Ansible playbook on a target cluster by emitting a Tekton `PipelineRun` from a namespaced `AnsibleRun` XR. The PipelineRun is rendered by `function-kcl` (KCL module [`kcl-tekton-pr`](https://github.com/stuttgart-things/kcl-tekton-pr)) and applied via `provider-kubernetes`. | [`ghcr.io/stuttgart-things/crossplane-configurations/ansible-run`](https://github.com/stuttgart-things/crossplane-configurations/pkgs/container/crossplane-configurations%2Fansible-run) |
| cicd | [packer-build](cicd/packer-build/) | v0.3.0 | Builds a machine image with HashiCorp Packer on a target cluster by emitting a Tekton `PipelineRun` from a namespaced `PackerBuild` XR — the packer sibling of [ansible-run](cicd/ansible-run/). The PipelineRun is rendered by `function-kcl` (KCL module [`kcl-tekton-pr-packer`](https://github.com/stuttgart-things/kcl-tekton-pr-packer)) and applied via `provider-kubernetes`, with per-environment defaults from a `packer-build`-scoped `EnvironmentConfig`. Deals with TWO repos — the pipeline definition (stage-time) and the `.pkr.hcl` templates (stuttgart-things) — and a second `function-kcl` step lifts the live PipelineRun's `Succeeded` condition, timings, child TaskRuns and pipeline results (notably `template-name`, the template the build produced) onto the XR status, so with `deriveReadiness` (default) the XR is Ready only once the build actually succeeds. | [`ghcr.io/stuttgart-things/crossplane-configurations/packer-build`](https://github.com/stuttgart-things/crossplane-configurations/pkgs/container/crossplane-configurations%2Fpacker-build) |
| cicd | [packer-release](cicd/packer-release/) | v0.1.0 | Builds a machine image **and proves it works**: a namespaced `PackerRelease` XR composes a [packer-build](cicd/packer-build/) and, once that build succeeds and reports its `template-name`, a [vm-provision](machinery/vm-provision/) `VMProvision` that clones the freshly built template and runs Ansible against the clone. The test VM is composed only while the gate is open and is torn down by the Composition ceasing to render it — a failed test deliberately leaves it up for inspection. `Ready` therefore means built AND verified. | [`ghcr.io/stuttgart-things/crossplane-configurations/packer-release`](https://github.com/stuttgart-things/crossplane-configurations/pkgs/container/crossplane-configurations%2Fpacker-release) |
| k8s | [cloud-config](k8s/cloud-config/) | v0.5.4 | Renders cloud-init userdata as a Kubernetes Secret from a namespaced `CloudInit` XR, and manages the target namespace. | [`ghcr.io/stuttgart-things/crossplane-configurations/cloud-config`](https://github.com/stuttgart-things/crossplane-configurations/pkgs/container/crossplane-configurations%2Fcloud-config) |
| k8s | [namespace](k8s/namespace/) | v0.1.1 | Manages a Kubernetes Namespace from a namespaced `ManagedNamespace` XR, with optional labels/annotations, `ResourceQuota`, `LimitRange`, default-deny `NetworkPolicy` and `RoleBindings`. | [`ghcr.io/stuttgart-things/crossplane-configurations/namespace`](https://github.com/stuttgart-things/crossplane-configurations/pkgs/container/crossplane-configurations%2Fnamespace) |
| k8s | [volume-claim](k8s/volume-claim/) | v0.1.0 | Provisions a Kubernetes `PersistentVolumeClaim` from a namespaced `VolumeClaim` XR, with optional labels/annotations, label `selector` for pre-provisioned PVs and `dataSource` for snapshot restore / PVC cloning. Falls back to the cluster's default StorageClass when unset. | [`ghcr.io/stuttgart-things/crossplane-configurations/volume-claim`](https://github.com/stuttgart-things/crossplane-configurations/pkgs/container/crossplane-configurations%2Fvolume-claim) |
| machinery | [harvester-vm](machinery/harvester-vm/) | v0.1.0 | Provisions a Harvester / KubeVirt virtual machine from a namespaced `HarvesterVM` XR. Composes the `volume-claim` (root disk PVC) and `cloud-config` (cloud-init Secret) Configurations, then renders a KubeVirt `VirtualMachine` via `provider-kubernetes`; shared placement/image default from an `EnvironmentConfig`. | [`ghcr.io/stuttgart-things/crossplane-configurations/harvester-vm`](https://github.com/stuttgart-things/crossplane-configurations/pkgs/container/crossplane-configurations%2Fharvester-vm) |
| machinery | [proxmox-vm](machinery/proxmox-vm/) | v0.1.0 | Provisions a Proxmox VE virtual machine from a namespaced `ProxmoxVM` XR. An OpenTofu `Workspace` (`provider-opentofu`) runs the [`proxmox-vm`](https://github.com/stuttgart-things/proxmox-vm) Terraform module; shared placement/credentials default from an `EnvironmentConfig`. | [`ghcr.io/stuttgart-things/crossplane-configurations/proxmox-vm`](https://github.com/stuttgart-things/crossplane-configurations/pkgs/container/crossplane-configurations%2Fproxmox-vm) |
| machinery | [proxmoxvm](machinery/proxmoxvm/) | v0.1.0 | Provisions a Proxmox VE virtual machine from a namespaced `NativeProxmoxVM` XR using the NATIVE [`provider-proxmox-bpg`](https://github.com/valkiriaaquatica/provider-proxmox-bpg) managed `EnvironmentVM` resource (no Terraform/OpenTofu; built on `bpg/terraform-provider-proxmox`). The native-provider sibling of `proxmox-vm`: clones by template VMID and bootstraps via cloud-init (`initialization`); shared placement defaults from an `EnvironmentConfig`, rendered by `function-kcl`. | [`ghcr.io/stuttgart-things/crossplane-configurations/proxmoxvm`](https://github.com/stuttgart-things/crossplane-configurations/pkgs/container/crossplane-configurations%2Fproxmoxvm) |
| machinery | [rancher-cluster](machinery/rancher-cluster/) | v0.7.0 | Provisions a generic (custom-node) Rancher cluster from a namespaced `RancherCluster` XR (k3s/rke2 `provisioning.cattle.io` Cluster), then wires the downstream cluster's Rancher-managed kubeconfig Secret into a `provider-kubernetes` `ClusterProviderConfig` and bootstraps a namespace on it as proof — all via `function-go-templating`. Optionally registers the cluster in Argo CD via [clusterbook-operator](https://github.com/stuttgart-things/clusterbook-operator) (assembles a proxy kubeconfig with a dedicated non-expiring token), and optionally reserves a clusterbook IP/DNS (`spec.argocd.reservation`) to stamp the `allocation-ip` anchor the network-platform ApplicationSets require. When `network-platform/cert-manager-vault-pki` is enabled it also inlines the [vault-pki-secrets](bootstrap/vault-pki-secrets/) logic — pushing the Vault token + CA Secrets onto the downstream cluster's `cert-manager` namespace so the `vault-pki` ClusterIssuer has its prerequisites. | [`ghcr.io/stuttgart-things/crossplane-configurations/rancher-cluster`](https://github.com/stuttgart-things/crossplane-configurations/pkgs/container/crossplane-configurations%2Francher-cluster) |
| machinery | [virtual-machine](machinery/virtual-machine/) | v0.1.0 | High-level VM API: a namespaced `XVirtualMachine` XR asks for a VM by t-shirt `size`, `provider` (vsphere/proxmox) and `environment`, resolves the datacenter topology from an `EnvironmentConfig`, and emits a `VMProvision` (via `function-kcl`). | [`ghcr.io/stuttgart-things/crossplane-configurations/virtual-machine`](https://github.com/stuttgart-things/crossplane-configurations/pkgs/container/crossplane-configurations%2Fvirtual-machine) |
| machinery | [vm-provision](machinery/vm-provision/) | v0.1.0 | Unified VM router: a namespaced `VMProvision` XR routes to a `VsphereVM` or `ProxmoxVM` (per `spec.provider`) and optionally chains an `AnsibleRun`, composing the `vsphere-vm`, `proxmox-vm` and `ansible-run` Configurations (via `function-kcl`). | [`ghcr.io/stuttgart-things/crossplane-configurations/vm-provision`](https://github.com/stuttgart-things/crossplane-configurations/pkgs/container/crossplane-configurations%2Fvm-provision) |
| machinery | [vsphere-vm](machinery/vsphere-vm/) | v0.1.0 | Provisions a VMware vSphere virtual machine from a namespaced `VsphereVM` XR. An OpenTofu `Workspace` (`provider-opentofu`) runs the [`vsphere-vm`](https://github.com/stuttgart-things/vsphere-vm) Terraform module; shared placement/credentials default from an `EnvironmentConfig`. | [`ghcr.io/stuttgart-things/crossplane-configurations/vsphere-vm`](https://github.com/stuttgart-things/crossplane-configurations/pkgs/container/crossplane-configurations%2Fvsphere-vm) |

## Tasks

Common workflows are wrapped in [`Taskfile.yaml`](Taskfile.yaml):

| Task | Purpose |
|---|---|
| `task render` | Render a Configuration's example XR locally (no cluster needed) |
| `task check` | Verify a target cluster satisfies a Configuration's dependencies |
| `task apply-dev` | Apply a Configuration and/or example XR from local files (dev install) |
| `task pre-commit` | Run the repo's pre-commit hooks (file hygiene, YAML, workflows, secrets) |
| `task verify` | Offline package + example XR validation (same dagger module the CI workflow runs) |
| `task push` | Build and push a Configuration as an OCI package to `ghcr.io` (bumps `meta.crossplane.io/version`) |
| `task push-dev` | Build and push to [ttl.sh](https://ttl.sh) for quick try-on-cluster dev iteration (anonymous, public, ephemeral — default 1 h TTL); no version bump, no canonical-registry touch |

Each interactive task uses [`gum`](https://github.com/charmbracelet/gum) pickers by default. For CI, scripts and agents — anywhere without a TTY — every picker can be bypassed with an env var (see _Non-interactive usage_ below).

<details>
<summary><b>Non-interactive usage</b></summary>

Every `gum choose` / `gum confirm` in the Taskfile has an env-var bypass. When the env var is set, the picker is skipped and its value is validated; when unset, the picker runs as before. Mix and match — set only what you need to silence.

| Task | Env vars (bypass) |
|---|---|
| `render`     | `CONFIG=k8s/<name>`, `XR=<file>` (path, or basename resolved against `<CONFIG>/examples/`) |
| `check`      | `KUBECONFIG=<path>`, `CONFIG=k8s/<name>` |
| `apply-dev`  | `KUBECONFIG=<path>`, `CONFIG=k8s/<name>`, `WHAT=configuration\|xr\|both`, `XR=<file>`, `YES=1` (skip confirm) |
| `verify`     | `CONFIG=k8s/<name>` |
| `push`       | `CONFIG=k8s/<name>`, `BUMP=patch\|minor\|major\|custom\|vX.Y.Z` (`custom` requires `VERSION=vX.Y.Z`), `YES=1` (skip confirm), plus the existing `REGISTRY` / `USERNAME` / `PASSWORD_ENV` / `PREFIX` |
| `push-dev`   | `CONFIG=k8s/<name>`, `TTL=1h\|24h\|…` (default `1h`), `TTL_PREFIX=<scope>` (default `$(whoami)/crossplane-configurations`) |

Examples:

```bash
# fully non-interactive render
CONFIG=k8s/namespace XR=xr-min.yaml task render

# verify a specific Configuration (no picker)
CONFIG=k8s/namespace task verify

# dev-install everything for one Configuration onto a chosen cluster
KUBECONFIG=~/.kube/lab.yaml CONFIG=k8s/namespace WHAT=both XR=xr.yaml YES=1 task apply-dev

# tag and push without prompts (token comes from $GITHUB_TOKEN by default)
CONFIG=k8s/namespace BUMP=patch YES=1 task push

# pin a specific version
CONFIG=k8s/namespace BUMP=custom VERSION=v0.2.0 YES=1 task push

# dev iteration: push to ttl.sh, install on a test cluster, throw away
CONFIG=k8s/volume-claim task push-dev   # → ttl.sh/<you>/crossplane-configurations/volume-claim:1h
# task prints a kubectl-apply'able Configuration manifest pointing at the dev URL
```

`push` will refuse to prompt for a password when `YES=1` is set and `$PASSWORD_ENV` (default `GITHUB_TOKEN`) is unset — set the token in the environment for unattended use.

</details>

<details>
<summary><b>Dev iteration with ttl.sh</b></summary>

`task push-dev` publishes the Configuration to [ttl.sh](https://ttl.sh) — an anonymous, public, ephemeral OCI registry where images auto-delete after a configurable duration (the *tag* is the TTL). Use it to try a package on a test cluster without going through the canonical `ghcr.io` release path (which carries version-bump ceremony and the [private-by-default visibility flip](https://github.blog/changelog/2023-08-22/github-packages-set-visibility-for-container-packages/) on first publish per package).

Workflow:

```bash
# 1. push the current working tree to ttl.sh
CONFIG=k8s/volume-claim task push-dev

#    ✓ pushed ttl.sh/<you>/crossplane-configurations/volume-claim:1h
#
#    Install on a test cluster:
#    kubectl apply -f - <<MANIFEST
#    ---
#    apiVersion: pkg.crossplane.io/v1
#    kind: Configuration
#    metadata:
#      name: volume-claim-dev   # ← suffix avoids clashing with a canonical install
#    spec:
#      package: ttl.sh/<you>/crossplane-configurations/volume-claim:1h
#    MANIFEST

# 2. copy-paste-apply that manifest on the test cluster, exercise XRs, repeat
# 3. when happy, run `task push` for the canonical ghcr.io release
```

The dagger module is the same one `task push` uses; the only differences are `--registry ttl.sh`, an anonymous credential, no version-bump and no visibility check.

</details>

<details>
<summary><b>Repository layout</b></summary>

Configurations are grouped by the kind of resource they manage:

```
crossplane-configurations/
├── bootstrap/                    # cluster bootstrap (secrets/auth tooling)
│   ├── vault-auth/               # Vault Kubernetes auth backends (OpenTofu)
│   └── vault-config/             # Vault secret-tooling (CSI/VSO/ESO) + auth bootstrap (Helm/Kubernetes)
├── cicd/                         # CI/CD workflow resources
│   └── ansible-run/              # Tekton PipelineRun wrapping an Ansible playbook
├── k8s/                          # Kubernetes-native resources
│   ├── cloud-config/             # cloud-init userdata Secrets for VMs
│   ├── namespace/                # managed Namespaces (+ quota/limits/netpol/RBAC)
│   └── volume-claim/             # PersistentVolumeClaims (default-SC fallback, snapshot restore)
└── machinery/                    # VM provisioning
    ├── harvester-vm/            # Harvester / KubeVirt VMs (EnvironmentConfig defaults)
    ├── proxmox-vm/               # Proxmox VE VMs (OpenTofu, EnvironmentConfig defaults)
    ├── rancher-cluster/         # generic Rancher clusters + auto-wired downstream kubeconfig
    ├── virtual-machine/         # high-level VM API (size/provider/env → VMProvision)
    ├── vm-provision/            # VM router (VMProvision → Vsphere/Proxmox + Ansible)
    └── vsphere-vm/               # VMware vSphere VMs (OpenTofu, EnvironmentConfig defaults)
```

Each Configuration follows the same structure:

```
<configuration>/
├── crossplane.yaml               # Package metadata + dependencies
├── apis/
│   ├── definition.yaml           # XRD (CompositeResourceDefinition)
│   └── composition.yaml          # Composition pipeline
└── examples/
    ├── xr.yaml                   # Example namespaced XR (Crossplane v2)
    ├── configuration.yaml        # Configuration manifest (OCI ref)
    └── functions.yaml            # Required Crossplane Functions
```

</details>

<details>
<summary><b>Requirements</b></summary>

**Target cluster**

- Crossplane `>= v2.1.3`

**Dev environment**

- [`crossplane` CLI](https://docs.crossplane.io/latest/cli/) — local rendering, packaging
- [`task`](https://taskfile.dev/) — runs the workflows in [`Taskfile.yaml`](Taskfile.yaml)
- [`gum`](https://github.com/charmbracelet/gum) — interactive prompts inside the tasks
- [`dagger`](https://dagger.io/) — used by `task pre-commit` (lint hooks), `task verify` (offline validation), and `task push` (OCI build & publish)
- `kubectl`, `yq` — invoked by `task check` / `task apply-dev`
- A reachable Kubernetes cluster — required for `task check`, `task apply-dev`, and end-to-end iteration

</details>

<details>
<summary><b>Dependencies used across this repo</b></summary>

Each Configuration declares its own provider and function dependencies in its `crossplane.yaml`. Examples currently in use:

| Type | Package | Version constraint |
|---|---|---|
| Provider | `xpkg.crossplane.io/crossplane-contrib/provider-kubernetes` | `>=v1.2.0,<v2.0.0` |
| Function | `xpkg.crossplane.io/crossplane-contrib/function-go-templating` | `>=v0.12.1,<v0.13.0` |
| Function | `xpkg.crossplane.io/crossplane-contrib/function-kcl` | `>=v0.12.0,<v0.13.0` |
| Function | `xpkg.crossplane.io/crossplane-contrib/function-auto-ready` | `>=v0.6.5,<v0.7.0` |

</details>

<details>
<summary><b>Local rendering</b></summary>

Test a Composition without applying it to a cluster:

```bash
cd k8s/cloud-config
crossplane render examples/xr.yaml apis/composition.yaml examples/functions.yaml --include-function-results
```

Or via Taskfile (auto-discovers Configurations and example XRs):

```bash
task render
```

</details>

<details>
<summary><b>Pre-commit hooks (local + CI)</b></summary>

[`.pre-commit-config.yaml`](.pre-commit-config.yaml) runs the standard file-hygiene hooks (trailing whitespace, EOF, large files, merge conflicts, symlinks, YAML, private keys), `shellcheck`, `check-github-workflows`, and Yelp's `detect-secrets` (gated by [`.secrets.baseline`](.secrets.baseline) — a snapshot of known not-a-secret matches that the hook ignores).

**Local**

```bash
task pre-commit
```

Wraps the [`dagger/linting`](https://github.com/stuttgart-things/dagger/tree/main/linting) module's `run-pre-commit`, so the host doesn't need Python, the hook environments, or even `pre-commit` itself installed.

**Regenerating the baseline** after intentionally introducing a string `detect-secrets` flags (or after upstream rules change):

```bash
docker run --rm -v "$PWD:/work" -w /work python:3.12-slim sh -c \
  "apt-get update -qq && apt-get install -qq -y git >/dev/null && \
   git config --global --add safe.directory /work && \
   pip install --quiet detect-secrets && detect-secrets scan" > .secrets.baseline
```

**CI**

The `pre-commit` job in [`.github/workflows/verify.yaml`](.github/workflows/verify.yaml) calls the reusable [`call-pre-commit.yaml`](https://github.com/stuttgart-things/github-workflow-templates/blob/main/.github/workflows/call-pre-commit.yaml). Findings are printed to the job log, attached as an artifact, and added to the run summary. The job fails on any hook reporting `Failed`.

</details>

<details>
<summary><b>Verification (local + CI)</b></summary>

Offline, cluster-free validation of a Configuration package. Both the local task and the GitHub workflow drive the same [`dagger/crossplane`](https://github.com/stuttgart-things/dagger/tree/main/crossplane) `verify` function, so a CI failure reproduces with one command.

Per Configuration, `verify` runs four checks:

1. **`crossplane xpkg build`** — the package itself is well-formed.
2. **Layer 1: XR ↔ XRD** — each `examples/xr*.yaml` validates against the Configuration's own XRD (via `kubeconform`).
3. **Layer 2: Object wrapper** — every rendered `kubernetes.m.crossplane.io/v1alpha1` Object validates against the `provider-kubernetes` CRD schema (version pinned per Configuration in `crossplane.yaml`).
4. **Layer 3: embedded manifest** — each `spec.forProvider.manifest` validates against the built-in Kubernetes schemas.

**Local**

```bash
task verify
# pick a Configuration in the prompt
```

The task sniffs the `provider-kubernetes` floor from the Configuration's `dependsOn` and passes it through as `--provider-kubernetes-version`, matching what CI does. Override the dagger module pin with `DAGGER_MOD=...`.

**CI**

`.github/workflows/verify.yaml` runs on every pull request and every push to `main`. A `discover` job lists all Configuration directories, intersects them with the diff against the base SHA, and matrix-invokes the reusable [`call-crossplane-verify.yaml`](https://github.com/stuttgart-things/github-workflow-templates/blob/main/.github/workflows/call-crossplane-verify.yaml) workflow — so a PR only re-verifies the Configurations it actually touched. `fail-fast: false`, so one Configuration's regressions don't mask another's.

</details>

<details>
<summary><b>PR preview packages (ttl.sh)</b></summary>

On every pull request, the `preview` job in [`.github/workflows/verify.yaml`](.github/workflows/verify.yaml) reuses the same `discover` matrix to build each **changed** Configuration and `task push-dev` it to the public, ephemeral [ttl.sh](https://ttl.sh) registry. ttl.sh is anonymous, so the job runs **without secrets** and is safe for fork PRs.

The preview is published to `ttl.sh/stuttgart-things/crossplane-configurations-pr<PR>-<sha7>/<name>:24h` — `24h` is ttl.sh's maximum TTL (long enough to outlive a review cycle), and the PR-number/short-SHA path segment keeps each commit's preview distinct and traceable.

A second `preview-comment` job aggregates every changed config's install manifest into one sticky PR comment (updated in place via a hidden marker), so a reviewer can copy-paste a `kind: Configuration` pointing at the preview and try it on any Crossplane v2 cluster. Unchanged configs are skipped; the image auto-expires 24 h after the last push.

This is **Phase 1** of the [CI/CD roadmap](docs/ci-cd-roadmap.md).

</details>

## License

[Apache-2.0](LICENSE)
