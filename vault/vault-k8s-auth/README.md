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
| `vault.vault.m.upbound.io/Policy` `…-policy-{p}` | policy `xp-{clusterName}-{p}` | per `policies` entry |
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
- **Nothing is adopted unless asked.** Without `adoption.enabled` the MRs carry
  no `crossplane.io/external-name`, so a mount that already exists (e.g. created
  by bootstrap/vault-auth for the same cluster name) makes the Backend fail with
  *path is already in use* rather than being taken over silently. See
  [Adopting existing Vault objects](#adopting-existing-vault-objects).

## Adopting existing Vault objects

`spec.adoption.enabled: true` takes over the mount, role, config and policies an
earlier owner created for the same `clusterName` — the way an existing cluster
moves off bootstrap/vault-auth without a rebuild (#454).

```yaml
spec:
  clusterName: homerun2-test1
  adoption:
    enabled: true
    acceptDifferences: false   # default
    deleteOnRemoval: false     # default — see "Migrating" below
```

**How it proceeds, per auth:**

1. Every MR gets `crossplane.io/external-name` — the Terraform import ID:
   `<mount>` for the Backend, `auth/<mount>/role/<name>`, `auth/<mount>/config`,
   and the policy name — and starts with `managementPolicies: [Observe]`.
   Nothing is written.
2. Once everything the auth composes has been read — present and Ready, or
   confirmed missing — it is **compared with the spec**:
   - Backend `type`
   - Policy body (whitespace-trimmed)
   - Role `boundServiceAccountNames`, `boundServiceAccountNamespaces`,
     `tokenPolicies` (order-insensitive), `tokenTtl`
   - Config `kubernetesHost`, `kubernetesCaCert`, `disableIssValidation`,
     `disableLocalCaJwt`. The reviewer JWT is not compared — Vault never returns it.
3. **No differences:** the auth is handed over **as a whole** to
   `[Observe, Create, Update, LateInitialize]` — without `Delete`. Objects
   confirmed missing (a policy the spec adds, a role not yet created) are created
   now; they are listed beforehand in `status.share.auths[].createOnHandOver`.
4. **Differences:** the auth stays Observe-only, the XR is `Ready=False`, and
   `AdoptionComplete=False` (reason `DifferencesFound`) names each one, e.g.
   `eso: role tokenPolicies observed ["read-cicd"], spec ["read-cicd","x-kv-own"]`.
   Fix the spec to match Vault, or set `acceptDifferences: true` to hand over
   anyway, i.e. **overwrite Vault** with the spec on the next reconcile.

A mount that does not exist at all holds the adoption too — adopting is a claim
that something is there; for a fresh cluster leave `adoption` off.

Hand-over is sticky: once any MR of an auth carries `Create`, the auth stays
managed, and a later drift is simply corrected rather than re-held.

`status.share.auths[].adoption` is `observing` | `differences` | `handedOver`;
`AdoptionComplete=True` (reason `HandedOver`) says whether deletion is included.

External names stay on an MR once set, also after `adoption.enabled` is turned
off: removing one would make the provider treat the object as new and try to
create it again.

### Migrating from bootstrap/vault-auth

During the migration the same Vault objects have **two owners**: the OpenTofu
Workspace with its tfstate, and this XR. Neither may delete while the other
still holds them — a `tofu destroy` from the old side removes the mount under the
new one, and a delete from the new side removes it under the old one. Hence
`deleteOnRemoval: false` until the old side has let go.

The order:

1. **Keep the specs identical.** Apply the `VaultK8sAuth` (this group) with
   `adoption.enabled: true` for the same `clusterName` and auths. Until step 3,
   both sides reconcile: the Workspace re-applies its values on every run, this
   XR its own — any difference between the two flaps back and forth. The
   comparison in step 2 of the adoption is what shows there is none.
2. **Wait for `AdoptionComplete=True`.**
3. **Release the OpenTofu side without a destroy.** Deleting the old XR (or the
   Platform that composes it) deletes its Workspace, and a Workspace deleted
   with its default policies runs `tofu destroy`. Either
   - orphan it: `kubectl delete vaultk8sauths.config.stuttgart-things.com <name> --cascade=orphan`,
     then patch each `<cluster>-<auth>-vault-auth` Workspace to
     `managementPolicies: ["Observe"]` and delete it — without `Delete` in its
     policies nothing is destroyed; or
   - `tofu state rm` every address in the Workspace's state first, so the
     destroy has nothing to remove.
   Policies created by bootstrap/vault-auth are named `{clusterName}-{name}`; this
   Configuration names them `xp-{clusterName}-{name}`. On adoption the new names
   appear as missing and are created at hand-over (listed in
   `createOnHandOver`), and the role's `tokenPolicies` show as a difference until
   the spec and Vault agree. The old policies are left in Vault — delete them by
   hand once no role names them. On u26-kind3 no vault-auth XR creates policies
   (`policies: {}` throughout), so there is nothing to rename there.
4. **Set `deleteOnRemoval: true`.** From here this XR is the only owner, and
   deleting it deletes the Vault objects as a fresh one would.

> Not yet exercised against a real Vault — the provider-vault credential on
> u26-kind3 is still missing the ACL for this (#454 point 1). The hand-over logic
> itself is covered by the `xr-max` golden and render variants; step 3 is the
> part to walk through by hand on a test cluster first.

## Who may grant what

Decided in #454 (point 5 and its [addendum](https://github.com/stuttgart-things/crossplane-configurations/issues/454#issuecomment-5700426267)).
Recorded here because it is a boundary, not a detail.

`VaultK8sAuth` builds every policy body itself; free-form HCL is never accepted,
so no XR can grant `sys/`, `auth/` or `sudo`. What remains:

- `kvMount` is free — except for the denied mounts below.
- `read` entries other than `own` are literals. With a `<mount>/<cluster>` layout
  `read: [homerun2-test1]` *is* that cluster's subtree, so one cluster's XR can
  grant itself a neighbour's secrets. **Accepted.**
- `tokenPolicies` attaches **any existing policy** by name. A Kubernetes auth role
  has no `allowed_policies`: whoever may write `auth/<mount>/role/*` may assign
  policies it does not hold itself. This is the remaining path to admin
  credentials and is **not** closed in the package — see the last row.

The boundary is layered:

| | prevents | enforced by |
|---|---|---|
| **Fixed prefix `xp-`.** Every created policy is `xp-{clusterName}-{name}`; the AppRole behind the ClusterProviderConfig gets `sys/policies/acl/xp-*` only | overwriting a hand-maintained policy (`pki-issue`, `read-homerun2-pr`, …) — whatever the XR says | Vault |
| **Deny-list on `policies[].kvMount`** (CEL, at admission): `kubeconfigs`, `ssh`, every mount starting with `cicd-`, and `sys`, `auth`, `identity`, `cubbyhole` | creating a policy that reads admin credentials — a role reading `kubeconfigs` is cluster-admin on every cluster of the fleet. *Impossible*, not merely forbidden | Kubernetes API server |
| **RBAC on `vaultk8sauths.vault.stuttgart-things.com`.** Create/update only for the platform/machinery identity | a tenant granting itself read on someone else's mount or subtree, **or attaching an existing admin policy through `tokenPolicies`** | Kubernetes |
| **Platform: orderers name secret stores, never policies** (#454 addendum, option 2). The eso role's `tokenPolicies` are derived from `ClusterStack.spec.secretStores` via a fixed store → policy map in the catalog, and `xplane-cluster` strips `tokenPolicies` from the `spec.platform` passthrough | the `tokenPolicies` path for everyone who can order a ClusterStack | the Platform (`xplane-cluster`); not built yet |

**About the deny-list.**

- The names are **infra.sthings-vsphere's**. vault-vsphere.tiab.labda has its own
  mounts, which a package-wide list cannot know.
- A new admin mount **needs a package release** — except `cicd-*`, which is a
  prefix so the next CI mount is covered. CEL in an XRD cannot read an
  EnvironmentConfig, so the list cannot be per environment.
- Exact names, not prefixes, for everything but `cicd-`: `kubeconfigs2` or
  `kubeconfigs-labda` would pass. Name such a mount `cicd-…` or add it here.
- Deliberately **not** denied: the app sets clusters legitimately read through ESO
  (`homerun2-pr`, `homerun2-cd`, `schmetterpause`, `observability`, `minio`,
  `zitadel`) and `clusters`. `machinery-catalog-locator` (a GitHub App key with repo
  write) is the first candidate if its reach grows.
- `sys`, `auth`, `identity`, `cubbyhole` grant nothing to a KV-shaped body today;
  they are on the list because a deny-list cannot predict what a later Vault
  serves there.

Not chosen, and why:

- **A per-cluster prefix** (`sys/policies/acl/<cluster>-*`) cannot be enforced: one
  AppRole serves all clusters, and Vault globs only at the end of a path.
- **An allow-list of mounts/subtrees** (EnvironmentConfig, enforced by the
  Composition) was proposed and declined in favour of the deny-list: it would
  have to enumerate every legitimate app set per environment. If one is added
  later it restricts mounts only, not subtrees.
- **A deny-list on `tokenPolicies`** would have to know every dangerous policy by
  name and keep up with new ones; the Platform-side derivation above closes that
  path where it matters instead.

**RBAC as it stands.** Crossplane creates `crossplane:composite:vaultk8sauths…:aggregate-to-edit`
for this XRD, labelled into Crossplane's **own** `crossplane-edit` /
`crossplane-admin` — not into Kubernetes' built-in `edit` / `admin`. So a
namespace `edit` binding does *not* allow creating a `VaultK8sAuth`; a binding to
`crossplane-edit` or `crossplane-admin` does, for **every** XR kind at once. On
u26-kind3 (2026-09-16) the only such binding is `crossplane-admin` →
`Group:crossplane:masters`. Keep it that way: grant tenants roles that list their
XR kinds explicitly rather than `crossplane-edit`.

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
   orphans the mount), create/update/delete on **`sys/policies/acl/xp-*`** — not
   `*`, see [Who may grant what](#who-may-grant-what) — and on `auth/*`.

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
| `xr-max.yaml` | every field, incl. `backendConfig` and `adoption` — fixtures in `tests/render/extra-resources/vault/vault-k8s-auth/`: one auth that matches and is handed over, one held on differences |

```bash
KUBECONFIG=~/.kube/kind3 CONFIG=vault/vault-k8s-auth WHAT=both XR=xr.yaml YES=1 task apply-dev
crossplane resource trace vaultk8sauth.vault.stuttgart-things.com vault-k8s-auth -n default
```
