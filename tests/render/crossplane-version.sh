#!/usr/bin/env bash
# Sourceable guard for the golden-snapshot scripts: load the repo's pinned
# Crossplane versions, then refuse to run on a CLI older than the floor.
#
# WHY a guard and not a note in the README. `render-golden.sh` probes for the
# `--crossplane-image` FLAG, not for a version, and falls back silently when it
# is absent. That fallback is right for its stated purpose — the same script
# runs on both CLI generations — but it is also what let a v2.1.3 CLI through
# and rewrite all 106 goldens, dropping spec.crossplane.resourceRefs from every
# one of them. Nothing in the run said the CLI was too old: the script printed
# `wrote <file>` per snapshot with no version anywhere, and the diff was uniform
# removals of boilerplate, i.e. it read like a cleanup rather than damage (#393).
#
# The same guard belongs in check-golden.sh: a CHECK run with an old CLI reports
# drift that is not there, and would send someone hunting a phantom regression.
#
# Not executable on its own — source it:
#     . "$(dirname "$0")/crossplane-version.sh"
#     crossplane_versions_load
#     crossplane_version_guard render-golden

# Load crossplane-versions.env (the single source) into the environment.
# Values already set in the environment win, so CI and one-off overrides still
# work: `CROSSPLANE_RENDER_IMAGE=... tests/render/render-golden.sh`.
crossplane_versions_load() {
  local root="${ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
  local file="${CROSSPLANE_VERSIONS_FILE:-$root/crossplane-versions.env}"

  if [ ! -f "$file" ]; then
    echo "crossplane-version: $file not found" >&2
    return 1
  fi

  # Bash indirection rather than eval: the file is repo-controlled, but a
  # `KEY=value` reader that evals is a habit worth not having. `continue` is
  # spelled out with if/then/fi because `[ ... ] && continue` returns 1 on the
  # else branch, which under `set -e` kills the caller mid-loop.
  local key value
  while IFS='=' read -r key value; do
    case "$key" in ''|\#*) continue ;; esac
    if [ -n "${!key:-}" ]; then
      # Already set by the caller (CI, or a one-off override) — that wins.
      continue
    fi
    printf -v "$key" '%s' "$value"
    export "${key?}"
  done < "$file"
}

# Compare two dotted versions. True when $1 is strictly older than $2.
crossplane_version_lt() {
  [ "$1" != "$2" ] &&
    [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -n1)" = "$1" ]
}

# Refuse to continue on a CLI below CROSSPLANE_MIN_VERSION, and — pass or fail —
# say which CLI is about to render. $1 is the caller's name, used as the message
# prefix so the output reads the same as the rest of the script's.
crossplane_version_guard() {
  local ctx="${1:-crossplane-version}"

  if ! command -v crossplane >/dev/null 2>&1; then
    echo "$ctx: 'crossplane' CLI not found on PATH" >&2
    return 127
  fi

  if [ -n "${CROSSPLANE_SKIP_VERSION_CHECK:-}" ]; then
    echo "$ctx: CROSSPLANE_SKIP_VERSION_CHECK set — version guard disabled" >&2
    return 0
  fi

  # --client, because plain `crossplane version` also reaches for the SERVER
  # version and dies on "failed to get kubeconfig" where there is no cluster.
  # There is no `--version` flag to fall back to.
  local raw have
  raw=$(crossplane version --client 2>/dev/null || true)
  have=$(printf '%s\n' "$raw" |
           sed -n 's/.*[Vv]ersion:[[:space:]]*v\{0,1\}\([0-9][0-9.]*\).*/\1/p' |
           head -n1)

  if [ -z "$have" ]; then
    # Refusing rather than warning: an unrecognised format means the check is
    # not doing its job, and the failure this guards against is silent. The
    # escape hatch is one env var, named in the message.
    {
      echo "$ctx: cannot read a version from 'crossplane version --client':"
      printf '%s\n' "$raw" | sed 's/^/    /'
      echo "$ctx: refusing to render with an unidentified CLI."
      echo "       Override with CROSSPLANE_SKIP_VERSION_CHECK=1 if you are sure."
    } >&2
    return 1
  fi

  local min="${CROSSPLANE_MIN_VERSION#v}"
  local pin="${CROSSPLANE_VERSION:-unset}"
  if crossplane_version_lt "$have" "$min"; then
    {
      echo "$ctx: crossplane v$have is older than the required v$min"
      echo "       CI pins $pin (crossplane-versions.env)."
      echo "       Regenerating with this CLI drops spec.crossplane.resourceRefs"
      echo "       from every snapshot. Refusing."
      echo "       Install: https://cli.crossplane.io/stable/$pin/bin/"
    } >&2
    return 1
  fi

  echo "$ctx: crossplane v$have (CI pins $pin, floor v$min)"
}
