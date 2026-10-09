"""
Live broadcast plumbing shared between the web process and the stream worker.

A live broadcast is driven by three Redis keys, all scoped by **output id**
(not the mount slug, so a rename mid-broadcast cannot cross wires):

- ``songhive:stream:live:{output_id}`` — a short-TTL JSON claim written by
  the ingest WebSocket handler (``streaming/live.py``) and refreshed while
  the broadcaster's socket is open. ``SET NX`` enforces a single
  broadcaster per mount; its expiry means the broadcaster's web process
  died. The worker polls this key to start/stop the live source.
- ``songhive:stream:ingest:{output_id}`` — a capped stream carrying the
  compressed audio as it arrives:
  ``{"start": ingest_id, "mime": ...}``, then ``{"d": <b64>}`` chunks, then
  ``{"end": ingest_id}``. The worker's live decoder (``kind="live"``,
  ``IcecastDriver``) reads this stream into ffmpeg's stdin.
- ``songhive:stream:ingest:header:{output_id}`` — the first audio chunk of
  the current broadcast, so a worker that joins after the ``start`` entry
  was trimmed can still hand ffmpeg a container header and resync on the
  next cluster/page boundary.

A short-TTL ``songhive:stream:worker:{worker_id}`` heartbeat lets the ingest
endpoint fail fast with "stream worker unavailable" instead of letting the
broadcaster wait out the start timeout.
"""

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

from redis.exceptions import WatchError

logger = logging.getLogger(__name__)

LIVE_KEY_TTL_SECONDS = 15
LIVE_KEY_REFRESH_SECONDS = 5.0
LIVE_KEY_PATTERN = "songhive:stream:live:*"
WORKER_HEARTBEAT_TTL_SECONDS = 10
WORKER_HEARTBEAT_PATTERN = "songhive:stream:worker:*"
INGEST_HEADER_TTL_SECONDS = 3600

# Browser MediaRecorder container types → ffmpeg demuxer names. ``webm`` is
# deliberately not a demuxer name: ffmpeg reads WebM through ``matroska``.
# Bytes are only ever decoded through this allowlist — never auto-probed.
MIME_TO_DEMUXER = {
    "audio/webm": "matroska",
    "audio/ogg": "ogg",
    "audio/mp4": "mp4",
}


def live_key(output_id: str) -> str:
    """Claim key marking a live broadcast requested on an output."""
    return f"songhive:stream:live:{output_id}"


def ingest_key(output_id: str) -> str:
    """Capped stream carrying live audio chunks to the stream worker."""
    return f"songhive:stream:ingest:{output_id}"


def ingest_header_key(output_id: str) -> str:
    """Cached container header chunk for late-joining workers."""
    return f"songhive:stream:ingest:header:{output_id}"


def worker_heartbeat_key(worker_id: str) -> str:
    """TTL'd heartbeat key proving a stream worker is running."""
    return f"songhive:stream:worker:{worker_id}"


def demuxer_for_mime(mime: Optional[str]) -> Optional[str]:
    """Map an ingest MIME type to an ffmpeg demuxer name, if supported."""
    base = str(mime or "").split(";", 1)[0].strip().lower()
    return MIME_TO_DEMUXER.get(base)


def encode_live_state(
    *,
    ingest_id: str,
    user_id: str,
    mime: str,
    title: str,
    started_at: Optional[float] = None,
) -> str:
    """Serialize the live claim payload exactly as stored in Redis."""
    return json.dumps(
        {
            "ingest_id": ingest_id,
            "user_id": user_id,
            "mime": mime,
            "title": title,
            "started_at": started_at if started_at is not None else time.time(),
        }
    )


@dataclass(frozen=True)
class LiveState:
    """Parsed contents of ``songhive:stream:live:{output_id}``."""

    ingest_id: str
    user_id: str
    mime: str
    title: str
    started_at: float


async def read_live_state(redis, output_id: str) -> Optional[LiveState]:
    """Return the parsed live claim for an output, or None."""
    raw = await redis.get(live_key(output_id))
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("ingest_id"):
        return None
    return LiveState(
        ingest_id=str(data["ingest_id"]),
        user_id=str(data.get("user_id") or ""),
        mime=str(data.get("mime") or ""),
        title=str(data.get("title") or ""),
        started_at=float(data.get("started_at") or 0.0),
    )


async def _compare_and_update(redis, output_id: str, payload: str, update) -> bool:
    """
    Run ``update(pipe, key)`` in a transaction only if the key still holds
    ``payload`` — a compare-and-set that works without server-side Lua.
    """
    key = live_key(output_id)
    for _ in range(3):
        try:
            async with redis.pipeline(transaction=True) as pipe:
                await pipe.watch(key)
                if (await pipe.get(key)) != payload:
                    await pipe.unwatch()
                    return False
                pipe.multi()
                update(pipe, key)
                await pipe.execute()
                return True
        except WatchError:
            continue
    return False


