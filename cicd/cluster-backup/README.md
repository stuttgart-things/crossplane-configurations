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
| `CronJob` | three ordered stages: export → encrypt → push |

The Secret permission is deliberately a namespaced Role rather than part of the ClusterRole: this job must never be able to read every Secret on the cluster.

### Why initContainers, and one image per stage

The three stages are **initContainers** plus one final container, not three containers: a Pod's containers run in *parallel*, while these are strictly ordered and hand files to each other through a shared `emptyDir` at `/work`. initContainers are the only shape that guarantees the order.

Each stage runs on the image published by the tool's own project, rather than one image carrying all three:

| stage | image (default) | needs a shell? |
|---|---|---|
| `export` (init) | `alpine/k8s` — kubectl | yes |
| `encrypt` (init) | `ghcr.io/getsops/sops:*-alpine` | **no** — the entrypoint *is* sops, so the stage is a pure argv |
| `push` | `ghcr.io/oras-project/oras` | yes — provided by busybox; its `/bin/oras` entrypoint is overridden |

That avoids maintaining and patching a fourth image just to hold three binaries. Override any of them per XR via `spec.images.{kubectl,sops,oras}`.

Registry credentials are mounted on the **push stage only** — the export and encrypt stages have no business holding them. The Pod sets `fsGroup: 65532` so the non-root stages can write to the shared volume.

Artifacts land at `<registry>/<clusterName>:<timestamp>` plus a moving `:latest`.

## Encryption is not optional

`spec.ageRecipient` is an age **public** key and is XRD-required. Two consequences, both intentional:

- The job can encrypt but **never decrypt**. Compromising it yields neither plaintext nor a usable key. Restoring requires the private half, which stays with the sops-secrets-operator.
- There is no default. There is no safe answer to "who may decrypt this", and the bundle contains OpenTofu state — which holds credentials in plaintext by construction. Registry privacy is access control, not encryption.

The Composition rejects an `ageRecipient` that does not start with `age1`, so a private `AGE-SECRET-KEY` pasted into the field fails at render instead of being shipped to a registry.

## What this is *not*

Not disaster recovery on its own. **An OCI registry has no object-lock and no lifecycle policy: a token that can push can also delete.** Treat this as the convenient primary copy and keep an immutable offsite one (S3 with versioning + object lock).

Restore is deliberately not automated — see below.

## Restore (read before you need it)

Restoring is **deliberately manual**. The bundle is a set of external-names and OpenTofu state; applying it wrong duplicates or destroys real infrastructure. There is no "restore button" by design — a human verifies each external-name against reality before Crossplane is allowed to act.

> **Tested boundary, stated honestly.** Steps 1–2 (fetch + decrypt) are verified end to end on kind1 — the plaintext sha256 round-trips and the bundle contains the real UUIDs. Steps 3–6 (applying into a live target) are **not yet exercised end to end**; treat this runbook as the intended procedure, and rehearse it on a throwaway cluster before trusting it in anger.

### 1. Fetch and decrypt

```bash
oras pull ghcr.io/stuttgart-things/backups/<cluster>:latest      # or :h14, :d24, a timestamp
# The private half of spec.ageRecipient — held by the sops-secrets-operator,
# e.g. its --global-age-key-secret. NEVER commit or export it further.
export SOPS_AGE_KEY="$(kubectl -n sops-secrets-operator-system get secret sops-age-key -o jsonpath='{.data.age\.agekey}' | base64 -d)"
sops --decrypt --input-type binary --output-type binary bundle.tar.gz.sops > bundle.tar.gz
tar xzf bundle.tar.gz
# -> one <kind>.yaml per resource kind, plus tfstate.yaml
```

Confirm you have the right snapshot: the manifest's `com.stuttgart-things.backup.plaintext-sha256` annotation must equal `sha256sum` of the *normalized* projection, and `org.opencontainers.image.created` tells you when it was taken.

```bash
oras manifest fetch ghcr.io/stuttgart-things/backups/<cluster>:latest | jq '.annotations'
```

### 2. Restore prerequisites first

Before any managed resource, the target cluster needs the things those MRs depend on, or they strand:

