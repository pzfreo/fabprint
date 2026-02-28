#!/usr/bin/env bash
# Build and optionally push fabprint Docker images.
#
# Usage:
#   ./scripts/build-docker.sh slicer 2.3.1       # build slicer image
#   ./scripts/build-docker.sh slicer 2.3.1 --push
#   ./scripts/build-docker.sh cloud-bridge        # build cloud bridge image
#   ./scripts/build-docker.sh cloud-bridge --push
#
# Legacy (slicer only):
#   ./scripts/build-docker.sh 2.3.1          # build slicer image
#   ./scripts/build-docker.sh 2.3.1 --push

set -euo pipefail

TARGET="${1:?Usage: $0 <slicer|cloud-bridge|orca-version> [version] [--push]}"

case "$TARGET" in
    slicer)
        VERSION="${2:?Usage: $0 slicer <orca-version> [--push]}"
        PUSH="${3:-}"
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
            docker push "${IMAGE}"
            docker push fabprint/fabprint:latest
            echo "Pushed."
        fi
        ;;

    cloud-bridge)
        PUSH="${2:-}"
        IMAGE="fabprint/cloud-bridge:latest"

        echo "Building ${IMAGE} ..."
        docker build \
            --platform linux/amd64 \
            -f Dockerfile.cloud-bridge \
            -t "${IMAGE}" \
            .

        echo "Build complete: ${IMAGE}"

        if [ "${PUSH}" = "--push" ]; then
            docker push "${IMAGE}"
            echo "Pushed."
        fi
        ;;

    *)
        # Legacy: treat first arg as OrcaSlicer version
        VERSION="$TARGET"
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
            docker push "${IMAGE}"
            docker push fabprint/fabprint:latest
            echo "Pushed."
        fi
        ;;
esac
