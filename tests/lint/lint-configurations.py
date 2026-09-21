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

One check breaks that rule deliberately and is therefore opt-in: `--registry`
asks ghcr.io whether the version the repo declares was ever published. Nothing
else here can see that, because the repo agreeing with itself says nothing about
what a `helm`/`task push` run can actually pull.

A "Configuration" is any directory containing a `crossplane.yaml` that is not
itself under an `examples/` subtree.

Rules are split by severity:
  ERROR   — a hard convention violation; fails the lint (exit 1).
  WARNING — drift from a documented preference that is not yet universal in the
            repo (e.g. dependsOn version caps), or a heuristic that can have
            false positives (e.g. deletionPolicy detection). Reported, never
            fatal.

Usage:
    python3 tests/lint/lint-configurations.py [--root .] [--strict] [--registry]
    python3 tests/lint/lint-configurations.py --write

    --strict    treat warnings as errors too.
    --write     regenerate the derived diagrams under docs/diagrams/ and exit.
                They are otherwise CHECKED, like gofmt -l: a committed diagram
                that no longer matches the repo fails the lint, so the
                regenerated file lands in the same PR as the change that moved
                it (#302).
    --registry  additionally compare each package against its published tags in
                ghcr.io. OFF by default: it is the one check here that needs
                network, and a linter that goes red when a registry has a bad
                day gets switched off — after which it checks nothing at all.
                CI turns it on; local runs stay offline and instant.

Requires PyYAML (declared as the pre-commit hook's additional_dependencies, and
`pip install`ed in the CI job).
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import sys
import urllib.error
import urllib.request
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

# A cluster-scoped XRD must say why, on the XRD itself (see check_definition).
CLUSTER_SCOPE_REASON = "stuttgart-things.com/cluster-scope-reason"
CLUSTER_SCOPE_REASON_MIN = 40


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
    """Every Configuration directory in the working tree.

    DOT-DIRECTORIES ARE PRUNED WHOLESALE, not just `.git`. A git worktree is an
    ordinary directory with the whole repo checked out inside it, and the
    convention here puts them under `.claude/worktrees/<name>/` -- so a single
    leftover worktree made the linter walk a SECOND copy of every package:

      lint-configurations: 70 Configurations, 38 error(s), 52 warning(s)

    Every one of those errors was about the worktree's stale checkout, which is
    not the tree anyone is linting. Two of them were the kind that stops a push
    on purpose ("ghcr.io has v0.11.6, repo declares v0.10.0 ... DIFFERENT
    artifacts"), and they were false. CI never saw any of it -- a fresh
    checkout has no worktrees -- so the noise landed only on the workstation,
    which is exactly where the linter is supposed to be believed.

    `.git` alone was never the right rule; it is one dot-directory among the
    ones that accumulate beside a checkout (`.claude/`, `.venv/`, `.task/`).
    None of them can hold a Configuration this repo publishes.
    """
    configs = []
    for meta in root.rglob("crossplane.yaml"):
        parts = meta.relative_to(root).parts
        if any(part.startswith(".") for part in parts) or "examples" in parts:
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
    scope = spec.get("scope")
    if scope == "Cluster":
        # The one exception, and it has to argue for itself where it is taken.
        # An annotation on the XRD rather than an allow-list here: the reason
        # sits next to `scope: Cluster`, a review sees both in one diff, and it
        # cannot drift away from the XRD it excuses. The length floor only
        # rejects the empty and the placeholder ("tbd", "needs cluster scope");
        # whether the argument holds is the review's job.
        reason = ((doc.get("metadata") or {}).get("annotations") or {}).get(CLUSTER_SCOPE_REASON)
        reason = reason.strip() if isinstance(reason, str) else ""
        if len(reason) < CLUSTER_SCOPE_REASON_MIN:
            f.error(config, f"definition.yaml: spec.scope Cluster needs annotation "
                            f"{CLUSTER_SCOPE_REASON} stating the actual argument "
                            f"(at least {CLUSTER_SCOPE_REASON_MIN} characters, got "
                            f"{len(reason)}) — v2 XRs are namespaced unless an XRD says why not")
    elif scope != "Namespaced":
        f.error(config, f"definition.yaml: spec.scope must be Namespaced "
                        f"(got {scope!r}) — v2 XRs are namespaced; Cluster only with "
                        f"{CLUSTER_SCOPE_REASON}")
    if "claimNames" in spec:
        f.error(config, "definition.yaml: spec.claimNames set — v2 has no Claim kind")
    if "claimNames" in (spec.get("names") or {}):
        f.error(config, "definition.yaml: spec.names.claimNames set — v2 has no Claim")


def check_examples_against_xrd(config: str, cdir: Path, f: Findings) -> None:
    """Every spec field an example XR uses must be declared in the XRD.

    The CI verify harness already checks this (Layer 1: kubeconform against a
    JSON schema generated from the XRD, where an undeclared field surfaces as
    `additionalProperties '<name>' not allowed`). But it only runs after a push,
    on a runner, per Configuration — and the failure mode it catches is one you
    produce locally: add a field to the Composition and the examples, forget the
    XRD, or add it to the XRD at the wrong indentation so it lands beside
    `properties` instead of inside it. The YAML still parses, the render still
    works, the goldens still regenerate. Nothing local says a word.

    Checking it here costs a dict lookup and moves that finding from a CI round
    trip to the commit that causes it. ERROR, because on a cluster the API
    server rejects such an XR outright.
    """
    xrd_path = cdir / "apis/definition.yaml"
    if not xrd_path.is_file():
        return
    try:
        xrd = load_single(xrd_path)
    except Exception:  # noqa: BLE001
        return  # already reported by check_definition
    spec = xrd.get("spec") or {}
    kind = (spec.get("names") or {}).get("kind")
    versions = spec.get("versions") or []
    if not kind or not versions:
        return
    schema = ((versions[0].get("schema") or {}).get("openAPIV3Schema") or {})
    declared = set((((schema.get("properties") or {}).get("spec") or {})
                    .get("properties") or {}))
    if not declared:
        return

    for ex in sorted((cdir / "examples").glob("xr*.yaml")):
        try:
            doc = load_single(ex)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(doc, dict) or doc.get("kind") != kind:
            continue
        used = set((doc.get("spec") or {}))
        undeclared = sorted(used - declared)
        if undeclared:
            f.error(config, f"examples/{ex.name}: spec field(s) not declared in the XRD: "
                            f"{', '.join(undeclared)} — the API server rejects this XR, "
                            f"and CI verify fails with \"additionalProperties not allowed\"")


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


# A row of the root README's Configurations table:
#   | <category> | [<name>](<path>/) | <version> | <description> | <OCI> |
README_ROW = re.compile(
    r"^\|\s*[a-z0-9-]+\s*\|\s*\[([^\]]+)\]\(([^)]+)\)\s*\|\s*(\S+)\s*\|"
)

# Version cell for a Configuration that exists in the repo but was never pushed.
# Not a placeholder to be filled in later — it says "no OCI artifact exists".
UNPUBLISHED = "—"


def check_readme_table(root: Path, configs: list[Path], f: Findings) -> None:
    """The root README's Configurations table must list every package, at its
    real version.

    Repo-level, not per-Configuration. `task push` bumps
    meta.crossplane.io/version in the package and nothing updates the table, so
    this drifts silently — by 2026-08 it was wrong for 11 of 26 rows and missing
    3 packages entirely, which makes the table worse than no table: it reads as
    authoritative while pointing at versions that were months old.

    An error rather than a warning on purpose: the fix is one line, and only a
    failing check reliably lands it in the same PR as the bump.
    """
    readme = root / "README.md"
    if not readme.exists():
        f.error("README.md", "missing")
        return

    listed: dict[str, str] = {}
    for line in readme.read_text().splitlines():
        m = README_ROW.match(line)
        if m:
            listed[m.group(2).rstrip("/")] = m.group(3)

    for cdir in configs:
        rel = str(cdir.relative_to(root))
        if rel not in listed:
            f.error("README.md", f"Configurations table has no row for {rel}")
            continue
        if listed[rel] == UNPUBLISHED:
            continue
        try:
            meta = load_single(cdir / "crossplane.yaml")
            actual = (meta.get("metadata", {}).get("annotations", {})
                      or {}).get("meta.crossplane.io/version")
        except Exception:
            continue  # check_crossplane_meta reports the parse failure
        if actual and listed[rel] != actual:
            f.error("README.md",
                    f"{rel}: table says {listed[rel]}, "
                    f"crossplane.yaml says {actual}")

    known = {str(c.relative_to(root)) for c in configs}
    for path in sorted(set(listed) - known):
        f.error("README.md",
                f"table row {path} has no crossplane.yaml — stale or mistyped")


# ---------------------------------------------------------------------------
# Derived diagrams (--write)
# ---------------------------------------------------------------------------

DIAGRAMS_DIR = "docs/diagrams"

GENERATED_BANNER = (
    "<!-- GENERATED FILE — do not edit by hand.\n"
    "     Regenerate with: python3 tests/lint/lint-configurations.py --write\n"
    "     The generator is check_diagrams() in tests/lint/lint-configurations.py. -->"
)

# Everything below is parsed, never pattern-matched out of a Composition body.
# The first draft of this did match: it looked for a sibling XR's kind in the
# Composition text to draw "this XR composes that one". It reported
# `ArgocdCluster -> ClusterStack` from a COMMENT ("A ClusterStack lives in…"),
# and missed vm-batch's real children because their kind is computed
# (`"NativeProxmoxVM" if provider == …`). Both directions wrong, in a file that a
# reviewer is asked to approve — which is exactly the "a wrong diagram is worse
# than no diagram" this generator exists to prevent (#301). So the composed-
# resource level is simply not claimed here; what the repo states exactly
# (dependsOn, XRD, module pins) is.
REPO_PACKAGE_PREFIX = "ghcr.io/stuttgart-things/crossplane-configurations/"


def _mermaid_id(name: str) -> str:
    """A Mermaid-safe node id. Package names are unique, so this is injective."""
    return "n_" + re.sub(r"[^A-Za-z0-9_]", "_", name)


def collect_facts(root: Path, configs: list[Path]) -> list[dict]:
    """One record per Configuration, from parsed YAML only.

    A file that cannot be parsed is SKIPPED rather than guessed at — the other
    checks already report it as an error, and half-read facts in a committed
    artifact are worse than a missing row.
    """
    facts = []
    for cdir in configs:
        rel = str(cdir.relative_to(root))
        try:
            meta = load_single(cdir / "crossplane.yaml")
            xrd = load_single(cdir / "apis/definition.yaml")
            comp = load_single(cdir / "apis/composition.yaml")
        except Exception:  # noqa: BLE001
            continue
        annotations = (meta.get("metadata") or {}).get("annotations") or {}
        xrd_spec = xrd.get("spec") or {}

        deps_pkg, deps_ext = [], []
        for dep in (meta.get("spec") or {}).get("dependsOn") or []:
            ref = (dep.get("configuration") or dep.get("provider")
                   or dep.get("function") or dep.get("package") or "")
            if ref.startswith(REPO_PACKAGE_PREFIX):
                deps_pkg.append(ref[len(REPO_PACKAGE_PREFIX):])
            elif ref:
                deps_ext.append(ref)

        steps = []
        for step in (comp.get("spec") or {}).get("pipeline") or []:
            inp = step.get("input") or {}
            spec = inp.get("spec")
            source = spec.get("source") if isinstance(spec, dict) else None
            steps.append({
                "name": step.get("step", ""),
                "function": (step.get("functionRef") or {}).get("name", ""),
                "module": source.strip() if isinstance(source, str)
                          and source.strip().startswith("oci://") else None,
            })

        facts.append({
            "path": rel,
            "category": rel.split("/")[0],
            "package": (meta.get("metadata") or {}).get("name") or rel.split("/")[-1],
            "version": annotations.get("meta.crossplane.io/version") or "—",
            "kind": (xrd_spec.get("names") or {}).get("kind") or "?",
            "group": xrd_spec.get("group") or "?",
            "scope": xrd_spec.get("scope") or "?",
            "deps": sorted(deps_pkg),
            "deps_ext": sorted(deps_ext),
            "steps": steps,
        })
    return sorted(facts, key=lambda f: (f["category"], f["package"]))


def render_xr_ownership(facts: list[dict]) -> str:
    known = {f["package"] for f in facts}
    by_pkg = {f["package"]: f for f in facts}

    edges = sorted({(f["package"], d) for f in facts for d in f["deps"] if d in known})
    depended_on = {d for _, d in edges}
    has_deps = {s for s, _ in edges}

    out = [GENERATED_BANNER, "", "# XR ownership", "",
           "Which Configuration brings which along, and what each one's XR is.",
           "Derived from the repo, not maintained beside it: every number and every",
           "edge below is parsed out of `*/crossplane.yaml`, `apis/definition.yaml`",
           "and `apis/composition.yaml` at generation time. A stale copy fails the",
           "lint rather than quietly misinforming — see *How this stays true* at the",
           "bottom.", ""]

    # --- the graph -------------------------------------------------------
    out += ["## The chain", "",
            "`A --> B` reads *A's package declares B in `dependsOn`*, so installing A",
            "installs B. Only edges inside this repo are drawn; providers and",
            "functions are listed per Configuration further down.", "",
            "```mermaid", "graph LR"]
    for cat in sorted({f["category"] for f in facts}):
        members = [f for f in facts if f["category"] == cat
                   and (f["package"] in has_deps or f["package"] in depended_on)]
        if not members:
            continue
        out.append(f'  subgraph {cat}')
        for f in members:
            out.append(f'    {_mermaid_id(f["package"])}["{f["package"]}<br/><i>{f["kind"]}</i>"]')
        out.append("  end")
    for src, dst in edges:
        out.append(f'  {_mermaid_id(src)} --> {_mermaid_id(dst)}')
    out += ["```", ""]

    standalone = sorted(f["package"] for f in facts
                        if f["package"] not in has_deps and f["package"] not in depended_on)
    if standalone:
        out += [f"Not in the graph ({len(standalone)}): "
                + ", ".join(f"`{p}`" for p in standalone)
                + " — neither depends on a Configuration of this repo nor is"
                  " depended on by one.", ""]

    roots = sorted(p for p in has_deps if p not in depended_on)
    if roots:
        out += ["Entry points (nothing in this repo depends on them): "
                + ", ".join(f"`{p}`" for p in roots) + ".", ""]

    # --- the table -------------------------------------------------------
    out += ["## What each one is", "",
            "| category | Configuration | XR kind | group | scope | version | brings along |",
            "|---|---|---|---|---|---|---|"]
    for f in facts:
        brings = ", ".join(f"`{d}`" for d in f["deps"] if d in known) or "—"
        out.append(f'| {f["category"]} | [{f["package"]}]({"../../" + f["path"]}/) '
                   f'| `{f["kind"]}` | `{f["group"]}` | {f["scope"]} | {f["version"]} | {brings} |')
    out.append("")

    # --- bodies ----------------------------------------------------------
    module_bodied = [f for f in facts if any(s["module"] for s in f["steps"])]
    out += ["## Where each Composition's body lives", "",
            "A pipeline step whose `source` is an `oci://` reference has its logic in",
            "a KCL module, not in this repo. That is the boundary of what these",
            "diagrams can see.", "",
            "| Configuration | pipeline | module pins |", "|---|---|---|"]
    for f in facts:
        pipeline = " → ".join(s["function"].replace("function-", "") for s in f["steps"]) or "—"
        mods = "<br/>".join(f"`{s['module']}`" for s in f["steps"] if s["module"]) or "inline"
        out.append(f'| `{f["package"]}` | {pipeline} | {mods} |')
    out.append("")

    # --- external deps ---------------------------------------------------
    out += ["## Providers and functions each one requires", "",
            "| Configuration | dependsOn (outside this repo) |", "|---|---|"]
    for f in facts:
        ext = "<br/>".join(f"`{d}`" for d in f["deps_ext"]) or "—"
        out.append(f'| `{f["package"]}` | {ext} |')
    out.append("")

    # --- the honest part -------------------------------------------------
    out += [
        "## What this deliberately does not show", "",
        f"**Which managed resources an XR composes.** {len(module_bodied)} of "
        f"{len(facts)} Configurations delegate their Composition body to a KCL",
        "module (see the table above), so their children are not in this repo at",
        "all. For the rest the body is inline, but reading kinds out of it means",
        "pattern-matching a template — and the first draft of this generator did",
        "exactly that: it reported an edge that came from a **comment**, and missed",
        "the real children of a Composition that computes its kind. Both errors",
        "land in a file a reviewer is asked to approve. Pulling the modules over",
        "OCI is the honest way to close this gap (#302, step 4).", "",
        "**Runtime numbers.** How many Objects a Platform holds, which apps are",
        "synced, what a cluster currently runs — none of it is in these files, so",
        "none of it is here. That is the [#301](https://github.com/stuttgart-things/crossplane-configurations/issues/301)",
        "rule: a diagram that reads as authoritative while pointing at last",
        "month's state is worse than no diagram.", "",
        "## How this stays true", "",
        "`check_diagrams()` in `tests/lint/lint-configurations.py` regenerates this",
        "file in memory on every lint run and fails when the committed copy",
        "differs — the same shape as `gofmt -l`, and the same reason",
        "`check_readme_table()` is an ERROR rather than a warning: the fix is one",
        "command, and only a failing check reliably lands it in the same PR as the",
        "change that caused it.", "",
        "```console",
        "$ python3 tests/lint/lint-configurations.py           # checks, red on drift",
        "$ python3 tests/lint/lint-configurations.py --write   # regenerates docs/diagrams/",
        "```", "",
        "It runs in the `lint-invariants` CI job, which is ungated by `discover` —",
        "a cross-cutting artifact can go stale from a change to any package file.",
        "",
    ]
    return "\n".join(out)


def build_diagrams(root: Path, configs: list[Path]) -> dict[str, str]:
    """Relative path -> file content. One entry per generated diagram."""
    facts = collect_facts(root, configs)
    return {f"{DIAGRAMS_DIR}/xr-ownership.md": render_xr_ownership(facts)}


def write_diagrams(root: Path, configs: list[Path]) -> list[str]:
    """Write the generated diagrams; return the paths that actually changed."""
    changed = []
    for rel, content in sorted(build_diagrams(root, configs).items()):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text() != content:
            path.write_text(content)
            changed.append(rel)
    return changed


def check_diagrams(root: Path, configs: list[Path], f: Findings) -> None:
    """The committed diagrams must equal what the repo generates right now.

    Same drift pattern as check_readme_table, for the same reason: a derived
    artifact nobody regenerates is not documentation, it is a claim about a state
    the repo left behind. Failing here puts the regenerated file in the PR that
    caused the change, where a reviewer sees both in one diff — a generator that
    only runs on `main` after the merge hides the change in a bot commit.
    """
    for rel, content in sorted(build_diagrams(root, configs).items()):
        path = root / rel
        if not path.exists():
            f.error(rel, "generated diagram missing — run "
                         "`python3 tests/lint/lint-configurations.py --write`")
            continue
        if path.read_text() != content:
            f.error(rel, "generated diagram is stale (the repo has moved on) — run "
                         "`python3 tests/lint/lint-configurations.py --write` and "
                         "commit the result")


# ---------------------------------------------------------------------------
# Registry parity (--registry)
# ---------------------------------------------------------------------------

GHCR_REPO_PREFIX = "stuttgart-things/crossplane-configurations"
GHCR_TIMEOUT = 10

# vX.Y.Z only. Anything else in the tag list — a branch build, a `latest`, a
# digest-ish string — is ignored rather than guessed at: this check exists to
# compare RELEASES, and a tag we cannot order is not evidence of anything.
SEMVER_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")

# `Link: </v2/…/tags/list?last=…>; rel="next"` — the OCI distribution spec's
# pagination cursor.
NEXT_PAGE = re.compile(r'<([^>]+)>\s*;\s*rel="next"')

# Backstop against a registry that never stops handing out cursors: 50 pages is
# 5000 tags. Looping forever inside CI is a worse failure than a partial answer.
MAX_TAG_PAGES = 50


def _parse_tag(tag: str):
    m = SEMVER_TAG.match(tag)
    return tuple(int(g) for g in m.groups()) if m else None


def _fetch_digest(package: str, tag: str) -> str | None:
    """Manifest digest of one tag, or None if it cannot be read.

    Only used to tell two shapes of "registry is ahead" apart, which need
    opposite reactions:

      same digest as the declared version   someone re-pushed identical content
                                            under a higher tag. Nothing is lost;
                                            the tag is just a lie about history.
      different digest                      an artifact whose source is not in
                                            this repo. Do not push over it
                                            before finding out what it is.

    Without this the check can only say "registry is ahead", and the reader has
    to do exactly this lookup by hand to know which of the two it is.
    """
    repo = f"{GHCR_REPO_PREFIX}/{package}"
    try:
        with urllib.request.urlopen(
            f"https://ghcr.io/token?scope=repository:{repo}:pull&service=ghcr.io",
            timeout=GHCR_TIMEOUT,
        ) as r:
            token = json.load(r)["token"]
        req = urllib.request.Request(
            f"https://ghcr.io/v2/{repo}/manifests/{tag}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": ", ".join((
                    "application/vnd.oci.image.manifest.v1+json",
                    "application/vnd.docker.distribution.manifest.v2+json",
                )),
            },
        )
        with urllib.request.urlopen(req, timeout=GHCR_TIMEOUT) as r:
            return r.headers.get("Docker-Content-Digest")
    except Exception:
        return None


