# crossplane-configurations

A collection of [Crossplane](https://www.crossplane.io/) Configurations maintained by stuttgart-things.

Each subdirectory is a self-contained Crossplane Configuration package — an XRD (`CompositeResourceDefinition`) plus its `Composition`(s) and example namespaced XRs — that can be built and pushed as an OCI package and consumed by a Crossplane control plane.

## Layout

Configurations are grouped by the kind of resource they manage.

```
crossplane-configurations/
└── k8s/                          # Kubernetes-native resources
    └── cloud-config/             # cloud-init userdata Secrets for VMs
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

## Configurations

| Category | Name | Version | Description | OCI |
|---|---|---|---|---|
| k8s | [cloud-config](k8s/cloud-config/) | v0.5.2 | Renders cloud-init userdata as a Kubernetes Secret from a namespaced `CloudInit` XR, and manages the target namespace. | [`ghcr.io/stuttgart-things/crossplane-configurations/cloud-config`](https://github.com/stuttgart-things/crossplane-configurations/pkgs/container/crossplane-configurations%2Fcloud-config) |

## Requirements

**Target cluster**

- Crossplane `>= v2.1.3`

**Dev environment**

- [`crossplane` CLI](https://docs.crossplane.io/latest/cli/) — local rendering, packaging
- [`task`](https://taskfile.dev/) — runs the workflows in [`Taskfile.yaml`](Taskfile.yaml)
- [`gum`](https://github.com/charmbracelet/gum) — interactive prompts inside the tasks
- [`dagger`](https://dagger.io/) — used by `task push` to build and publish the OCI package
- `kubectl`, `yq` — invoked by `task check` / `task apply-dev`
- A reachable Kubernetes cluster — required for `task check`, `task apply-dev`, and end-to-end iteration

Each Configuration declares its own provider and function dependencies in its `crossplane.yaml`. Examples of packages used across this repo:

| Type | Package | Version constraint |
|---|---|---|
| Provider | `xpkg.crossplane.io/crossplane-contrib/provider-kubernetes` | `>=v1.2.0,<v2.0.0` |
| Function | `xpkg.crossplane.io/crossplane-contrib/function-go-templating` | `>=v0.12.1,<v0.13.0` |
| Function | `xpkg.crossplane.io/crossplane-contrib/function-auto-ready` | `>=v0.6.5,<v0.7.0` |

## Tasks

Common workflows are wrapped in [`Taskfile.yaml`](Taskfile.yaml):

| Task | Purpose |
|---|---|
| `task render` | Render a Configuration's example XR locally (no cluster needed) |
| `task check` | Verify a target cluster satisfies a Configuration's dependencies |
| `task apply-dev` | Apply a Configuration and/or example XR from local files (dev install) |
| `task push` | Build and push a Configuration as an OCI package (bumps `meta.crossplane.io/version`) |

## Local rendering

Test a Composition without applying it to a cluster:

```bash
cd k8s/cloud-config
crossplane render examples/xr.yaml apis/composition.yaml examples/functions.yaml --include-function-results
```

## License

[Apache-2.0](LICENSE)
