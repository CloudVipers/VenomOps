"""`venom update`: check for a newer venom and download it, verified, for this system.

The package is verified against the repository's public key, which is pinned INSIDE venom (``venom-repo.asc`` and
``venom-repo.gpg``, fingerprint :data:`KEY_FINGERPRINT`), never against anything fetched from the network:

* ``.rpm``: ``rpm -K`` with that key imported into a throwaway rpm database (the system's is never touched).
* ``.deb``: the signed ``InRelease`` (checked with ``gpgv`` and that key) names the hash of ``Packages.gz``, which names
  the hash of the ``.deb``: the same chain apt follows.

Nothing is installed unless ``--install`` is given, and then only after the check and a confirmation. No shell is ever
used: every subprocess call below receives a fixed argument list (hence the S603 exemption).
"""

# ruff: noqa: S603

from __future__ import annotations

import gzip
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse

import typer

from . import __version__

DEFAULT_BASE_URL = "https://cloudvipers.github.io/VenomOps"
BASE_URL_ENV = "VENOM_UPDATE_BASE_URL"  # for tests and mirrors: https, or http on loopback only
KEY_FINGERPRINT = "A7BE1F5E03EC7AA9C797A9DE3237E8D79E6E29C5"
UPDATE_AVAILABLE_EXIT = 100  # like `dnf check-update`: a script can tell "there is an update" from "error"

MAX_JSON_BYTES = 1 << 20
MAX_INDEX_BYTES = 16 << 20
MAX_PACKAGE_BYTES = 300 << 20
TIMEOUT_SECONDS = 30
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

RPM_ARCH = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}
DEB_ARCH = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}
RPM_FAMILY = {"rhel", "fedora", "centos", "amzn", "rocky", "almalinux", "ol"}
DEB_FAMILY = {"debian", "ubuntu"}


class UpdateError(Exception):
    """Something prevented checking, downloading or verifying an update. The message says what to do."""


@dataclass(frozen=True)
class Package:
    format: str  # "rpm" | "deb"
    arch: str  # as the package manager names it: x86_64, aarch64, amd64, arm64
    file: str
    path: str  # relative to the site root, for example rpm/x86_64/venom-0.1.5-1.x86_64.rpm
    sha256: str


@dataclass(frozen=True)
class Latest:
    version: str
    packages: tuple[Package, ...]


# --------------------------------------------------------------------------------------------- versions and platform


def parse_version(text: str) -> tuple[int, ...]:
    if not re.fullmatch(r"\d+(\.\d+){1,3}", text):
        raise UpdateError(f"unexpected version {text!r}")
    return tuple(int(part) for part in text.split("."))


def base_url() -> str:
    """The site to update from. Only https (or http on loopback, for tests) is accepted."""
    url = os.environ.get(BASE_URL_ENV, DEFAULT_BASE_URL).rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in LOOPBACK_HOSTS):
        return url
    raise UpdateError(f"{BASE_URL_ENV} must be an https URL (got {url!r})")


def package_format(os_release: str | None = None) -> str:
    """``rpm`` or ``deb``, from /etc/os-release (``ID`` and ``ID_LIKE``) and, failing that, from the tools present."""
    if platform.system() != "Linux":
        raise UpdateError(
            "venom update only works on Linux (the venom package is for Linux). On macOS or Windows install the "
            "kubectl plugin with krew or download the binary: https://cloudvipers.github.io/VenomOps/instalacion.html"
        )
    if os_release is None:
        try:
            os_release = Path("/etc/os-release").read_text(encoding="utf-8")
        except OSError:
            os_release = ""
    ids: set[str] = set()
    for line in os_release.splitlines():
        key, _, value = line.partition("=")
        if key in ("ID", "ID_LIKE"):
            ids.update(value.strip().strip("\"'").lower().split())
    if ids & DEB_FAMILY:
        return "deb"
    if ids & RPM_FAMILY:
        return "rpm"
    if shutil.which("dnf") or shutil.which("yum") or shutil.which("rpm"):
        return "rpm"
    if shutil.which("apt-get") or shutil.which("dpkg"):
        return "deb"
    raise UpdateError("could not tell whether this system uses rpm or deb packages")


def system_arch(fmt: str, machine: str | None = None) -> str:
    machine = (machine or platform.machine()).lower()
    table = RPM_ARCH if fmt == "rpm" else DEB_ARCH
    if machine not in table:
        raise UpdateError(f"no venom package for the architecture {machine!r}")
    return table[machine]


# ------------------------------------------------------------------------------------------------------ network


