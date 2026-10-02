#!/usr/bin/env bash
# Builds a SIGNED yum/dnf and apt repository from the .rpm/.deb files of `venom`. Publishes nothing: the output
# directory is a static site you can host anywhere (GitHub Pages, S3, any web server).
#
# Usage: GPG_KEY_FILE=key.asc [GPG_PASSPHRASE=...] packaging/repo/build-repo.sh <packages-dir> <out-dir>
#   <packages-dir>  directory with venom-*.rpm and venom_*.deb (any mix of x86_64/aarch64 and amd64/arm64)
#   GPG_KEY_FILE    the ARMORED PRIVATE key that signs the repository (never committed; keep it in a secret store)
# Needs docker. Everything runs in throwaway Rocky 9 / Debian 12 containers; the key never leaves them.
set -euo pipefail

if [ $# -ne 2 ] || [ -z "${GPG_KEY_FILE:-}" ]; then
  sed -n '2,10p' "$0" >&2; exit 2
fi
PKG_DIR=$(cd "$1" && pwd)
mkdir -p "$2"; OUT=$(cd "$2" && pwd)
KEY=$(cd "$(dirname "$GPG_KEY_FILE")" && pwd)/$(basename "$GPG_KEY_FILE")
HERE=$(cd "$(dirname "$0")" && pwd)
[ -f "$KEY" ] || { echo "key file not found: $KEY" >&2; exit 2; }
ls "$PKG_DIR"/*.rpm "$PKG_DIR"/*.deb >/dev/null 2>&1 || { echo "no .rpm/.deb in $PKG_DIR" >&2; exit 2; }
rm -rf "${OUT:?}/rpm" "${OUT:?}/deb"

run() { # run <image> <script>: the container sees the packages (ro), the output (rw) and the key (ro)
  docker run --rm -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" -e GPG_PASSPHRASE="${GPG_PASSPHRASE:-}" \
    -v "$PKG_DIR":/pkgs:ro -v "$OUT":/out -v "$KEY":/key.asc:ro -v "$HERE":/scripts:ro "$1" bash "/scripts/$2"
}
echo "==> rpm repository (createrepo_c, signed packages and metadata)"; run rockylinux:9 make-rpm-repo.sh
echo "==> apt repository (apt-ftparchive, signed Release)";              run debian:12 make-deb-repo.sh
echo "==> done: $OUT"; find "$OUT" -maxdepth 3 -type f | sed "s|^$OUT/||" | sort | head -30
