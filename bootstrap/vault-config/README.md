# vault-config

Crossplane v2 **namespaced** `VaultConfig` Configuration that deploys
Vault-adjacent secret tooling and the Kubernetes-auth bootstrap from a single
XR, via the **namespaced** provider-helm / provider-kubernetes APIs.

The Composition is a `function-kcl` wrapper around the
[`xplane-vault-config`](https://github.com/stuttgart-things/kcl/tree/main/crossplane/xplane-vault-config)
KCL module (pulled from OCI at render time).

- **XR group/kind:** `config.stuttgart-things.com/v1alpha1` / `VaultConfig`
- **Scope:** Namespaced
- **Resources generated** (namespaced APIs `helm.m.crossplane.io/v1beta1`,
  `kubernetes.m.crossplane.io/v1alpha1`):
  - Helm `Release` per enabled service — Secrets Store CSI Driver
    (`csiEnabled`), Vault Secrets Operator (`vsoEnabled`), External Secrets
    Operator (`esoEnabled`).
  - Per `k8sAuths` entry: a ServiceAccount, a SA-token Secret, a
    `system:auth-delegator` ClusterRoleBinding, and an Observe-only
    token-reader Object.

## Cluster preconditions

A `ClusterProviderConfig` for **both** providers, whose name matches the XR's
`spec.providerConfigName` (examples reference `in-cluster` — see
[`examples/cluster-provider-config.yaml`](examples/cluster-provider-config.yaml)):

- `helm.m.crossplane.io/v1beta1` ClusterProviderConfig — requires
  **provider-helm v1.0.0+** (the version that introduced the namespaced API).
- `kubernetes.m.crossplane.io/v1alpha1` ClusterProviderConfig.

## Install

```bash
# Providers + functions + provider configs
kubectl apply -f examples/provider.yaml
kubectl apply -f examples/functions.yaml
kubectl apply -f examples/cluster-provider-config.yaml

# XRD + Composition
kubectl apply -f apis/definition.yaml
kubectl apply -f apis/composition.yaml
```

> **Function names.** The Composition references the short Function names
> `function-kcl` / `function-auto-ready`. Do not rename — long-named
> duplicates collide in Crossplane's package lock on the fleet.

## Use

```bash
kubectl apply -f examples/xr.yaml

# Watch composed resources
kubectl get releases.helm.m.crossplane.io -A
kubectl get objects.kubernetes.m.crossplane.io -A
```

## Spec

| Field | Required | Default | Notes |
|---|---|---|---|
| `name` | ✅ | — | Configuration name (resource-name component). |
| `clusterName` | ✅ | `default` | Naming suffix; default for `providerConfigName`. |
| `providerConfigName` | | `clusterName` | helm/kubernetes (Cluster)ProviderConfig name. |
| `providerConfigKind` | | `ClusterProviderConfig` | Or `ProviderConfig`. |
| `namespace` | | XR namespace | Namespace for composed Release/Object resources. |
| `csiEnabled` | | `false` | Deploy Secrets Store CSI Driver. |
| `vsoEnabled` | | `false` | Deploy Vault Secrets Operator. |
| `esoEnabled` | | `false` | Deploy External Secrets Operator. |
| `isVcluster` | | `false` | Disable ESO probes (vcluster-friendly). |
| `csiChartVersion` | | `1.5.4` | |
| `vsoChartVersion` | | `1.0.1` | |
| `esoChartVersion` | | `0.11.3` | |
| `namespaceCsi` | | `secrets-store-csi` | CSI chart install namespace. |
| `namespaceVso` | | `vault-secrets-operator` | VSO chart install namespace. |
| `namespaceEso` | | `external-secrets` | ESO chart install namespace. |
| `k8sAuths[]` | | one auto-named entry | `{name, namespace}` per Vault auth bootstrap. |

## Migration notes

Migrated from the legacy `stuttgart-things/crossplane` repo. Changes vs the
old package:

- **Fully v2 now.** Composed resources moved from the legacy cluster-scoped
  `helm.crossplane.io/v1beta1` / `kubernetes.crossplane.io/v1alpha2` to the
  namespaced `helm.m.crossplane.io/v1beta1` /
  `kubernetes.m.crossplane.io/v1alpha1` (KCL module bumped `0.4.0` → `0.5.0`).
- **Providers switched to upstream contrib** (`xpkg.crossplane.io/crossplane-contrib/provider-helm`
  v1.0.0+, `provider-kubernetes` v1.2.0+) instead of the custom
  `ghcr.io/stuttgart-things` builds.
- **Short Function names** (`function-kcl`, not `crossplane-contrib-function-kcl`).
- `examples/claim.yaml` → `examples/xr.yaml` (+ `xr-min` / `xr-max`).