- the **ProviderConfigs / ClusterProviderConfigs** and their credential Secrets — restore these first, or every MR errors and can stick in `crossplane.io/external-create-pending`;
- the **OpenTofu state**, applied *before* any `Workspace` reconciles:

  ```bash
  # tfstate.yaml holds the tfstate-* Secrets. With backend "kubernetes" these
  # ARE the state. Apply them before the Workspaces below, or OpenTofu starts
  # from empty state and re-creates (or collides with) the Vault auth backends
  # and roles those Workspaces already own.
  kubectl apply -n crossplane-system -f tfstate.yaml
  ```

### 3. Pause every managed resource *before* it goes live

This is the step that makes the difference between adoption and a duplicate. Apply the MRs **paused**, so nothing reconciles until you have checked it:

```bash
# For each <kind>.yaml, add the annotation before applying. Example with yq:
for f in *.yaml; do
  [ "$f" = tfstate.yaml ] && continue
  yq -i '.items[].metadata.annotations."crossplane.io/paused" = "true"' "$f"
  kubectl apply -f "$f"
done
```

### 4. Verify external-names against reality

For every managed resource, the `crossplane.io/external-name` in the backup must match the object that still exists in the provider (vSphere, Proxmox, Vault, …):

```bash
kubectl get managed -o custom-columns=\
KIND:.kind,NAME:.metadata.name,EXT:.metadata.annotations.crossplane\\.io/external-name
```

A **wrong or missing** external-name means Crossplane will not adopt the existing object — on unpause it creates a **second** one. A `deletionPolicy: Delete` on a mis-restored MR can **delete the real thing**. This check is the whole reason restore is manual; do not skip it.

### 5. Unpause, one blast radius at a time

Remove the pause annotation deliberately — ideally the cheapest / most reversible resources first (Objects, Releases), the destructive ones (VirtualMachine, Workspace) last, watching each settle to `SYNCED=True READY=True` before the next:

```bash
kubectl annotate <kind>/<name> crossplane.io/paused- --overwrite
```

### 6. Confirm no duplicates were created

After each unpause, check the provider side (vSphere/Proxmox console, `vault list auth`) that the count of real objects did not grow. If it did, an external-name was wrong — pause again immediately and reconcile the annotation before more damage.

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
```

Images are optional — each stage falls back to the Composition's default.

See [`examples/xr-min.yaml`](examples/xr-min.yaml) (EnvironmentConfig-driven), [`examples/xr.yaml`](examples/xr.yaml) and [`examples/xr-max.yaml`](examples/xr-max.yaml).

### Keeping `resourceKinds` and `resourceGroups` in sync

`resourceKinds` is what gets exported; `resourceGroups` is what the ClusterRole grants. They are separate because a ClusterRole cannot express Crossplane's `managed` category. A kind whose group is missing from `resourceGroups` would be silently skipped at runtime, so the Composition **asserts** the consistency at render time and names the offending groups. Add a provider → update both.

## Cluster preconditions

1. A provider-kubernetes `ClusterProviderConfig` named by `spec.crossplaneProviderConfig`.
2. `spec.namespace` exists (use the `namespace` Configuration, or an existing one).
3. A `kubernetes.io/dockerconfigjson` Secret named by `spec.registryCredentialsSecretName` **in `spec.namespace`** (where the CronJob's Pod runs — *not* the XR's namespace; a Pod can only mount a Secret from its own namespace), with **push access to `spec.registry` only**.
4. The provider-kubernetes ServiceAccount must be allowed to create the composed objects — ServiceAccounts, CronJobs and RBAC — **and** to `get,list` the backed-up groups (granting RBAC requires holding the permissions granted). On a fleet cluster where the provider SA is `cluster-admin` this is automatic; on a scoped cluster (e.g. kind1) it is not, and every composed `Object` sits `Synced=False` with `forbidden` until a ClusterRole grants it. See `docs/provider-rbac.yaml`.
5. Nothing else — the three stage images are pulled from upstream registries (`alpine/k8s`, `ghcr.io/getsops/sops`, `ghcr.io/oras-project/oras`). Override them via `spec.images` if the cluster mirrors or pins its own.

> **EnvironmentConfig timing:** when you apply the XR and its EnvironmentConfig together, the first reconcile can fail with `expected exactly one required resource, got 0` even though the label matches — the composite is cached before the EnvironmentConfig is indexed. It clears on the next reconcile; force one with `kubectl annotate clusterbackup <name> nudge=$(date +%s) --overwrite` if you don't want to wait.

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
