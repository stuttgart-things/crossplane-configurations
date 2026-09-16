# vault-secrets (prototype)

Writes secrets into a Vault KV engine from a namespaced `VaultSecretSet` XR,
through [provider-vault](https://github.com/upbound/provider-vault). Values come
from Secrets next to the XR, from literals, or are generated once at random.

## Shape

```yaml
apiVersion: vault.stuttgart-things.com/v1alpha1
kind: VaultSecretSet
spec:
  mount: {path: apps, type: kv-v2}      # kv-v2 | kv; create: true composes the mount
  secrets:
    - path: demo/db
      data:
        - key: username
          value: demo                    # literal — non-sensitive only
        - key: password
          generate: {length: 32}         # alphanumeric | ascii | numeric
        - key: admin-token
          secretKeyRef: {name: demo-source, key: admin-token}   # XR namespace only
    - path: demo/ci-report
      claim:                             # own the entry, never its data
        customMetadata: {owner: ci}
```

Each `secrets` entry has exactly one of `data` (own the secret) or `claim` (own
the entry). One entry per path.

## How it works

Per `secrets` entry the Composition (inline `function-go-templating`):

1. reads every `secretKeyRef` Secret and its own previously composed data Secret
   (ExtraResources),
2. builds the JSON document and composes it as Secret `<xr>-<hash>` (key
   `data.json`) in the XR's namespace — `<hash>` is derived from `mount/path`, so
   reordering `secrets` does not re-create anything,
3. composes `kv.vault.m.upbound.io/SecretV2` (kv-v2) or `…/Secret` (kv v1) with
   `dataJsonSecretRef` pointing at it. No value ever appears in an MR spec.

### Generated values

Generated once, then read back from the composed data Secret on every reconcile.

**If that Secret is deleted after Vault was written, nothing is generated.** The
XR holds at `Ready=False` with `GeneratedValuesPreserved=False` naming the Secret
and the keys, `status.share.secrets[].blocked` lists them, and the KV MR is
paused (`crossplane.io/paused`) so it cannot write either. A consumer keeps
reading the value it already has instead of being handed a new password that the
running service never saw (#454).

Two ways out:

- **Restore** the Secret (from a backup, or re-create it with the value read from
  Vault). The hold lifts on the next reconcile.
- **Rotate** by setting `generate.regenerate: true` on the keys concerned — a new
  value is generated and written. Unset it again afterwards; while it is set, the
  next lost Secret rotates silently again.

"Vault was written" is decided from the KV MR, not from its mere existence: the
`crossplane.io/external-name` (set by the provider only after a successful apply),
a `Ready=True` condition, or `status.atProvider.id`. An MR whose first write failed
(a 403 on a missing ACL, say) carries none of them, so it never locks the XR.
**Not** `crossplane.io/external-create-succeeded`: provider-vault creates
asynchronously, and that annotation is set as soon as the async call returns —
also when the create then fails. Measured on u26-kind3 (#454 point 1). A key that is only absent
from an otherwise present data Secret is new, not lost, and is generated.

While held, the paused MR is not reconciled — including deletion. Deleting the XR
then waits until the hold is lifted by one of the two ways above.

The data Secret carries `app.kubernetes.io/managed-by: crossplane`,
`vault.stuttgart-things.com/role: data` and, when it holds generated values, the
annotations `vault.stuttgart-things.com/holds-generated-values` and
`…/on-delete`, so a cleanup can tell it apart.

Two more consequences worth knowing:

- Pointing a `VaultSecretSet` at a path that already holds a real credential and
  asking to `generate` that key replaces it on first reconcile.
- The composed Secret is the local copy of everything written, so anything in the
  same namespace allowed to read Secrets can read the values without Vault. Its
  name is published as `status.share.secrets[].dataSecret`.

### Claiming an entry

`claim` is for an entry that **something else writes** — the rancher join play
writes `kubeconfigs/<cluster>` and keeps adding versions — but whose lifetime
belongs to this XR. A `data` entry there would overwrite the kubeconfig on its
next reconcile, because it owns the whole secret.

A claim composes `generic.vault.m.upbound.io/Endpoint` on `<mount>/metadata/<path>`:

- **writes** `custom_metadata` only — whatever `customMetadata` sets, plus
  `managed-by: crossplane`, which is always set and marks the entry as owned;
- **never reads or writes the data**. `disableRead: true`: a GET on the metadata
  path returns versions and timestamps rather than what was written, so reading
  it back would diff forever;
- **on delete, DELETEs `<mount>/metadata/<path>`** — the metadata and *every
  version*, whoever wrote them. `keepOnDelete: true` skips that.

The entry does not need to exist yet: custom_metadata can be written before the
first version, so the claim can land before the writer runs.

kv-v2 only (kv v1 has no metadata endpoint) — rejected at admission otherwise.
This is the same call as the `kubeconfig-vault` OpenTofu Workspace in
`xplane-cluster`.

ACL a claim needs on `<mount>/metadata/<path>` — `create` + `update` to write
custom_metadata (the first OpenTofu version failed on exactly this,
stuttgart-things/stuttgart-things#2990) and `delete` for teardown; no `read`:

```hcl
path "kubeconfigs/metadata/+" { capabilities = ["create", "update", "delete"] }
```

Verified live for provider-vault on u26-kind3 (#454 point 1) with the real
`write-kubeconfigs` policy: create, update and delete.

### Missing sources

A secret is written **only once every source resolves** — a first write with a
key missing would publish a partial secret a consumer happily reads. If a source
disappears *after* the first write, the last written value is kept (not blanked)
and the XR condition `SourcesResolved=False` names the missing `secret/key`. In
both cases the XR is held at `Ready=False`.

### Deletion

- Secrets: deleted with the XR (kv-v2 soft-deletes the latest version, or
  destroys everything with `deleteAllVersions: true`). `keepOnDelete: true` drops
  the Delete management policy and leaves them in Vault.
- Mount (`create: true`): **never deleted** — no Delete policy. Removing a KV
  mount removes every secret in it, including ones this XR never wrote.
- Claims: `<mount>/metadata/<path>` is deleted with the XR — every version,
  including those written by someone else. `keepOnDelete: true` leaves it.
- A `data` entry owns the **whole** secret at its path: keys written by anything
  else are removed on the next reconcile. Use `claim` when that is not yours.

## Cluster preconditions

1. provider-vault as `upbound-provider-vault` ([examples/provider.yaml](examples/provider.yaml)).
2. A `vault.m.upbound.io` ClusterProviderConfig (default `vault`) with
   `skip_child_token: true` and a login whose policy covers the case — see
   [ACL per case](#acl-per-case). Setup as in
   [vault-k8s-auth](../vault-k8s-auth/README.md#cluster-preconditions).
3. The `secretKeyRef` Secrets, in the XR's namespace.

## ACL per case

**Measured** on u26-kind3 against infra.sthings-vsphere, 2026-09-16 (#454 point 1),
provider-vault 4.0.4 (terraform-provider-vault 5.9.0). "Verified" = that policy,
the XR Ready, and a clean delete confirmed by a read-only probe afterwards.

**`data`, `mount.create: false`, kv-v2** — the paths the provider calls:

| operation | calls | capability |
|---|---|---|
| create | `PUT <mount>/data/<p>` | `create` |
| observe | `GET <mount>/data/<p>`, `GET <mount>/metadata/<p>` | `read` on both |
| **update** (any value change, rotation) | `PUT <mount>/data/<p>`, **`PUT <mount>/metadata/<p>`** | `update` on data **and `create`/`update` on metadata** |
| delete, `deleteAllVersions: true` | `DELETE <mount>/metadata/<p>` | `delete` on metadata |

```hcl
path "observability/data/+"     { capabilities = ["create", "update", "read"] }
path "observability/metadata/+" { capabilities = ["create", "update", "read", "list", "delete"] }
```

Why metadata on update: `custom_metadata` is `Optional`+`Computed` in the
Terraform resource. Every observe fills it from Vault, and the shared
create/update function writes metadata whenever that field is set — so the
create gets by without it, every later update does not. With a policy that
lacks it (`write-observability-clusters` as of 2026-09-16) the update fails
**after** the data write has landed, and each retry adds a data version: 6 → 15
within minutes on u26-kind3. Create, observe and delete work with that policy;
changing a value does not.

**`claim`** — `write-kubeconfigs` as is: create, update, delete verified.

```hcl
path "kubeconfigs/metadata/+" { capabilities = ["create", "update", "delete"] }
```

**`mount.create: true`** — verified with a policy of `create`/`read`/`update` on
`sys/mounts/<mount>` and full CRUD below it. The mount is never deleted by this
XR, so no `delete` on `sys/mounts` is needed — and removing a test mount needs
someone else's credential.

### Value changes reach Vault immediately

A changed value lands in the data Secret, which the KV MR only references — its
own spec does not change, and without a nudge it would not be reconciled before
the provider's next poll. The MR therefore carries the data Secret's
`resourceVersion` as `vault.stuttgart-things.com/data-revision`: every change to
the data is an MR event. Measured: update requested within seconds.

## Examples

| File | Purpose |
|---|---|
| `xr-min.yaml` | one generated key — exercises every default |
| `xr.yaml` | literal + generated + secretKeyRef (needs `source-secret.yaml`), plus a bare `claim: {}` |
| `xr-max.yaml` | every field: composed mount, keepOnDelete, two data paths, all charsets, `regenerate`, a claim with `customMetadata` |

Render fixtures (source Secret, pre-existing data Secrets that pin generated
values) are in `tests/render/extra-resources/vault/vault-secrets/`.
