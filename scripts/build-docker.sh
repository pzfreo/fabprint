#!/usr/bin/env bash
# Build and optionally push a versioned fabprint Docker image.
#
# Usage:
#   ./scripts/build-docker.sh 2.3.1          # build only
#   ./scripts/build-docker.sh 2.3.1 --push   # build and push to Docker Hub

set -euo pipefail

VERSION="${1:?Usage: $0 <orca-version> [--push]}"
PUSH="${2:-}"
IMAGE="fabprint/fabprint:orca-${VERSION}"

echo "Building ${IMAGE} ..."
docker build \
    --platform linux/amd64 \
    --build-arg "ORCA_VERSION=${VERSION}" \
    -t "${IMAGE}" \
    .

echo "Tagging as fabprint/fabprint:latest ..."
docker tag "${IMAGE}" fabprint/fabprint:latest

echo "Build complete: ${IMAGE}"

if [ "${PUSH}" = "--push" ]; then
    echo "Pushing ${IMAGE} ..."
    docker push "${IMAGE}"
    echo "Pushing fabprint/fabprint:latest ..."
    docker push fabprint/fabprint:latest
    echo "Done."
fi
