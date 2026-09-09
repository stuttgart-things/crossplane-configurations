# Handover: #392 — compose the Vault auth mount into `rancher-cluster`

Written from a workstation with **no cluster access**. Everything below that is
marked *verified* was checked here by rendering; everything marked *from #392*
is the issue author's measurement on `test1` and has **not** been re-checked.

The point of this document: the next agent has `crossplane-mgmt` and can finish
the work without re-deriving what is already known.

## What is already done (merged / in review)

Render coverage for the region #392 changes. Before this, the `rancher-cluster`
goldens stopped at the Observe-only extraction Object — the Argo CD kubeconfig,
the `ClusterbookCluster` and the **entire vault-pki block never rendered**, so no
snapshot covered them and any change there was invisible to CI.

| example | composed resources before | after |
|---|---|---|
| `xr-max` | 7 | 16 |
| `xr-harvester` | 9 | 11 |

Two mechanisms were needed, both now in the repo:

1. `tests/render/extra-resources/<config>/*.yaml` — observed-state fixtures,
   passed to `crossplane render --extra-resources` by `render-golden.sh`.
2. `spec.environmentConfig` set explicitly on examples. **`crossplane render`
   does not apply XRD schema defaults** — no API server, no defaulting
   admission. `xr-max` omitted the field, the `load-environment` selector
   (`fromFieldPathPolicy: Optional`) dropped its matchLabel, `$env` rendered
   empty, and every block gated on an env key silently vanished. *Verified.*

## Phase 1 is now implemented (still unvalidated against a cluster)

`spec.vaultAuth.enabled` on `machinery/rancher-cluster` supplies all three
things below. Verified **by rendering only**:

| step | resource |
|---|---|
| (6a) | `ServiceAccount` `<reviewerNamespace>/<reviewerServiceAccount>`, `automountServiceAccountToken: false` |
| (6b) | `ClusterRoleBinding` to **`system:auth-delegator`** |
| (6c) | SA-token `Secret`, `[Observe, Create]` |
| (6d) | Observe-only Object → connection Secret `<name>-vault-reviewer` (`token`, `ca.crt`, `apiserverIp`) |
| (6e) | `status.vaultReviewerSecret` + `status.vaultKubernetesHost` |
| (5a2) | `ServiceAccount` `cert-manager/certmanager` (only when the vault-pki block is on too) |

`xr-max` renders 21 composed resources and this status:

```yaml
vaultKubernetesHost: https://192.168.10.135:6443
vaultReviewerSecret: rke2-prod-vault-reviewer
```

**What the golden does NOT prove**, and what the cluster agent has to check
first:

1. **The token encoding.** (6d) mirrors `data.token` through
   `connectionDetails`, copying what step (4d) does for the Argo CD SA — which
   demonstrably works, since clusters do get registered. But whether the value
   that lands in the connection Secret is the JWT or a base64 of it was never
   verified here; `VaultK8sAuth.backendConfig` needs the JWT. If auth fails with
   a malformed-token error, this is the first place to look.
2. **`system:auth-delegator` is enough.** Reasoned from what a TokenReview
   needs, not measured.
3. That the reviewer SA does not collide with the one `vault-base-setup` or
   `blueprints CreateVaultKubernetesAuth` create — see the ownership question
   below. `spec.vaultAuth.reviewerServiceAccount` exists so the name can be
   aligned rather than duplicated.

## What is left after that

Composing the `VaultK8sAuth` itself — deliberately NOT done, because it needs the
packaging decision below. Today the XR is still applied separately; it just no
longer needs a human with the root token in front of it.

## What each remaining part needs

### 1. Reviewer ServiceAccount + mirrored Secret — the only real work

`VaultK8sAuth.spec.k8sAuths[].backendConfig` wants a Secret with `ca.crt` and
`token` **on crossplane-mgmt**, next to the XR. It is the token OpenBao uses to
review login requests from the target cluster.

The Composition already does this exact dance in the other direction. Steps
(4a)–(4e) of `machinery/rancher-cluster/apis/composition.yaml`:

| step | what it does | reuse for the reviewer |
|---|---|---|
| (4a) | `ServiceAccount` on the target | same, `kube-system/vault-auth-reviewer`, `automountServiceAccountToken: false` |
| (4b) | `ClusterRoleBinding` cluster-admin | **`system:auth-delegator`**, not cluster-admin |
| (4c) | SA-token `Secret`, `[Observe, Create]` | same shape |
| (4d) | **Observe-only** Object with `connectionDetails` → `writeConnectionSecretToRef` | same; needs `data.token` + `data[ca.crt]` |
| (4e) | `ExtraResources` pull into the pipeline | same |

