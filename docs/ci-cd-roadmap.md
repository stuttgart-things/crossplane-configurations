# CI/CD Roadmap

Plan for evolving this repo's automation from "renders offline" to "installs
and reconciles for real," plus automated, per-package releases. **Design doc —
not yet implemented.** Each phase below maps to a tracking GitHub issue and is
independently shippable.

## What already exists (build on, don't duplicate)

- **`discover`** job — computes the matrix of changed Configurations (dirs with
  a `crossplane.yaml`) on a PR.
- **`verify.yaml`** workflow — offline `crossplane render` + `kubeconform` +
  `xpkg build` per changed config, via the `dagger/crossplane` module.
- **Taskfile** — `verify` (offline), `push` (→ ghcr, bumps
  `meta.crossplane.io/version`), `push-dev` (→ ttl.sh, ephemeral, no bump),
  `apply-dev`, `render`, `check`.
- **Version convention** — `meta.crossplane.io/version` per `crossplane.yaml`
  is the local source of truth that `task push` reads/bumps; the OCI tag is the
  real version.
- **Commit convention** — Conventional Commits with the **scope = package
  name** (`feat(vsphere-vm): …`, `fix(ansible-run): …`).

## Target pipeline

```
PR open/sync ──► discover (exists)
             ──► verify  offline render+kubeconform+xpkg build (exists)
             ──► push-dev → ttl.sh         [Phase 1]  preview package per changed config
             ──► kind smoke-install         [Phase 4]  (gated/optional)
             ──► PR comment: install manifest

merge to main ──► release tooling opens/updates per-package release PR  [Phase 2]

release (tag) ──► build + push to ghcr (dagger module)  [Phase 3]
             ──► kind verify against the ghcr package    [Phase 4]
             ──► GitHub Release + changelog              [Phase 2]
```

## Dependency graph

```
Phase 1 (ttl.sh previews) ───────────────► Phase 4 (kind testing)
Phase 2 (release automation) ──► Phase 3 (release→ghcr) ──► Phase 4
```

Phase 1 and Phase 2 are independent and can land in either order. Phase 3 needs
Phase 2 (it triggers on the tags release tooling creates). Phase 4 consumes
Phase 1 previews (PR) and/or Phase 3 artifacts (release) but can start with a
manual/static install target.

---

## Phase 1 — PR-level ttl.sh previews ✅ implemented

