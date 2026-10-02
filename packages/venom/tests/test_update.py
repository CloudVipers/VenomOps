from __future__ import annotations

import gzip
import hashlib
import http.server
import json
import platform
import shutil
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from venom import update
from venom.cli import app

runner = CliRunner()
RPM_NAME = "venom-9.9.9-1.x86_64.rpm"
RPM_BYTES = b"not a real rpm, only bytes"


# ------------------------------------------------------------------------------------------------------ helpers


def latest_doc(version: str = "9.9.9", **override: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "schema": 1,
        "version": version,
        "packages": [
            {
                "format": "rpm",
                "arch": "x86_64",
                "file": RPM_NAME,
                "path": f"rpm/x86_64/{RPM_NAME}",
                "sha256": hashlib.sha256(RPM_BYTES).hexdigest(),
            }
        ],
    }
    doc.update(override)
    return doc


@contextmanager
def serve(root: Path) -> Iterator[str]:
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(root), **k)  # noqa: E731
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    root = tmp_path / "site"
    (root / "rpm" / "x86_64").mkdir(parents=True)
    (root / "rpm" / "x86_64" / RPM_NAME).write_bytes(RPM_BYTES)
    (root / "latest.json").write_text(json.dumps(latest_doc()))
    with serve(root) as url:
        monkeypatch.setenv(update.BASE_URL_ENV, url)
        monkeypatch.setattr(update, "package_format", lambda *_: "rpm")
        monkeypatch.setattr(update, "system_arch", lambda *_: "x86_64")
        monkeypatch.setattr(update, "__version__", "0.1.0")
        yield root


# ---------------------------------------------------------------------------------------- versions and platform


def test_versions_are_compared_numerically_not_as_text() -> None:
    assert update.parse_version("0.10.0") > update.parse_version("0.9.0")
    assert update.parse_version("1.0") < update.parse_version("1.0.1")
    for bad in ("", "v1.2.3", "1.2.x", "1.2.3-rc1", "1"):
        with pytest.raises(update.UpdateError):
            update.parse_version(bad)


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://cloudvipers.github.io/VenomOps/", True),
        ("http://127.0.0.1:8000", True),
        ("http://localhost:8000", True),
        ("http://example.com", False),  # plain http to a real host: could be tampered with
        ("ftp://example.com", False),
        ("file:///tmp", False),
    ],
)
def test_the_update_site_must_be_https_or_loopback(monkeypatch: pytest.MonkeyPatch, url: str, ok: bool) -> None:
    monkeypatch.setenv(update.BASE_URL_ENV, url)
    if ok:
        assert not update.base_url().endswith("/")
    else:
        with pytest.raises(update.UpdateError, match="https"):
            update.base_url()


@pytest.mark.parametrize(
    ("os_release", "expected"),
    [
        ("ID=ubuntu\nID_LIKE=debian\n", "deb"),
        ("ID=debian\n", "deb"),
        ('ID="rocky"\nID_LIKE="rhel centos fedora"\n', "rpm"),
        ('ID="amzn"\nID_LIKE="fedora"\n', "rpm"),
        ('ID=linuxmint\nID_LIKE="ubuntu debian"\n', "deb"),
    ],
)
def test_the_package_format_follows_os_release(os_release: str, expected: str) -> None:
    assert update.package_format(os_release) == expected


def test_an_unknown_system_falls_back_to_the_tools_present_or_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name if name == "apt-get" else None)
    assert update.package_format("ID=weird\n") == "deb"
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(update.UpdateError, match="rpm or deb"):
        update.package_format("ID=weird\n")


def test_other_operating_systems_are_told_how_to_install_instead(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    with pytest.raises(update.UpdateError, match="only works on Linux"):
        update.package_format("")


def test_architectures_use_each_package_managers_own_names() -> None:
    assert update.system_arch("rpm", "x86_64") == "x86_64" and update.system_arch("deb", "x86_64") == "amd64"
    assert update.system_arch("rpm", "arm64") == "aarch64" and update.system_arch("deb", "aarch64") == "arm64"
    with pytest.raises(update.UpdateError, match="architecture"):
        update.system_arch("rpm", "riscv64")


# ---------------------------------------------------------------------------------------------------- latest.json


def test_a_valid_latest_json_is_parsed() -> None:
    latest = update.parse_latest(json.dumps(latest_doc()).encode())
    assert latest.version == "9.9.9" and update.pick_package(latest, "rpm", "x86_64").file == RPM_NAME
    with pytest.raises(update.UpdateError, match="no deb package"):
        update.pick_package(latest, "deb", "amd64")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(schema=2),
        lambda d: d.update(version="nope"),
        lambda d: d["packages"][0].update(sha256="abc"),
        lambda d: d["packages"][0].update(format="exe"),
        lambda d: d["packages"][0].update(path="rpm/x86_64/../../etc/passwd", file="passwd"),
        lambda d: d["packages"][0].update(path="deb/x86_64/" + RPM_NAME),  # directory of another format
        lambda d: d["packages"][0].update(file="other.rpm"),  # file does not match the path
        lambda d: d.pop("packages"),
    ],
)
def test_a_malformed_or_suspicious_latest_json_is_rejected(mutate: Any) -> None:
    doc = latest_doc()
    mutate(doc)
    with pytest.raises(update.UpdateError):
        update.parse_latest(json.dumps(doc).encode())
    with pytest.raises(update.UpdateError, match="not valid"):
        update.parse_latest(b"{not json")


