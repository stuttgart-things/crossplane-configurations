# CLAUDE.md

Context for working on this repo with Claude Code.

## What this repo is

A collection of **Crossplane v2 Configuration packages**. Each subdirectory under a category (`k8s/`, future: networking, storage, …) is one Configuration: an XRD + Composition(s) + examples that builds into a single OCI artifact and gets pushed to `ghcr.io/stuttgart-things/crossplane-configurations/<name>`.

## Crossplane v2 essentials (worth re-checking before changes)

- **No more claims.** v2 promotes XRDs to `apiextensions.crossplane.io/v2` and adds `scope: Namespaced`. The XR itself is namespaced — there is no separate `Claim` kind. Don't use `claimNames:`. Don't name example files `claim.yaml`; use `xr.yaml`.
- **Composition stays on `/v1`.** The API group `apiextensions.crossplane.io` has `v1` and `v2` for `CompositeResourceDefinition`, but **only `v1` for `Composition`** (no `Composition/v2` exists). Mixing them in the same package is correct, not a bug.
- **Two CRDs from provider-kubernetes are NOT interchangeable:**
  - `kubernetes.crossplane.io/v1alpha2` — legacy, cluster-scoped, supports `deletionPolicy`.
  - `kubernetes.m.crossplane.io/v1alpha1` — namespaced "managed" variant for v2. **No `deletionPolicy` field.** Control delete behavior via `managementPolicies` only (omit `Delete`).
- **Canonical registry is `xpkg.crossplane.io`.** `xpkg.upbound.io` still serves the same packages but is the legacy path. Use the crossplane.io path in `dependsOn` for new work.

## What a Configuration must contain

```
<name>/
├── crossplane.yaml               # Configuration metadata (kind: Configuration, apiVersion: meta.pkg.crossplane.io/v1)
├── README.md                     # human docs (also referenced from meta.crossplane.io/readme)
├── apis/
│   ├── definition.yaml           # XRD — apiextensions.crossplane.io/v2, scope: Namespaced
│   └── composition.yaml          # Composition — apiextensions.crossplane.io/v1, mode: Pipeline
└── examples/
    ├── xr-min.yaml               # only XRD-required fields — exercises defaults
    ├── xr.yaml                   # realistic "standard" usage — the copy-paste template
    ├── xr-max.yaml               # every spec field set — catches templating breakage
    ├── configuration.yaml        # kind: Configuration (the runtime install manifest, points at OCI ref)
    └── functions.yaml            # the pipeline Functions the Composition references
```

### `crossplane.yaml` conventions

```yaml
apiVersion: meta.pkg.crossplane.io/v1
kind: Configuration
metadata:
  name: <name>                  # = the OCI package name
  annotations:
    meta.crossplane.io/version: v0.1.0       # custom: read & bumped by `task push`
    meta.crossplane.io/maintainer: ...
    meta.crossplane.io/source: github.com/stuttgart-things/crossplane-configurations
    meta.crossplane.io/license: Apache-2.0
    meta.crossplane.io/description: |        # one-paragraph summary
    meta.crossplane.io/readme: |             # condensed Markdown — Crossplane CLI does NOT auto-inline README.md
spec:
  crossplane:
    version: ">=v2.1.3"
  dependsOn:
    - provider|function: xpkg.crossplane.io/...
      version: ">=vX.Y.Z,<v(X+1).0.0"        # floor *and* cap, even for stable 1.x
```

- The OCI tag is the **real** package version. `meta.crossplane.io/version` is our local convention so `task push` can read/bump it automatically.
- Drop dead commented-out deps. The file is package metadata, not scratch space.

### Composition conventions

- Self-contain preconditions where possible. For Configurations that need a target namespace, the Composition manages it itself via a `kubernetes.m.crossplane.io/v1alpha1` Object with:
  ```yaml
  managementPolicies: [Observe, Create]   # adopt if exists; create if missing; never modify/delete
  ```
