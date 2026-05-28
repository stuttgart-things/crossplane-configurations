# crossplane-configurations

A collection of [Crossplane](https://www.crossplane.io/) Configurations maintained by stuttgart-things.

Each subdirectory is a self-contained Crossplane Configuration package — an XRD (`CompositeResourceDefinition`) plus its `Composition`(s) and example namespaced XRs — that can be built and pushed as an OCI package and consumed by a Crossplane control plane.

## Configurations

| Category | Name | Version | Description | OCI |
|---|---|---|---|---|
| k8s | [cloud-config](k8s/cloud-config/) | v0.5.4 | Renders cloud-init userdata as a Kubernetes Secret from a namespaced `CloudInit` XR, and manages the target namespace. | [`ghcr.io/stuttgart-things/crossplane-configurations/cloud-config`](https://github.com/stuttgart-things/crossplane-configurations/pkgs/container/crossplane-configurations%2Fcloud-config) |
| k8s | [namespace](k8s/namespace/) | v0.1.1 | Manages a Kubernetes Namespace from a namespaced `ManagedNamespace` XR, with optional labels/annotations, `ResourceQuota`, `LimitRange`, default-deny `NetworkPolicy` and `RoleBindings`. | [`ghcr.io/stuttgart-things/crossplane-configurations/namespace`](https://github.com/stuttgart-things/crossplane-configurations/pkgs/container/crossplane-configurations%2Fnamespace) |
| k8s | [volume-claim](k8s/volume-claim/) | v0.1.0 | Provisions a Kubernetes `PersistentVolumeClaim` from a namespaced `VolumeClaim` XR, with optional labels/annotations, label `selector` for pre-provisioned PVs and `dataSource` for snapshot restore / PVC cloning. Falls back to the cluster's default StorageClass when unset. | [`ghcr.io/stuttgart-things/crossplane-configurations/volume-claim`](https://github.com/stuttgart-things/crossplane-configurations/pkgs/container/crossplane-configurations%2Fvolume-claim) |

## Tasks

Common workflows are wrapped in [`Taskfile.yaml`](Taskfile.yaml):

| Task | Purpose |
|---|---|
| `task render` | Render a Configuration's example XR locally (no cluster needed) |
| `task check` | Verify a target cluster satisfies a Configuration's dependencies |
| `task apply-dev` | Apply a Configuration and/or example XR from local files (dev install) |
| `task pre-commit` | Run the repo's pre-commit hooks (file hygiene, YAML, workflows, secrets) |
| `task verify` | Offline package + example XR validation (same dagger module the CI workflow runs) |
| `task push` | Build and push a Configuration as an OCI package (bumps `meta.crossplane.io/version`) |

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
```

`push` will refuse to prompt for a password when `YES=1` is set and `$PASSWORD_ENV` (default `GITHUB_TOKEN`) is unset — set the token in the environment for unattended use.

</details>

<details>
<summary><b>Repository layout</b></summary>

Configurations are grouped by the kind of resource they manage:

```
crossplane-configurations/
└── k8s/                          # Kubernetes-native resources
    ├── cloud-config/             # cloud-init userdata Secrets for VMs
    ├── namespace/                # managed Namespaces (+ quota/limits/netpol/RBAC)
    └── volume-claim/             # PersistentVolumeClaims (default-SC fallback, snapshot restore)
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

## License

[Apache-2.0](LICENSE)