# ----------------------------------------------------------------------------------------------------- network


def test_responses_larger_than_the_limit_and_http_errors_are_refused(site: Path) -> None:
    base = update.base_url()
    with pytest.raises(update.UpdateError, match="larger than"):
        update.http_get(f"{base}/latest.json", 10)
    with pytest.raises(update.UpdateError, match="HTTP 404"):
        update.http_get(f"{base}/missing", 1000)


def test_a_redirect_that_leaves_https_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Redirect(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(302)
            self.send_header("Location", "http://example.com/evil.rpm")
            self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with pytest.raises(update.UpdateError, match="refusing a redirect"):
            update.http_get(f"http://127.0.0.1:{httpd.server_port}/x", 1000)
    finally:
        httpd.shutdown()


def test_a_download_whose_hash_differs_from_latest_json_is_not_kept(site: Path, tmp_path: Path) -> None:
    (site / "rpm" / "x86_64" / RPM_NAME).write_bytes(b"tampered")
    pkg = update.pick_package(update.fetch_latest(update.base_url()), "rpm", "x86_64")
    with pytest.raises(update.UpdateError, match="sha256 does not match"):
        update.download_package(update.base_url(), pkg, tmp_path / "out")
    assert not list((tmp_path / "out").glob("*"))  # no file, not even a partial one


# --------------------------------------------------------------------------------------------- the command flows


def test_check_reports_an_update_with_exit_100_and_up_to_date_with_0(
    site: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = runner.invoke(app, ["update", "--check"])
    assert result.exit_code == update.UPDATE_AVAILABLE_EXIT and "update is available" in result.output
    monkeypatch.setattr(update, "__version__", "9.9.9")
    result = runner.invoke(app, ["update", "--check"])
    assert result.exit_code == 0 and "up to date" in result.output


def test_nothing_is_downloaded_when_already_up_to_date(
    site: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(update, "__version__", "9.9.9")
    result = runner.invoke(app, ["update", "--dir", str(tmp_path / "dl")])
    assert result.exit_code == 0 and "Nothing to do" in result.output and not (tmp_path / "dl").exists()


def test_a_newer_version_is_downloaded_verified_and_not_installed(
    site: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    verified: list[Path] = []
    monkeypatch.setattr(update, "verify", lambda base, pkg, path: verified.append(path))
    monkeypatch.setattr(update, "install_command", lambda fmt, path, **_: ["sudo", "dnf", "install", "-y", str(path)])
    ran: list[Any] = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: ran.append(a))
    result = runner.invoke(app, ["update", "--dir", str(tmp_path / "dl")])
    saved = tmp_path / "dl" / RPM_NAME
    assert result.exit_code == 0, result.output
    assert saved.read_bytes() == RPM_BYTES and verified == [saved]
    assert "Not installed" in result.output and "venom update --install" in result.output
    assert ran == []  # without --install nothing is executed


def test_a_package_that_fails_verification_is_deleted_and_the_command_fails(
    site: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_: Any) -> None:
        raise update.UpdateError("the rpm signature is NOT valid")

    monkeypatch.setattr(update, "verify", refuse)
    result = runner.invoke(app, ["update", "--install", "--yes", "--dir", str(tmp_path / "dl")])
    assert result.exit_code == 1 and "NOT valid" in result.output
    assert not (tmp_path / "dl" / RPM_NAME).exists()  # an unverified package is never left behind


def test_install_asks_first_and_runs_exactly_the_announced_command(
    site: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(update, "verify", lambda *_: None)
    monkeypatch.setattr(update, "install_command", lambda fmt, path, **_: ["dnf", "install", "-y", str(path)])
    calls: list[Any] = []

    def fake_run(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kw))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    declined = runner.invoke(app, ["update", "--install", "--dir", str(tmp_path / "a")], input="n\n")
    assert declined.exit_code == 1 and "Cancelled" in declined.output and calls == []
    accepted = runner.invoke(app, ["update", "--install", "--dir", str(tmp_path / "b")], input="y\n")
    assert accepted.exit_code == 0 and len(calls) == 1
    argv, kwargs = calls[0]
    assert argv == ["dnf", "install", "-y", str(tmp_path / "b" / RPM_NAME)] and "shell" not in kwargs
    assert "About to run" in accepted.output


def test_install_exits_with_the_package_managers_status(
    site: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(update, "verify", lambda *_: None)
    monkeypatch.setattr(update, "install_command", lambda fmt, path, **_: ["dnf", "install", "-y", str(path)])
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 7))
    result = runner.invoke(app, ["update", "--install", "--yes", "--dir", str(tmp_path)])
    assert result.exit_code == 7


def test_install_commands_name_the_file_and_use_sudo_only_when_needed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg = tmp_path / "venom.rpm"
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name if name in ("dnf", "sudo") else None)
    assert update.install_command("rpm", pkg, is_root=True) == ["dnf", "install", "-y", str(pkg.resolve())]
    assert update.install_command("rpm", pkg, is_root=False)[:2] == ["sudo", "dnf"]
    assert update.install_command("deb", pkg, is_root=True) == ["apt-get", "install", "-y", str(pkg.resolve())]
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(update.UpdateError, match="sudo"):
        update.install_command("rpm", pkg, is_root=False)


def test_the_rpm_signature_is_checked_with_a_throwaway_database_and_the_pinned_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[str]] = []

    def fake_run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        if "--import" in argv:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if "-qa" in argv:
            return subprocess.CompletedProcess(argv, 0, "gpg-pubkey-9e6e29c5-68000000\n", "")
        return subprocess.CompletedProcess(argv, 0, f"{argv[-1]}: digests signatures OK\n", "")

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/rpm")
    monkeypatch.setattr(subprocess, "run", fake_run)
    update.verify_rpm(tmp_path / "x.rpm")
    dbpaths = {argv[argv.index("--dbpath") + 1] for argv in seen}
    assert len(dbpaths) == 1 and not dbpaths & {"/var/lib/rpm", "/usr/lib/sysimage/rpm"}  # never the system's database


