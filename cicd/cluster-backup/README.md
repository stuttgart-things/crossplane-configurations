# cluster-backup

Exports the state a Crossplane cluster **cannot** rebuild from git, encrypts it to an age public key, and pushes it to an OCI registry on a schedule.

## Why this exists

A machinery cluster is already reconstructible: the bootstrap play rebuilds it, git holds the XRs, Configurations are pinned to OCI versions, and secrets come from the sops-git flow. Two things are not in git and cannot be recomputed:

- **`crossplane.io/external-name` annotations.** That annotation is the only link between a managed resource and the real object — for a vSphere VM it is the BIOS UUID. Lose it and Crossplane does not adopt the existing VM; it builds a second one.
- **OpenTofu state.** With the fleet's `backend "kubernetes"` (see the `ClusterProviderConfig`'s `configuration` field) the state lives *only* as `tfstate-*` Secrets in `crossplane-system`. It is in no external backend, so losing the cluster orphans every resource those Workspaces created — and a rebuild then collides with the objects it cannot see.

Together that is a few hundred kilobytes. That is why this is a CronJob pushing an OCI artifact, not a cluster backup product.

## What it composes

A namespaced `ClusterBackup` XR renders, through `provider-kubernetes`:

| resource | purpose |
|---|---|
| `ServiceAccount` | identity for the job |
| `ClusterRole` + binding | `get,list` on `spec.resourceGroups` **only** |
| `Role` + binding in `spec.tfStateNamespace` | `get,list` on Secrets **in that one namespace** |
| `CronJob` | kubectl → tar → `sops --encrypt` → `oras push` |

The Secret permission is deliberately a namespaced Role rather than part of the ClusterRole: this job must never be able to read every Secret on the cluster.

Artifacts land at `<registry>/<clusterName>:<timestamp>` plus a moving `:latest`.

## Encryption is not optional

`spec.ageRecipient` is an age **public** key and is XRD-required. Two consequences, both intentional:

- The job can encrypt but **never decrypt**. Compromising it yields neither plaintext nor a usable key. Restoring requires the private half, which stays with the sops-secrets-operator.
- There is no default. There is no safe answer to "who may decrypt this", and the bundle contains OpenTofu state — which holds credentials in plaintext by construction. Registry privacy is access control, not encryption.

The Composition rejects an `ageRecipient` that does not start with `age1`, so a private `AGE-SECRET-KEY` pasted into the field fails at render instead of being shipped to a registry.

## What this is *not*

Not disaster recovery on its own. **An OCI registry has no object-lock and no lifecycle policy: a token that can push can also delete.** Treat this as the convenient primary copy and keep an immutable offsite one (S3 with versioning + object lock).

Restore is deliberately not automated — see below.

## Restore notes (read before you need them)

Restoring external-names and tfstate into a live cluster can duplicate or destroy real infrastructure. The order matters:

1. **Pause first.** Apply `crossplane.io/paused: "true"` to the managed resources *before* restoring anything. Otherwise the providers race the restore and may reconcile against half-applied state.
2. Restore ProviderConfigs and credentials **before** the managed resources, or the MRs error out and can strand in `crossplane.io/external-create-pending`.
3. Verify `crossplane.io/external-name` on every restored MR against the real infrastructure before unpausing. A wrong or missing external-name means Crossplane creates a duplicate; a `deletionPolicy: Delete` on a mis-restored MR can delete the real thing.
4. Unpause.

## Usage

```yaml
apiVersion: resources.stuttgart-things.com/v1alpha1
kind: ClusterBackup
metadata:
  name: cluster-backup
  namespace: default
spec:
  clusterName: kind1
  ageRecipient: age1...              # PUBLIC key
  registry: ghcr.io/stuttgart-things/backups
  registryCredentialsSecretName: backup-registry
  # note the nested path — ghcr.io/stuttgart-things/machineshop does not exist
  image: ghcr.io/stuttgart-things/github.com/stuttgart-things/machineshop:v2.6.13
```

