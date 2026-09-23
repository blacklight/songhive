"""
Playback session control plane.

This module owns the persisted session state, command dispatch, and fan-out for
Spotify-Connect-style playback on server outputs. It intentionally avoids
importing real providers; the worker owns driver/runtime details.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models.output_stream import OutputStream
from ..models.playback_session import PlaybackSession, PlaybackSessionOutput
from ..models.track import Track
from ..models.user import User
from ..services.redis import get_sync_redis_client
from ..services.scrobbler import thresholds_for
from ..services.streaming import record_listen

logger = logging.getLogger(__name__)

REDIS_CONTROL_PREFIX = "songhive:playback:control:{session_id}"


def _now_utc() -> datetime:
    """Return a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def _live_position_seconds(session: PlaybackSession, now: Optional[datetime] = None) -> float:
    """Return the current playback position, including elapsed time since the anchor."""
    now = now or _now_utc()
    if session.state == "playing" and session.position_anchor_at is not None:
        return session.position_seconds + (now - session.position_anchor_at).total_seconds()
    return session.position_seconds


def _clamp_position(seconds: float, duration: Optional[float] = None) -> float:
    """Clamp a seek position to a non-negative value and, if known, the track duration."""
    seconds = max(0.0, seconds)
    if duration is not None and duration > 0:
        seconds = min(seconds, duration)
    return seconds


def _current_track(session: PlaybackSession) -> Optional[dict]:
    """Return the current queue track dict, or None."""
    queue = session.queue or []
    if 0 <= session.current_index < len(queue):
        return queue[session.current_index]
    return None


def _track_duration(session: PlaybackSession) -> Optional[float]:
    """Return the duration of the current track, if known."""
    track = _current_track(session)
    if track is None:
        return None
    duration = track.get("duration")
    if isinstance(duration, (int, float)) and duration > 0:
        return float(duration)
    return None


def _queue_image_url(track: Track) -> Optional[str]:
    """Return the artwork URL for a queue track, falling back to the album cover."""
    if track.image_file_id and track.image_file is not None:
        return f"/api/v1/files/{track.image_file_id}/download"
    if track.album is not None:
        if track.album.cover_file_id and track.album.cover_file is not None:
            return f"/api/v1/files/{track.album.cover_file_id}/download"
        return track.album.cover_url
    return None


async def _enrich_queue(db: AsyncSession, queue: list) -> list:
    """Overlay display metadata from local tracks onto stored queue entries.

    The persisted queue only carries a small dict per track. Entries that
    resolve to local ``Track`` rows gain fresh ``artist_id``/``album_id``/
    ``image_url``/``visibility`` so clients can render the player bar while a
    remote output drives playback. Entries without a local row (e.g. remote
    attachments) keep whatever fields the client stored.
    """
    ids = [t.get("id") for t in queue if isinstance(t, dict) and t.get("id")]
    if not ids:
        return queue
    result = await db.execute(select(Track).where(Track.id.in_(ids)))
    tracks = {str(track.id): track for track in result.scalars().all()}
    if not tracks:
        return queue

    enriched = []
    for entry in queue:
        if not isinstance(entry, dict):
            enriched.append(entry)
            continue
        track = tracks.get(str(entry.get("id")))
        if track is None:
            enriched.append(entry)
            continue
        merged = dict(entry)
        merged["artist_id"] = track.artist_id
        merged["album_id"] = track.album_id
        if track.artist is not None:
            merged["artist"] = track.artist.name
        if track.album is not None:
            merged["album"] = track.album.title
        if track.duration:
            merged["duration"] = track.duration
        merged["visibility"] = track.visibility
        merged["image_url"] = _queue_image_url(track)
        enriched.append(merged)
    return enriched


