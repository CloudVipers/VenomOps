#!/usr/bin/env bash
# Builds packaging/dist/venom-<version>-1.<arch>.rpm and venom_<version>_<arch>.deb for the CURRENT machine architecture.
# Needs: docker, go and nfpm (go install github.com/goreleaser/nfpm/v2/cmd/nfpm@v2.47.0, the version the release workflow uses). Installs and publishes nothing.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
VERSION=${VERSION:-$(python3 -c "import tomllib,sys;print(tomllib.load(open('$ROOT/packages/venom/pyproject.toml','rb'))['project']['version'])")}
case "$(uname -m)" in
  x86_64)  GOARCH=amd64; DOCKER_PLATFORM=linux/amd64 ;;
  aarch64) GOARCH=arm64; DOCKER_PLATFORM=linux/arm64 ;;
  *) echo "unsupported architecture $(uname -m)" >&2; exit 1 ;;
esac
# Build image pinned by its multi-arch index digest (same tag, reproducible base). Refresh it deliberately.
BUILD_IMAGE=${BUILD_IMAGE:-rockylinux:9@sha256:03303863808e9cd79f0c58c833db3e5f9cde9f2abe16804a267b05dc54af86e4}
STAGE=$ROOT/packaging/stage-$GOARCH
OUT=$ROOT/packaging/dist
rm -rf "$STAGE"; mkdir -p "$STAGE" "$OUT"

echo "==> venom (PyInstaller, Rocky Linux 9 container (glibc 2.34))"
docker run --rm --platform "$DOCKER_PLATFORM" -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" -v "$ROOT":/src:ro -v "$STAGE":/out \
  "$BUILD_IMAGE" bash /src/packaging/build-in-container.sh

echo "==> kubectl-venom_doctor (static Go binary)"
(cd "$ROOT/packages/venom-doctor" && CGO_ENABLED=0 GOOS=linux GOARCH=$GOARCH go build -trimpath \
  -ldflags "-s -w -X main.version=$VERSION" -o "$STAGE/kubectl-venom_doctor" ./cmd/kubectl-venom_doctor)

echo "==> rpm + deb (nfpm)"
# nfpm does not expand variables in `src`, so resolve the template first.
export VERSION ROOT STAGE PKG_ARCH=$GOARCH
envsubst '${VERSION} ${ROOT} ${STAGE} ${PKG_ARCH}' < "$ROOT/packaging/nfpm.yaml" > "$STAGE/nfpm.yaml"
for format in rpm deb; do
  nfpm package --config "$STAGE/nfpm.yaml" --packager "$format" --target "$OUT/"
done
ls -lh "$OUT"