**Tracking: [#11](https://github.com/stuttgart-things/crossplane-configurations/issues/11)**

Build a preview OCI package per changed Configuration on each PR and surface a
copy-paste install manifest, so reviewers can try a package on any cluster
without a canonical release.

- Reuse the `discover` matrix → run `task push-dev` per changed config.
- ttl.sh is anonymous / public / ephemeral (tag = TTL, auto-expiry) → **no
  secrets in CI**, safe for fork PRs.
- Post a sticky PR comment with the `kind: Configuration` manifest pointing at
  the ttl.sh ref.

**Decisions (resolved):**

- **TTL = `24h`** — ttl.sh's maximum, the longest single window we can give a
  review cycle.
- **Preview path encodes PR# + short SHA**:
  `ttl.sh/stuttgart-things/crossplane-configurations-pr<PR>-<sha7>/<name>:24h`.
  The tag *is* the TTL, so traceability lives in the repo path; a fresh SHA
  segment per commit also avoids reusing a previous commit's image.
- **Sticky comment, updated in place** (one comment for the whole PR, keyed by a
  hidden `<!-- crossplane-ttl-preview -->` marker), aggregating one
  `<details>` block per changed config — not append, not one comment per config.

Implemented as the `preview` + `preview-comment` jobs in
[`.github/workflows/verify.yaml`](../.github/workflows/verify.yaml). The matrix
`preview` job is secret-free (fork-safe); a single write-scoped `preview-comment`
job aggregates the per-config snippets so matrix legs never race on the comment.
Posting the comment still needs `pull-requests: write`, which the default token
lacks on fork PRs — the preview *images* publish regardless; only the comment is
skipped there (a future `workflow_run` hand-off can close that gap).

**Acceptance:** opening/updating a PR that changes a config publishes
`ttl.sh/<scope>/crossplane-configurations/<name>:<ttl>` and comments the install
manifest; unchanged configs are skipped. ✅

---

## Phase 2 — Automated per-package releases

**Tracking: [#12](https://github.com/stuttgart-things/crossplane-configurations/issues/12)**

Automate version bump + git tag + changelog from Conventional Commits,
retiring the manual `meta.crossplane.io/version` bump.

- This is a **monorepo with N independently-versioned packages**. Recommended:
  **release-please (manifest mode)** over plain `semantic-release` — it natively
  does per-path versioning, opens a "release PR" per package, and maintains
  changelogs. (semantic-release needs `multi-semantic-release`/monorepo plugins
  to approximate this.)
- Drive off the **commit scope = package name** convention. Per-path config maps
  `machinery/vsphere-vm` → tag `vsphere-vm-vX.Y.Z`, `cicd/ansible-run` →
  `ansible-run-vX.Y.Z`, etc.
- Release tooling owns `meta.crossplane.io/version` **and**
  `examples/configuration.yaml` (updated via its extra-files/exec hook), so
  `task push` becomes "build the version already in the file," not "bump."

**Decisions:** release-please vs semantic-release; tag format `<name>-vX.Y.Z`;
seed the release manifest from the current hand-maintained versions as baseline.

**Acceptance:** merging a `feat(<pkg>): …` / `fix(<pkg>): …` to main opens or
updates a release PR for that package; merging the release PR creates the
`<pkg>-vX.Y.Z` tag, GitHub Release and changelog, and the bumped
`crossplane.yaml` + `configuration.yaml`.

---

## Phase 3 — Release → ghcr propagation

**Tracking: [#13](https://github.com/stuttgart-things/crossplane-configurations/issues/13)**

On a package release tag, build that one Configuration and push it to ghcr via
the existing `dagger/crossplane` module (same path as `task push`, minus the
bump — Phase 2 owns versioning).

- Trigger on the `<name>-vX.Y.Z` tag → parse `<name>` → build+push only that
  config.
- **ghcr visibility is the friction point:** org container packages publish
  **private**, and there is *no API* to flip them. Plan: accept a one-time
  manual flip per *new* package and have the workflow **detect + warn loudly**
  (the `push` task already prints this); document it as a per-config one-time
  step. (Alternatives: pre-create packages public; a bot PAT with package
  admin.)
- Idempotency: skip if `<name>:<version>` already exists on ghcr (tags are
  mutable — guard against accidental overwrite).

**Decisions:** visibility automation vs documented manual flip; overwrite
policy.

**Acceptance:** creating a `<name>-vX.Y.Z` tag publishes
`ghcr.io/stuttgart-things/crossplane-configurations/<name>:vX.Y.Z`; re-running is
a no-op; a private package emits a clear warning with the flip URL.

---

## Phase 4 — kind install testing

**Tracking: [#14](https://github.com/stuttgart-things/crossplane-configurations/issues/14)**

Prove a package **installs + reconciles** on a real Crossplane control plane —
the gap `verify` (render-only) can't cover.

- Flow: `kind create` → helm-install Crossplane → install the `dependsOn`
  providers/functions → install the Configuration **from the ttl.sh preview
  (PR)** or **ghcr (release)** → assert: package `Healthy=True`, XRD
  `Established`, and a **min XR composes** (`SYNCED=True`, expected managed
  resource created with correctly patched fields).
- **Realistic depth:** most configs can't fully provision in kind (vsphere-vm
  needs vCenter, ansible-run needs Tekton + a target). Assert **up to
  managed-resource creation**, not full readiness — mirrors what we validate by
  hand on `cicd-crossplane`. Optionally stand up cheap backends (e.g. Tekton for
  ansible-run).
- **Gating:** this pulls Crossplane + every function/KCL module from ghcr (the
  same dependency that has produced transient `502`s) → add retries/backoff and
  gate behind a label (`needs-kind`) or release-only, not every PR push.

This layer would automatically catch two classes of bug we hit by hand:
short-function-name package-lock collisions, and EnvironmentConfig label
collisions (`expected exactly one required resource, got 2`).

**Decisions:** depth (install-only → compose-assert → mocked-backends); trigger
gating (every PR vs labeled vs release-only); per-config skip/mock matrix.

**Acceptance:** a gated job spins up kind, installs the package + deps, and
asserts XRD-established + min-XR-composes for each targeted config; failures are
actionable (package/XRD/compose stage identified).

---

## Cross-cutting

- **ghcr availability** — Phases 3 and 4 both pull functions/modules from ghcr;
  design for transient `502`s (retry/backoff) since they will otherwise cause
  flaky reds.
- **Secrets posture** — keep PR-triggered jobs (Phase 1, PR-side Phase 4)
  secret-free (ttl.sh, public packages); reserve ghcr-write credentials for
  push-on-release (Phase 3).
- **Reuse the `discover` matrix** across Phases 1 and 4 so "what changed" is
  computed once.
