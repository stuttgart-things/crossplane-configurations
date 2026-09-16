# vault-k8s-auth (prototype)

Vault Kubernetes auth backends from a namespaced `VaultK8sAuth` XR, composed as
**native [provider-vault](https://github.com/upbound/provider-vault) managed
resources** — the counterpart of [bootstrap/vault-auth](../../bootstrap/vault-auth/),
which runs the Vault Terraform provider inside one OpenTofu `Workspace` per auth.

The XR lives in its own API group, `vault.stuttgart-things.com`, so both
Configurations can be installed side by side while this one is being proven.

## What gets composed

Per `spec.k8sAuths` entry, all named after `{clusterName}-{name}`:

| MR | Vault object | When |
|---|---|---|
| `auth.vault.m.upbound.io/Backend` `…-backend` | kubernetes auth mount `{clusterName}-{name}` | always |
| `vault.vault.m.upbound.io/Policy` `…-policy-{p}` | policy `{clusterName}-{p}` | per `policies` entry |
| `kubernetes.vault.m.upbound.io/AuthBackendRole` `…-role` | `auth/{mount}/role/{name}` | once the mount **and every created policy** are Ready — then sticky |
| `kubernetes.vault.m.upbound.io/AuthBackendConfig` `…-config` | `auth/{mount}/config` | with `backendConfig`, once the mount is Ready and the CA is readable — then sticky |

What that buys over the OpenTofu variant: drift is detected per Vault object,
`crossplane resource trace` shows the objects themselves, there is no Terraform
state in the cluster, and a failing policy does not hold the role hostage in an
opaque `tofu apply` log.

### Differences to bootstrap/vault-auth

- **Connection lives in the ProviderConfig.** `vaultTokenSecret`,
  `vaultTokenSecretKey`, `skipTlsVerify` and the OpenTofu `providerConfigName`
  are gone; `providerConfigRef` names a `vault.m.upbound.io` (Cluster)ProviderConfig.
  `vaultAddr` is optional and only republished in `status.share`.
- **`backendConfig.secretNamespace` is gone.** The reviewer Secret must sit in
  the XR's namespace: the JWT reaches the provider as a *local* secret reference,
  and reading a Secret from another namespace would let whoever may create this
  XR aim Vault at a token they cannot read themselves.
- **The ordering is explicit.** OpenTofu ordered policy → role through a
  resource reference; here the role is simply not composed until the policies are
  Ready. Same reason: Vault accepts a role naming a missing policy and the token
  then silently has no permissions.
- **Nothing is adopted.** The MRs carry no `crossplane.io/external-name`, so a
  mount that already exists (e.g. created by bootstrap/vault-auth for the same
  cluster name) makes the Backend fail with *path is already in use* rather than
  taking it over. Migration by adoption is the next step, not part of the prototype.

## Readiness and status

- `status.share.auths[]`: `name`, `ready`, and — only once Ready — `mountPath`,
  `role`, `policies`. Absent, never empty (same contract as bootstrap/vault-auth).
- A `backendConfig` whose reviewer Secret is missing holds the Backend at
  `Ready=False` (otherwise the XR would report Ready on the mount alone) and sets
  the XR condition `ReviewerSecretReady=False` naming the Secret and key.

## Cluster preconditions

1. provider-vault, installed as **`upbound-provider-vault`** — the name the package
   manager derives from `dependsOn`, so a later OCI install does not add a second
   Lock node ([examples/provider.yaml](examples/provider.yaml)).
2. A `vault.m.upbound.io` **ClusterProviderConfig** (default name `vault`), see
   [examples/cluster-provider-config.yaml](examples/cluster-provider-config.yaml).
   The AppRole behind it needs `sudo` + create/update/delete on `sys/auth/*`
   (mounting an auth backend is root-protected; without `delete` teardown
   orphans the mount) and create/update/delete on `sys/policies/acl/*` and `auth/*`.

   The credentials Secret can be derived from an existing `terraform.tfvars`-shaped
   AppRole Secret without printing anything. `kubectl create`, **not** `apply`:
   `apply` records the whole Secret, credentials included, in the
   `last-applied-configuration` annotation.

   ```bash
   kubectl get secret vault-approle -n default -o jsonpath='{.data.terraform\.tfvars}' | base64 -d \
     | sed -nE 's/^[[:space:]]*(vault_role_id|vault_secret_id)[[:space:]]*=[[:space:]]*"([^"]*)".*/\1 \2/p' \
     | jq -Rn 'reduce (inputs | split(" ")) as [$k, $v] ({}; .[$k] = $v)
               | {auth_login: {path: "auth/approle/login",
                               parameters: {role_id: .vault_role_id, secret_id: .vault_secret_id}}}' \
     | kubectl create secret generic vault-provider-creds -n crossplane-system --from-file=credentials=/dev/stdin
   ```
3. For `backendConfig`: the reviewer ServiceAccount-token Secret (`ca.crt`,
   `token`) in the XR's namespace.

## Examples

| File | Purpose |
|---|---|
| `xr-min.yaml` | required fields only — exercises every default |
| `xr.yaml` | one auth referencing policies, one creating its own |
| `xr-max.yaml` | every field, incl. `backendConfig` (render fixture: `tests/render/extra-resources/vault/vault-k8s-auth/`) |

```bash
KUBECONFIG=~/.kube/kind3 CONFIG=vault/vault-k8s-auth WHAT=both XR=xr.yaml YES=1 task apply-dev
crossplane resource trace vaultk8sauth.vault.stuttgart-things.com vault-k8s-auth -n default
```
