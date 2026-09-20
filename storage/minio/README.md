# minio

A Crossplane v2 **cluster-scoped** `MinioBucket` Configuration: one XR per
bucket, composing the bucket, its policies and the users carrying them as
native [provider-minio](https://github.com/vshn/provider-minio) managed
resources.

It replaces the `mc` commands that created every bucket in the fleet by hand —
the schmetterpause archive, the packer artifacts, the OpenTofu state bucket
([#482](https://github.com/stuttgart-things/crossplane-configurations/issues/482)).

- **XR group/kind:** `storage.stuttgart-things.com/v1alpha1` / `MinioBucket`
- **Scope:** Cluster (see below)
- **Pipeline:** `function-go-templating` → `function-auto-ready`

## What it composes

| From | Composed | Named |
|---|---|---|
| the XR | `Bucket` | `spec.bucketName`, default the XR name |
| each `spec.access` entry | `Policy` | `<bucket>-<entry>` |
| each entry with `user: true` (default) | `User` | `<bucket>-<entry>`, keys into a Secret |

A user's Secret holds `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` — the
shape an S3 backend, CNPG's barman plugin and rclone all read.

```yaml
apiVersion: storage.stuttgart-things.com/v1alpha1
kind: MinioBucket
metadata:
  name: tofu-state
spec:
  access:
    - name: writer
```

That is the whole XR for "a bucket with one reader-writer": bucket `tofu-state`,
policy and user `tofu-state-writer`, and the keys in
`crossplane-system/tofu-state-writer`.

## Deleting

`spec.deletionPolicy` decides what deleting the XR does to the **bucket**:

| | |
|---|---|
| `Orphan` (default) | the bucket stays on the instance |
| `DeleteIfEmpty` | an empty bucket is removed, a full one fails the delete |
| `DeleteAll` | bucket and every object in it are removed |

Policies and users are deleted in every case: they are access, not data.
Verified on a lab MinIO — deleting a default XR left the bucket and removed its
user.

## Why cluster-scoped

Every kind provider-minio serves is cluster-scoped, and Crossplane v2 refuses
to compose a cluster-scoped resource from a namespaced XR:

```
cannot compose resources: cannot apply cluster scoped composed resource
"bucket" (a Bucket named ) for a namespaced composite resource
```

A bucket is instance-wide anyway — its name is unique per MinIO, not per
namespace — so a namespaced XR would promise an isolation the object does not
have. The reason is recorded on the XRD in
`stuttgart-things.com/cluster-scope-reason`, as the repo requires. The user keys
still land wherever `access[].connectionSecret.namespace` says (default
`crossplane-system`).

## Two provider behaviours worth knowing

Both found on a lab MinIO with provider-minio v0.4.5 and Crossplane 2.3.3.

**A policy is named after its Kubernetes object, not its external-name.**
`Policy` has no name field in `forProvider`, and the provider ignores
`crossplane.io/external-name`. A composed policy would otherwise land on the
instance as `tofu-state-b6ad350b4b7b` — the generated resource name — and the
user referencing it is refused with `policy not found`. The Composition
therefore sets `metadata.name` explicitly on every composed object.

**An existing bucket cannot be adopted.** Against a bucket that already exists
the provider fails with `observe failed: bucket already exists, try changing
bucket name` rather than observing it. So the buckets created by hand in the
fleet cannot simply be put under an XR: either they are recreated under a new
name and the data is moved, or they stay outside Crossplane. Worth checking
again on a newer provider release before anyone plans a migration.

## Ordering

A `User` is composed only once its `Policy` is Ready. provider-minio validates
in an admission webhook that the policy exists **on the instance**, so applying
bucket, policy and user together is refused once with `policy not found`.
Crossplane would retry until it passes; composing in order keeps that out of
the events.

## status.share

Absent until the bucket is Ready, never half-filled:

```yaml
status:
  share:
    bucket: schmetterpause-cnpg
    region: us-east-1
    policies: [schmetterpause-cnpg-backup, schmetterpause-cnpg-auditor]
    users:
      - name: schmetterpause-cnpg-backup
        connectionSecret: schmetterpause/schmetterpause-cnpg-backup
```

## Cluster preconditions

- **provider-minio**, installed as `vshn-provider-minio` — the CR name the
  package manager derives from this package's `dependsOn`. See
  `examples/provider.yaml` for why the name matters.
- **A `minio.crossplane.io/v1` ProviderConfig** (cluster-scoped) whose Secret
  carries the MinIO admin keys as `AWS_ACCESS_KEY_ID` /
  `AWS_SECRET_ACCESS_KEY`. In the fleet that Secret comes from Vault through
  ESO (entry `minio-secrets`), not from a file. See
  `examples/provider-config.yaml`.

## Putting the keys into Vault

This Configuration writes the user keys to a Kubernetes Secret. To serve them
from Vault like every other app credential, copy that Secret into a Vault entry
with a `VaultSecretSet` (`vault/vault-secrets`), whose `data[].secretKeyRef`
reads exactly such a Secret. The bucket XR stays the owner of the credential;
Vault becomes the distribution path.
