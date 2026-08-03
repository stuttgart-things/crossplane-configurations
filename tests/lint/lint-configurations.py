#!/usr/bin/env python3
"""Structural invariant linter for the Crossplane Configuration packages.

This enforces the repo conventions that are documented in the root CLAUDE.md as
"gotchas we learned (don't repeat)" but were, until now, only guarded by memory.
Several of them are load-bearing — e.g. a long-named Function CR on the same
registry as a dependsOn-derived one froze the package resolver fleet-wide, and
`deletionPolicy` on the namespaced Object variant is a hard schema rejection.

It is a *structural* check: it parses the package YAML and asserts shape. It does
NOT render Compositions (that is what `task verify` / the CI verify job do) — so
it is fast, needs no cluster, and runs in pre-commit and CI.

A "Configuration" is any directory containing a `crossplane.yaml` that is not
itself under an `examples/` subtree.

Rules are split by severity:
  ERROR   — a hard convention violation; fails the lint (exit 1).
  WARNING — drift from a documented preference that is not yet universal in the
            repo (e.g. dependsOn version caps), or a heuristic that can have
            false positives (e.g. deletionPolicy detection). Reported, never
            fatal.

Usage:
    python3 tests/lint/lint-configurations.py [--root .] [--strict]

    --strict  treat warnings as errors too.

Requires PyYAML (declared as the pre-commit hook's additional_dependencies, and
`pip install`ed in the CI job).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

# The Function CRs we author pin the upbound mirror ON PURPOSE (root CLAUDE.md,
# "Function CR names"): our dependsOn entries use xpkg.crossplane.io, and the
# package manager derives a long-named CR from that path — so a short-named CR
# on the SAME registry collides in the package Lock graph. The differing mirror
# is what keeps the two Lock nodes distinct. Hence functions.yaml must use
# xpkg.upbound.io, not "the canonical" xpkg.crossplane.io.
FUNCTIONS_REGISTRY = "xpkg.upbound.io"

REQUIRED_ANNOTATIONS = (
    "meta.crossplane.io/version",
    "meta.crossplane.io/maintainer",
    "meta.crossplane.io/source",
    "meta.crossplane.io/license",
    "meta.crossplane.io/description",
    "meta.crossplane.io/readme",
)

# Files every Configuration package must ship. examples/configuration.yaml is
# intentionally NOT required — several Configurations legitimately omit it.
REQUIRED_FILES = (
    "crossplane.yaml",
    "apis/definition.yaml",
    "apis/composition.yaml",
    "README.md",
    "examples/functions.yaml",
    "examples/xr-min.yaml",
    "examples/xr.yaml",
    "examples/xr-max.yaml",
)

LONG_FUNCTION_PREFIX = "crossplane-contrib-"


class Findings:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, config: str, msg: str) -> None:
        self.errors.append(f"{config}: {msg}")

    def warn(self, config: str, msg: str) -> None:
        self.warnings.append(f"{config}: {msg}")


def load_yaml_docs(path: Path) -> list:
    """Return all YAML documents in a file, or raise for a parse error."""
    with path.open() as fh:
        return [d for d in yaml.safe_load_all(fh) if d is not None]


def load_single(path: Path):
    docs = load_yaml_docs(path)
    if not docs:
        raise ValueError("no YAML documents")
    return docs[0]


def find_configs(root: Path) -> list[Path]:
    configs = []
    for meta in root.rglob("crossplane.yaml"):
        parts = meta.relative_to(root).parts
        if ".git" in parts or "examples" in parts:
            continue
        configs.append(meta.parent)
    return sorted(configs, key=lambda p: str(p))


def check_files(config: str, cdir: Path, f: Findings) -> None:
    for rel in REQUIRED_FILES:
        if not (cdir / rel).is_file():
            f.error(config, f"missing required file {rel}")
    # No claim-shaped examples (Crossplane v2 has no Claim kind).
    if (cdir / "examples/claim.yaml").exists():
        f.error(config, "examples/claim.yaml exists — v2 has no Claim; use xr.yaml")


def check_crossplane_meta(config: str, cdir: Path, f: Findings) -> None:
    path = cdir / "crossplane.yaml"
    if not path.is_file():
        return
    try:
        doc = load_single(path)
    except Exception as exc:  # noqa: BLE001 - report parse errors as findings
        f.error(config, f"crossplane.yaml: unparseable ({exc})")
        return
    if doc.get("apiVersion") != "meta.pkg.crossplane.io/v1":
        f.error(config, f"crossplane.yaml: apiVersion must be "
                        f"meta.pkg.crossplane.io/v1 (got {doc.get('apiVersion')!r})")
    if doc.get("kind") != "Configuration":
        f.error(config, f"crossplane.yaml: kind must be Configuration "
                        f"(got {doc.get('kind')!r})")
    annotations = (doc.get("metadata") or {}).get("annotations") or {}
    for key in REQUIRED_ANNOTATIONS:
        val = annotations.get(key)
        if val is None or (isinstance(val, str) and val.strip() == ""):
            f.error(config, f"crossplane.yaml: missing/empty annotation {key}")

    # dependsOn version constraints: floor is required, cap is preferred.
    for dep in doc.get("spec", {}).get("dependsOn", []) or []:
        name = dep.get("provider") or dep.get("function") or dep.get("configuration") \
            or dep.get("package") or "<unnamed dep>"
        version = dep.get("version")
        if not version:
            f.error(config, f"dependsOn {name}: no version constraint")
            continue
        version = str(version)
        if ">=" not in version and not version.startswith("v"):
            f.error(config, f"dependsOn {name}: version {version!r} has no floor (>=)")
        if "<" not in version:
            f.warn(config, f"dependsOn {name}: version {version!r} has no upper cap "
                           f"(<vX.0.0) — CLAUDE.md asks for floor AND cap")


def check_definition(config: str, cdir: Path, f: Findings) -> None:
    path = cdir / "apis/definition.yaml"
    if not path.is_file():
        return
    try:
        doc = load_single(path)
    except Exception as exc:  # noqa: BLE001
        f.error(config, f"definition.yaml: unparseable ({exc})")
        return
    if doc.get("apiVersion") != "apiextensions.crossplane.io/v2":
        f.error(config, f"definition.yaml: XRD must be apiextensions.crossplane.io/v2 "
                        f"(got {doc.get('apiVersion')!r})")
    spec = doc.get("spec") or {}
    if spec.get("scope") != "Namespaced":
        f.error(config, f"definition.yaml: spec.scope must be Namespaced "
                        f"(got {spec.get('scope')!r}) — v2 XRs are namespaced")
    if "claimNames" in spec:
        f.error(config, "definition.yaml: spec.claimNames set — v2 has no Claim kind")
    if "claimNames" in (spec.get("names") or {}):
        f.error(config, "definition.yaml: spec.names.claimNames set — v2 has no Claim")


def check_composition(config: str, cdir: Path, f: Findings) -> None:
    path = cdir / "apis/composition.yaml"
    if not path.is_file():
        return
    try:
        doc = load_single(path)
        raw = path.read_text()
    except Exception as exc:  # noqa: BLE001
        f.error(config, f"composition.yaml: unparseable ({exc})")
        return
    # Composition stays on /v1 — there is no Composition/v2.
    if doc.get("apiVersion") != "apiextensions.crossplane.io/v1":
        f.error(config, f"composition.yaml: Composition must be "
                        f"apiextensions.crossplane.io/v1 (got {doc.get('apiVersion')!r})")
    spec = doc.get("spec") or {}
    if spec.get("mode") != "Pipeline":
        f.error(config, f"composition.yaml: spec.mode must be Pipeline "
                        f"(got {spec.get('mode')!r})")
    # Function CR references must use the short form.
    for step in spec.get("pipeline", []) or []:
        ref = (step.get("functionRef") or {}).get("name", "")
        if ref.startswith(LONG_FUNCTION_PREFIX):
            f.error(config, f"composition.yaml: functionRef.name {ref!r} uses the long "
                            f"form — use the short name (drop {LONG_FUNCTION_PREFIX!r})")
    # deletionPolicy is a schema rejection on kubernetes.m.crossplane.io/v1alpha1
    # Objects. Heuristic (templated YAML can't be parsed field-by-field): warn if
    # the string appears at all, since the whole repo is on the m-variant.
    if re.search(r"\bdeletionPolicy\b", raw):
        f.warn(config, "composition.yaml: 'deletionPolicy' present — invalid on "
                       "kubernetes.m.crossplane.io/v1alpha1 Objects; use "
                       "managementPolicies (ignore if this is a legacy v1alpha2 Object)")


def check_functions(config: str, cdir: Path, f: Findings) -> None:
    path = cdir / "examples/functions.yaml"
    if not path.is_file():
        return
    try:
        docs = load_yaml_docs(path)
    except Exception as exc:  # noqa: BLE001
        f.error(config, f"functions.yaml: unparseable ({exc})")
        return
    for doc in docs:
        if not isinstance(doc, dict) or doc.get("kind") != "Function":
            continue
        name = (doc.get("metadata") or {}).get("name", "")
        if name.startswith(LONG_FUNCTION_PREFIX):
            f.error(config, f"functions.yaml: Function name {name!r} uses the long form "
                            f"— use the short name (drop {LONG_FUNCTION_PREFIX!r})")
        package = (doc.get("spec") or {}).get("package", "")
        if package and not package.startswith(FUNCTIONS_REGISTRY + "/"):
            f.error(config, f"functions.yaml: Function {name!r} package {package!r} must "
                            f"pin {FUNCTIONS_REGISTRY} (the deliberate mirror split — see "
                            f"CLAUDE.md); do NOT 'modernise' to xpkg.crossplane.io")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".", help="repo root (default: cwd)")
    ap.add_argument("--strict", action="store_true",
                    help="treat warnings as errors")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    configs = find_configs(root)
    if not configs:
        print(f"no Configurations found under {root}", file=sys.stderr)
        return 1

    f = Findings()
    for cdir in configs:
        config = str(cdir.relative_to(root))
        check_files(config, cdir, f)
        check_crossplane_meta(config, cdir, f)
        check_definition(config, cdir, f)
        check_composition(config, cdir, f)
        check_functions(config, cdir, f)

    for w in f.warnings:
        print(f"WARN  {w}")
    for e in f.errors:
        print(f"ERROR {e}")

    n_err, n_warn = len(f.errors), len(f.warnings)
    print(f"\nlint-configurations: {len(configs)} Configurations, "
          f"{n_err} error(s), {n_warn} warning(s)")

    if n_err or (args.strict and n_warn):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
