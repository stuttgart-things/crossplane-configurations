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
```

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
`crossplane.io/external-create-succeeded` annotation, a `Ready=True` condition, or
`status.atProvider.id`. An MR whose first write failed (a 403 on a missing ACL,
say) carries none of them, so it never locks the XR. A key that is only absent
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
- The XR owns the **whole** secret at a path: keys written by anything else are
  removed on the next reconcile.

## Cluster preconditions

1. provider-vault as `upbound-provider-vault` ([examples/provider.yaml](examples/provider.yaml)).
2. A `vault.m.upbound.io` ClusterProviderConfig (default `vault`) whose login may
   create/read/update/delete `<mount>/data/*` and `<mount>/metadata/*` — plus
   `sys/mounts/<mount>` if `mount.create`. Setup as in
   [vault-k8s-auth](../vault-k8s-auth/README.md#cluster-preconditions).
3. The `secretKeyRef` Secrets, in the XR's namespace.

## Examples

| File | Purpose |
|---|---|
| `xr-min.yaml` | one generated key — exercises every default |
| `xr.yaml` | literal + generated + secretKeyRef (needs `source-secret.yaml`) |
| `xr-max.yaml` | every field: composed mount, keepOnDelete, two paths, all charsets |

Render fixtures (source Secret, pre-existing data Secrets that pin generated
values) are in `tests/render/extra-resources/vault/vault-secrets/`.
