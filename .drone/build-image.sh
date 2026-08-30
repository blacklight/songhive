#!/bin/sh

set -e

# Use sensible defaults for the registry and username.
REGISTRY_ENDPOINT="${REGISTRY_ENDPOINT:-quay.io}"
DOCKER_USER="${DOCKER_USER:-blacklight}"

if [ -z "$DOCKER_PASS" ]; then
    echo "Please set the DOCKER_PASS environment variable" >&2
    exit 1
fi

# Use the git tag as the image version, stripping an optional leading "v".
# Fall back to the version in pyproject.toml if DRONE_TAG is not set.
VERSION="${DRONE_TAG#v}"
if [ -z "$VERSION" ]; then
    VERSION=$(sed -n '/current_version/s/.*= "\([^"]*\)".*/\1/p' pyproject.toml)
fi

IMAGE_NAME="${IMAGE_NAME:-$REGISTRY_ENDPOINT/$DOCKER_USER/songhive}"

# Log in to the container registry
echo -n "$DOCKER_PASS" | docker login "$REGISTRY_ENDPOINT" -u "$DOCKER_USER" --password-stdin

# Set up QEMU for multi-platform builds
docker run --rm --privileged multiarch/qemu-user-static --reset -p yes

# Create a BuildKit builder for multi-platform images
BUILDER=multiarch
docker buildx rm "$BUILDER" 2>/dev/null || true
docker buildx create --name="$BUILDER" --driver=docker-container --use
trap 'docker buildx rm "$BUILDER" 2>/dev/null || true' EXIT

CACHE_TAG="${IMAGE_NAME}:cache"

# Build and publish the images, exporting BuildKit cache to the registry.
docker buildx build \
    -f Dockerfile \
    -t "$IMAGE_NAME:$VERSION" \
    -t "$IMAGE_NAME:latest" \
    --builder "$BUILDER" \
    --cache-from "type=registry,ref=$CACHE_TAG" \
    --cache-to "type=registry,ref=$CACHE_TAG,mode=max" \
    --push .