@pytest.mark.parametrize(
    ("rc", "out"),
    [(1, "x.rpm: digests SIGNATURES NOT OK"), (0, "x.rpm: DIGESTS signatures NOT OK"), (0, "x.rpm: digests OK")],
)
def test_a_bad_unsigned_or_untrusted_rpm_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rc: int, out: str
) -> None:
    def fake_run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        if "-qa" in argv:
            return subprocess.CompletedProcess(argv, 0, "gpg-pubkey-9e6e29c5-1\n", "")
        if "--import" in argv:
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, rc, out + "\n", "")

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/rpm")
    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(update.UpdateError, match="NOT valid"):
        update.verify_rpm(tmp_path / "x.rpm")


def test_a_key_that_is_not_the_pinned_one_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, "gpg-pubkey-deadbeef-1\n" if "-qa" in argv else "", "")

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/rpm")
    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(update.UpdateError, match="fingerprint"):
        update.verify_rpm(tmp_path / "x.rpm")


# ----------------------------------------- the deb chain with a real gpg/gpgv and a throwaway key (skipped without them)

needs_gpg = pytest.mark.skipif(not (shutil.which("gpg") and shutil.which("gpgv")), reason="gpg and gpgv are needed")


@pytest.fixture
def deb_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, Path, Path]]:
    """A tiny apt repository signed with a throwaway key, and that key pinned in place of the real one."""
    home = tmp_path / "gnupg"
    home.mkdir(mode=0o700)
    env = {"GNUPGHOME": str(home), "PATH": "/usr/bin:/bin"}

    def gpg(*args: str, stdin: bytes | None = None) -> bytes:
        done = subprocess.run(["gpg", "--batch", "--yes", *args], input=stdin, capture_output=True, env=env, check=True)  # noqa: S603, S607
        return done.stdout

    gpg(
        "--passphrase",
        "",
        "--pinentry-mode",
        "loopback",
        "--quick-generate-key",
        "Test <t@example.com>",
        "rsa3072",
        "sign",
        "never",
    )
    fingerprint = gpg("--list-keys", "--with-colons").decode().split("fpr:::::::::")[1].split(":")[0]
    keyring = tmp_path / "venom-repo.gpg"
    keyring.write_bytes(gpg("--export"))

    root = tmp_path / "site"
    pool = root / "deb" / "pool" / "main"
    pool.mkdir(parents=True)
    deb = pool / "venom_9.9.9_amd64.deb"
    deb.write_bytes(b"pretend deb")
    dist = root / "deb" / "dists" / "stable"
    (dist / "main" / "binary-amd64").mkdir(parents=True)
    stanza = (
        "Package: venom\nVersion: 9.9.9\nArchitecture: amd64\n"
        f"Filename: pool/main/{deb.name}\nSHA256: {hashlib.sha256(deb.read_bytes()).hexdigest()}\n"
    )
    packages_gz = gzip.compress(stanza.encode())
    (dist / "main" / "binary-amd64" / "Packages.gz").write_bytes(packages_gz)
    release = (
        "Origin: VenomOps\nSuite: stable\nSHA256:\n"
        f" {hashlib.sha256(packages_gz).hexdigest()} {len(packages_gz)} main/binary-amd64/Packages.gz\n"
    )
    (dist / "InRelease").write_bytes(gpg("--clearsign", stdin=release.encode()))

    monkeypatch.setattr(update, "KEY_FINGERPRINT", fingerprint.upper())

    @contextmanager
    def key_file(name: str) -> Iterator[Path]:
        yield keyring

    monkeypatch.setattr(update, "_key_file", key_file)
    with serve(root) as url:
        yield url, deb, root