def compute_next_index(
    session: PlaybackSession,
    *,
    direction: str = "next",
    position_seconds: Optional[float] = None,
) -> Optional[int]:
    """
    Compute the next queue index, matching ``frontend/src/stores/player.ts``.

    For ``next``: ``repeat one`` replays the current track; otherwise move to
    the next index, wrapping to 0 when ``repeat all`` is on and the end is
    reached. Returns ``None`` when playback should stop.

    For ``prev``: if the current position is greater than 3 seconds, restart
    the current track; otherwise move to the previous index, wrapping to the
    end when ``repeat all`` is on.
    """
    queue = session.queue or []
    length = len(queue)
    if length == 0:
        return None

    current = session.current_index
    if direction == "next":
        if session.repeat == "one":
            return current
        next_index = current + 1
        if next_index < length:
            return next_index
        if session.repeat == "all":
            return 0
        return None

    if direction == "prev":
        if position_seconds is not None and position_seconds > 3:
            return current
        prev_index = current - 1
        if prev_index >= 0:
            return prev_index
        if session.repeat == "all":
            return length - 1
        return None

    return None


async def get_or_create_session(db: AsyncSession, user: User) -> PlaybackSession:
    """Load the user's playback session, creating one if necessary."""
    result = await db.execute(
        select(PlaybackSession)
        .where(PlaybackSession.user_id == str(user.id))
        .options(selectinload(PlaybackSession.outputs))
    )
    session = result.scalar_one_or_none()
    if session is None:
        session = PlaybackSession(
            user_id=str(user.id),
            state="idle",
            current_index=0,
            position_seconds=0.0,
            position_anchor_at=None,
            repeat="off",
            shuffle=False,
            queue=[],
            last_active_at=_now_utc(),
        )
        db.add(session)
        await db.flush()
    return session


async def get_session_for_user(db: AsyncSession, user_id: str) -> Optional[PlaybackSession]:
    """Load a session by user id, returning None if none exists."""
    result = await db.execute(
        select(PlaybackSession).where(PlaybackSession.user_id == user_id).options(selectinload(PlaybackSession.outputs))
    )
    return result.scalar_one_or_none()


async def select_outputs(
    db: AsyncSession,
    session: PlaybackSession,
    output_ids: list[str],
    user: User,
    *,
    connection_id: Optional[str] = None,
) -> PlaybackSession:
    """Replace the outputs attached to a session."""
    await db.execute(delete(PlaybackSessionOutput).where(PlaybackSessionOutput.session_id == session.id))

    if connection_id:
        session.controller_connection_id = connection_id

    stream_count = 0
    for output_id in output_ids:
        if output_id == "web":
            db.add(
                PlaybackSessionOutput(
                    session_id=session.id,
                    output_kind="web",
                    connection_id=connection_id,
                    status="live",
                )
            )
        else:
            stream_count += 1
            if stream_count > 1:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="Only one stream output is supported in v1",
                )
            output = await db.get(OutputStream, output_id)
            if output is None or output.user_id != str(user.id):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Output not found: {output_id}",
                )
            db.add(
                PlaybackSessionOutput(
                    session_id=session.id,
                    output_kind="stream",
                    output_stream_id=output.id,
                    status="connecting",
                )
            )

    session.last_active_at = _now_utc()
    await db.flush()
    await db.refresh(session, ["outputs"])
    return session


async def session_state_dict(db: AsyncSession, session: PlaybackSession) -> dict:
    """Return a JSON-safe representation of the current session state."""
    await db.refresh(session, ["outputs"])
    now = _now_utc()
    live_position = _live_position_seconds(session, now)

    # Resolve output names in one query.
    stream_ids = [out.output_stream_id for out in session.outputs or [] if out.output_stream_id is not None]
    names: dict[str, Optional[str]] = {}
    if stream_ids:
        result = await db.execute(select(OutputStream.id, OutputStream.name).where(OutputStream.id.in_(stream_ids)))
        for row in result.all():
            names[row[0]] = row[1]

    outputs = []
    for out in session.outputs or []:
        outputs.append(
            {
                "id": out.id,
                "output_kind": out.output_kind,
                "output_stream_id": out.output_stream_id,
                "connection_id": out.connection_id,
                "status": out.status,
                "last_error": out.last_error,
                "latency_offset_ms": out.latency_offset_ms,
                "name": names.get(out.output_stream_id) if out.output_stream_id else None,
            }
        )

    return {
        "session_id": session.id,
        "user_id": session.user_id,
        "state": session.state,
        "current_index": session.current_index,
        "position_seconds": session.position_seconds,
        "position_anchor_at": session.position_anchor_at.isoformat() if session.position_anchor_at else None,
        "live_position_seconds": live_position,
        "repeat": session.repeat,
        "shuffle": session.shuffle,
        "volume": session.volume,
        "controller_connection_id": session.controller_connection_id,
        "queue": await _enrich_queue(db, session.queue or []),
        "outputs": outputs,
        "last_active_at": session.last_active_at.isoformat() if session.last_active_at else None,
    }


