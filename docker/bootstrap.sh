#!/bin/sh

# Bootstrap a docker-compose environment for Songhive
# by downloading the latest files from Github.

set -e

export BASE_URL="https://raw.githubusercontent.com/blacklight/songhive/refs/heads"
export BRANCH="${BRANCH:-main}"

if type curl >/dev/null 2>&1; then
    CURL="curl -s"
elif type wget >/dev/null 2>&1; then
    CURL=wget
else
    echo "Unable to find curl or wget" >&2
    exit 1
fi

DOCKER_DIR="./docker"
SCRIPTS_DIR="./scripts"

echo "Bootstrapping from branch $BRANCH"
mkdir -p "$DOCKER_DIR"
mkdir -p "$SCRIPTS_DIR"

echo "Downloading docker-compose.yml"
$CURL -o docker-compose.yml "$BASE_URL/$BRANCH/docker-compose.yml"
echo "Downloading nginx configuration"
$CURL -o "$DOCKER_DIR/nginx.conf" "$BASE_URL/$BRANCH/docker/nginx.conf"
echo "Downloading setup volumes script"
$CURL -o "$SCRIPTS_DIR/setup-volumes.sh" "$BASE_URL/$BRANCH/scripts/setup-volumes.sh"

if [ ! -e config.toml ]; then
    echo "Downloading sample configuration"
    $CURL -o config.toml "$BASE_URL/$BRANCH/config.toml.example"
else
    echo "Using existing config.toml"
fi

chmod +x "$SCRIPTS_DIR/setup-volumes.sh"

echo
echo "Set up your instance settings in config.toml."
echo "Then run docker compose up."