def deb_package() -> update.Package:
    return update.Package("deb", "amd64", "venom_9.9.9_amd64.deb", "deb/pool/main/venom_9.9.9_amd64.deb", "0" * 64)


@needs_gpg
def test_a_deb_covered_by_the_signed_chain_is_accepted(deb_repo: tuple[str, Path, Path]) -> None:
    url, deb, _ = deb_repo
    update.verify_deb(url, deb_package(), deb)


@needs_gpg
def test_a_tampered_deb_is_rejected(deb_repo: tuple[str, Path, Path]) -> None:
    url, deb, _ = deb_repo
    deb.write_bytes(b"pretend deb, but changed")
    with pytest.raises(update.UpdateError, match="does not match the signed Packages index"):
        update.verify_deb(url, deb_package(), deb)


@needs_gpg
def test_a_tampered_packages_index_is_rejected(deb_repo: tuple[str, Path, Path]) -> None:
    url, deb, root = deb_repo
    index = root / "deb" / "dists" / "stable" / "main" / "binary-amd64" / "Packages.gz"
    index.write_bytes(
        gzip.compress(b"Package: venom\nFilename: pool/main/venom_9.9.9_amd64.deb\nSHA256: " + b"0" * 64 + b"\n")
    )
    with pytest.raises(update.UpdateError, match="does not match the signed repository index"):
        update.verify_deb(url, deb_package(), deb)


@needs_gpg
def test_an_index_signed_by_another_key_is_rejected(
    deb_repo: tuple[str, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    url, deb, _ = deb_repo
    monkeypatch.setattr(update, "KEY_FINGERPRINT", "A" * 40)  # what is pinned is not what signed the index
    with pytest.raises(update.UpdateError, match="NOT valid for the VenomOps key"):
        update.verify_deb(url, deb_package(), deb)


@needs_gpg
def test_an_unsigned_or_altered_inrelease_is_rejected(deb_repo: tuple[str, Path, Path]) -> None:
    url, deb, root = deb_repo
    inrelease = root / "deb" / "dists" / "stable" / "InRelease"
    inrelease.write_bytes(inrelease.read_bytes().replace(b"VenomOps", b"Evil"))
    with pytest.raises(update.UpdateError, match="NOT valid"):
        update.verify_deb(url, deb_package(), deb)


# -------------------------------------------------------------------------------------- the pinned key is the real one


@needs_gpg
@pytest.mark.parametrize("name", ["venom-repo.asc", "venom-repo.gpg"])
def test_both_embedded_key_files_carry_the_pinned_fingerprint(name: str) -> None:
    with update._key_file(name) as path:
        out = subprocess.run(
            ["gpg", "--show-keys", "--with-colons", str(path)], capture_output=True, text=True, check=True
        ).stdout  # noqa: S603, S607
    assert update.KEY_FINGERPRINT in out and out.count("\nfpr:") + out.startswith("fpr:") == 1  # exactly this one key
