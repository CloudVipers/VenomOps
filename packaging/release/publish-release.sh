#!/usr/bin/env bash
# Publishes the GitHub releases of a version (kdoctor-vX.Y.Z and venom-vX.Y.Z) from the artifacts already built locally:
#   packages/kdoctor/dist  (goreleaser)   and   packaging/dist  (packaging/build.sh)
# It checks everything it can BEFORE publishing anything, then asks for confirmation. Run it yourself: releases are public.
#
# Usage: packaging/release/publish-release.sh <version> [--dry-run] [--yes]
#   --dry-run  run every check and print what would be published, without publishing
#   --yes      do not ask for confirmation
set -euo pipefail

VERSION=${1:-}; shift || true
DRY=0; YES=0
for arg in "$@"; do case "$arg" in --dry-run) DRY=1 ;; --yes) YES=1 ;; *) echo "unknown option: $arg" >&2; exit 2 ;; esac; done
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "usage: $0 <version like 0.1.3> [--dry-run] [--yes]" >&2; exit 2; }

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
REPO=${REPO:-CloudVipers/VenomOps}
KD="$ROOT/packages/kdoctor/dist"; VN="$ROOT/packaging/dist"
fail() { echo "ERROR: $*" >&2; exit 1; }

echo "==> files for $VERSION"
kd_files=(); for f in darwin_amd64.tar.gz darwin_arm64.tar.gz linux_amd64.tar.gz linux_arm64.tar.gz windows_amd64.zip; do kd_files+=("$KD/kdoctor_v${VERSION}_$f"); done
kd_files+=("$KD/checksums.txt")
vn_files=("$VN/venom-${VERSION}-1.x86_64.rpm" "$VN/venom_${VERSION}_amd64.deb" "$VN/checksums.txt")
for f in "${kd_files[@]}" "${vn_files[@]}"; do [ -f "$f" ] || fail "missing $f (build it first: goreleaser / packaging/build.sh)"; done
# arm64 packages are optional (they come from the CI workflow artifacts)
for f in "$VN/venom-${VERSION}-1.aarch64.rpm" "$VN/venom_${VERSION}_arm64.deb" "$VN/checksums-arm64.txt"; do [ -f "$f" ] && vn_files+=("$f"); done
printf '   %s\n' "${kd_files[@]##*/}" "${vn_files[@]##*/}"

echo "==> checksums match the files"
(cd "$KD" && sha256sum -c checksums.txt --quiet) || fail "kdoctor checksums do not match the archives"
(cd "$VN" && sha256sum -c checksums.txt --quiet) || fail "venom checksums do not match the packages"
[ -f "$VN/checksums-arm64.txt" ] && { (cd "$VN" && sha256sum -c checksums-arm64.txt --quiet) || fail "arm64 checksums do not match"; }

echo "==> the krew manifest points at THESE archives"
python3 - "$ROOT/packages/kdoctor/kdoctor.yaml" "$KD/checksums.txt" "$VERSION" <<'PY' || exit 1
import re, sys
manifest, sums, version = sys.argv[1:4]
text = open(manifest, encoding="utf-8").read()
if f"kdoctor-v{version}" not in text:
    sys.exit(f"ERROR: kdoctor.yaml does not reference kdoctor-v{version}: update the manifest first")
zeros, wrong = 0, []
for line in open(sums, encoding="utf-8"):
    digest, name = line.split()
    m = re.search(rf"{re.escape(name)}\s+sha256:\s*\"?([0-9a-f]{{64}})\"?", text)
    if not m:
        sys.exit(f"ERROR: {name} is not in kdoctor.yaml")
    if set(m.group(1)) == {"0"}:
        zeros += 1
    elif m.group(1) != digest:
        wrong.append(name)
if wrong:
    sys.exit("ERROR: the manifest has DIFFERENT hashes than these archives (rebuilt after the manifest was filled?): " + ", ".join(wrong))
print("   all-zeros placeholders: fill them with a PR right after publishing" if zeros else "   hashes match")
PY

echo "==> GitHub access"
gh repo view "$REPO" --json nameWithOwner -q .nameWithOwner >/dev/null 2>&1 || fail "cannot access $REPO with gh (run gh auth status; check the REPO name)"

echo "==> the releases do not exist yet"
for tag in "kdoctor-v$VERSION" "venom-v$VERSION"; do
  if out=$(gh release view "$tag" --repo "$REPO" 2>&1); then
    fail "release $tag already exists (never delete a release a manifest or PR points to)"
  elif ! grep -qi "release not found" <<<"$out"; then
    fail "cannot check whether $tag exists (network or authentication problem?): $out"   # do not assume "not found"
  fi
done

echo "==> will publish on $REPO (target: main):"
echo "   kdoctor-v$VERSION  ($((${#kd_files[@]})) files)"; echo "   venom-v$VERSION    ($((${#vn_files[@]})) files)"
if [ "$DRY" = 1 ]; then echo "dry run: nothing published"; exit 0; fi
if [ "$YES" != 1 ]; then read -r -p "Publish now? [y/N] " ans; [[ "$ans" =~ ^[yY]$ ]] || { echo "aborted"; exit 1; }; fi

gh release create "kdoctor-v$VERSION" "${kd_files[@]}" --repo "$REPO" --target main --title "kdoctor v$VERSION" \
  --notes "kdoctor v$VERSION: plugin de kubectl (kubectl venom-doctor), solo lectura. Archivos para Linux, macOS y Windows." --generate-notes
gh release create "venom-v$VERSION" "${vn_files[@]}" --repo "$REPO" --target main --title "venom v$VERSION" \
  --notes "venom v$VERSION: venom doctor, venom fix y venom review, con kubectl-venom_doctor incluido. Paquetes .rpm y .deb sin Python." --generate-notes

echo "==> verifying the public downloads against the manifest"
python3 "$ROOT/packaging/krew/check_manifest.py" "$ROOT/packages/kdoctor/kdoctor.yaml" || echo "(expected while the manifest still has placeholders: fill it with a PR)"
cat <<NEXT

Next steps:
  1. Merge the PR that fills the krew manifest hashes (if it was pending), and update your krew-index PR branch.
  2. Publish the signed repository:
     gh workflow run publish-repo.yml --repo $REPO -f tag=venom-v$VERSION -f publish=true
NEXT