class NotPublic(Exception):
    """The registry has no anonymously pullable artifact under this name.

    Carries WHY, because the two reasons need different fixes and GHCR
    distinguishes them at the token endpoint — measured 2026-08-20:

        403   the package does not exist. Nothing was ever pushed.
        401   it exists but is PRIVATE. A push happened; the visibility flip
              (UI-only) did not.

    The second is the nastier one. `task push` reports success, the README and
    crossplane.yaml agree, the tag is really there — and Crossplane still cannot
    install it, because it pulls anonymously. Exactly that happened to
    vault-config minutes after this check first flagged it as unpublished.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _fetch_tags(package: str) -> list[str] | None:
    """Published tags for one package.

    Returns the tag list, or None if the registry could not be asked. Raises
    NotPublic when the registry ANSWERED that there is nothing to pull.

    Three outcomes, not two, and conflating any pair of them breaks the check:
    a timeout reported as "never published" accuses every package the moment
    GHCR hiccups, and a private package reported as "unreachable" is silently
    dropped — which is the failure this check exists to catch.

    Plain HTTP against the Docker registry API rather than shelling out to
    `oras`: the linter otherwise needs nothing but PyYAML, and it runs as a
    pre-commit hook where an extra binary is one more thing to install and to
    have missing. The anonymous token below is what `docker pull` uses for a
    public package; no credential is involved.
    """
    repo = f"{GHCR_REPO_PREFIX}/{package}"
    try:
        with urllib.request.urlopen(
            f"https://ghcr.io/token?scope=repository:{repo}:pull&service=ghcr.io",
            timeout=GHCR_TIMEOUT,
        ) as r:
            token = json.load(r)["token"]
    except urllib.error.HTTPError as e:
        if e.code == 403:
            raise NotPublic("nothing was ever pushed under this name") from None
        if e.code == 401:
            raise NotPublic(
                "the package exists but is PRIVATE — Crossplane pulls "
                "anonymously and will fail to install it. Flip it to Public in "
                "the GitHub package settings") from None
        return None
    except Exception:
        return None

    url = f"https://ghcr.io/v2/{repo}/tags/list"
    tags: list[str] = []
    try:
        # PAGINATED. Registries cap a tag list and hand out a
        # `Link: …; rel="next"` cursor; reading only the first page reports the
        # packages with the MOST releases as unpublished. Measured on
        # xpkg.crossplane.io while writing the sibling check in
        # stuttgart-things/kcl: exactly 100 tags, cursor set, and three correct
        # Function pins came back as "does not exist". No package here is near
        # the cap today — which is precisely why this would have gone unnoticed
        # until the day one was.
        for _ in range(MAX_TAG_PAGES):
            req = urllib.request.Request(
                url, headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, timeout=GHCR_TIMEOUT) as r:
                tags.extend(json.load(r).get("tags") or [])
                link = NEXT_PAGE.search(r.headers.get("Link", "") or "")
            if not link:
                return tags
            nxt = link.group(1)
            url = nxt if nxt.startswith("http") else f"https://ghcr.io{nxt}"
        return tags
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404):
            raise NotPublic("the registry declined to list its tags") from None
        return None
    except Exception:
        return None


def check_registry_parity(root: Path, configs: list[Path], f: Findings) -> None:
    """Compare each package's declared version against what ghcr.io actually has.

    `check_readme_table` keeps the README and crossplane.yaml agreeing with each
    other. Neither of them knows whether the version they agree on was ever
    pushed — so the two can be in perfect, documented agreement about an
    artifact that does not exist. Found on 2026-08-19: four of 32 Configurations
    had drifted, in three different directions (#345).

    The cost is real and was paid twice. vspherevm v0.9.1 sat in the repo,
    documented and tabled, for a day without being pushed; the machinery play
    pinned v0.9.0 because that was the newest one that could actually be pulled,
    so a run delivered something other than what the repo described, silently.
    proxmoxvm and vm-batch were in the same state at the same time, from the
    same commit.

    Three cases, three severities — and telling them apart is the point:

      registry lacks the version   WARNING  Normal between merge and push. As an
                                            error it would block the very PR
                                            that closes the gap.
      registry has a HIGHER one    ERROR    An artifact whose source is not in
                                            the repo. There is no legitimate
                                            path to that state.
      table says '—', tags exist   ERROR    Marked unpublished while published:
                                            the table is asserting something
                                            false.

    A package the registry would not talk about is SKIPPED, not reported.
    """
    readme = root / "README.md"
    listed: dict[str, str] = {}
    if readme.exists():
        for line in readme.read_text().splitlines():
            m = README_ROW.match(line)
            if m:
                listed[m.group(2).rstrip("/")] = m.group(3)

    # (config-relative-path, package-name, declared-version)
    targets: list[tuple[str, str, str | None]] = []
    for cdir in configs:
        rel = str(cdir.relative_to(root))
        try:
            meta = load_single(cdir / "crossplane.yaml")
        except Exception:
            continue  # check_crossplane_meta already reported it
        name = (meta.get("metadata", {}) or {}).get("name")
        version = ((meta.get("metadata", {}).get("annotations", {}) or {})
                   .get("meta.crossplane.io/version"))
        if not name:
            continue
        targets.append((rel, name, version))

    def probe(name: str):
        """(tags, not-public-reason) — exactly one of the two is set."""
        try:
            return _fetch_tags(name), None
        except NotPublic as e:
            return [], e.reason

    # One round trip per package; serially that is ~30 s of pure waiting.
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        fetched = dict(zip(
            (t[1] for t in targets),
            pool.map(probe, (t[1] for t in targets)),
        ))

    skipped = []
    for rel, name, version in targets:
        tags, not_public = fetched.get(name, (None, None))
        if tags is None:
            skipped.append(name)
            continue

        semver = sorted(filter(None, (_parse_tag(t) for t in tags)))

        newest = "v" + ".".join(map(str, semver[-1])) if semver else None

        if listed.get(rel) == UNPUBLISHED:
            if tags:
                detail = f", newest {newest}" if newest else ""
                f.error(rel, f"README table says {UNPUBLISHED} (unpublished) but "
                             f"ghcr.io has {len(tags)} tag(s){detail}")
            continue

        if not version:
            continue  # check_crossplane_meta reports the missing annotation

        declared = _parse_tag(version)
        if declared is None:
            continue  # not semver; nothing to order it against

        if not_public:
            # NOT a warning like "not pushed yet": this one is a package that
            # looks published from inside the repo and cannot be installed.
            # `task push` reported success; only the visibility flip is missing.
            f.error(rel, f"{version} is not anonymously pullable — {not_public}")
        elif version not in tags:
            f.warn(rel, f"{version} is not in ghcr.io (newest published: "
                        f"{newest or 'none'}) — push it, or the play pins "
                        f"something other than what the repo documents")

        if semver and semver[-1] > declared:
            here = _fetch_digest(name, version)
            there = _fetch_digest(name, newest)
            if here and there and here == there:
                f.error(rel, f"ghcr.io has {newest}, repo declares {version} — "
                             f"same digest ({here[:19]}…), so identical content "
                             f"was re-pushed under a higher tag. Nothing is lost; "
                             f"the registry is simply claiming a version this repo "
                             f"never had. Decide which of the two is real and make "
                             f"the repo say so")
            else:
                f.error(rel, f"ghcr.io has {newest}, repo declares {version}, and "
                             f"they are DIFFERENT artifacts — something was built "
                             f"from a source that is not in this repo. Do not push "
                             f"over it before finding out what it is")

    if skipped:
        print(f"note: registry parity skipped for {len(skipped)} package(s) "
              f"(registry unreachable): {', '.join(sorted(skipped))}",
              file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".", help="repo root (default: cwd)")
    ap.add_argument("--strict", action="store_true",
                    help="treat warnings as errors")
    ap.add_argument("--registry", action="store_true",
                    help="also compare declared versions against ghcr.io tags "
                         "(needs network; skipped silently if unreachable)")
    ap.add_argument("--write", action="store_true",
                    help="regenerate the derived diagrams under docs/diagrams/ "
                         "instead of checking them, then exit. Offline; the other "
                         "checks are not run.")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    configs = find_configs(root)
    if not configs:
        print(f"no Configurations found under {root}", file=sys.stderr)
        return 1

    # `--write` is the generator half of check_diagrams, and it writes rather
    # than reports — so it exits here instead of also running the checks. A run
    # that both rewrites files and returns 1 over an unrelated finding is a
    # confusing thing to put in a Makefile.
    if args.write:
        changed = write_diagrams(root, configs)
        for rel in changed:
            print(f"wrote {rel}")
        print(f"\nlint-configurations --write: {len(configs)} Configurations, "
              f"{len(changed)} file(s) changed")
        return 0

    f = Findings()
    for cdir in configs:
        config = str(cdir.relative_to(root))
        check_files(config, cdir, f)
        check_crossplane_meta(config, cdir, f)
        check_definition(config, cdir, f)
        check_examples_against_xrd(config, cdir, f)
        check_composition(config, cdir, f)
        check_functions(config, cdir, f)

    check_readme_table(root, configs, f)
    check_diagrams(root, configs, f)
    if args.registry:
        check_registry_parity(root, configs, f)

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