class _NoDowngrade(urllib.request.HTTPRedirectHandler):
    """Refuse a redirect that would leave https (or loopback), so a download cannot be silently downgraded."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        parsed = urlparse(newurl)
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in LOOPBACK_HOSTS):
            raise UpdateError(f"refusing a redirect to {newurl!r}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def http_get(url: str, limit: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": f"venom-update/{__version__}"})  # noqa: S310
    opener = urllib.request.build_opener(_NoDowngrade)
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310 - scheme checked in base_url()
            data: bytes = response.read(limit + 1)
    except urllib.error.HTTPError as exc:
        raise UpdateError(f"{url}: HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError(f"cannot reach {url}: {exc}") from exc
    if len(data) > limit:
        raise UpdateError(f"{url} is larger than the {limit // (1 << 20)} MiB limit")
    return data


def parse_latest(data: bytes) -> Latest:
    try:
        doc = json.loads(data)
        if doc["schema"] != 1:
            raise UpdateError(f"latest.json has an unsupported schema {doc['schema']!r}: update venom by hand")
        version = str(doc["version"])
        parse_version(version)
        packages = []
        for item in doc["packages"]:
            pkg = Package(item["format"], item["arch"], item["file"], item["path"], str(item["sha256"]).lower())
            if pkg.format not in ("rpm", "deb") or not re.fullmatch(r"[0-9a-f]{64}", pkg.sha256):
                raise ValueError("bad package entry")
            parts = pkg.path.split("/")
            if ".." in parts or parts[0] != pkg.format or parts[-1] != pkg.file or "/" in pkg.file:
                raise ValueError(f"suspicious package path {pkg.path!r}")
            packages.append(pkg)
    except UpdateError:
        raise
    except (ValueError, KeyError, TypeError) as exc:
        raise UpdateError(f"latest.json is not valid: {exc}") from exc
    return Latest(version, tuple(packages))


def fetch_latest(base: str) -> Latest:
    return parse_latest(http_get(f"{base}/latest.json", MAX_JSON_BYTES))


def pick_package(latest: Latest, fmt: str, arch: str) -> Package:
    for pkg in latest.packages:
        if pkg.format == fmt and pkg.arch == arch:
            return pkg
    raise UpdateError(f"the release {latest.version} has no {fmt} package for {arch}")


# ----------------------------------------------------------------------------------------------- verification


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_package(base: str, pkg: Package, dest_dir: Path) -> Path:
    """Downloads the package and checks its sha256 against latest.json. This is a first check, not the trust anchor."""
    data = http_get(f"{base}/{pkg.path}", MAX_PACKAGE_BYTES)
    if hashlib.sha256(data).hexdigest() != pkg.sha256:
        raise UpdateError(f"{pkg.file}: the sha256 does not match latest.json; nothing was kept")
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / pkg.file
    tmp = target.with_name(target.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(target)
    return target


def _key_file(name: str) -> Any:
    return resources.as_file(resources.files("venom") / name)


def verify_rpm(path: Path) -> None:
    """``rpm -K`` with the pinned key in a throwaway database: the system's rpm database is never modified."""
    rpm = shutil.which("rpm")
    if not rpm:
        raise UpdateError("cannot verify the signature: the `rpm` tool is not installed")
    with tempfile.TemporaryDirectory(prefix="venom-rpmdb-") as db, _key_file("venom-repo.asc") as key:
        imported = subprocess.run(
            [rpm, "--dbpath", db, "--import", str(key)], capture_output=True, text=True, check=False
        )
        if imported.returncode != 0:
            raise UpdateError(f"cannot import the pinned key: {imported.stderr.strip()}")
        keys = subprocess.run([rpm, "--dbpath", db, "-qa", "gpg-pubkey*"], capture_output=True, text=True, check=False)
        if KEY_FINGERPRINT[-8:].lower() not in keys.stdout.lower():
            raise UpdateError("the pinned key does not match the expected fingerprint: this venom is damaged")
        checked = subprocess.run([rpm, "--dbpath", db, "-K", str(path)], capture_output=True, text=True, check=False)
    output = checked.stdout + checked.stderr
    if checked.returncode != 0 or "NOT OK" in output.upper() or "signatures OK" not in output:
        raise UpdateError(
            f"{path.name}: the rpm signature is NOT valid for the VenomOps key. Do not install it. ({output.strip()})"
        )


def _verify_inrelease(inrelease: Path) -> None:
    gpgv = shutil.which("gpgv")
    if not gpgv:
        raise UpdateError("cannot verify the signature: `gpgv` is not installed (apt-get install gpgv)")
    with _key_file("venom-repo.gpg") as keyring:
        result = subprocess.run(
            [gpgv, "--status-fd", "1", "--keyring", str(keyring), str(inrelease)],
            capture_output=True,
            text=True,
            check=False,
        )
    valid = re.search(r"^\[GNUPG:\] VALIDSIG (.+)$", result.stdout, re.M)
    fingerprints = (
        {f.upper() for f in valid.group(1).split() if re.fullmatch(r"[0-9A-Fa-f]{40}", f)} if valid else set()
    )
    if result.returncode != 0 or KEY_FINGERPRINT not in fingerprints:
        raise UpdateError("the signature of the repository index is NOT valid for the VenomOps key. Do not install.")


def _release_hash(inrelease_text: str, wanted: str) -> str:
    """The SHA256 listed in the verified InRelease for a path such as ``main/binary-amd64/Packages.gz``."""
    in_block = False
    for line in inrelease_text.splitlines():
        if line.startswith("SHA256:"):
            in_block = True
            continue
        if in_block:
            if not line.startswith(" "):
                break
            parts = line.split()
            if len(parts) == 3 and parts[2] == wanted:
                return parts[0].lower()
    raise UpdateError(f"{wanted} is not listed in the signed repository index")