async def refresh_live_key(redis, output_id: str, payload: str) -> bool:
    """Extend the live key's TTL only if it still holds ``payload``."""
    return await _compare_and_update(
        redis,
        output_id,
        payload,
        lambda pipe, key: pipe.set(key, payload, ex=LIVE_KEY_TTL_SECONDS),
    )


async def delete_live_key(redis, output_id: str, payload: str) -> bool:
    """Delete the live key only if it still holds ``payload``."""
    return await _compare_and_update(
        redis,
        output_id,
        payload,
        lambda pipe, key: pipe.delete(key),
    )


async def store_ingest_header(redis, output_id: str, ingest_id: str, chunk: bytes) -> None:
    """Cache the broadcast's first audio chunk for late-joining workers."""
    payload = json.dumps({"ingest_id": ingest_id, "d": base64.b64encode(chunk).decode("ascii")})
    await redis.set(ingest_header_key(output_id), payload, ex=INGEST_HEADER_TTL_SECONDS)


async def drop_ingest_header(redis, output_id: str, ingest_id: str) -> None:
    """Delete the cached header only if it belongs to ``ingest_id``."""
    raw = await redis.get(ingest_header_key(output_id))
    if raw is None:
        return
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return
    if isinstance(data, dict) and data.get("ingest_id") == ingest_id:
        await redis.delete(ingest_header_key(output_id))


async def find_ingest_start(redis, output_id: str, ingest_id: str) -> Optional[str]:
    """
    Return the stream id of the ``start`` entry for ``ingest_id``.

    None means the entry was trimmed by MAXLEN (or never written); callers
    then fall back to the cached container header plus a ``$`` tail.
    """
    entries = await redis.xrange(ingest_key(output_id))
    for entry_id, fields in entries:
        if fields.get("start") == ingest_id:
            return entry_id
    return None


async def worker_heartbeat_present(redis) -> bool:
    """Return True when at least one stream worker heartbeat key exists."""
    async for _ in redis.scan_iter(match=WORKER_HEARTBEAT_PATTERN, count=20):
        return True
    return False


async def ingest_iterator(
    redis,
    output_id: str,
    ingest_id: str,
    *,
    start_id: Optional[str],
) -> AsyncIterator[bytes]:
    """
    Yield the live audio chunks of one broadcast in arrival order.

    Tails ``songhive:stream:ingest:{output_id}`` from the broadcast's
    ``start`` entry. Returns when the matching ``{"end": ingest_id}``
    arrives, when a ``start`` for a different ``ingest_id`` supersedes the
    broadcast, or when a read timeout finds the live key gone or owned by
    another broadcast (the ingest handler crashed). No lag skipping: the
    bytes are a container stream that cannot be cut mid-cluster, and the
    broadcaster's real-time arrival already paces the decoder.

    When ``start_id`` is None (worker claimed the output after the entry
    was trimmed), the cached container header is replayed first and only
    new entries are tailed — ffmpeg resyncs on the next cluster/page
    boundary. Without a stored header there is no decodable cut point and
    the iterator ends immediately.
    """
    key = ingest_key(output_id)
    if start_id is None:
        raw = await redis.get(ingest_header_key(output_id))
        payload: dict[str, Any] = {}
        if raw is not None:
            try:
                data = json.loads(raw)
                if isinstance(data, dict):
                    payload = data
            except (TypeError, ValueError):
                pass
        if payload.get("ingest_id") != ingest_id or not payload.get("d"):
            return
        yield base64.b64decode(payload["d"])
        last_id = "$"
    else:
        last_id = start_id

    block_ms = max(1000, int(LIVE_KEY_REFRESH_SECONDS * 1000))
    while True:
        try:
            result = await redis.xread({key: last_id}, count=64, block=block_ms)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A transient Redis error must not kill the broadcast; the next
            # read retry resolves the truth.
            await asyncio.sleep(0.5)
            continue
        entries: list = []
        if isinstance(result, dict):
            entries = result.get(key) or []
        elif result:
            entries = [e for name, e in result if name == key]
            entries = entries[0] if entries else []
        if not entries:
            # Read timeout: the live key is the broadcast's heartbeat — if it
            # expired or was taken over, this feed is over even without an
            # explicit ``end`` entry.
            try:
                state = await read_live_state(redis, output_id)
            except Exception:
                state = LiveState(ingest_id=ingest_id, user_id="", mime="", title="", started_at=0.0)
            if state is None or state.ingest_id != ingest_id:
                return
            continue
        for entry_id, fields in entries:
            last_id = entry_id
            if fields.get("end") == ingest_id:
                return
            start_marker = fields.get("start")
            if start_marker is not None and start_marker != ingest_id:
                # A new broadcaster took over; this feed is dead.
                return
            data = fields.get("d")
            if data:
                yield base64.b64decode(data)
