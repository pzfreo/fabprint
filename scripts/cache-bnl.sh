#!/usr/bin/env bash
# Cache libbambu_networking.so from a running cloud-bridge image to the
# private pzfreo/bnl GitHub repo as a release asset.
#
# Usage:
#   ./scripts/cache-bnl.sh                     # uses default version 02.05.00.00
#   ./scripts/cache-bnl.sh 02.06.00.00         # specify version

set -euo pipefail

VERSION="${1:-02.05.00.00}"
IMAGE="fabprint/cloud-bridge:latest"
TMPFILE=$(mktemp)

echo "Extracting libbambu_networking.so from ${IMAGE} ..."
docker run --rm --entrypoint cat "${IMAGE}" /tmp/bambu_plugin/libbambu_networking.so > "${TMPFILE}"

SHA256=$(shasum -a 256 "${TMPFILE}" | awk '{print $1}')
echo "SHA256: ${SHA256}"

echo "Creating release v${VERSION} on pzfreo/bnl ..."
gh release create "v${VERSION}" \
    --repo pzfreo/bnl \
    --title "v${VERSION}" \
    --notes "libbambu_networking.so v${VERSION}
SHA256: ${SHA256}
Arch: x86_64 Linux" \
    "${TMPFILE}#libbambu_networking.so" \
    2>/dev/null \
|| (echo "Release exists, uploading asset..." \
    && gh release upload "v${VERSION}" \
        --repo pzfreo/bnl \
        --clobber \
        "${TMPFILE}#libbambu_networking.so")

rm "${TMPFILE}"
echo "Done. Cached v${VERSION} to pzfreo/bnl"
