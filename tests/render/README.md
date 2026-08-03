# Golden render snapshots

Output-assertion tests for the Configuration packages. They complement `verify`
(render + kubeconform + xpkg build): `verify` proves a Composition renders
**without erroring**; the goldens prove it renders the **same output** as a
committed snapshot. This is the layer that catches the silent-wrong-output bugs
the root `CLAUDE.md` records — an `nindent` column off by 12, a Secret encoded as
`\n` instead of a block scalar — which all render successfully and pass `verify`.

## Layout

For each Configuration, every example XR (`examples/xr*.yaml`) is rendered to a
sibling snapshot:

```
k8s/namespace/examples/xr.yaml         →  k8s/namespace/examples/xr.rendered.yaml
k8s/namespace/examples/xr-min.yaml     →  k8s/namespace/examples/xr-min.rendered.yaml
k8s/namespace/examples/xr-max.yaml     →  k8s/namespace/examples/xr-max.rendered.yaml
```

## Scripts

| Script | What it does |
|---|---|
| `render-golden.sh` | (Re)generate the `*.rendered.yaml` snapshots in place. |
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
git add '**/examples/*.rendered.yaml'
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
  (e.g. `k8s/cloud-config`, the `vault-*` packages) will produce snapshots the
  `detect-secrets` pre-commit hook flags. Update `.secrets.baseline`
  (`detect-secrets scan --update .secrets.baseline`) or add a
  `# pragma: allowlist secret` when committing the seed.

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
