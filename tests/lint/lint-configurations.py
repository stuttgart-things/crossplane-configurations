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

    --strict    treat warnings as errors too.
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


def _fetch_tags(package: str) -> list[str] | None:
    """Published tags for one package, or None if the registry could not be asked.

    None means "no answer", NOT "no tags" — the caller must not treat the two
    alike. That distinction is the whole reason this returns an Optional: a
    timeout reported as "never published" would accuse every package on the
    cluster the moment GHCR hiccups.

    Plain HTTP against the Docker registry API rather than shelling out to
    `oras`: the linter otherwise needs nothing but PyYAML, and it runs as a
    pre-commit hook where an extra binary is one more thing to install and to
    have missing. The anonymous token below is what `docker pull` uses for a
    public package; no credential is involved.
    """
    repo = f"{GHCR_REPO_PREFIX}/{package}"
    tags: list[str] = []
    try:
        with urllib.request.urlopen(
            f"https://ghcr.io/token?scope=repository:{repo}:pull&service=ghcr.io",
            timeout=GHCR_TIMEOUT,
        ) as r:
            token = json.load(r)["token"]
        url = f"https://ghcr.io/v2/{repo}/tags/list"
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
        # 403/404 are ANSWERS: no public artifact exists under this name.
        #
        # GHCR does not 404 for a package that was never pushed — it hands out a
        # token that the tags endpoint then rejects, and the *token* request
        # already comes back 403 (measured against `cilium` and `vault-config`,
        # 2026-08-20). Treating that as "unreachable" would silently drop the
        # third case this check exists for: a package marked '—' in the table
        # while artifacts exist, or one that everyone assumes is published and
        # is not.
        #
        # A private package answers 403 too. That is fine to fold in here: every
        # Configuration in this repo is public by policy — `task push` verifies
        # it after every push — and a private one could not be pulled by
        # Crossplane anyway. Either way the operator needs to hear about it.
        #
        # Everything else (5xx, rate limit, a proxy in the way) is the registry
        # declining to answer, which is not the package's fault.
        return [] if e.code in (403, 404) else None
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

    # One round trip per package; serially that is ~30 s of pure waiting.
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        fetched = dict(zip(
            (t[1] for t in targets),
            pool.map(_fetch_tags, (t[1] for t in targets)),
        ))

    skipped = []
    for rel, name, version in targets:
        tags = fetched.get(name)
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

        if version not in tags:
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

    check_readme_table(root, configs, f)
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
