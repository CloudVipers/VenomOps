"""Tests for make_latest.py. The file it writes must be exactly what `venom update` accepts."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
SCRIPT = HERE / "make_latest.py"


def site(tmp_path: Path, rpms: list[str], debs: list[str]) -> Path:
    for name in rpms:
        arch = name.rsplit(".", 2)[1]
        (tmp_path / "rpm" / arch).mkdir(parents=True, exist_ok=True)
        (tmp_path / "rpm" / arch / name).write_bytes(name.encode())
    (tmp_path / "deb" / "pool" / "main").mkdir(parents=True, exist_ok=True)
    for name in debs:
        (tmp_path / "deb" / "pool" / "main" / name).write_bytes(name.encode())
    return tmp_path


def run(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True, check=False)  # noqa: S603


def test_it_lists_every_package_with_its_hash(tmp_path: Path) -> None:
    root = site(tmp_path, ["venom-1.2.3-1.x86_64.rpm", "venom-1.2.3-1.aarch64.rpm"], ["venom_1.2.3_amd64.deb", "venom_1.2.3_arm64.deb"])
    assert run(root).returncode == 0
    doc = json.loads((root / "latest.json").read_text())
    assert doc["schema"] == 1 and doc["version"] == "1.2.3" and len(doc["packages"]) == 4
    rpm = next(p for p in doc["packages"] if p["format"] == "rpm" and p["arch"] == "x86_64")
    assert rpm["path"] == "rpm/x86_64/venom-1.2.3-1.x86_64.rpm"
    assert rpm["sha256"] == hashlib.sha256(b"venom-1.2.3-1.x86_64.rpm").hexdigest()


def test_the_file_is_accepted_by_venom_update(tmp_path: Path) -> None:
    update = pytest.importorskip("venom.update")
    root = site(tmp_path, ["venom-1.2.3-1.x86_64.rpm"], ["venom_1.2.3_amd64.deb"])
    assert run(root).returncode == 0
    latest = update.parse_latest((root / "latest.json").read_bytes())
    assert latest.version == "1.2.3"
    assert update.pick_package(latest, "deb", "amd64").path == "deb/pool/main/venom_1.2.3_amd64.deb"


def test_it_refuses_a_site_that_mixes_versions_or_has_no_packages(tmp_path: Path) -> None:
    mixed = site(tmp_path / "a", ["venom-1.2.3-1.x86_64.rpm"], ["venom_1.2.4_amd64.deb"])
    out = run(mixed)
    assert out.returncode != 0 and "mixes versions" in out.stderr
    empty = tmp_path / "b"
    empty.mkdir()
    assert run(empty).returncode != 0
