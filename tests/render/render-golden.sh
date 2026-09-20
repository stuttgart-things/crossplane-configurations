#!/usr/bin/env bash
# Render golden snapshots of every example XR for every Configuration (or a
# single CONFIG), writing tests/render/golden/<config>/<xr>.yaml.
#
# WHY NOT next to the source XR, which is where these lived first: the crossplane
# dagger module's verify harness loops over `examples/xr*.yaml`, and
# `examples/xr-max.rendered.yaml` matches that glob. It then validated rendered
# OUTPUT as if it were an input XR and failed every Configuration on
# `additionalProperties 'conditions' not allowed` — rendered XRs carry a status,
# input XRs never do. Snapshots are test fixtures, so they live under tests/,
# where no examples/ glob can reach them. Bonus: verify.yaml already carries
# `paths-ignore: tests/**`, so re-seeding no longer fans out 29 verify jobs.
#
# WHY. The CI `verify` only proves a Composition renders WITHOUT erroring; it
# asserts nothing about WHAT it produces. The bugs the root CLAUDE.md records —
# an `nindent` column off by 12, a Secret encoded as `\n` instead of a block
# scalar — all render successfully and produce wrong YAML. Golden snapshots turn
# "it didn't crash" into "it produced exactly this": regenerate and diff, and any
# silent change in rendered output shows up as a reviewable diff.
#
# This is the generator. `tests/render/check-golden.sh` (and the render-golden
# CI workflow) regenerate into a scratch copy and fail if the committed goldens
# drift. Seed the goldens once with:
#
#     task render-golden          # or: tests/render/render-golden.sh
#     git add tests/render/golden && git commit
#
# Requires the `crossplane` CLI on PATH (the same one `task render` uses) and a
# working container runtime for the render Functions. Pin the CLI version in CI
# so the goldens stay reproducible.
set -euo pipefail

ROOT="${ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
cd "$ROOT"

GOLDEN_ROOT="${GOLDEN_ROOT:-tests/render/golden}"

# Presence AND version. The version half is the point: an older CLI does not
# fail, it renders without spec.crossplane.resourceRefs and quietly strips that
# block from every snapshot it rewrites (#393). The guard also prints the CLI it
# is about to render with, so the run says which tool produced the output.
# shellcheck source=tests/render/crossplane-version.sh
. "$(cd "$(dirname "$0")" && pwd)/crossplane-version.sh"

# The extra resources are selected with yq (mikefarah v4). Without it they would
# silently be empty and every Composition that reads one would render less.
command -v yq >/dev/null 2>&1 || { echo "render-golden: yq (mikefarah v4) is required" >&2; exit 1; }
crossplane_versions_load
crossplane_version_guard render-golden

# Pin the Crossplane core image the renderer runs `internal render` in.
#
# CLI >= v2.3.0 no longer executes the pipeline itself; it starts
# `crossplane internal render` inside a core image, defaulting to the FLOATING
# tag xpkg.crossplane.io/crossplane/crossplane:stable. When that tag points at
# an image without `internal`, every render dies with
# "unexpected argument internal" — which is exactly how this broke in June
# without a single code change (stuttgart-things/dagger#295). A floating tag in
# the execution path is the same hazard as one in a package pin.
#
# Still probed by FLAG rather than assumed from the version guard above. The
# floor (v2.3.0) is the release that introduced both the out-of-process render
# and this flag, so in practice the probe always succeeds now — but it is what
# keeps the fallback honest if a CLI ever renders in-process again, and it costs
# one --help call.
#
# The image itself comes from crossplane-versions.env (CROSSPLANE_RENDER_IMAGE),
# already loaded above — one place for it instead of a default repeated here, in
# the Taskfile and in the workflow.
IMAGE_ARG=""
if crossplane render --help 2>&1 | grep -q -- '--crossplane-image'; then
  IMAGE_ARG="--crossplane-image=${CROSSPLANE_RENDER_IMAGE}"
  echo "render-golden: core image ${CROSSPLANE_RENDER_IMAGE}"
fi

# Restrict to one Configuration with CONFIG=<path> (e.g. CONFIG=k8s/namespace);
# default is every Configuration in the repo.
if [ -n "${CONFIG:-}" ]; then
  CONFIGS="$CONFIG"
