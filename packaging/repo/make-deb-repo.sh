#!/usr/bin/env bash
set -euo pipefail
apt-get update -qq >/dev/null && apt-get install -y -qq apt-utils gnupg >/dev/null
# shellcheck source=packaging/repo/common.sh
. /scripts/common.sh

mkdir -p /out/deb/pool/main
cp /pkgs/*.deb /out/deb/pool/main/
cd /out/deb
archs=""
for arch in amd64 arm64; do
  if compgen -G "pool/main/*_${arch}.deb" >/dev/null; then
    archs="$archs $arch"
    mkdir -p "dists/stable/main/binary-$arch"
    apt-ftparchive --arch "$arch" packages pool/main > "dists/stable/main/binary-$arch/Packages"
    gzip -9kf "dists/stable/main/binary-$arch/Packages"
  fi
done
apt-ftparchive -o APT::FTPArchive::Release::Origin=VenomOps -o APT::FTPArchive::Release::Label=VenomOps \
  -o APT::FTPArchive::Release::Suite=stable -o APT::FTPArchive::Release::Codename=stable \
  -o "APT::FTPArchive::Release::Architectures=${archs# }" -o APT::FTPArchive::Release::Components=main \
  release dists/stable > dists/stable/Release
$GPG --armor --detach-sign -o dists/stable/Release.gpg dists/stable/Release
$GPG --clearsign -o dists/stable/InRelease dists/stable/Release
$GPG --export "$KEYID" > /out/venom-repo.gpg          # binary keyring for apt's signed-by=
echo "deb:$archs"
