"""Tests for check_manifest.py with a local HTTP server (no network, nothing public)."""

from __future__ import annotations

import hashlib
import http.server
import io
import tarfile
import threading
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

import check_manifest as cm


def tgz(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            t.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def zipped(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


@pytest.fixture
def server(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(tmp_path), **k)  # noqa: E731
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}", tmp_path
    httpd.shutdown()


def manifest(entries: list[tuple[str, str, str]]) -> str:
    head = "apiVersion: krew.googlecontainertools.github.com/v1alpha2\nkind: Plugin\nspec:\n  platforms:\n"
    entry = (
        "    - selector:\n"
        "        matchLabels: {{os: linux, arch: amd64}}\n"
        "      uri: {uri}\n"
        '      sha256: "{sha}"\n'
        "      bin: {binary}\n"
    )
    return head + "".join(entry.format(uri=u, sha=h, binary=b) for u, h, b in entries)


def run(text: str, tmp: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    m = tmp / "manifest.yaml"
    m.write_text(text)
    code = cm.main(["check_manifest.py", str(m)])
    return code, capsys.readouterr().out


def test_a_good_manifest_passes(server: tuple[str, Path], capsys: pytest.CaptureFixture[str]) -> None:
    url, tmp = server
    data = tgz({"kubectl-venom_doctor": b"binary", "LICENSE": b"x"})
    (tmp / "ok.tar.gz").write_bytes(data)
    code, out = run(
        manifest([(f"{url}/ok.tar.gz", hashlib.sha256(data).hexdigest(), "kubectl-venom_doctor")]), tmp, capsys
    )
    assert code == 0 and "1/1 platforms ok" in out


def test_zip_archives_are_supported(server: tuple[str, Path], capsys: pytest.CaptureFixture[str]) -> None:
    url, tmp = server
    data = zipped({"kubectl-venom_doctor.exe": b"binary"})
    (tmp / "win.zip").write_bytes(data)
    code, _ = run(
        manifest([(f"{url}/win.zip", hashlib.sha256(data).hexdigest(), "kubectl-venom_doctor.exe")]), tmp, capsys
    )
    assert code == 0


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("missing", "HTTP 404"),
        ("hash", "sha256 mismatch"),
        ("nobin", "does not contain"),
        ("zeros", "all-zeros"),
        ("garbage", "not a valid archive"),
    ],
)
def test_each_failure_mode_is_reported_for_the_right_reason(
    server: tuple[str, Path], capsys: pytest.CaptureFixture[str], case: str, expected: str
) -> None:
    url, tmp = server
    good = tgz({"kubectl-venom_doctor": b"binary"})
    if case == "missing":
        entry = (f"{url}/gone.tar.gz", "a" * 64, "kubectl-venom_doctor")  # a deleted release: 404
    elif case == "hash":
        (tmp / "f.tar.gz").write_bytes(good)
        entry = (f"{url}/f.tar.gz", "b" * 64, "kubectl-venom_doctor")
    elif case == "nobin":
        data = tgz({"something-else": b"x"})
        (tmp / "f.tar.gz").write_bytes(data)
        entry = (f"{url}/f.tar.gz", hashlib.sha256(data).hexdigest(), "kubectl-venom_doctor")
    elif case == "zeros":
        entry = (f"{url}/f.tar.gz", "0" * 64, "kubectl-venom_doctor")
    else:
        data = b"this is not an archive"
        (tmp / "f.tar.gz").write_bytes(data)
        entry = (f"{url}/f.tar.gz", hashlib.sha256(data).hexdigest(), "kubectl-venom_doctor")
    code, out = run(manifest([entry]), tmp, capsys)
    assert code == 1 and expected in out, out


def test_one_bad_platform_fails_the_whole_run(server: tuple[str, Path], capsys: pytest.CaptureFixture[str]) -> None:
    url, tmp = server
    data = tgz({"kubectl-venom_doctor": b"binary"})
    (tmp / "ok.tar.gz").write_bytes(data)
    good = (f"{url}/ok.tar.gz", hashlib.sha256(data).hexdigest(), "kubectl-venom_doctor")
    bad = (f"{url}/gone.tar.gz", "a" * 64, "kubectl-venom_doctor")
    code, out = run(manifest([good, bad]), tmp, capsys)
    assert code == 1 and "1/2 platforms ok" in out


def test_the_real_manifest_is_parsed_completely() -> None:
    platforms = cm.parse_platforms(cm.DEFAULT.read_text())
    assert len(platforms) == 5 and all({"uri", "sha256", "bin"} <= p.keys() for p in platforms)
    assert sorted(p["bin"] for p in platforms).count("kubectl-venom_doctor.exe") == 1
