# vault-auth

Crossplane v2 **namespaced** `VaultK8sAuth` Configuration that creates Vault
Kubernetes auth backends (plus optional `backend_config`) via the **OpenTofu**
provider.

The Composition is a thin `function-kcl` wrapper around the
[`xplane-vault-auth`](https://github.com/stuttgart-things/kcl/tree/main/crossplane/xplane-vault-auth)
KCL module (pulled from OCI at render time).

- **XR group/kind:** `config.stuttgart-things.com/v1alpha1` / `VaultK8sAuth`
- **Scope:** Namespaced
- **Resources generated:** one `opentofu.m.upbound.io/v1beta1` `Workspace` per `k8sAuths` entry

## Cluster preconditions

- An OpenTofu `ClusterProviderConfig` whose name matches the XR's
  `spec.providerConfigName` (examples reference `default` — see
  [`examples/cluster-provider-config.yaml`](examples/cluster-provider-config.yaml)).
- A Secret co-located in the XR's namespace holding `terraform.tfvars` with
  `vault_token = "hvs...."` (name from `spec.vaultTokenSecret`, default
  `vault` — see [`examples/vault-secret.yaml`](examples/vault-secret.yaml)).

## Install

```bash
# Functions + provider (+ runtime config + provider config)
kubectl apply -f examples/functions.yaml
kubectl apply -f examples/provider.yaml
kubectl apply -f examples/deployment-runtime-config.yaml
kubectl apply -f examples/cluster-provider-config.yaml

# XRD + Composition
kubectl apply -f apis/definition.yaml
kubectl apply -f apis/composition.yaml
```

> **Function names.** The Composition references `function-kcl` and
> `function-auto-ready` (short form). Target clusters in the stuttgart-things
> fleet install Functions under these short names; long-named duplicates
> collide in Crossplane's package lock — do not rename.

## Use

1. Create the Vault token Secret in the namespace you'll use for the
   `VaultK8sAuth`:

   ```bash
   kubectl apply -f examples/vault-secret.yaml
   ```

2. Apply an XR:

   ```bash
   kubectl apply -f examples/xr.yaml
   ```

3. Watch the generated Workspaces reconcile:

   ```bash
   kubectl get workspaces.opentofu.m.upbound.io -A
   ```

## Spec

| Field | Required | Default | Notes |
|---|---|---|---|
| `clusterName` | ✅ | — | Prefix for Vault backend paths (`<cluster>-<authName>`). |
| `vaultAddr` | ✅ | — | Vault server URL. |
| `skipTlsVerify` | | `true` | |
| `kubernetesHost` | | `https://kubernetes.default.svc:443` | Used when any `k8sAuths` entry has `backendConfig`. |
| `vaultTokenSecret` | | `vault` | Name of the Secret (same ns) holding `vault_token`. |
| `vaultTokenSecretKey` | | `terraform.tfvars` | |
| `providerConfigName` | | `default` | OpenTofu `(Cluster)ProviderConfig` name. |
| `providerConfigKind` | | `ClusterProviderConfig` | Or `ProviderConfig`. |
| `k8sAuths[]` | ✅ | — | See below. |

### `k8sAuths[]`

| Field | Required | Default |
|---|---|---|
| `name` | ✅ | — |
| `tokenPolicies` | ✅ | — |
| `tokenTtl` | | `3600` |
| `boundServiceAccountNames` | | `["default"]` |
| `boundServiceAccountNamespaces` | | `["default"]` |
| `backendConfig` | | (unset) |

### `backendConfig`

If set, the generated Workspace additionally renders a
`vault_kubernetes_auth_backend_config` resource that reads the CA cert and
token reviewer JWT from a Kubernetes Secret (typically a ServiceAccount token
secret).

| Field | Required | Default |
|---|---|---|
| `secretName` | ✅ | — |
| `secretNamespace` | | XR namespace |
| `caCertKey` | | `ca.crt` |
| `tokenKey` | | `token` |
| `disableIssValidation` | | `true` |
| `disableLocalCaJwt` | | `true` |

#### `backendConfig` prerequisites

The module's HCL uses the Terraform `kubernetes` provider's
`data "kubernetes_secret"` block to read the CA cert and token reviewer JWT
at `tofu apply` time. This means:

1. **The referenced Secret must already exist** before the `VaultK8sAuth` XR
   is applied. If it's missing, `tofu plan` fails with `Attempt to index null
   value` (the data source returns a `null` `.data` map for non-existent
   Secrets).
2. **It must be a ServiceAccount token Secret** — since Kubernetes 1.24 these
   are no longer auto-created. Make one explicitly:

   ```yaml
   apiVersion: v1
   kind: Secret
   metadata:
     name: vault-dev
     namespace: default
     annotations:
       kubernetes.io/service-account.name: vault-auth-reviewer
   type: kubernetes.io/service-account-token
   ```

   (plus the `vault-auth-reviewer` ServiceAccount and a `system:auth-delegator`
   ClusterRoleBinding for the token-review call to succeed).
3. **The opentofu provider's pod SA** needs RBAC to read that Secret.

For a minimal smoke test, leave `backendConfig` unset on every entry — the
Workspace still creates the Vault auth backend and the role.
