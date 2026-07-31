#!/usr/bin/env bash
# Regenerate the golden render snapshots in place and fail if the committed
# goldens have drifted — i.e. a Composition/XR/Function change altered rendered
# output without the snapshot being updated in the same commit.
#
# Used by the render-golden CI workflow and runnable locally:
#     tests/render/check-golden.sh            # all Configurations
#     CONFIG=k8s/namespace tests/render/check-golden.sh
#
# Semantics: `git diff --exit-code` reports drift in TRACKED goldens only, so
# before the goldens are seeded (none tracked yet) this passes and the freshly
# generated files are left untracked for a maintainer to commit. Once seeded,
# any change to a committed *.rendered.yaml fails the check.
set -euo pipefail

ROOT="${ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
cd "$ROOT"

here="$(cd "$(dirname "$0")" && pwd)"
"$here/render-golden.sh"

# Drift in already-committed goldens.
if ! git diff --exit-code -- '**/examples/*.rendered.yaml'; then
  echo >&2
  echo "check-golden: rendered output drifted from the committed goldens." >&2
  echo "  Review the diff above. If the change is intended, commit the updated" >&2
  echo "  snapshots:  git add '**/examples/*.rendered.yaml'" >&2
  exit 1
fi

# New, not-yet-committed goldens (first seed, or a newly added XR/Configuration).
untracked=$(git ls-files --others --exclude-standard -- '**/examples/*.rendered.yaml')
if [ -n "$untracked" ]; then
  echo
  echo "check-golden: new goldens generated (not yet committed):"
  while IFS= read -r path; do
    [ -n "$path" ] && echo "  + $path"
  done <<EOF
$untracked
EOF
  echo "  Commit them to seed the snapshot:  git add '**/examples/*.rendered.yaml'"
fi

echo "check-golden: committed goldens are up to date"