See [`examples/xr-min.yaml`](examples/xr-min.yaml) (EnvironmentConfig-driven), [`examples/xr.yaml`](examples/xr.yaml) and [`examples/xr-max.yaml`](examples/xr-max.yaml).

### Keeping `resourceKinds` and `resourceGroups` in sync

`resourceKinds` is what gets exported; `resourceGroups` is what the ClusterRole grants. They are separate because a ClusterRole cannot express Crossplane's `managed` category. A kind whose group is missing from `resourceGroups` would be silently skipped at runtime, so the Composition **asserts** the consistency at render time and names the offending groups. Add a provider → update both.

## Cluster preconditions

1. A provider-kubernetes `ClusterProviderConfig` named by `spec.crossplaneProviderConfig`.
2. `spec.namespace` exists (use the `namespace` Configuration, or an existing one).
3. A `kubernetes.io/dockerconfigjson` Secret named by `spec.registryCredentialsSecretName` in that namespace, with **push access to `spec.registry` only**.
4. An image providing `kubectl`, `sops`, `oras`, `sha256sum` and a POSIX shell.
   **This does not exist in the stuttgart-things catalog yet** (checked 2026-07-24): `machineshop` and `sthings-workflow` carry `kubectl`, none carries `oras`. Either add `oras` to one of them, or split the CronJob into initContainers using the upstream `kubectl` / `getsops/sops` / `oras` images — the latter needs no image maintenance at all. Note the registry path is nested: `ghcr.io/stuttgart-things/github.com/stuttgart-things/machineshop`, not `ghcr.io/stuttgart-things/machineshop`.

## Notes

- `status.lastSuccessfulTime` — not XR readiness — is what tells you a backup actually ran. The XR is Ready once the CronJob object exists, which says nothing about whether a push ever succeeded.
- The job refuses to push an empty bundle. A missing CRD or missing RBAC logs a `WARN` per kind and continues; if *nothing* was exported it exits non-zero rather than publish a reassuring but empty artifact.
- `concurrencyPolicy` defaults to `Forbid`: two exports racing produce two artifacts of the same state and waste registry tags.

## How often, and how this avoids drowning in artifacts

The backed-up data only changes when a managed resource is created/deleted or an OpenTofu Workspace applies. That is rare and event-driven — between changes, an hourly export produces 24 byte-identical bundles a day. So the schedule is not what should bound artifact count; two other things do.

**`skipUnchanged` (default `true`)** compares the sha256 of the *plaintext* bundle against the previous one and exits without pushing when they match. The digest travels as an OCI annotation on the manifest and is read back from `:latest` with a single request. It must be the plaintext digest: sops picks a fresh data key every run, so the encrypted artifact differs every time even when nothing changed, and content-addressing it would never dedup.

With that in place, **the number of artifacts tracks infrastructure changes, not schedule ticks** — so a frequent schedule is cheap. Hourly is a reasonable default; the shipped default is `17 * * * *` (off the hour, to avoid stacking with everything else that runs at :00).

**`tagStrategy` (default `rolling`)** bounds the tags:

| strategy | tags | keeps |
|---|---|---|
| `rolling` | `h00`..`h23`, `d01`..`d31`, `latest` — **max 56** | hourly for a day, daily for a month |
| `timestamp` | one per push, forever | everything, unbounded |

**The honest caveat:** overwriting a tag does not delete the old manifest, it makes it *untagged*, and ghcr keeps untagged versions. So `rolling` bounds **tags**, not **storage**. Actually reclaiming space needs a pruner that deletes package versions through the GitHub API.

Deliberately not built into this CronJob: pruning requires `delete:packages`, and putting a token that can delete backups on the cluster being backed up recreates the exact weakness called out above ("a token that can push can also delete"). Run the pruner as a scheduled GitHub Action with its own scoped token, outside the cluster.
