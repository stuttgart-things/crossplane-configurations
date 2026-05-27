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
| k8s | [cloud-config](k8s/cloud-config/) | v0.5.2 | Renders cloud-init userdata as a Kubernetes Secret from a namespaced `CloudInit` XR, and manages the target namespace. | `ghcr.io/stuttgart-things/crossplane-configurations/cloud-config` |

## Requirements

- Crossplane `>= v2.1.3`
- Crossplane CLI (for local rendering)

Each Configuration declares its own provider and function dependencies in its `crossplane.yaml`.

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