def publish_playback_event(user_id: str, state: dict) -> None:
    """Fan out the current session state to all of the user's tabs."""
    # Import locally to avoid an import cycle with ``ws.events``.
    from ..ws.events import EventWebSocket

    try:
        EventWebSocket.send_to_user(user_id, "playback_session", state)
    except Exception:
        logger.exception("Failed to send playback_session event to %s", user_id)


def _control_key(session_id: str) -> str:
    return REDIS_CONTROL_PREFIX.format(session_id=session_id)


def publish_control_command(
    session_id: str,
    command: str,
    args: dict,
    issued_by: Optional[str],
) -> None:
    """Push a command envelope to the worker's control list."""
    envelope = {
        "type": "command",
        "command": command,
        "args": args,
        "issued_by": issued_by,
    }
    try:
        get_sync_redis_client().rpush(_control_key(session_id), json.dumps(envelope))
    except Exception:
        logger.exception("Failed to publish control command to %s", session_id)


def _assert_control(session: PlaybackSession, connection_id: Optional[str]) -> None:
    """Require the caller to be the current controller (or no controller)."""
    if not connection_id:
        return
    if session.controller_connection_id is None:
        session.controller_connection_id = connection_id
    elif session.controller_connection_id != connection_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Another connection is controlling this session",
        )


def _refresh_anchor(session: PlaybackSession, now: Optional[datetime] = None) -> None:
    """Mark playback as live from this moment at the current position."""
    session.position_anchor_at = now or _now_utc()


async def _commit_command(
    db: AsyncSession,
    session: PlaybackSession,
    command: str,
    args: dict,
    connection_id: Optional[str],
) -> None:
    """Persist the mutation, publish the event, and notify the worker."""
    session.last_active_at = _now_utc()
    await db.flush()
    state = await session_state_dict(db, session)
    # Commit before notifying the worker: it reads the session through its own
    # connection, so an earlier control envelope would race the commit and make
    # it sync the driver to stale state.
    await db.commit()
    publish_playback_event(session.user_id, state)
    publish_control_command(session.id, command, args, connection_id)


async def cmd_play(
    db: AsyncSession,
    session: PlaybackSession,
    connection_id: Optional[str] = None,
) -> None:
    """Start or resume playback from the current position."""
    _assert_control(session, connection_id)
    track = _current_track(session)
    if track is None:
        return
    session.state = "playing"
    _refresh_anchor(session)
    await _commit_command(db, session, "play", {}, connection_id)


async def cmd_play_at(
    db: AsyncSession,
    session: PlaybackSession,
    index: int,
    connection_id: Optional[str] = None,
) -> None:
    """Start playback at the given queue index."""
    _assert_control(session, connection_id)
    queue = session.queue or []
    if index < 0 or index >= len(queue):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Queue index out of range",
        )
    session.current_index = index
    session.position_seconds = 0.0
    session.state = "playing"
    _refresh_anchor(session)
    await _commit_command(db, session, "play_at", {"index": index}, connection_id)


async def cmd_pause(
    db: AsyncSession,
    session: PlaybackSession,
    connection_id: Optional[str] = None,
) -> None:
    """Pause playback, freezing the live position."""
    _assert_control(session, connection_id)
    if session.state != "playing":
        return
    session.position_seconds = _live_position_seconds(session)
    session.position_anchor_at = None
    session.state = "paused"
    await _commit_command(db, session, "pause", {}, connection_id)