else
  # Dot-directories are pruned whole: a git worktree under .claude/worktrees/
  # is a second checkout of every package, and rendering into one writes
  # goldens nobody reads. Same rule as tests/lint/lint-configurations.py.
  CONFIGS=$(find . -type d -name '.?*' -prune -o \
              -type f -name crossplane.yaml -not -path '*/examples/*' \
            -printf '%h\n' | sed 's|^\./||' | sort -u)
fi

rendered=0
for c in $CONFIGS; do
  comp="$c/apis/composition.yaml"
  funcs="$c/examples/functions.yaml"
  if [ ! -f "$comp" ] || [ ! -f "$funcs" ]; then
    echo "render-golden: skip $c (missing composition or functions)" >&2
    continue
  fi

  # Extra-resources: every manifest in examples/ that is not an input XR
  # (xr*.yaml) and not package metadata (Function, Configuration, Provider,
  # DeploymentRuntimeConfig) — the same rule the dagger verify harness applies
  # (stuttgart-things/dagger#388), so a golden and a verify run see the same
  # cluster. That covers EnvironmentConfigs for a load-environment step and
  # resources a function requests by name (the cluster Configuration's
  # AppSecretProfiles). Collected into a scratch dir and handed to crossplane
  # render as a directory, which is version-agnostic (older CLIs take a single
  # --extra-resources path, newer ones a repeatable flag).
  #
  # Observed-state fixtures: tests/render/extra-resources/<config>/*.yaml is
  # copied into the same scratch dir. Large parts of a Composition can hang off
  # resources that only exist once a provider has OBSERVED the target cluster —
  # rancher-cluster's Argo CD registration and its whole vault-pki block do — and
  # `crossplane render` observes nothing, so those branches render to nothing and
  # no snapshot covers them. That is how `releaseOnDelete: false` (#388) shipped
  # unnoticed. Fixtures live under tests/ for the same reason the goldens do: an
  # examples/ glob must not reach them.
  extra_dir=""
  example_files=$(find "$c/examples" -maxdepth 1 -type f -name '*.yaml' ! -name 'xr*.yaml' \
                    2>/dev/null | sort || true)
  example_extra=""
  if [ -n "$example_files" ]; then
    # shellcheck disable=SC2086
    example_extra=$(yq ea 'select(.kind != null and .kind != "Function" and .kind != "Configuration" and .kind != "Provider" and .kind != "DeploymentRuntimeConfig")' $example_files 2>/dev/null || true)
  fi
  fixture_files=$(find "tests/render/extra-resources/$c" -maxdepth 1 -type f \
                    -name '*.yaml' 2>/dev/null | sort || true)
  if [ -n "$example_extra" ] || [ -n "$fixture_files" ]; then
    extra_dir=$(mktemp -d)
    if [ -n "$example_extra" ]; then
      printf '%s\n' "$example_extra" > "$extra_dir/examples.yaml"
    fi
    if [ -n "$fixture_files" ]; then
      # shellcheck disable=SC2086
      cp $fixture_files "$extra_dir"/
    fi
  fi

  for xr in "$c"/examples/xr*.yaml; do
    [ -f "$xr" ] || continue
    # Goldens live under tests/ now, so this can only match a leftover from a
    # working tree that predates the move. Cheap guard against rendering one.
    case "$xr" in *.rendered.yaml) continue ;; esac

    base=$(basename "$xr" .yaml)
    out="$GOLDEN_ROOT/$c/${base}.yaml"
    mkdir -p "$(dirname "$out")"

    set +e
    if [ -n "$extra_dir" ]; then
      # shellcheck disable=SC2086
      crossplane render "$xr" "$comp" "$funcs" --extra-resources "$extra_dir" $IMAGE_ARG > "$out.tmp" 2>"$out.err"
    else
      # shellcheck disable=SC2086
      crossplane render "$xr" "$comp" "$funcs" $IMAGE_ARG > "$out.tmp" 2>"$out.err"
    fi
    rc=$?
    set -e
    if [ "$rc" -ne 0 ]; then
      echo "render-golden: FAILED $xr (rc=$rc)" >&2
      sed 's/^/    /' "$out.err" >&2 || true
      rm -f "$out.tmp" "$out.err"
      [ -n "$extra_dir" ] && rm -rf "$extra_dir"
      exit "$rc"
    fi
    mv "$out.tmp" "$out"
    rm -f "$out.err"
    echo "render-golden: wrote $out"
    rendered=$((rendered + 1))
  done

  [ -n "$extra_dir" ] && rm -rf "$extra_dir"
done

echo "render-golden: $rendered snapshot(s) written"
