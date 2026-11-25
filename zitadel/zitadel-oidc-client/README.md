# zitadel-oidc-client

One ZITADEL OIDC client in an **existing** project, from a namespaced
`ZitadelOidcClient` XR. An OpenTofu `Workspace` runs the
[`oidc-app`](https://github.com/stuttgart-things/zitadel-base-setup/tree/main/modules/oidc-app)
module of zitadel-base-setup, and its client ID and secret go to a Vault KV
entry, where the target cluster's ESO reads them. Nothing cluster-specific
ends up in git, and nobody copies credentials by hand.

It is the Crossplane counterpart of the pipeline plan in
[stuttgart-things#4855](https://github.com/stuttgart-things/stuttgart-things/issues/4855):
each cluster gets its own client, because ZITADEL compares redirect URIs
exactly and has no wildcards. Each client is its own XR with its own state.
That means no map variables merged across tfvars files, no `-target`, and no
access to the lab root's shared state.

## Shape

```yaml
apiVersion: zitadel.stuttgart-things.com/v1alpha1
kind: ZitadelOidcClient
metadata:
  name: argocd-cluster1
  namespace: default
spec:
  zitadel:
    domain: zitadel.example.com          # no scheme, no port
  project: argocd                        # EXISTING project, exact name
  app:
    name: argocd-cluster1                # unique in the project
    redirectUris: [https://argocd.cluster1.example.com/auth/callback]
    postLogoutRedirectUris: [https://argocd.cluster1.example.com/]
  vault:                                 # optional
    mount: kv
    path: cluster1/argocd-oidc           # keys client_id, client_secret
```

The app takes the module's defaults for everything left out: a confidential
web client, the code flow, basic auth, bearer tokens, and roles plus userinfo
in the ID token. A public browser client (Kargo, PKCE) sets
`appType: OIDC_APP_TYPE_USER_AGENT` and `authMethodType:
OIDC_AUTH_METHOD_TYPE_NONE`, and gets no secret. See `examples/xr-max.yaml`
for every field.

## How it works

Inline `function-go-templating`, two composed resources:

1. **`Workspace`** (`opentofu.m.upbound.io/v1beta1`, inline root):
   - The root configures the `zitadel` provider and calls
     `zitadel-base-setup//modules/oidc-app?ref=v0.2.0`.
   - Every XR value reaches OpenTofu through `varmap`, typed. No string is
     spliced into HCL. A field left out is `null`, which the module reads as
     *its* default, so the defaults live in one place.
   - The machine key comes from a tfvars Secret next to the XR (`varFiles`)
     and never appears in a spec.
   - provider-opentofu writes **every** output to the connection Secret
     `<xr>-zitadel-oidc`, each under its own name: `client_id`,
     `client_secret`, `vault_data_json` (the entry as one JSON object), and
     also `app_id` and `project_id`. Those two are not sensitive, so they
     appear in the Workspace status as well and from there in the XR's
     `status.app`. Only `vault_data_json` is read by anything here.
2. **`SecretV2`** (`kv.vault.m.upbound.io/v1alpha1`, only with `spec.vault`):
   - It owns the **whole** entry `<mount>/<path>` and reads it through
     `dataJsonSecretRef` from the Workspace's connection Secret. The
     credentials never pass through an MR spec or the XR.
   - It is composed only once the Workspace is Ready (before that the
     connection Secret does not exist), and it stays composed after that.
   - The XR reports `Ready=False` until the entry is Ready, so a cluster
     waiting on Vault does not see a green XR too early.

The project, its roles and its user grants are **not** managed here. They
stay in the lab's ZITADEL root, which a person applies (`oidc-project`). A user
with a role on the project can therefore log in to a new cluster's app at
once, without a new grant.

## Deletion: `releaseOnDelete` (default `true`)

A recreated app gets a **new client ID and secret**, and every login that uses
the old ones breaks. By default both composed resources therefore lack the
`Delete` management policy: deleting the XR leaves the app in ZITADEL and the
entry in Vault. Set `releaseOnDelete: false` only to tear an app down on
purpose. With it, deleting the XR runs `tofu destroy` and deletes every
version of the entry.

Changing `app.name`, `project` or `zitadel.orgName` on a live XR **replaces**
the app, for the same reason.

## Cluster preconditions

| What | Default | Notes |
|---|---|---|
| provider-opentofu `ClusterProviderConfig` | `default` | `spec.providerConfigRef`. Its backend holds the state; see below |
| Secret with the machine key, in the XR's namespace | `zitadel-terraform` / `terraform.tfvars` | tfvars line `jwt_profile_json = "<key JSON>"`, see `examples/credentials-secret.yaml` |
| ZITADEL machine user | — | rights on the project's apps, read on orgs and projects (the module looks both up by name) |
| provider-vault `ClusterProviderConfig` (with `spec.vault`) | `vault` | may write `<mount>/data/<path>` and `<mount>/metadata/<path>` (and delete the metadata, for `releaseOnDelete: false`); the KV v2 mount must exist. See below |
| ZITADEL's CA, trusted by the provider-opentofu pod | — | the zitadel provider talks TLS from inside that pod. A lab CA that the image does not carry fails the TLS handshake, so no Workspace gets Ready. See below |

**Which Vault credential.** The default name `vault` is only a default. On
the LabUL management clusters the CPC called `vault` logs in as
`vault-k8sauth-bootstrap`, which has no KV write at all; the one that can
write is `vault-cluster-secrets` (mounts `cicd`, `kargo`, `observability`,
`homerun2`, `schmetterpause`). Its policies grant `<mount>/data/+`, and `+`
is exactly **one** path segment, so `path: pilot/oidc` is denied while
`path: pilot-oidc` is not. Set `spec.vault.providerConfigRef` and pick `path`
to fit the policy, not the other way round.

**CA trust.** The fleet already distributes the lab CA: trust-manager's
`cluster-trust-bundle` ConfigMap (public CAs plus the lab's) exists in
`crossplane-system`. The provider pod still has to mount it — a
`DeploymentRuntimeConfig` volume from that ConfigMap (`optional: true`) at
`/etc/ssl/custom` plus `SSL_CERT_DIR=/etc/ssl/certs:/etc/ssl/custom`. Until
the fleet's catalog renders that, a cluster without it cannot run this
Configuration against a lab ZITADEL.

**State.** With the `kubernetes` backend (`examples/cluster-provider-config.yaml`)
the state lives on the management cluster and is lost with it. A new
management cluster would then try to create the app again. Depending on
ZITADEL that gives either a name conflict or a second app with new
credentials. [#482](https://github.com/stuttgart-things/crossplane-configurations/issues/482)
moves the state to S3, so use this Configuration for pilots only until then.

## Status

```yaml
status:
  app:   {ready: true, appId: "...", projectId: "..."}
  vault: {entry: kv/cluster1/argocd-oidc, ready: true}
```

## Tests

- `examples/xr-min.yaml`, `xr.yaml`, `xr-max.yaml` (golden snapshots under
  `tests/render/golden/zitadel/zitadel-oidc-client/`).
- `tests/render/observed-resources/zitadel/zitadel-oidc-client/xr--applied.yaml`:
  the Workspace is Ready, which is the only path that composes the Vault
  entry and fills `status.app`.
- Pilot on machinery-dev against the LabUL ZITADEL, 2026-11-25 (module
  v0.2.0): created the app, wrote the Vault entry with exactly `client_id`
  and `client_secret`, and with `releaseOnDelete: false` removed both again
  on delete. It needed the CA trust and the `vault-cluster-secrets` CPC from
  the preconditions above.
