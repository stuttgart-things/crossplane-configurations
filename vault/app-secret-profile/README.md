# app-secret-profile

What an app needs in Vault, declared **once per app** — #464, decision 3.

A ClusterStack names **catalog profiles** (`profiles: [tabletennis]`). Each catalog
profile in `xplane-cluster-catalog` lists the `AppSecretProfile`s it delivers
(`appSecrets`); `xplane-cluster` reads those as extra resources and composes the
[`VaultSecretSet`](../vault-secrets/)s for that cluster. Adding an app means
adding its `AppSecretProfile` and listing it where it is delivered — not touching
an order, a module or a chart.

**`metadata.name` is the app, not necessarily a catalog profile.** tabletennis-platform
ships schmetterpause and zaehlwerk as well, so the catalog profile `tabletennis`
lists `appSecrets: [schmetterpause, zaehlwerk, tabletennis]`.

```yaml
apiVersion: secrets.stuttgart-things.com/v1alpha1
kind: AppSecretProfile
metadata:
  name: schmetterpause          # = the APP, as listed in a catalog profile's appSecrets
spec:
  mount: schmetterpause         # LOGICAL — resolved per environment
  entries:
    - suffix: ""                # entry <mount>/<cluster>
      keys:
        username:    {literal: schmetterpause}
        password:    {generate: {length: 32}, overridable: true}
    - suffix: "-backup"         # entry <mount>/<cluster>-backup
      keys:
        access_key:  {shared: object-store-backup}
  reads: {}                     # keys of OTHER apps, see zaehlwerk
```

## Two halves

| | where | what |
|---|---|---|
| **app** — same in every environment | `AppSecretProfile` (this package) | entries, key sources, overridable, reads |
| **environment** — per Vault | EnvironmentConfig `cluster-vault-<env>` | logical → real mount, read policies, shared name → `_` entry, writer ProviderConfig |

## Key sources — exactly one per key, enforced at admission

| source | written into the cluster's entry? | consumer reads |
|---|---|---|
| `generate: {length, charset}` | yes, once per cluster, then stable | `<mount>/<cluster><suffix>` |
| `literal: <string>` | yes — non-sensitive only, it is stored here in clear text | same |
| `shared: <name>` | **no** — resolved per environment to a `_` entry | that shared entry, directly |

`reads.<key>.from: {app, suffix, key}` is not a source of this app's entry: the
key is read directly from **another** app's entry in the same cluster, never
copied (a copied webhook token once drifted into a 401). Structured rather than
`"app-suffix/key"` because app names contain dashes too.

### `overridable`

May an order replace this key per cluster via `secretOverrides` (`secretKeyRef` or
`vaultRef` — e.g. to keep homerun2-test1's existing DB password on migration)?
Unset means **`generate` yes, `literal` and `shared` no**; `shared: …` with
`overridable: true` is rejected at admission — it is never written, there is
nothing to override. `status.resolved.entries[].overridable` shows the effective
set.

## What the Composition does

**Composes nothing.** It publishes `status.resolved` — per entry the written keys
(`generated`, `literal`), the effective `overridable` set and the `shared` names;
per read whether the referenced key exists — and the condition `ReadsResolved`.
A read is broken, with the reason in the condition and the profile at
`Ready=False`, if the other profile does not exist, has no entry with that
suffix, has no such key, or the key is `shared` there (then declare it `shared`
here instead).

Whether the other app is **in the same order** is not a property of the profile;
`xplane-cluster` checks it when the order renders (zaehlwerk without homerun2).

```
$ kubectl get appsecretprofiles
NAME             MOUNT            ENTRIES   READS   SYNCED   READY
homerun2         homerun2         1         0       True     True
schmetterpause   schmetterpause   3         0       True     True
tabletennis                       0         1       True     True
zaehlwerk                         0         3       True     True
```

## Why cluster-scoped

The XRD carries `stuttgart-things.com/cluster-scope-reason` — the one exception to
this repo's namespaced XRDs (see `CLAUDE.md`). A profile decides which secrets
**every** cluster ordering that app receives, and which of them an order may
override. That is an RBAC boundary: only admins or the app's owners may change
it. A namespaced profile could be rewritten by anyone with write access to its
namespace, and an order resolving profiles from its own namespace could bring
its own.

RBAC per object is what an XRD buys over one shared EnvironmentConfig: an app team
can be granted its own profile. Crossplane aggregates this XRD's roles into
`crossplane-edit` / `crossplane-admin`, not into Kubernetes' `edit` / `admin`.

## Cluster preconditions

- `function-go-templating`. No provider, no ProviderConfig — the profile never
  talks to Vault.

## Examples

| file | profile |
|---|---|
| `examples/xr-min.yaml` | **tabletennis** — reads only (homerun2's Redis password) |
| `examples/xr.yaml` | **schmetterpause** — generated, literal and shared keys, three entries |
| `examples/profiles/homerun2.yaml` | **homerun2** — owns its entry, GitHub PAT shared |
| `examples/profiles/zaehlwerk.yaml` | **zaehlwerk** — owns nothing, reads homerun2 and schmetterpause |
| `examples/xr-max.yaml` | synthetic `demo-max` — every field |

Render fixtures (the profiles `xr-min` and `xr-max` read) are in
`tests/render/extra-resources/vault/app-secret-profile/`.

```bash
KUBECONFIG=~/.kube/kind3 CONFIG=vault/app-secret-profile WHAT=both XR=xr.yaml YES=1 task apply-dev
kubectl apply -f vault/app-secret-profile/examples/profiles/
```
