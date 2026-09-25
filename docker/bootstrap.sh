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

echo "Bootstrapping from branch $BRANCH"
mkdir -p "$DOCKER_DIR"

echo "Downloading docker-compose.yml"
$CURL -o docker-compose.yml "$BASE_URL/$BRANCH/docker-compose.yml"
echo "Downloading nginx configuration"
$CURL -o "$DOCKER_DIR/nginx.conf" "$BASE_URL/$BRANCH/docker/nginx.conf"

if [ ! -e config.toml ]; then
    echo "Downloading sample configuration"
    $CURL -o config.toml "$BASE_URL/$BRANCH/config.toml.example"
else
    echo "Using existing config.toml"
fi

if [ ! -e .env ]; then
    echo "Downloading sample .env"
    $CURL -o .env "$BASE_URL/$BRANCH/.env.example"
else
    echo "Using existing .env"
fi

echo
echo "Set your instance settings in config.toml and .env (make sure that PUID"
echo "and PGID match your host user, e.g. $(id -u) and $(id -g))."
echo "Then run docker compose up -d."