async def cmd_seek(
    db: AsyncSession,
    session: PlaybackSession,
    seconds: float,
    connection_id: Optional[str] = None,
) -> None:
    """Seek to the given position in the current track."""
    _assert_control(session, connection_id)
    duration = _track_duration(session)
    session.position_seconds = _clamp_position(seconds, duration)
    if session.state == "playing":
        _refresh_anchor(session)
    await _commit_command(db, session, "seek", {"seconds": session.position_seconds}, connection_id)


async def cmd_next(
    db: AsyncSession,
    session: PlaybackSession,
    connection_id: Optional[str] = None,
) -> None:
    """Advance to the next track."""
    _assert_control(session, connection_id)
    next_index = compute_next_index(session, direction="next")
    if next_index is None:
        return
    session.current_index = next_index
    session.position_seconds = 0.0
    session.state = "playing"
    _refresh_anchor(session)
    await _commit_command(db, session, "next", {}, connection_id)


async def cmd_prev(
    db: AsyncSession,
    session: PlaybackSession,
    connection_id: Optional[str] = None,
) -> None:
    """Return to the previous track or restart the current one."""
    _assert_control(session, connection_id)
    live_position = _live_position_seconds(session)
    prev_index = compute_next_index(session, direction="prev", position_seconds=live_position)
    if prev_index is None:
        return
    session.current_index = prev_index
    session.position_seconds = 0.0
    session.state = "playing"
    _refresh_anchor(session)
    await _commit_command(db, session, "prev", {}, connection_id)


async def cmd_set_queue(
    db: AsyncSession,
    session: PlaybackSession,
    queue: list[dict],
    connection_id: Optional[str] = None,
) -> None:
    """Replace the session queue, preserving playback when the current track survives."""
    _assert_control(session, connection_id)

    current = _current_track(session)
    current_id = current.get("id") if current else None
    surviving_index = None
    if current_id is not None:
        for i, entry in enumerate(queue):
            if isinstance(entry, dict) and entry.get("id") == current_id:
                surviving_index = i
                break

    session.queue = queue
    if surviving_index is not None:
        # The current track is still in the queue: keep playing it
        # uninterrupted, only adjusting the index to its new position.
        session.current_index = surviving_index
        if session.state == "playing":
            session.position_seconds = _live_position_seconds(session)
            _refresh_anchor(session)
    else:
        session.current_index = 0
        session.position_seconds = 0.0
        session.position_anchor_at = None
        session.state = "idle"
    await _commit_command(db, session, "set_queue", {"queue_length": len(queue)}, connection_id)


async def cmd_set_repeat(
    db: AsyncSession,
    session: PlaybackSession,
    repeat: str,
    connection_id: Optional[str] = None,
) -> None:
    """Set the repeat mode."""
    _assert_control(session, connection_id)
    if repeat not in ("off", "all", "one"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="repeat must be one of off/all/one",
        )
    session.repeat = repeat
    await _commit_command(db, session, "set_repeat", {"repeat": repeat}, connection_id)


async def cmd_set_shuffle(
    db: AsyncSession,
    session: PlaybackSession,
    shuffle: bool,
    connection_id: Optional[str] = None,
) -> None:
    """Set the shuffle flag."""
    _assert_control(session, connection_id)
    session.shuffle = bool(shuffle)
    await _commit_command(db, session, "set_shuffle", {"shuffle": session.shuffle}, connection_id)


async def cmd_set_volume(
    db: AsyncSession,
    session: PlaybackSession,
    volume: float,
    connection_id: Optional[str] = None,
) -> None:
    """Set the output gain (0.0–1.0) for the session's stream outputs."""
    _assert_control(session, connection_id)
    session.volume = min(max(volume, 0.0), 1.0)
    await _commit_command(db, session, "set_volume", {"volume": session.volume}, connection_id)


async def cmd_stop(
    db: AsyncSession,
    session: PlaybackSession,
    connection_id: Optional[str] = None,
) -> None:
    """Stop playback."""
    _assert_control(session, connection_id)
    session.state = "idle"
    session.position_seconds = 0.0
    session.position_anchor_at = None
    await _commit_command(db, session, "stop", {}, connection_id)


