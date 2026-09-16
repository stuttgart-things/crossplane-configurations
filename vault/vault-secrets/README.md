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
**If that Secret is deleted, a new value is generated and overwrites Vault** —
that is the rotation path, and also the footgun. Two consequences worth knowing:

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
