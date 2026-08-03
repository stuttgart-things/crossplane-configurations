#!/usr/bin/env bash
# Render golden snapshots of every example XR for every Configuration (or a
# single CONFIG), writing examples/<xr>.rendered.yaml next to each source XR.
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
#     git add '**/examples/*.rendered.yaml' && git commit
#
# Requires the `crossplane` CLI on PATH (the same one `task render` uses) and a
# working container runtime for the render Functions. Pin the CLI version in CI
# so the goldens stay reproducible.
set -euo pipefail

ROOT="${ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
cd "$ROOT"

if ! command -v crossplane >/dev/null 2>&1; then
  echo "render-golden: 'crossplane' CLI not found on PATH" >&2
  exit 127
fi

# Restrict to one Configuration with CONFIG=<path> (e.g. CONFIG=k8s/namespace);
# default is every Configuration in the repo.
if [ -n "${CONFIG:-}" ]; then
  CONFIGS="$CONFIG"
else
  CONFIGS=$(find . -type f -name crossplane.yaml \
              -not -path './.git/*' -not -path '*/examples/*' \
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

  # Extra-resources: pass every EnvironmentConfig example so Compositions with a
  # load-environment step find exactly one match. Names differ across the repo
  # (environmentconfig.yaml and environment-config.yaml both occur), and some
  # Configurations ship more than one — collect them into a scratch dir and hand
  # crossplane render the directory, which is version-agnostic (older CLIs take a
  # single --extra-resources path, newer ones a repeatable flag).
  extra_dir=""
  env_files=$(find "$c/examples" -maxdepth 1 -type f \
                \( -name '*environmentconfig*.yaml' -o -name '*environment-config*.yaml' \) \
                2>/dev/null | sort || true)
  if [ -n "$env_files" ]; then
    extra_dir=$(mktemp -d)
    # shellcheck disable=SC2086
    cp $env_files "$extra_dir"/
  fi

  for xr in "$c"/examples/xr*.yaml; do
    [ -f "$xr" ] || continue
    case "$xr" in *.rendered.yaml) continue ;; esac   # don't render a golden

    base=$(basename "$xr" .yaml)
    out="$c/examples/${base}.rendered.yaml"

    set +e
    if [ -n "$extra_dir" ]; then
      crossplane render "$xr" "$comp" "$funcs" --extra-resources "$extra_dir" > "$out.tmp" 2>"$out.err"
    else
      crossplane render "$xr" "$comp" "$funcs" > "$out.tmp" 2>"$out.err"
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
