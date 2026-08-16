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
  `vault_role_id` and `vault_secret_id` (name from `spec.vaultTokenSecret`,
  default `vault` — see
  [`examples/vault-secret.yaml`](examples/vault-secret.yaml)). AppRole, **not**
  a static token, despite the field name: the composed module declares both as
  required variables and has no `vault_token` variable at all.
- The AppRole must carry **`sudo` on `sys/auth/*`** — enabling an auth mount is
  a root-protected operation. A wildcard policy without `sudo`
  (`path "*" { capabilities = ["create","read","update","delete","list"] }`)
  authenticates fine and then 403s on the mount, which reads like a broken
  package rather than a missing capability.
- That policy also needs **`delete` on `sys/auth/*`** if the XR is ever to be
  deleted. Without it `tofu destroy` 403s, the Workspace hangs in its finalizer
  and the Vault mount is orphaned — verified on kind3, 2026-08-13.

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

## Policies: created, not assumed

`tokenPolicies` **references** policies that already exist in Vault. `policies`
**creates** them, named `{clusterName}-{name}` and appended to `tokenPolicies`:

```yaml
k8sAuths:
  - name: eso
    boundServiceAccountNames: ["external-secrets"]
    boundServiceAccountNamespaces: ["external-secrets"]
    policies:
      - name: kv-own
        kvMount: clusters
        read: [own]
      - name: kv-cicd
        kvMount: clusters
        read: [cicd]
```

A **list**, because a cluster usually wants more than one: its own KV subtree,
plus whatever the role it plays grants it. Those are separate grants with
separate lifetimes, not one policy with a longer body.

### The body is built here, not supplied

`policies[]` took an HCL `rules` string until v0.3.0. It does not any more, and
the reason is not tidiness: a free-form body plus the ability to bind the result
to your own auth role is the right to grant yourself anything. A cluster could
declare `path "clusters/*" { capabilities = ["read"] }` and read every other
cluster's secrets. Restricting the policy **name** does not help — Vault cannot
restrict what a policy body contains, so the body has to come from the module
([#285](https://github.com/stuttgart-things/crossplane-configurations/issues/285)).

What you configure is the part that is safe to configure. The example above
becomes:

```hcl
path "clusters/data/mgmt-test1/*"     { capabilities = ["read"] }
path "clusters/metadata/mgmt-test1/*" { capabilities = ["read", "list"] }
path "clusters/data/cicd/*"           { capabilities = ["read"] }
path "clusters/metadata/cicd/*"       { capabilities = ["read", "list"] }
```

- `own` is the **only** value that resolves to a cluster name. Writing a
  neighbour's name there grants a subtree of that literal, which is not where
  anyone's secrets live.
- Subtree names are constrained to `^[a-z0-9][a-z0-9._-]*$`, so no slash, glob
  or traversal reaches the path the module anchors.
- Every path is anchored at `{kvMount}/data/` or `{kvMount}/metadata/`, every
  capability list is a literal. No input reaches `sys/`, `auth/` or `sudo`.

`metadata` gets `list` as well as `read` deliberately: without it `vault kv list`
and the external-secrets `find` selector fail on a tree the token can otherwise
read — which presents as a missing secret rather than a missing capability.

Note that creating policies at all needs an approle allowed to write them, and
the fleet's bootstrap approle is not (`sudo` on `sys/auth`, nothing on
`sys/policies/acl`). Against `infra.sthings-vsphere`, keep using `tokenPolicies`
against a policy created in the privileged Terraform.

They are created in the **same OpenTofu plan** as the role that names them, and
that is the whole point. Vault accepts a role referencing a policy that does not
exist — the token then simply has no permissions, with no error at creation,
nothing in the audit trail pointing at the cause, and a consumer that reports
403 as if it were a network problem. In one plan the role references the policy
resource, so tofu orders creation and reverses it on destroy.

The names are derived rather than typed, so they cannot be misspelled into that
same silence.

**The AppRole needs `sys/policies/acl/*` write** for this. Without it the
Workspace fails at apply with a permission error, which is at least loud.

## What the XR publishes

`status.share.auths[]`, lifted from the Workspace outputs:

```yaml
status:
  share:
    clusterName: mgmt-test1
    vaultAddr: https://vault.example
    ready: true
    auths:
      - name: eso
        workspace: mgmt-test1-eso-vault-auth
        ready: true
        mountPath: mgmt-test1-eso          # what Vault actually created
        role: eso
        policies: [mgmt-test1-kv-own, mgmt-test1-kv-cicd]
```

`mountPath`, `role` and `policies` are **absent** while the Workspace is still
applying — never published empty. A consumer resolving a substitution source to
`""` would send a blank mount path to Vault, and Vault answers that with a 403
that names nothing: the value looks supplied and is not. Absent is a state a
consumer can act on.

This is what lets external-secrets discover its own cluster's Vault mount rather
than being told it by hand.

## Development

### Render the Composition

```bash
crossplane render examples/xr.yaml apis/composition.yaml examples/functions.yaml --include-function-results
```

Or via the repo Taskfile:

```bash
task render
# or non-interactive:
CONFIG=bootstrap/vault-auth XR=xr.yaml task render
```

## Spec

| Field | Required | Default | Notes |
|---|---|---|---|
| `clusterName` | ✅ | — | Prefix for Vault backend paths (`<cluster>-<authName>`). |
| `vaultAddr` | ✅ | — | Vault server URL. |
| `skipTlsVerify` | | `true` | |
| `kubernetesHost` | | `https://kubernetes.default.svc:443` | Used when any `k8sAuths` entry has `backendConfig`. |
| `vaultTokenSecret` | | `vault` | Name of the Secret (same ns) holding `vault_role_id` + `vault_secret_id`. AppRole, not a token — the field name is historical. |
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