Two details from (4c)/(4d) that are load-bearing and easy to lose:

- create and extract **must be separate Objects**. provider-kubernetes does not
  surface `connectionDetails` on an Object that also manages the target.
- (4c) is `[Observe, Create]`, never update, so the token controller's fields are
  not stripped.

Ownership question the next agent has to settle: `vault-base-setup` creates the
same reviewer itself (`k8s_auth_reviewer_create`, default true) and
`blueprints CreateVaultKubernetesAuth` creates the same identity on
pipeline-built clusters. Two owners for one identity is not resolved by either
side. Decide before writing the Object.

### 2. `kubernetesHost` — already computed, just not passed on

`composition.yaml` builds it at the `$discovered` assignment:

```gotemplate
{{- $discovered = printf "https://%s:6443" (index $saData "apiserverIp" | b64dec) }}
```

`apiserverIp` comes from the downstream `kubernetes` Endpoints via (4d). On
`test1` this was `https://192.168.10.118:6443` *(from #392)*.

Both paths now have golden coverage: `xr-harvester` exercises **discovery** (no
`argocd.server`), `xr-max` exercises the **override**.

### 3. `cert-manager/certmanager` ServiceAccount — one Object, no owner today

The part #392 says it would not have predicted. With the mount created and
correct, the issuer still failed *(from #392)*:

```
Failed to initialize Vault client: while requesting a Vault token using the
Kubernetes auth: while requesting a token for the service account
/certmanager: serviceaccounts "certmanager" not found
```

`VaultK8sAuth` configures Vault and nothing on the target — correctly. The
`cert-manager-tokenrequest` Role arrives on its own from the
`cert-manager-vault-pki` ApplicationSet, gated on `method=kubernetes`. The
ServiceAccount has no owner at all. One more `Object` next to step (5a), same
`[Observe, Create]`, same downstream `ClusterProviderConfig`.

The error reads like a Vault fault. It is a missing ServiceAccount.

## The open decision I could not make

**How does `VaultK8sAuth` get composed?**

- *As a nested child XR* — the shape `bootstrap/platform` already uses. But
  `machinery/rancher-cluster` would then need a `dependsOn` on our **own**
  `vault-auth` package, and CLAUDE.md is explicit that a `dependsOn` on our own
  Configurations is what triggers the fatal Lock collision when a short-named CR
  is upgraded. `bootstrap/platform` already depends on `vault-auth`.
- *As a composed `Workspace` directly* — no new `dependsOn`, but duplicates what
  `bootstrap/vault-auth` exists to encapsulate, and drifts from it.

This is a packaging decision with fleet-wide consequences, not a coding one.

## Prerequisites — trust but verify

All *from #392*, none re-checked here:

- OpenBao AppRole verified with `sys/capabilities-self`: can create, configure
  and delete auth mounts, nothing else. `auth/approle/*`, `sys/policies/acl/*`,
  `pki/*`, `sys/raw/*` all deny
- `vault-auth` installed on crossplane-mgmt under the **package-manager-derived
  long name** (#385). Short would collide — `bootstrap/platform` declares a
  `dependsOn` on it
- AppRole Secret `default/vault` on crossplane-mgmt, not test1-specific

Checked from here: none of the 12 reachable kubeconfigs is crossplane-mgmt.
`kind3` has `vault-auth` + provider-opentofu but **no** `RancherCluster` XRD and
**no** `default/vault` Secret, so it is not a substitute.

## Teardown, so it is not rediscovered

*From #392, on test1:* deleting the XR removed the Workspace **and** the Vault
mount in 30 seconds, no finalizer wedge. `pki-issue` and the other four mounts
untouched. The wedge the `vault-auth` README records from kind3 happens when
`delete` on `sys/auth/*` is missing from the policy — check that first if a
teardown hangs.

Also relevant and already in CLAUDE.md: a `Usage` whose `by` is a
`resourceSelector` strands on delete. The two vault-pki Usages use explicit
`resourceRef` with deterministic names — keep any new Usage the same way.

## Sequencing

The reviewer token cannot exist before the cluster does. That ordering does not
go away; what goes away is the human and the root token. In Crossplane terms the
`VaultK8sAuth` is simply another composed resource that becomes ready later —
the same shape as the Argo CD registration block, which already waits on
observed state.

## How to verify a change from here on

```bash
CONFIG=machinery/rancher-cluster tests/render/render-golden.sh
git diff tests/render/golden/
```

Anything added behind observed state needs a matching fixture in
`tests/render/extra-resources/machinery/rancher-cluster/`, or it will render to
nothing and the golden will say everything is fine.