- The only documented per-Configuration **precondition** that must live on the cluster (not in the package) is the `ClusterProviderConfig` referenced by `spec.providerConfigRef` on the XR. Note this in the per-Configuration README under "Cluster preconditions".
- **Function CR names: short form, always.** In both `examples/functions.yaml` (`metadata.name`) and the Composition (`functionRef.name`), use the short form — `function-go-templating`, not `crossplane-contrib-function-go-templating`. Same rule for every Function (drop the `crossplane-contrib-` prefix). Target clusters in the stuttgart-things fleet install Functions under the short names via Argo CD; a long-named CR pointing at the same package adds a duplicate node to Crossplane's package lock graph (`cannot resolve package dependencies: ... node ... already exists`), which freezes the dependency resolver for **every** package on the cluster — providers and other Functions flip to `Healthy=False`, and any Configuration delete blocks on foreground deletion. Applies to new Configurations and migrations alike.

  **The trigger is same source under two CR names — not two CR names as such.** Verified on kind1 (2026-07-18). A cluster normally carries both `function-kcl` (short, Helm/Argo-installed, `xpkg.upbound.io`) and `crossplane-contrib-function-kcl` (long, **auto-installed by the package manager** from a Configuration's `dependsOn`, `xpkg.crossplane.io` — Crossplane derives that CR name from the package path, so deleting it just brings it back within seconds). That cross-mirror pair is **healthy and expected**: two Lock nodes, two distinct sources, resolves by digest.

  So do **not** "clean this up" by aligning the mirrors. Repointing the Helm-installed short-named CR at `xpkg.crossplane.io` to match the `dependsOn` gives two Lock nodes with an *identical* source and instantly triggers `node xpkg.crossplane.io/crossplane-contrib/function-kcl already exists` — every Function on the cluster goes `Healthy=False`. The mirror split is what keeps the two nodes distinct.

  **Recovery** (deleting the long-named CR does *not* work — it is recreated): the Lock keeps the stale source after you revert the CR, so drop the offending entry and let the package manager rebuild it:
  ```bash
  IDX=$(kubectl get lock lock -o json | jq '[.packages[].name] | index("function-kcl-<hash>")')
  kubectl patch lock lock --type=json -p "[{\"op\":\"remove\",\"path\":\"/packages/$IDX\"}]"
  ```
  Health returns in under a minute. Corollary: `functionRef: function-kcl` resolves only because the fleet pre-installs short names via Argo CD — on a bare cluster the auto-installed dependency lands as `crossplane-contrib-function-kcl` and a short-named `functionRef` would not resolve.

  **So every `examples/functions.yaml` pins `xpkg.upbound.io`, deliberately.** Our `dependsOn` entries all use `xpkg.crossplane.io`, and the package manager derives a long CR name from that path — so a short-named CR on the *same* registry is the collision above, by construction. The differing mirror is load-bearing, not legacy drift. Do not "modernise" these to `xpkg.crossplane.io`; the rule in the section above ("use the crossplane.io path for new work") is about `dependsOn`, not about Function CRs we author.

  **`task apply-dev` no longer applies `examples/functions.yaml` unless you pass `FUNCTIONS=1`.** It used to, and that made the task destructive against any fleet cluster in two ways — hit for real on kind1, 2026-07-20, which took all six Functions to `Healthy=False`:

  1. the Lock collision above, if a pin's registry matched a `dependsOn`-derived CR;
  2. a silent **downgrade** — the pins in `examples/` drift behind the fleet (e.g. `function-environment-configs` v0.7.2 on kind1 vs v0.7.0 in the file), and `kubectl apply` happily walks a Function backwards.

  Fleet clusters get their Functions from Argo CD and need neither. Use `FUNCTIONS=1` only when bootstrapping a bare dev cluster.

### Example XR conventions

- `metadata.namespace` is where the XR object lives.
- `spec.namespace` (if the XRD has one) is the target namespace for managed resources — separate concern.
- Provide all three variants. We've seen each catch different bugs:
  - `xr-min` validates XRD defaults.
  - `xr` is the realistic case.
  - `xr-max` exercises every field — caught a multi-line `write_files.content` indentation bug.

## Repo-level conventions

- Tooling for all interactive workflows is in [`Taskfile.yaml`](Taskfile.yaml). Tasks reuse the same picker pattern: `find ... -name crossplane.yaml` → `gum choose`. Adding a new Configuration costs nothing in tooling.
- Tasks: `render` (local), `check` (cluster fit), `apply-dev` (kubectl apply, no OCI), `push` (Dagger → OCI).
- The OCI push goes through `github.com/stuttgart-things/dagger/crossplane` (the Dagger module in the `dagger` repo, *not* a "crossplane-configurations" module). It runs `crossplane xpkg build` then `xpkg push` inside a wolfi container.

## Gotchas we learned (don't repeat)

1. **`nindent` columns** in Composition templates must be the **final-output** column, not the template-file column. When the value is embedded inside an outer block scalar (e.g. `userdata: |`), don't pile extra indentation on top. For `write_files.content` inside the `userdata: |` block, the correct value was `nindent 16` (not 28, not 12).
2. **`deletionPolicy` on `kubernetes.m.crossplane.io/v1alpha1` Object** = schema rejection. Use `managementPolicies` only.
3. **`apply-dev` auto-creates the XR's own metadata namespace** but not its `spec.namespace`. The latter is the Composition's job — integrate it into the Composition rather than treating it as a precondition or auto-creating from the task (would couple the generic task to Configuration-specific spec fields).
4. **`task check` does URL-prefix matching, but Crossplane's package manager does NOT.** Switching a Configuration to `xpkg.crossplane.io` while the cluster has `xpkg.upbound.io` installed will produce a false-positive "missing" from `task check` — same bytes, different mirror. The package manager itself resolves cross-mirror `dependsOn` correctly (matches by digest, retries after an initial "missing dependencies" condition that can take 30-60s to clear). So: if `task check` reports a missing dep but the cluster has the same package under the other mirror, **install anyway** — the package will land. The `task check` mismatch is the false positive, not the actual install.
5. **`crossplane render` output of multi-line strings** may render as quoted-with-`\n` rather than block-scalar `|` style. That's the YAML serializer's choice; both produce identical Secret data. Verify the rendered **live Secret**, not the render output, when debugging.

6. **`task push` used to leak the registry PAT into its own output.** `dagger --progress plain` echoes every DAG op's *arguments*, and the crossplane module writes the credential into `/root/.docker/config.json` via `withNewFile` — so `base64("<user>:<token>")` appeared verbatim in the progress log, and from there into two session transcripts. Fixed by piping the push through a `sed` redactor rather than dropping `--progress plain` (that output is what makes a failing push debuggable). The redactor is **value-based first** — it redacts the actual secret and its base64 forms, read at runtime — so it does not depend on guessing dagger's formatting; pattern rules for `"auth"` fields and `ghp_*` shapes are only a fallback. `2>&1` scrubs stderr too, and `set -o pipefail` is what keeps a failed push from reporting success through the pipe. **If you add another `dagger call` that takes a real credential, pipe it through the same redactor.** (The root cause is really module-side: `github.com/stuttgart-things/dagger/crossplane` should mount the credential as a Secret rather than inlining it into a file's contents.)

## Memory & future work

- Verification pipeline (render + kubeconform + xpkg build) is tracked as a Dagger-side issue: [stuttgart-things/dagger#277](https://github.com/stuttgart-things/dagger/issues/277). Lands as `crossplane.Verify(...)` plus a `call-crossplane-verify.yaml` reusable workflow.
- When adding new Configurations, update the **Configurations** table in [`README.md`](README.md) and add the per-Configuration README. Keep the table sorted by `category`, then `name`.

## Related repos

- `stuttgart-things/dagger` — Dagger modules. The `crossplane/` submodule supplies the package/push/verify functions our Taskfile drives.
- `stuttgart-things/argocd` — has the structural template for the PR-verification workflow we'll mirror here.
- `stuttgart-things/github-workflow-templates` — where the reusable `call-*` workflows live.