def verify_deb(base: str, pkg: Package, path: Path) -> None:
    """InRelease (signed with the pinned key) -> hash of Packages.gz -> hash of the .deb, as apt does."""
    with tempfile.TemporaryDirectory(prefix="venom-apt-") as tmp:
        inrelease = Path(tmp) / "InRelease"
        inrelease.write_bytes(http_get(f"{base}/deb/dists/stable/InRelease", MAX_INDEX_BYTES))
        _verify_inrelease(inrelease)
        packages_path = f"main/binary-{pkg.arch}/Packages.gz"
        packages_gz = http_get(f"{base}/deb/dists/stable/{packages_path}", MAX_INDEX_BYTES)
        if hashlib.sha256(packages_gz).hexdigest() != _release_hash(
            inrelease.read_text(encoding="utf-8"), packages_path
        ):
            raise UpdateError("the Packages index does not match the signed repository index. Do not install.")
        try:
            index = gzip.decompress(packages_gz).decode("utf-8")
        except (OSError, EOFError, UnicodeDecodeError) as exc:
            raise UpdateError(f"cannot read the Packages index: {exc}") from exc
    filename = pkg.path.removeprefix("deb/")
    for stanza in index.split("\n\n"):
        fields = dict(line.split(": ", 1) for line in stanza.splitlines() if ": " in line and not line.startswith(" "))
        if fields.get("Package") == "venom" and fields.get("Filename") == filename:
            if fields.get("SHA256", "").lower() != sha256_of(path):
                raise UpdateError(f"{path.name} does not match the signed Packages index. Do not install.")
            return
    raise UpdateError(f"{filename} is not listed in the signed Packages index")


def verify(base: str, pkg: Package, path: Path) -> None:
    if pkg.format == "rpm":
        verify_rpm(path)
    else:
        verify_deb(base, pkg, path)


# ------------------------------------------------------------------------------------------------------- install


def install_command(fmt: str, path: Path, *, is_root: bool | None = None) -> list[str]:
    """The exact command that installs the verified file. It always names the file by an explicit path."""
    if fmt == "rpm":
        tool = "dnf" if shutil.which("dnf") else "yum"
        command = [tool, "install", "-y", str(path.resolve())]
    else:
        command = ["apt-get", "install", "-y", str(path.resolve())]
    if (os.geteuid() == 0) if is_root is None else is_root:
        return command
    if not shutil.which("sudo"):
        raise UpdateError("installing needs root and `sudo` is not available: run the command above as root")
    return ["sudo", *command]


# ---------------------------------------------------------------------------------------------------- the command


def update_command(
    check: Annotated[
        bool, typer.Option("--check", help="Only say whether a newer version exists (exit 100 if so).")
    ] = False,
    install: Annotated[
        bool, typer.Option("--install", help="After verifying the package, install it (asks first, uses sudo).")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="With --install, do not ask for confirmation.")] = False,
    force: Annotated[
        bool, typer.Option("--force", help="Download and verify even if this is already the latest.")
    ] = False,
    dest: Annotated[Path, typer.Option("--dir", help="Where to save the package.")] = Path("."),
) -> None:
    """Check for the latest venom and download its package for this system, verified. Installs only with --install."""
    try:
        _run(check, install, yes, force, dest)
    except UpdateError as exc:
        typer.secho(f"Error: {exc}", fg="red", err=True)
        raise typer.Exit(1) from exc


def _run(check: bool, install: bool, yes: bool, force: bool, dest: Path) -> None:
    base = base_url()
    latest = fetch_latest(base)
    newer = parse_version(latest.version) > parse_version(__version__)
    typer.echo(f"Installed: {__version__}")
    typer.echo(f"Latest:    {latest.version}")
    if check:
        typer.echo("An update is available." if newer else "You are up to date.")
        raise typer.Exit(UPDATE_AVAILABLE_EXIT if newer else 0)
    if not newer and not force:
        typer.echo("You are up to date. Nothing to do (use --force to download it anyway).")
        return

    fmt = package_format()
    pkg = pick_package(latest, fmt, system_arch(fmt))
    typer.echo(f"Downloading {pkg.file} ...")
    path = download_package(base, pkg, dest)
    typer.echo("Verifying the signature with the VenomOps key pinned in venom ...")
    try:
        verify(base, pkg, path)
    except UpdateError:
        path.unlink(missing_ok=True)  # never leave an unverified package lying around
        raise
    typer.secho(f"Verified: {path}", fg="green")

    command = install_command(fmt, path)
    if not install:
        typer.echo("Not installed. To install it:\n  " + " ".join(command) + "\nor run: venom update --install")
        return
    typer.echo("About to run:\n  " + " ".join(command))
    if not yes and not typer.confirm("Install now?", default=False):
        typer.echo("Cancelled. The verified package stays at " + str(path))
        raise typer.Exit(1)
    raise typer.Exit(subprocess.run(command, check=False).returncode)
