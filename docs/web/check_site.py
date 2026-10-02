#!/usr/bin/env python3
"""Checks a built site: internal links and anchors, assets, page metadata and leftovers. Standard library only.

Usage: python docs/web/check_site.py <site-dir> [--strict]      (exit code 1 when something is wrong)

The public keys of the package repository (venom-repo.asc / .gpg) are added when the signed repository is built, not by
the site generator, so they are only required with --strict (which the publish workflow uses).
"""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse

# The product is called venom: the old tool name must not reappear in the published pages.
FORBIDDEN_TERMS = ("kdoctor", "KD-K8S")
REPOSITORY_FILES = {"venom-repo.asc", "venom-repo.gpg"}


class Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.links: list[str] = []
        self.assets: list[str] = []
        self.h1 = 0
        self.title = ""
        self.description = ""
        self._in_title = False
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if "id" in a:
            self.ids.add(a["id"])
        if tag == "a" and "href" in a:
            self.links.append(a["href"])
        if tag in ("img", "script") and "src" in a:
            self.assets.append(a["src"])
        if tag == "link" and a.get("rel") in ("stylesheet", "icon") and "href" in a:
            self.assets.append(a["href"])
        if tag == "h1":
            self.h1 += 1
        if tag == "title":
            self._in_title = True
        if tag == "meta" and a.get("name") == "description":
            self.description = a.get("content", "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        self.text.append(data)


def check(site: Path, strict: bool = False) -> list[str]:
    problems: list[str] = []
    pages: dict[str, Page] = {}
    for f in sorted(site.glob("*.html")):
        p = Page()
        raw = f.read_text(encoding="utf-8")
        p.feed(raw)
        pages[f.name] = p
        if re.search(r"\{\{[A-Za-z_]+\}\}", raw):
            problems.append(f"{f.name}: unreplaced placeholder")
        if not p.title.strip():
            problems.append(f"{f.name}: missing <title>")
        if len(p.description) < 40:
            problems.append(f"{f.name}: missing or too short meta description")
        if p.h1 != 1:
            problems.append(f"{f.name}: expected exactly one <h1>, found {p.h1}")
        for term in FORBIDDEN_TERMS:
            if term in raw:
                problems.append(f"{f.name}: contains the old name {term!r}")
    for name, p in pages.items():
        for src in p.assets:
            if urlparse(src).scheme:
                problems.append(f"{name}: external resource {src} (the site must not make external requests)")
            elif not (site / unquote(src.split("?")[0])).exists():
                problems.append(f"{name}: missing asset {src}")
        for href in p.links:
            u = urlparse(href)
            if u.scheme in ("http", "https", "mailto"):
                continue
            target = u.path or name
            if target in pages:
                if u.fragment and u.fragment not in pages[target].ids:
                    problems.append(f"{name}: anchor #{u.fragment} not found in {target}")
            elif not (site / unquote(target)).exists():
                if target in REPOSITORY_FILES and not strict:
                    continue
                problems.append(f"{name}: broken link {href}")
    return problems


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--strict"]
    if len(args) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    site = Path(args[0])
    problems = check(site, strict="--strict" in sys.argv)
    pages = len(list(site.glob("*.html")))
    if problems:
        print(f"{len(problems)} problem(s) in {pages} pages:")
        for p in problems:
            print("  -", p)
        return 1
    print(f"ok: {pages} pages, no broken links or anchors, no external requests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
