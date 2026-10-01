"""
Native HTTP stream directory routes.

HTTP mounts carry no dedicated ACL column: a mount configured with a
``listen_token`` is private — the token is the only credential a plain
``<audio>`` element or media player can present — while a mount without one
is public. The directory therefore lists public mounts to everyone
(including anonymous visitors) and token-protected mounts only to their
owner, who also gets a token-bearing playable URL back so the page's own
player can connect.
"""

import json
import logging
from datetime import datetime
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.user import User
from ...services.outputs import list_http_streams
from ...streams.http import (
    normalize_mount,
    stream_listener_pattern,
    stream_meta_key,
)
from ..deps import get_current_user_optional, get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/streams")


class StreamOwnerResponse(BaseModel):
    """Public-facing owner summary for a listed stream."""

    username: str
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None


class StreamNowPlayingResponse(BaseModel):
    """Currently-playing track on a live stream, when the driver reports one."""

    track_id: Optional[str] = None
    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None


class StreamResponse(BaseModel):
    """A native HTTP stream visible to the requester."""

    id: str
    name: str
    mount: str
    stream_url: str
    visibility: str
    is_owner: bool
    owner: StreamOwnerResponse
    enabled: bool
    online: bool
    description: Optional[str] = None
    genre: Optional[str] = None
    format: Optional[str] = None
    bitrate: Optional[str] = None
    content_type: Optional[str] = None
    now_playing: Optional[StreamNowPlayingResponse] = None
    listener_count: int = 0
    created_at: datetime


async def _listener_count(redis, mount: str) -> int:
    """Approximate active listeners via the mount's per-listener TTL keys."""
    count = 0
    try:
        async for _ in redis.scan_iter(match=stream_listener_pattern(mount), count=100):
            count += 1
    except Exception:
        logger.warning("Failed to count listeners for mount %s", mount)
    return count


def _stream_url(mount: str, token: Optional[str]) -> str:
    """Build the mountpoint URL, embedding the listen token when supplied."""
    url = f"/streams/{mount}"
    if token:
        url += f"?token={quote(token, safe='')}"
    return url


@router.get("/", response_model=list[StreamResponse])
async def list_streams(
    request: Request,
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """
    List native HTTP streams visible to the requester.

    Public mounts (no listen token) are listed for everyone; token-protected
    mounts only appear to their owner, as do disabled outputs.
    """
    redis = getattr(request.app.state, "redis", None)
    rows = await list_http_streams(db)

    items: list[StreamResponse] = []
    for output, owner, cfg in rows:
        is_owner = user is not None and output.user_id == str(user.id)
        token = str(cfg.get("listen_token") or "") or None
        if (token or not output.enabled) and not is_owner:
            continue

        mount = normalize_mount(cfg.get("mount"))
        meta: dict = {}
        online = False
        listeners = 0
        if redis is not None and mount:
            meta_raw = await redis.get(stream_meta_key(mount))
            online = meta_raw is not None
            if online:
                try:
                    meta = json.loads(meta_raw)
                except (TypeError, ValueError):
                    meta = {}
            listeners = await _listener_count(redis, mount)

        now_playing = None
        if meta.get("song") or meta.get("title"):
            now_playing = StreamNowPlayingResponse(
                track_id=str(meta.get("track_id") or "") or None,
                title=meta.get("title") or None,
                artist=meta.get("artist") or None,
                album=meta.get("album") or None,
            )

        items.append(
            StreamResponse(
                id=str(output.id),
                name=output.name,
                mount=mount,
                stream_url=_stream_url(mount, token if is_owner else None),
                visibility="private" if token else "public",
                is_owner=is_owner,
                owner=StreamOwnerResponse(
                    username=owner.username,
                    display_name=owner.display_name,
                    avatar_url=owner.avatar_url,
                ),
                enabled=output.enabled,
                online=online,
                description=meta.get("description") or cfg.get("description") or None,
                genre=meta.get("genre") or cfg.get("genre") or None,
                format=str(cfg.get("format") or "") or None,
                bitrate=str(meta.get("bitrate") or cfg.get("bitrate") or "") or None,
                content_type=meta.get("content_type") or None,
                now_playing=now_playing,
                listener_count=listeners,
                created_at=output.created_at,
            )
        )

    items.sort(key=lambda item: (not item.online, item.name.lower()))
    return items
