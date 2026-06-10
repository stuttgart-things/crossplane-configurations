# vault-pki-secrets

Crossplane v2 **namespaced** `VaultPkiSecret` Configuration that provisions the
**prerequisite Secrets** a Vault-backed cert-manager `ClusterIssuer` needs —
the CA bundle and the Vault token — onto a target cluster, via the
**namespaced** provider-kubernetes API.

The XR carries **no credentials**: it references two source Secrets
(`spec.tokenSecretRef` + `spec.caBundleSecretRef`, each name + namespace + key).
The token and CA bundle live in separate Secrets — matching how the platform
stores them, and how cert-manager's own `ClusterIssuer` references them
(separate `tokenSecretRef` + `caBundleSecretRef`). The Composition reads both
Secrets' values (they stay base64 the whole way — the token is never
materialised in plaintext) and copies them into the target cluster's
`cert-manager` namespace.

It deliberately does **not** create the `ClusterIssuer` itself. In the
stuttgart-things platform the `vault-pki` `ClusterIssuer` is owned by Argo CD:
the [`cert-manager-vault-pki`](https://github.com/stuttgart-things/argocd/blob/main/platforms/network/appset-cert-manager-vault-pki.yaml)
ApplicationSet fans the Helm chart
[`infra/cert-manager/vault-pki`](https://github.com/stuttgart-things/argocd/tree/main/infra/cert-manager/vault-pki)
onto every `network-platform` clusterbook cluster. That chart renders **only**
the `ClusterIssuer` and its `values.yaml` states the referenced Secrets *"must
already exist in the cert-manager namespace … provisioned out-of-band (e.g. by
Terraform vault-cert-issuer)."*

**This Configuration is that out-of-band step, done in Crossplane** — replacing
the legacy Terraform `vault.tf`. Argo keeps the issuer; Crossplane provides its
Secrets; the two never conflict.

- **XR group/kind:** `config.stuttgart-things.com/v1alpha1` / `VaultPkiSecret`
- **Scope:** Namespaced
- **Resources generated:** two `kubernetes.m.crossplane.io/v1alpha1` Objects,
  each pushing a `Secret` onto the target cluster's resolved namespace
  (default `cert-manager`):

  | Target Secret (`*SecretName`) | key | from | matches |
  |---|---|---|---|
  | `vault-pki-ca` | `ca.crt` | `spec.caBundleSecretRef` | ClusterIssuer `caBundleSecretRef` |
  | `vault-pki-token` | `token` | `spec.tokenSecretRef` | ClusterIssuer `auth.tokenSecretRef` |

## How values flow (no inlined credentials)

```
source Secrets (control-plane ns)
   tokenSecretRef → data[key]                ← base64
   caBundleSecretRef → data[key]             ← base64
        │  function-go-templating ExtraResources (read both)
        ▼
   provider-kubernetes Object × 2  ──push──▶  target cluster cert-manager ns
        │                                       vault-pki-ca   (data.ca.crt)
        │                                       vault-pki-token (data.token)
        ▼
   Argo-managed vault-pki ClusterIssuer reconciles to "Vault verified"
```

The Composition reads the source Secrets via function-go-templating's
`ExtraResources`. Because both source and target store base64 in `data`, the
values pass straight through — no decode step, and the raw token never appears
in the XR or in plaintext.

> **RBAC.** Reading the source Secrets happens through Crossplane's
> ExtraResources mechanism — the control-plane Crossplane ServiceAccount must be
> able to `get` them. The copied value does materialise (base64) in the composed
> provider-kubernetes `Object`, so RBAC-limit who can read `Objects` too. If
> either source Secret is missing, no target Secrets are produced and the XR
> stays NotReady.

## Per-environment defaults (EnvironmentConfig)

Target Secret names, keys, namespace and `providerConfigRef` default
**per-environment** from an EnvironmentConfig, selected by the config-scoped
label `vault-pki-secrets.config.stuttgart-things.com/environment` matched
against the XR's `spec.environmentConfig` (default `default`). Precedence:

```
XR spec  →  EnvironmentConfig data  →  built-in default
```

It mirrors the **clusterbook annotations** the appset reads on the target
cluster — keep them aligned (see
[`examples/environment-config.yaml`](examples/environment-config.yaml)):

| EnvironmentConfig `data` | clusterbook annotation / chart value |
|---|---|
| `namespace` | appset destination namespace (`cert-manager`) |
| `caSecretName` | chart `caBundleSecretRef.name` (`vault-pki-ca`) |
| `tokenSecretName` | `clusterbook.stuttgart-things.com/vault-token-secret` |
| `providerConfigRef` | the target cluster's provider-kubernetes CPC |

With it in place a `VaultPkiSecret` XR shrinks to just the two source refs.

## Cluster preconditions

- The **source Secrets** exist in the XR's namespace (or each ref's `namespace`)
  with the token and CA bundle — see
  [`examples/source-secrets.yaml`](examples/source-secrets.yaml).
- provider-kubernetes installed, with a `ClusterProviderConfig` whose name
  matches the resolved `providerConfigRef` (examples use `in-cluster` — see
  [`examples/cluster-provider-config.yaml`](examples/cluster-provider-config.yaml)).
  To push onto a **remote/managed** cluster, point `providerConfigRef` at a
  `ClusterProviderConfig` wired from that cluster's kubeconfig (e.g. the bridged
  kubeconfig from the `rancher-cluster` Configuration / clusterbook
  registration).
