#!/bin/sh
set -e

PUID=${PUID:-$(id -u)}
PGID=${PGID:-$(id -g)}
VOLUMES_DIR="${1:-${VOLUMES_DIR:-./volumes}}"

if [ "$(id -u)" -ne 0 ]; then
    if ! mkdir -p "$VOLUMES_DIR" 2>/dev/null; then
        echo "Error: cannot create $VOLUMES_DIR. Run with sudo or as root." >&2
        exit 1
    fi
fi

mkdir -p "$VOLUMES_DIR/config" \
         "$VOLUMES_DIR/data" \
         "$VOLUMES_DIR/db" \
         "$VOLUMES_DIR/redis" \
         "$VOLUMES_DIR/static"

chown -R "$PUID:$PGID" "$VOLUMES_DIR"
