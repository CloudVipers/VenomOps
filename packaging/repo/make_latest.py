#!/usr/bin/env python3
"""Writes latest.json at the root of the published site: the version and the packages `venom update` can fetch.

It is only a pointer. What makes a download trustworthy is the repository's GPG signature, which `venom update` checks with the
key pinned inside venom; the sha256 here lets it fail early on a truncated or swapped file. Standard library only.

Usage: python packaging/repo/make_latest.py <site-dir>        (the directory with rpm/ and deb/)
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

RPM_RE = re.compile(r"^venom-(?P<version>\d+(?:\.\d+){1,3})-\d+\.(?P<arch>x86_64|aarch64)\.rpm$")
DEB_RE = re.compile(r"^venom_(?P<version>\d+(?:\.\d+){1,3})_(?P<arch>amd64|arm64)\.deb$")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect(site: Path) -> tuple[str, list[dict[str, str]]]:
    packages: list[dict[str, str]] = []
    versions: set[str] = set()
    for fmt, pattern, files in (
        ("rpm", RPM_RE, sorted((site / "rpm").glob("*/*.rpm"))),
        ("deb", DEB_RE, sorted((site / "deb" / "pool" / "main").glob("*.deb"))),
    ):
        for path in files:
            m = pattern.match(path.name)
            if not m:
                raise SystemExit(f"unexpected package name: {path}")
            versions.add(m["version"])
            packages.append(
                {
                    "format": fmt,
                    "arch": m["arch"],
                    "file": path.name,
                    "path": path.relative_to(site).as_posix(),
                    "sha256": sha256_of(path),
                }
            )
    if not packages:
        raise SystemExit(f"no packages found under {site}")
    if len(versions) != 1:
        raise SystemExit(f"the site mixes versions {sorted(versions)}: the repository must hold only the latest")
    return versions.pop(), packages


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    site = Path(sys.argv[1])
    version, packages = collect(site)
    doc = {
        "schema": 1,
        "version": version,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "packages": packages,
    }
    (site / "latest.json").write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print(f"latest.json: version {version}, {len(packages)} packages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
