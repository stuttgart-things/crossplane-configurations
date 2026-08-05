# Golden render snapshots

Output-assertion tests for the Configuration packages. They complement `verify`
(render + kubeconform + xpkg build): `verify` proves a Composition renders
**without erroring**; the goldens prove it renders the **same output** as a
committed snapshot. This is the layer that catches the silent-wrong-output bugs
the root `CLAUDE.md` records — an `nindent` column off by 12, a Secret encoded as
`\n` instead of a block scalar — which all render successfully and pass `verify`.

## Layout

For each Configuration, every example XR (`examples/xr*.yaml`) is rendered to a
snapshot mirroring its path under `tests/render/golden/`:

```
k8s/namespace/examples/xr.yaml         →  tests/render/golden/k8s/namespace/xr.yaml
k8s/namespace/examples/xr-min.yaml     →  tests/render/golden/k8s/namespace/xr-min.yaml
k8s/namespace/examples/xr-max.yaml     →  tests/render/golden/k8s/namespace/xr-max.yaml
```

**Not** next to the source XR, which is where they lived first. The crossplane
dagger module's verify harness loops over `examples/xr*.yaml`, and
`examples/xr-max.rendered.yaml` matched that glob: verify then validated rendered
OUTPUT as an input XR and failed every Configuration on `additionalProperties
'conditions' not allowed` (rendered XRs carry a status, input XRs never do).
Snapshots are test fixtures, so they live under `tests/`, out of reach of any
`examples/` glob. It also keeps them out of `verify.yaml`, which already carries
`paths-ignore: tests/**` — re-seeding no longer fans out a verify job per
Configuration.

Override the root with `GOLDEN_ROOT=<path>`; both scripts honour it.

## Scripts

| Script | What it does |
|---|---|
| `render-golden.sh` | (Re)generate the snapshots under `tests/render/golden/`. |
| `check-golden.sh`  | Regenerate, then fail if a committed snapshot drifted. |

Both honour `CONFIG=<path>` (e.g. `CONFIG=k8s/namespace`) to scope to one
Configuration; with no `CONFIG` they cover every Configuration in the repo. Both
require the `crossplane` CLI on `PATH` and a container runtime for the render
Functions — the same prerequisites as `task render`.

Via the task runner:

```bash
task render-golden                 # regenerate all snapshots
CONFIG=k8s/namespace task render-golden
```

## Seeding the snapshots (one-time)

The snapshots are generated, not hand-written, so they are seeded once on a
machine (or CI runner) that has `crossplane` + a container runtime:

```bash
task render-golden
git add tests/render/golden
git commit -m "test: seed golden render snapshots"
```

The `render-golden` CI workflow (`.github/workflows/render-golden.yaml`) also
uploads freshly rendered snapshots as the `golden-render-snapshots` artifact on
every run, so you can trigger it via **workflow_dispatch**, download the artifact,
and commit that as the seed instead of rendering locally.

Two things to expect the first time:

- **`git diff --exit-code` ignores untracked files.** Before seeding, nothing is
  tracked, so `check-golden.sh` passes and just lists the new snapshots. Drift is
  only enforced once the snapshots are committed.
- **Rendered Secrets carry high-entropy data.** Compositions that emit a `Secret`
  (e.g. `k8s/cloud-config`, the `vault-*` packages) produce snapshots that
  `detect-secrets` flags — 71 hits across the seeded set, all of them the literal
  `password:` / `token:` KEYS the templates emit, none a credential. Rather than
  baseline them, `.pre-commit-config.yaml` excludes `^tests/render/golden/`
  outright: generated files would have to be re-baselined on every regeneration,
  and a permanently churning allowlist is how a baseline stops catching real
  hits. Nothing is lost — rendering only transforms inputs the hooks already
  scan.

## CI

`render-golden.yaml` runs nightly and on demand: it installs the pinned
`crossplane` CLI (`CROSSPLANE_VERSION`, kept in step with the crossplane dagger
module the rest of CI uses), regenerates every snapshot, and fails on drift in a
committed golden.

It deliberately does **not** gate PRs yet — pre-seed that would be noise. Once the
snapshots are committed, add a `pull_request` trigger to catch drift at review
time; ideally scope it to the changed Configurations (reuse verify.yaml's
`discover` diff logic) so a PR only re-renders what it touched instead of the
whole set.

## Determinism

`crossplane render` is deterministic for a given (XR, Composition, Functions,
EnvironmentConfigs, CLI version) tuple, which is why the CLI version is pinned.
If a snapshot ever diffs only in field ordering or a generated suffix, normalise
it in `render-golden.sh` (e.g. pipe through `yq -P`) rather than accepting the
churn.
