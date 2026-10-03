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

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.playback_session import PlaybackSession, PlaybackSessionOutput
from ...models.user import User
from ...services.auth import get_user_by_username
from ...services.outputs import (
    get_http_stream_for_manage,
    list_http_streams,
    set_output_enabled,
)
from ...services.playback import handle_command, session_state_dict
from ...streams.http import (
    normalize_mount,
    stream_listener_pattern,
    stream_meta_key,
)
from ..deps import get_current_user, get_current_user_optional, get_db

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
    # ``True`` when the requester may manage the stream (its owner or an admin).
    can_manage: bool
    owner: StreamOwnerResponse
    enabled: bool
    online: bool
    # State of the playback session driving this mount, when one is attached.
    playback_state: Optional[str] = None
    description: Optional[str] = None
    genre: Optional[str] = None
    format: Optional[str] = None
    bitrate: Optional[str] = None
    content_type: Optional[str] = None
    now_playing: Optional[StreamNowPlayingResponse] = None
    listener_count: int = 0
    created_at: datetime


class StreamUpdateRequest(BaseModel):
    """Owner/admin toggle for a listed stream."""

    enabled: bool


class StreamUpdateResponse(BaseModel):
    """Result of a stream toggle."""

    id: str
    enabled: bool


class StreamCommandRequest(BaseModel):
    """Owner/admin transport command for a listed stream (play or pause)."""

    command: str


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


async def _playback_states(db: AsyncSession, output_ids: list[str]) -> dict[str, str]:
    """Map each output id to the state of the session driving it, if any.

    Several sessions may have attached the same stream output over time; the
    most recently active one is the session the worker is actually driving.
    """
    if not output_ids:
        return {}
    stmt = (
        select(PlaybackSessionOutput.output_stream_id, PlaybackSession.state)
        .join(PlaybackSession, PlaybackSessionOutput.session_id == PlaybackSession.id)
        .where(
            PlaybackSessionOutput.output_kind == "stream",
            PlaybackSessionOutput.output_stream_id.in_(output_ids),
        )
        .order_by(PlaybackSession.last_active_at.desc().nulls_last())
    )
    states: dict[str, str] = {}
    for output_stream_id, state in (await db.execute(stmt)).all():
        states.setdefault(str(output_stream_id), state)
    return states


@router.get("/", response_model=list[StreamResponse])
async def list_streams(
    request: Request,
    owner_username: Optional[str] = Query(None, description="Filter by owner's username"),
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """
    List native HTTP streams visible to the requester.

    Public mounts (no listen token) are listed for everyone; token-protected
    and disabled mounts only appear to their owner and to admins.
    """
    owner_id: Optional[str] = None
    if owner_username:
        owner = await get_user_by_username(db, owner_username)
        if owner is None or not owner.is_active:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        owner_id = str(owner.id)

    redis = getattr(request.app.state, "redis", None)
    rows = await list_http_streams(db, user_id=owner_id)
    playback_states = await _playback_states(db, [str(output.id) for output, _, _ in rows])

    items: list[StreamResponse] = []
    for output, owner, cfg in rows:
        is_owner = user is not None and output.user_id == str(user.id)
        can_manage = is_owner or (user is not None and user.is_admin)
        token = str(cfg.get("listen_token") or "") or None
        if (token or not output.enabled) and not can_manage:
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
                stream_url=_stream_url(mount, token if can_manage else None),
                visibility="private" if token else "public",
                is_owner=is_owner,
                can_manage=can_manage,
                owner=StreamOwnerResponse(
                    username=owner.username,
                    display_name=owner.display_name,
                    avatar_url=owner.avatar_url,
                ),
                enabled=output.enabled,
                online=online,
                playback_state=playback_states.get(str(output.id)),
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


@router.patch("/{output_id}", response_model=StreamUpdateResponse)
async def update_stream(
    output_id: str,
    body: StreamUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Enable or disable a stream (owner or admin)."""
    output = await get_http_stream_for_manage(db, output_id, current_user)
    output = await set_output_enabled(db, output, current_user, body.enabled)
    await db.commit()
    return StreamUpdateResponse(id=str(output.id), enabled=output.enabled)


@router.post("/{output_id}/command")
async def stream_command(
    output_id: str,
    body: StreamCommandRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Send a transport command to the session driving a stream (owner or admin)."""
    if body.command not in ("play", "pause"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unsupported command: {body.command}",
        )
    output = await get_http_stream_for_manage(db, output_id, current_user)

    # The worker claims the most recently active session attached to this
    # output; commands target that same session so they reach its driver.
    session = (
        await db.execute(
            select(PlaybackSession)
            .join(
                PlaybackSessionOutput,
                PlaybackSessionOutput.session_id == PlaybackSession.id,
            )
            .where(
                PlaybackSessionOutput.output_kind == "stream",
                PlaybackSessionOutput.output_stream_id == str(output.id),
            )
            .order_by(PlaybackSession.last_active_at.desc().nulls_last())
            .limit(1)
        )
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No playback session is driving this stream",
        )

    await handle_command(db, session, body.command, {}, None)
    return await session_state_dict(db, session)