async def cmd_take_control(
    db: AsyncSession,
    session: PlaybackSession,
    connection_id: Optional[str],
) -> None:
    """Claim the session controller role."""
    if not connection_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="connection_id is required",
        )
    session.controller_connection_id = connection_id
    await _commit_command(db, session, "take_control", {}, connection_id)


async def cmd_release_control(
    db: AsyncSession,
    session: PlaybackSession,
    connection_id: Optional[str],
) -> None:
    """Release the session controller role if this connection holds it."""
    if not connection_id:
        return
    if session.controller_connection_id == connection_id:
        session.controller_connection_id = None
    await _commit_command(db, session, "release_control", {}, connection_id)


COMMAND_HANDLERS: dict[str, Callable[..., Awaitable[None]]] = {
    "play": cmd_play,
    "play_at": cmd_play_at,
    "pause": cmd_pause,
    "seek": cmd_seek,
    "next": cmd_next,
    "prev": cmd_prev,
    "set_queue": cmd_set_queue,
    "set_repeat": cmd_set_repeat,
    "set_shuffle": cmd_set_shuffle,
    "set_volume": cmd_set_volume,
    "stop": cmd_stop,
    "take_control": cmd_take_control,
    "release_control": cmd_release_control,
}


async def handle_command(
    db: AsyncSession,
    session: PlaybackSession,
    command: str,
    args: dict,
    connection_id: Optional[str],
) -> None:
    """Dispatch a playback command to the appropriate handler."""
    handler = COMMAND_HANDLERS.get(command)
    if handler is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown command: {command}",
        )

    # Normalize empty args for ``play_at``/``seek``/``set_queue``.
    if command == "play_at":
        index = args.get("index", 0)
        if not isinstance(index, int):
            try:
                index = int(index)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="index must be an integer",
                )
        await cmd_play_at(db, session, index, connection_id)
    elif command == "seek":
        seconds = args.get("seconds", 0)
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="seconds must be a number",
            )
        await cmd_seek(db, session, seconds, connection_id)
    elif command == "set_queue":
        queue = args.get("queue", [])
        if not isinstance(queue, list):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="queue must be a list",
            )
        await cmd_set_queue(db, session, queue, connection_id)
    elif command == "set_repeat":
        await cmd_set_repeat(db, session, args.get("repeat", "off"), connection_id)
    elif command == "set_shuffle":
        await cmd_set_shuffle(db, session, args.get("shuffle", False), connection_id)
    elif command == "set_volume":
        try:
            volume = float(args.get("volume", 1.0))
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="volume must be a number",
            )
        await cmd_set_volume(db, session, volume, connection_id)
    else:
        await handler(db, session, connection_id)


async def record_server_listen(
    db: AsyncSession,
    session: PlaybackSession,
    track_id: str,
    played_at: Optional[datetime] = None,
) -> bool:
    """Record a listen for server playback when the play crosses the user's threshold."""
    user = await db.get(User, session.user_id)
    min_seconds, min_percent = await thresholds_for(db, user)

    track = await db.get(Track, track_id)
    played_seconds = _live_position_seconds(session)

    threshold_met = played_seconds >= min_seconds
    if (min_percent is not None and track is not None and track.duration and track.duration > 0) and (
        played_seconds / track.duration
    ) * 100 >= min_percent:
        threshold_met = True

    if threshold_met:
        await record_listen(db, session.user_id, track_id, played_at)
        return True
    return False


async def clear_controller(
    db: AsyncSession,
    user_id: str,
    connection_id: str,
) -> None:
    """Clear the controller when its WebSocket connection closes."""
    result = await db.execute(
        select(PlaybackSession).where(PlaybackSession.user_id == user_id).options(selectinload(PlaybackSession.outputs))
    )
    session = result.scalar_one_or_none()
    if session is None:
        return
    if session.controller_connection_id != connection_id:
        return

    session.controller_connection_id = None
    await db.flush()
    state = await session_state_dict(db, session)
    publish_playback_event(session.user_id, state)
    publish_control_command(session.id, "release_control", {}, None)
