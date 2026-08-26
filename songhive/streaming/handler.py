"""
Tornado streaming handler for audio content delivery.
"""

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import tornado.iostream
import tornado.web

from ..api.middleware.auth import decode_access_token
from ..config.schema import SonghiveConfig, effective_bitrate
from ..models.base import get_session
from ..models.stored_file import StoredFile
from ..models.track import Track
from ..models.user import User
from ..services import acl
from ..services.auth import get_user_by_id
from ..services.storage import StorageService
from ..services.streaming import (
    cache_transcode,
    get_cached_transcode,
    record_listen,
    resolve_track_file,
)
from ..storage import get_storage
from ..storage.base import StorageBackend
from ..streaming.transcoder import Transcoder
from ..ws.events import EventWebSocket

logger = logging.getLogger(__name__)


@dataclass
class _StreamState:
    """
    Track streaming state for listen recording.
    """

    recorded: bool = False
    threshold_reached: bool = False


class StreamHandler(tornado.web.RequestHandler):
    """
    Tornado request handler for audio streaming.

    Supports range requests for the original source file, on-the-fly transcoding
    to common audio formats, and a content-addressed transcode cache.

    Authentication behaviour:
    * No ``Authorization`` header is treated as an anonymous request; public
      tracks are streamable and private tracks are rejected with ``403``.
    * A malformed or non-Bearer header, an invalid/expired token, or an
      inactive/deleted user is rejected with ``401``.
    """

    SUPPORTED_FORMATS = {"mp3", "ogg", "flac", "aac", "opus"}
    _FORMAT_BY_MIMETYPE = {fmt["mimetype"]: key for key, fmt in Transcoder.FORMAT_MAP.items()}

    @property
    def _config(self) -> SonghiveConfig:
        """Return the application configuration from Tornado settings."""
        return self.application.settings["config"]

    def _unauthorized(self):
        """Return a 401 Bearer challenge."""
        self.set_status(401)
        self.set_header("WWW-Authenticate", "Bearer")
        self.write({"error": "unauthorized"})

    def _forbidden(self):
        """Return a 403 access-denied response."""
        self.set_status(403)
        self.write({"error": "access denied"})

    def _not_found(self):
        """Return a 404 not-found response."""
        self.set_status(404)
        self.write({"error": "not found"})

    def _bad_request(self, message: str):
        """Return a 400 bad-request response."""
        self.set_status(400)
        self.write({"error": message})

    def _format_for_mimetype(self, content_type: str) -> Optional[str]:
        """Map a MIME type to one of the supported streaming formats."""
        mimetype = content_type.split(";")[0].strip().lower()
        return self._FORMAT_BY_MIMETYPE.get(mimetype)

    async def _load_user(self, session, token: str, config: SonghiveConfig) -> Optional[User]:
        """Decode a Bearer token and load the corresponding active user."""
        user_id = decode_access_token(token, config.auth.secret_key)
        if user_id is None:
            return None

        user = await get_user_by_id(session, user_id)
        if user is None or not user.is_active:
            return None

        return user

    async def _authenticate(self, session) -> Optional[User]:
        """Decode the Authorization header or access_token cookie and return the active user, if any."""
        auth_header = self.request.headers.get("Authorization", "")
        token: Optional[str] = None
        if auth_header:
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
            else:
                self._unauthorized()
                return None
        else:
            token = self.get_cookie("access_token")

        if not token:
            return None

        user = await self._load_user(session, token, self._config)
        if user is None:
            self._unauthorized()
            return None

        return user

    async def _resolve_track(self, session, track_id: str) -> Optional[tuple[Track, StoredFile]]:
        """Resolve a track and its backing stored file, returning 404 on failure."""
        track = await session.get(Track, track_id)
        stored_file = await resolve_track_file(session, track_id)
        if track is None or stored_file is None:
            self._not_found()
            return None

        return track, stored_file

    async def _check_access(self, session, track_id: str, user: Optional[User]) -> bool:
        """Check share-token and ACL access for the requested track."""
        share_token = self.get_argument("token", None)
        if not await acl.can_access(session, user, "track", track_id, share_token=share_token):
            self._forbidden()
            return False

        return True

    async def _require_local_path(self, storage_backend: StorageBackend, stored_file: StoredFile) -> Optional[Path]:
        """Retrieve the local path for a stored file, returning 404 on failure."""
        try:
            local_path = await storage_backend.retrieve(stored_file.storage_path)
        except ValueError:
            self._not_found()
            return None

        if local_path is None:
            self._not_found()
            return None

        return local_path

    def _broadcast_now_playing(self, track_id: str, user: Optional[User]):
        """Notify WebSocket subscribers that playback is starting."""
        EventWebSocket.broadcast(
            "now_playing",
            {
                "track_id": track_id,
                "user_id": str(user.id) if user is not None else None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            topic="now_playing",
        )

    def _parse_format(
        self,
        stored_file: StoredFile,
        fmt: Optional[str],
        bitrate: Optional[str],
    ) -> Optional[tuple[Optional[str], Optional[str], bool]]:
        """Resolve the requested format and bitrate and decide whether to passthrough."""
        source_format = self._format_for_mimetype(stored_file.content_type)

        if fmt is None and bitrate is None:
            return fmt, bitrate, True

        if fmt is None:
            fmt = source_format

        if fmt is None or fmt not in self.SUPPORTED_FORMATS:
            self._bad_request("unsupported format")
            return None

        passthrough = fmt == source_format and bitrate is None
        return fmt, bitrate, passthrough

    async def _record_listen_if_needed(
        self,
        session,
        track_id: str,
        user: Optional[User],
        state: _StreamState,
    ):
        """Record a listen once when the 30-second threshold has been crossed."""
        if state.recorded or user is None:
            return

        if state.threshold_reached:
            await record_listen(session, str(user.id), track_id)
            state.recorded = True

    async def _serve_passthrough(
        self,
        session,
        local_path: Path,
        stored_file: StoredFile,
        track: Track,
        track_id: str,
        user: Optional[User],
        state: _StreamState,
    ):
        """Serve the original source file with range request support."""
        mimetype = stored_file.content_type or "application/octet-stream"
        try:
            bytes_served = await self._serve_file(str(local_path), mimetype)
        except tornado.iostream.StreamClosedError:
            return

        if track.duration and stored_file.size:
            streamed_seconds = bytes_served / (stored_file.size / track.duration)
            if streamed_seconds >= 30:
                state.threshold_reached = True

        await self._record_listen_if_needed(session, track_id, user, state)
        await session.commit()

    async def _serve_cached_transcode(
        self,
        session,
        track: Track,
        track_id: str,
        fmt: str,
        bitrate: str,
        storage_backend: StorageBackend,
        user: Optional[User],
        state: _StreamState,
    ) -> bool:
        """Serve a cached transcode if one exists, returning True if handled."""
        cached = await get_cached_transcode(session, track_id, fmt, bitrate)
        if cached is None:
            return False

        try:
            cached_path = await storage_backend.retrieve(cached.storage_path)
        except ValueError:
            self._not_found()
            return True

        if cached_path is None:
            self._not_found()
            return True

        try:
            bytes_served = await self._serve_file(str(cached_path), cached.content_type)
        except tornado.iostream.StreamClosedError:
            return True

        if track.duration and cached.size:
            streamed_seconds = bytes_served / (cached.size / track.duration)
            if streamed_seconds >= 30:
                state.threshold_reached = True

        await self._record_listen_if_needed(session, track_id, user, state)
        await session.commit()
        return True

    async def _serve_live_transcode(
        self,
        session,
        local_path: Path,
        track_id: str,
        fmt: str,
        bitrate: str,
        mimetype: str,
        user: Optional[User],
        state: _StreamState,
        config: SonghiveConfig,
    ) -> bytearray:
        """Transcode the source file on the fly and stream it to the client."""
        self.set_header("Content-Type", mimetype)
        self.set_header("Cache-Control", "no-store")
        self.set_status(200)

        tee = bytearray()
        start_time: Optional[float] = None

        try:
            async for chunk in Transcoder(config.streaming.ffmpeg_path).stream(
                local_path,
                fmt,
                bitrate=bitrate,
                chunk_size=config.streaming.chunk_size,
            ):
                self.write(chunk)
                await self.flush()
                tee.extend(chunk)
                if start_time is None:
                    start_time = time.perf_counter()
                elif time.perf_counter() - start_time >= 30:
                    state.threshold_reached = True
                    await self._record_listen_if_needed(session, track_id, user, state)
        except tornado.iostream.StreamClosedError:
            pass

        await self._record_listen_if_needed(session, track_id, user, state)
        await session.commit()
        return tee

    async def get(self, track_id: str):  # pylint: disable=too-many-return-statements
        """Stream audio for the given track ID."""
        config = self._config
        storage_backend = get_storage(config.storage)

        async with get_session() as session:
            user = await self._authenticate(session)
            if self.get_status() != 200:
                return

            track_and_file = await self._resolve_track(session, track_id)
            if track_and_file is None:
                return
            track, stored_file = track_and_file

            if not await self._check_access(session, track_id, user):
                return

            local_path = await self._require_local_path(storage_backend, stored_file)
            if local_path is None:
                return

            self._broadcast_now_playing(track_id, user)
            fmt = self.get_argument("format", None)
            bitrate = self.get_argument("bitrate", None)
            parsed = self._parse_format(stored_file, fmt, bitrate)
            if parsed is None:
                return

            fmt, requested_bitrate, passthrough = parsed
            state = _StreamState()
            if passthrough:
                await self._serve_passthrough(
                    session,
                    local_path,
                    stored_file,
                    track,
                    track_id,
                    user,
                    state,
                )
                return

            assert fmt is not None
            effective = effective_bitrate(
                config.streaming,
                user.role if user is not None else "user",
                requested_bitrate,
            )
            fmt_mimetype = Transcoder.FORMAT_MAP[fmt]["mimetype"]

            if await self._serve_cached_transcode(
                session,
                track,
                track_id,
                fmt,
                effective,
                storage_backend,
                user,
                state,
            ):
                return

            storage_service = StorageService(storage_backend, config.storage)
            tee = await self._serve_live_transcode(
                session,
                local_path,
                track_id,
                fmt,
                effective,
                fmt_mimetype,
                user,
                state,
                config,
            )

            if config.streaming.transcode_cache_enabled and tee:
                try:
                    async with session.begin_nested():
                        await cache_transcode(
                            session,
                            storage_service,
                            track,
                            fmt,
                            effective,
                            bytes(tee),
                            fmt_mimetype,
                        )
                except Exception:
                    logger.exception("Failed to cache transcode for track %s", track_id)

    async def _serve_file(self, file_path: str, mimetype: str) -> int:
        """Serve a file with range request support and return bytes written."""
        file_size = os.path.getsize(file_path)

        range_header = self.request.headers.get("Range")
        if range_header:
            parsed = self._parse_range(range_header, file_size)
            if parsed is None:
                self._bad_request("invalid range")
                return 0
            start, end = parsed
            self.set_status(206)
            self.set_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            content_length = end - start + 1
        else:
            start = 0
            content_length = file_size

        self.set_header("Content-Type", mimetype)
        self.set_header("Content-Length", content_length)
        self.set_header("Accept-Ranges", "bytes")

        bytes_to_serve = content_length
        remaining = content_length
        with open(file_path, "rb") as f:
            f.seek(start)
            while remaining > 0:
                chunk_size = min(self._config.streaming.chunk_size, remaining)
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                self.write(chunk)
                await self.flush()
                remaining -= len(chunk)

        return bytes_to_serve - remaining

    @staticmethod
    def _parse_range(range_header: str, file_size: int) -> Optional[tuple[int, int]]:
        """
        Parse a Range header and return (start, end) byte positions.

        Returns ``None`` for syntactically invalid Range headers.
        """
        range_spec = range_header.replace("bytes=", "")
        parts = range_spec.split("-")
        if len(parts) != 2:
            return None
        try:
            start = int(parts[0]) if parts[0] else 0
            end = int(parts[1]) if parts[1] else file_size - 1
        except (ValueError, IndexError):
            return None
        return start, min(end, file_size - 1)