- provider-kubernetes' ServiceAccount must be able to create Secrets in the
  resolved namespace.

## Install

```bash
# Functions + provider (+ provider config + per-environment defaults)
kubectl apply -f examples/functions.yaml
kubectl apply -f examples/provider.yaml
kubectl apply -f examples/cluster-provider-config.yaml
kubectl apply -f examples/environment-config.yaml

# XRD + Composition
kubectl apply -f apis/definition.yaml
kubectl apply -f apis/composition.yaml
```

> **Function names.** The Composition references `function-environment-configs`,
> `function-go-templating` and `function-auto-ready` (short form). Do not
> rename — long-named duplicates collide in Crossplane's package lock on the
> fleet.

## Use

```bash
# 1. The source Secrets (token + CA bundle) in the XR's namespace
kubectl apply -f examples/source-secrets.yaml

# 2. The XR
kubectl apply -f examples/xr.yaml

# 3. Watch the composed Objects, then the Secrets on the target cluster
kubectl get objects.kubernetes.m.crossplane.io -A
kubectl --context <target> -n cert-manager get secret vault-pki-ca vault-pki-token
```

Once both Secrets exist, the Argo-managed `vault-pki` `ClusterIssuer` reconciles
to `Ready` / `Vault verified`.

## Development

### Render the Composition

The source Secrets (and optionally an EnvironmentConfig) are satisfied via
`--extra-resources`:

```bash
crossplane render examples/xr.yaml apis/composition.yaml examples/functions.yaml \
  --extra-resources examples/source-secrets.yaml --include-function-results
```

Or via the repo Taskfile:

```bash
task render
# or non-interactive:
CONFIG=bootstrap/vault-pki-secrets XR=xr.yaml task render
```

## Spec

| Field | Required | Default | Notes |
|---|---|---|---|
| `tokenSecretRef.name` | ✅ | — | Source Secret holding the Vault token. |
| `tokenSecretRef.namespace` | | XR namespace | Namespace of the source token Secret. |
| `tokenSecretRef.key` | | `token` | Key in the source Secret with the token. |
| `caBundleSecretRef.name` | ✅ | — | Source Secret holding the CA bundle. |
| `caBundleSecretRef.namespace` | | XR namespace | Namespace of the source CA Secret. |
| `caBundleSecretRef.key` | | `ca.crt` | Key in the source Secret with the CA bundle. |
| `environmentConfig` | | `default` | Selects the EnvironmentConfig supplying the defaults below. |
| `providerConfigRef` | | env → `in-cluster` | provider-kubernetes `(Cluster)ProviderConfig` for the target cluster. |
| `providerConfigKind` | | `ClusterProviderConfig` | Or `ProviderConfig`. |
| `namespace` | | env → `cert-manager` | Target namespace for both Secrets (cert-manager controller ns). |
| `caSecretName` | | env → `vault-pki-ca` | Target CA Secret (= issuer `caBundleSecretRef.name`). |
| `caSecretKey` | | env → `ca.crt` | Target CA key (= issuer `caBundleSecretRef.key`). |
| `tokenSecretName` | | env → `vault-pki-token` | Target token Secret (= issuer `auth.tokenSecretRef.name`). |
| `tokenSecretKey` | | env → `token` | Target token key (= issuer `auth.tokenSecretRef.key`). |

(_env_ = resolved from the selected EnvironmentConfig when present.)

## Notes

### Why not the `ClusterIssuer`?

`ClusterIssuer` is cluster-scoped and owned by the Argo CD ApplicationSet
(`selfHeal: true`). Rendering it here too would cause Crossplane and Argo to
fight over the same object. This package stops at the Secrets by design.
