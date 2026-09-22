"""MkDocs hooks for the TechDocs site (mkdocs.yml: `hooks:`).

The per-Configuration pages under docs/configurations/ are GENERATED stubs
(tests/lint/lint-configurations.py --write) that only name their source:

    <!-- include-readme: bootstrap/cluster/README.md -->

This hook replaces that marker with the README at build time, so each README
stays the one source and nothing is copied into docs/. It also rewrites the
README's relative links, which were written for GitHub and would otherwise
404 inside TechDocs:

- a link to another Configuration's directory goes to that Configuration's
  page on this site;
- a link into docs/ stays a link to that page;
- everything else (examples/, apis/, other files) goes to GitHub.

It also points the page's edit link at the README instead of the stub.

pymdownx.snippets would include the file too, but cannot rewrite links -- it
runs after the markdown is handed to the converter, where no hook sees it.
"""

import os
import posixpath
import re

REPO_URL = "https://github.com/stuttgart-things/crossplane-configurations"
BRANCH = "main"

MARKER = re.compile(r"<!--\s*include-readme:\s*(\S+)\s*-->")
# [text](target) and ![alt](target); target without spaces, optional title.
LINK = re.compile(r"(!?\[[^\]]*\]\()([^)\s]+)((?:\s+\"[^\"]*\")?\))")
SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.I)


def _repo_root(config):
    return os.path.dirname(os.path.abspath(config["config_file_path"]))


def _config_page(root, repo_path):
    """docs page for a Configuration directory, or None."""
    d = repo_path.rstrip("/")
    if d and os.path.isfile(os.path.join(root, d, "crossplane.yaml")):
        return f"configurations/{d}.md"
    return None


def _rewrite(markdown, readme_dir, page_src, root):
    page_dir = posixpath.dirname(page_src)

    def sub(m):
        target = m.group(2)
        if SCHEME.match(target) or target.startswith(("#", "/", "mailto:")):
            return m.group(0)
        path, _, anchor = target.partition("#")
        repo_path = posixpath.normpath(posixpath.join(readme_dir, path)) if path else readme_dir
        if repo_path.startswith(".."):
            return m.group(0)
        page = _config_page(root, repo_path)
        if not page and repo_path.startswith("docs/") and repo_path.endswith(".md"):
            page = repo_path[len("docs/"):]  # already a page of this site
        if page:
            new = posixpath.relpath(page, page_dir)
        else:
            kind = "tree" if os.path.isdir(os.path.join(root, repo_path)) else "blob"
            new = f"{REPO_URL}/{kind}/{BRANCH}/{repo_path}"
        if anchor:
            new += "#" + anchor
        return m.group(1) + new + m.group(3)

    return LINK.sub(sub, markdown)


def on_page_markdown(markdown, page, config, files, **kwargs):
    m = MARKER.search(markdown)
    if not m:
        return markdown
    root = _repo_root(config)
    rel = m.group(1)
    with open(os.path.join(root, rel), encoding="utf-8") as fh:
        readme = fh.read()
    readme = _rewrite(readme, posixpath.dirname(rel), page.file.src_uri, root)
    # "Edit this page" must open the README, not the generated stub.
    page.edit_url = f"{REPO_URL}/edit/{BRANCH}/{rel}"
    return markdown[: m.start()] + readme + markdown[m.end():]
