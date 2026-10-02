#!/usr/bin/env python3
"""Checks that every download in the krew manifest still exists, matches its sha256 and contains the plugin binary.

A manifest whose URLs return 404 (a deleted release) or whose hashes do not match cannot be installed by `kubectl krew`,
and the krew-index maintainers download these files to validate a submission. Standard library only.

Usage: check_manifest.py [manifest.yaml]     (default: packages/kdoctor/kdoctor.yaml)
Exit code 0 when every platform is fine, 1 otherwise.
"""

from __future__ import annotations

import hashlib
import io
import re
import sys
import tarfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

DEFAULT = Path(__file__).resolve().parents[2] / "packages" / "kdoctor" / "kdoctor.yaml"
MAX_BYTES = 200 * 1024 * 1024
ZEROS = "0" * 64


def parse_platforms(text: str) -> list[dict[str, str]]:
    """Extracts uri / sha256 / bin of each platform from the manifest without a YAML dependency."""
    out: list[dict[str, str]] = []
    for block in re.split(r"(?m)^    - selector:", text)[1:]:
        entry = {}
        for key in ("uri", "sha256", "bin"):
            m = re.search(rf'(?m)^\s+{key}:\s*"?([^"\n]+)"?\s*$', block)
            if m:
                entry[key] = m.group(1).strip()
        out.append(entry)
    return out


def download(url: str, attempts: int = 3) -> bytes:
    last: Exception | None = None
    for i in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 - https URL taken from our own manifest
                data: bytes = resp.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ValueError("file is larger than the sanity limit")
            return data
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise  # a missing file will not appear by retrying
            last = exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
        time.sleep(2 * (i + 1))
    assert last is not None
    raise last


def archive_names(url: str, data: bytes) -> list[str]:
    if url.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            return z.namelist()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as t:
        return [m.name.removeprefix("./") for m in t.getmembers()]


def check(platform: dict[str, str]) -> str | None:
    """Returns None when fine, otherwise the reason."""
    url, want, binary = platform.get("uri"), platform.get("sha256"), platform.get("bin")
    if not url or not want or not binary:
        return "incomplete entry (uri, sha256 and bin are required)"
    if want == ZEROS:
        return "sha256 is still the all-zeros placeholder (the release was not built or the manifest not updated)"
    try:
        data = download(url)
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code} downloading {url}"
    except Exception as exc:  # noqa: BLE001 - any network failure is a finding, not a crash
        return f"cannot download {url}: {exc}"
    got = hashlib.sha256(data).hexdigest()
    if got != want:
        return f"sha256 mismatch for {url}: manifest {want[:12]}…, file {got[:12]}…"
    try:
        names = archive_names(url, data)
    except (tarfile.TarError, zipfile.BadZipFile) as exc:
        return f"not a valid archive: {exc}"
    if binary not in names:
        return f"the archive does not contain {binary!r} at its root (has: {', '.join(sorted(names)[:5])})"
    return None


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT
    platforms = parse_platforms(path.read_text(encoding="utf-8"))
    if not platforms:
        print(f"no platforms found in {path}", file=sys.stderr)
        return 1
    failures = 0
    for p in platforms:
        problem = check(p)
        label = (p.get("uri") or "?").rsplit("/", 1)[-1]
        if problem:
            failures += 1
            print(f"FAIL  {label}: {problem}")
        else:
            print(f"ok    {label}")
    print(f"{len(platforms) - failures}/{len(platforms)} platforms ok")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
