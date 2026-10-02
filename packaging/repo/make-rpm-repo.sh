#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=packaging/repo/common.sh
. /scripts/common.sh
dnf install -y -q createrepo_c rpm-sign gnupg2 >/dev/null

# rpm signs through gpg; feed it the passphrase non-interactively.
cat > /root/.rpmmacros <<MACROS
%_gpg_name $KEYID
%__gpg_sign_cmd %{__gpg} gpg --batch --no-verbose --no-armor --pinentry-mode loopback --passphrase-file /tmp/pass --no-secmem-warning -u "%{_gpg_name}" -sbo %{__signature_filename} --digest-algo sha256 %{__plaintext_filename}
MACROS

for rpm in /pkgs/*.rpm; do
  arch=$(rpm -qp --queryformat '%{ARCH}' "$rpm" 2>/dev/null)   # x86_64 / aarch64 = dnf's $basearch
  mkdir -p "/out/rpm/$arch"; cp "$rpm" "/out/rpm/$arch/"
done
for dir in /out/rpm/*/; do
  rpmsign --addsign "$dir"*.rpm >/dev/null            # gpgcheck=1: each package is signed
  createrepo_c -q "$dir"
  $GPG --armor --detach-sign -o "$dir/repodata/repomd.xml.asc" "$dir/repodata/repomd.xml"   # repo_gpgcheck=1
done
$GPG --armor --export "$KEYID" > /out/venom-repo.asc
echo "rpm: $(find /out/rpm -mindepth 1 -maxdepth 1 -printf '%f ')"
