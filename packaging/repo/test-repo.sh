#!/usr/bin/env bash
# End-to-end test of the repository generator with a THROWAWAY key (generated in a temp dir, never stored):
# builds the repo from packaging/dist, serves it over HTTP and installs `venom` with the real dnf and apt, with signature
# checks ON. Then proves the checks bite: a tampered package and a missing signature must be rejected.
# Needs docker and the packages in packaging/dist, or PKG_DIR (run packaging/build.sh first). Publishes nothing.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
WORK=$(mktemp -d)
cleanup() { # also removes the throwaway private key and the generated site
  docker rm -f venom-repo-test >/dev/null 2>&1 || true
  docker network rm venom-repo-net >/dev/null 2>&1 || true
  [ -n "${WORK:-}" ] && [ -d "$WORK" ] && rm -rf -- "$WORK"
}
trap cleanup EXIT
PKG_DIR=${PKG_DIR:-$ROOT/packaging/dist}   # may also hold arm64 packages: the repo is generated for every arch present
PASS="test-passphrase-$$"

echo "==> throwaway signing key (with passphrase)"
export GNUPGHOME="$WORK/gnupg"; mkdir -p "$GNUPGHOME"; chmod 700 "$GNUPGHOME"
gpg --batch --pinentry-mode loopback --passphrase "$PASS" --quick-generate-key "VenomOps Test <test@example.com>" rsa3072 sign never 2>/dev/null
gpg --batch --pinentry-mode loopback --passphrase "$PASS" --armor --export-secret-keys > "$WORK/key.asc"

GPG_KEY_FILE="$WORK/key.asc" GPG_PASSPHRASE="$PASS" "$ROOT/packaging/repo/build-repo.sh" "$PKG_DIR" "$WORK/site" >/dev/null
echo "repository built: $(ls "$WORK/site")"
# latest.json (what `venom update` reads) must list every package present, with the hash of each
python3 "$ROOT/packaging/repo/make_latest.py" "$WORK/site"
python3 - "$WORK/site" <<'PY'
import hashlib, json, pathlib, sys
site = pathlib.Path(sys.argv[1]); doc = json.loads((site / "latest.json").read_text())
assert doc["schema"] == 1 and doc["packages"], doc
for p in doc["packages"]:
    assert hashlib.sha256((site / p["path"]).read_bytes()).hexdigest() == p["sha256"], p
print("latest.json ok:", doc["version"], [f'{p["format"]}/{p["arch"]}' for p in doc["packages"]])
PY

docker network create venom-repo-net >/dev/null
docker run -d --rm --name venom-repo-test --network venom-repo-net -v "$WORK/site":/usr/share/nginx/html:ro nginx:alpine >/dev/null
URL=http://venom-repo-test

rpm_client() { # rpm_client <image> <gpgcheck> <repo_gpgcheck> [pre-command]
  docker run --rm --network venom-repo-net "$1" bash -c "
    ${4:-true}
    cat > /etc/yum.repos.d/venom.repo <<REPO
[venom]
name=VenomOps
baseurl=$URL/rpm/\\\$basearch
enabled=1
gpgcheck=$2
repo_gpgcheck=$3
gpgkey=$URL/venom-repo.asc
REPO
    dnf install -y venom 2>&1 && venom --version && kubectl-venom_doctor --version"
}

echo "==> dnf: install with gpgcheck=1 repo_gpgcheck=1"
for image in rockylinux:9 amazonlinux:2023; do echo "-- $image"; rpm_client "$image" 1 1 | tail -5; done

echo "==> apt: install with signed-by"
for image in debian:12 ubuntu:24.04; do
  echo "-- $image"
  docker run --rm --network venom-repo-net "$image" bash -c "
    apt-get update -qq >/dev/null && apt-get install -y -qq curl ca-certificates gnupg >/dev/null
    curl -fsSL $URL/venom-repo.gpg -o /usr/share/keyrings/venom.gpg
    echo 'deb [signed-by=/usr/share/keyrings/venom.gpg] $URL/deb stable main' > /etc/apt/sources.list.d/venom.list
    apt-get update -qq && apt-get install -y -qq venom >/dev/null && venom --version && kubectl-venom_doctor --version"
done

# A negative test only means something if it fails for the RIGHT reason (not, say, a dead network): check the message.
expect_rejected() { # expect_rejected <description> <regex of the expected reason> <command...>
  local desc=$1 reason=$2; shift 2
  local out
  if out=$("$@" 2>&1); then echo "FAIL: $desc was accepted"; exit 1; fi
  if ! grep -qiE "$reason" <<<"$out"; then echo "FAIL: $desc was rejected, but not for the expected reason ($reason):"; echo "$out" | tail -5; exit 1; fi
  echo "ok: $desc rejected ($(grep -iE -m1 "$reason" <<<"$out" | cut -c1-90))"
}

echo "==> negative: rpm"
expect_rejected "client without a trusted key (rpm)" "gpg|signature|key" docker run --rm --network venom-repo-net rockylinux:9 bash -c "
  printf '[venom]\nname=v\nbaseurl=$URL/rpm/\$basearch\ngpgcheck=1\nrepo_gpgcheck=1\ngpgkey=file:///nonexistent\n' > /etc/yum.repos.d/venom.repo
  dnf install -y venom"
for rpm in "$WORK"/site/rpm/x86_64/*.rpm; do echo "tampered" >> "$rpm"; break; done
expect_rejected "tampered package (rpm)" "checksum|digest|signature|gpg|mismatch|corrupt|Cannot download" rpm_client rockylinux:9 1 0

echo "==> negative: apt"
expect_rejected "client without the key (apt)" "NO_PUBKEY|not signed|signature|GPG error" docker run --rm --network venom-repo-net debian:12 bash -c "
  apt-get update -qq >/dev/null 2>&1; apt-get install -y -qq curl ca-certificates >/dev/null 2>&1
  echo 'deb $URL/deb stable main' > /etc/apt/sources.list.d/venom.list
  apt-get update"
# apt downloads Packages.gz (not the plain file), so that is the one an attacker would have to change
printf "tampered" >> "$WORK/site/deb/dists/stable/main/binary-amd64/Packages.gz"
expect_rejected "tampered Packages.gz index (apt)" "Hash Sum mismatch|unexpected size|checksum|mismatch" docker run --rm --network venom-repo-net debian:12 bash -c "
  apt-get update -qq >/dev/null 2>&1; apt-get install -y -qq curl ca-certificates >/dev/null 2>&1
  curl -fsSL $URL/venom-repo.gpg -o /usr/share/keyrings/venom.gpg
  echo 'deb [signed-by=/usr/share/keyrings/venom.gpg] $URL/deb stable main' > /etc/apt/sources.list.d/venom.list
  apt-get update"

echo "ALL OK"
