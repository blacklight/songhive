#!/bin/sh
set -e

CONFIG_DIR=/etc/songhive
DATA_DIR=/data
MEDIA_DIR="$DATA_DIR/media"
FEDERATION_DIR="$DATA_DIR/federation"
SECRET_FILE="$DATA_DIR/secret_key"
STATIC_SOURCE=/app/songhive/static
STATIC_TARGET=/var/www/songhive

mkdir -p "$CONFIG_DIR" "$MEDIA_DIR" "$FEDERATION_DIR" "$STATIC_TARGET"

# Generate and persist an auth secret if the caller did not provide one.
# Multiple rootless containers (app and worker) share the /data volume, so
# an unprotected check-then-create would race and could leave an empty
# secret_key file.  Use a file lock and an atomic temp-file move instead.
_ensure_secret() {
    if [ -s "$SECRET_FILE" ]; then
        return 0
    fi

    mkdir -p "$DATA_DIR"
    (
        [ -e "${SECRET_FILE}.lock" ] || : > "${SECRET_FILE}.lock"
        flock -x 9
        # Re-check after acquiring the lock; another container may have created it.
        if [ -s "$SECRET_FILE" ]; then
            exit 0
        fi
        _tmp=$(mktemp "${SECRET_FILE}.XXXXXX")
        python -c "import secrets; print(secrets.token_urlsafe(64))" > "$_tmp"
        chmod 600 "$_tmp"
        mv -f "$_tmp" "$SECRET_FILE"
    ) 9> "${SECRET_FILE}.lock"
}

_ensure_secret

if [ -z "${SONGHIVE_AUTH__SECRET_KEY:-}" ]; then
    SONGHIVE_AUTH__SECRET_KEY="$(cat "$SECRET_FILE")"
    export SONGHIVE_AUTH__SECRET_KEY
fi

# Wait for Redis to be reachable.
python - <<'PY'
import socket
import time

for _ in range(30):
    try:
        with socket.create_connection(("redis", 6379), timeout=2):
            break
    except (socket.error, socket.timeout):
        time.sleep(1)
else:
    raise SystemExit("Redis is not reachable")
PY

# Wait for the database to be reachable.
db_ready=0
for _ in $(seq 1 30); do
    if python - <<'PY'
import asyncio
import songhive.models  # noqa: F401
from songhive.config import load_config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def check_db() -> None:
    config = load_config([])
    engine = create_async_engine(config.database.url)
    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))
    await engine.dispose()


asyncio.run(check_db())
PY
    then
        db_ready=1
        break
    fi
    sleep 2
done

if [ "$db_ready" -eq 0 ]; then
    echo "Database is not reachable" >&2
    exit 1
fi

# Only the main web server process needs to create the schema and publish
# static assets. Workers and admin CLI commands can use the existing schema.
if [ "$1" = "songhive" ] && [ -z "${2:-}" ]; then
    python - <<'PY'
import asyncio
import songhive.models  # noqa: F401
from songhive.config import load_config
from songhive.models.base import Base
from sqlalchemy.ext.asyncio import create_async_engine


async def init_db() -> None:
    config = load_config([])
    engine = create_async_engine(config.database.url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


asyncio.run(init_db())
PY

    # Copy the built frontend assets into the shared static volume used by nginx.
    if [ -d "$STATIC_SOURCE" ] && [ -n "$(ls -A "$STATIC_SOURCE")" ]; then
        cp -r "$STATIC_SOURCE/." "$STATIC_TARGET/"
    fi
fi

exec "$@"
