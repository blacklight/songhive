"""External-stream serving — extracted from ``streaming/handler.py``."""

import logging
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

import aiofiles
import httpx
import tornado.iostream
import tornado.web

from ..config.schema import SonghiveConfig
from ..external.errors import (
    ExternalItemNotFound,
    ExternalLibraryError,
    ExternalPermissionDenied,
    UnsupportedExternalOperation,
)
from ..external.types import ExternalStream
from ..models.track import Track
from ..models.user import User
from ..services.storage import StorageBackend, StorageService
from ..services.streaming import resolve_external_stream

if TYPE_CHECKING:
    from .handler import _StreamState


logger = logging.getLogger(__name__)


class _ExternalStreamMixin:
    if TYPE_CHECKING:
        # Provided by the host class (StreamHandler / RequestHandler).
        _redis: Any
        _stream_state: Any
        _not_found: Callable[[str], None]
        _forbidden: Callable[[], None]
        _bad_request: Callable[[str], None]
        _unprocessable: Callable[[str], None]
        set_header: Callable[..., None]
        set_status: Callable[..., None]
        write: Callable[..., Any]
        finish: Callable[..., Any]
        flush: Callable[..., Any]

        @property
        def _config(self) -> SonghiveConfig: ...

        def _update_streamed_threshold(self, *args: Any, **kwargs: Any) -> Any: ...
        async def _serve_file(self, *args: Any, **kwargs: Any) -> Any: ...
        async def _record_listen_if_needed(self, *args: Any, **kwargs: Any) -> Any: ...
        async def _require_local_path(self, *args: Any, **kwargs: Any) -> Optional[Path]: ...

    async def _spill_iterator_to_temp(self, stream: ExternalStream) -> Path:
        """Write all chunks from an external iterator to a temp file and return its path."""
        if stream.iterator is None:
            raise RuntimeError("external iterator stream missing iterator")

        temp_dir = self._config.external_libraries.stream_temp_dir
        if temp_dir is None:
            temp_dir = Path(tempfile.gettempdir())
        Path(temp_dir).mkdir(parents=True, exist_ok=True)

        fd, name = tempfile.mkstemp(dir=temp_dir, prefix="external-stream-")
        os.close(fd)
        temp_path = Path(name)

        async with aiofiles.open(temp_path, "wb") as f:
            async for chunk in stream.iterator:
                if chunk:
                    await f.write(chunk)

        return temp_path

    async def _serve_external_path(
        self,
        session,
        track: Track,
        track_id: str,
        stream: ExternalStream,
        user: Optional[User],
        state: _StreamState,
        range_header: Optional[str],
    ) -> int:
        """Serve an external stream backed by a local file path."""
        path = stream.path
        if path is None:
            self._not_found("external stream missing path")
            return 0

        mimetype = stream.content_type or "application/octet-stream"
        effective_range = range_header if (stream.supports_range and range_header) else ""
        bytes_served = 0
        try:
            bytes_served = await self._serve_file(str(path), mimetype, range_header=effective_range)
        except tornado.iostream.StreamClosedError:
            pass
        finally:
            if stream.temporary:
                try:
                    os.unlink(path)
                except OSError:
                    pass

        self._update_streamed_threshold(bytes_served, track, stream.size, state)
        await self._record_listen_if_needed(session, track_id, user, state)
        return bytes_served

    async def _serve_spilled_iterator(self, stream: ExternalStream, mimetype: str, range_header: Optional[str]) -> int:
        """Spill an oversized iterator to disk and serve it as a file."""
        temp_path = await self._spill_iterator_to_temp(stream)
        effective_range = range_header if (stream.supports_range and range_header) else ""
        try:
            return await self._serve_file(str(temp_path), mimetype, range_header=effective_range, file_size=stream.size)
        except tornado.iostream.StreamClosedError:
            return 0
        finally:
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    async def _serve_external_iterator(
        self,
        session,
        track: Track,
        track_id: str,
        stream: ExternalStream,
        user: Optional[User],
        state: _StreamState,
        range_header: Optional[str],
    ) -> int:
        """Serve an external stream backed by an async byte iterator."""
        mimetype = stream.content_type or "application/octet-stream"
        max_bytes = self._config.external_libraries.stream_max_proxy_bytes

        if stream.size is not None and max_bytes is not None and stream.size > max_bytes:
            bytes_served = await self._serve_spilled_iterator(stream, mimetype, range_header)
            self._update_streamed_threshold(bytes_served, track, stream.size, state)
            await self._record_listen_if_needed(session, track_id, user, state)
            return bytes_served

        if stream.iterator is None:
            self._not_found("external iterator stream missing iterator")
            return 0

        self.set_header("Content-Type", mimetype)
        if stream.size is not None:
            self.set_header("Content-Length", stream.size)
        if stream.content_range:
            self.set_header("Content-Range", stream.content_range)
        self.set_header("Accept-Ranges", "bytes" if stream.supports_range else "none")
        self.set_header("Cache-Control", "no-store")
        self.set_status(206 if stream.content_range else 200)

        bytes_served = 0
        try:
            async for chunk in stream.iterator:
                if not chunk:
                    continue
                self.write(chunk)
                await self.flush()
                bytes_served += len(chunk)
                self._update_streamed_threshold(bytes_served, track, stream.size, state)
                await self._record_listen_if_needed(session, track_id, user, state)
        except tornado.iostream.StreamClosedError:
            pass

        self._update_streamed_threshold(bytes_served, track, stream.size, state)
        await self._record_listen_if_needed(session, track_id, user, state)
        return bytes_served

    async def _proxy_external_url(
        self,
        session,
        track: Track,
        track_id: str,
        stream: ExternalStream,
        user: Optional[User],
        state: _StreamState,
        request_headers: dict,
    ) -> Optional[tuple]:
        """Stream the upstream URL; ``(bytes_served, stream_size)`` or ``None``."""
        timeout = httpx.Timeout(self._config.external_libraries.stream_proxy_timeout_seconds)
        bytes_served = 0
        stream_size = stream.size
        if stream.url is None:
            self._not_found("external stream missing URL")
            return None

        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                async with client.stream("GET", stream.url, headers=request_headers) as resp:
                    if resp.status_code == 404:
                        self._not_found("external URL not found")
                        return None
                    if resp.status_code >= 400:
                        self._bad_request(f"upstream error {resp.status_code}")
                        return None

                    mimetype = stream.content_type or resp.headers.get("content-type") or "application/octet-stream"
                    self.set_header("Content-Type", mimetype)
                    if "content-length" in resp.headers:
                        self.set_header("Content-Length", resp.headers["content-length"])
                        stream_size = int(resp.headers["content-length"])
                    if "content-range" in resp.headers:
                        self.set_header("Content-Range", resp.headers["content-range"])
                    self.set_header("Accept-Ranges", "bytes" if stream.supports_range else "none")
                    self.set_header("Cache-Control", "no-store")
                    self.set_status(resp.status_code)

                    async for chunk in resp.aiter_bytes(chunk_size=self._config.streaming.chunk_size):
                        if not chunk:
                            continue
                        self.write(chunk)
                        await self.flush()
                        bytes_served += len(chunk)
                        self._update_streamed_threshold(bytes_served, track, stream_size, state)
                        await self._record_listen_if_needed(session, track_id, user, state)
        except tornado.iostream.StreamClosedError:
            pass
        except httpx.HTTPError as exc:
            logger.exception("External URL proxy failed for track %s", track_id)
            self._bad_request(str(exc))
        return bytes_served, stream_size

    async def _serve_external_url(
        self,
        session,
        track: Track,
        track_id: str,
        stream: ExternalStream,
        user: Optional[User],
        state: _StreamState,
        range_header: Optional[str],
    ) -> int:
        """Serve an external stream described by a URL, either redirecting or proxying."""
        if stream.safe_to_redirect and stream.url:
            self.set_status(302)
            self.set_header("Location", stream.url)
            if stream.content_type:
                self.set_header("Content-Type", stream.content_type)
            await self.finish()
            return 0

        if not stream.url:
            self._not_found("external URL missing")
            return 0

        request_headers = dict(stream.headers)
        if range_header and stream.supports_range:
            request_headers["Range"] = range_header

        result = await self._proxy_external_url(session, track, track_id, stream, user, state, request_headers)
        if result is None:
            return 0
        bytes_served, stream_size = result

        self._update_streamed_threshold(bytes_served, track, stream_size, state)
        await self._record_listen_if_needed(session, track_id, user, state)
        return bytes_served

    async def _serve_external_stream(
        self,
        session,
        track: Track,
        track_id: str,
        stream: ExternalStream,
        user: Optional[User],
        state: _StreamState,
        range_header: Optional[str],
    ):
        """Dispatch an external stream to the appropriate delivery path."""
        try:
            if stream.kind == "path":
                await self._serve_external_path(session, track, track_id, stream, user, state, range_header)
            elif stream.kind == "iterator":
                await self._serve_external_iterator(session, track, track_id, stream, user, state, range_header)
            elif stream.kind == "url":
                await self._serve_external_url(session, track, track_id, stream, user, state, range_header)
            else:
                self._unprocessable(f"unsupported external stream kind: {stream.kind}")
        except tornado.iostream.StreamClosedError:
            pass

        await session.commit()

    async def _resolve_external_stream(
        self,
        session,
        track_id: str,
        range_header: Optional[str],
        user,
        variant: str,
    ) -> Optional[ExternalStream]:
        """Resolve the provider stream; error responses return ``None``."""
        try:
            return await resolve_external_stream(session, track_id, range_header, user=user, variant=variant)
        except ExternalItemNotFound as exc:
            self._not_found(str(exc))
        except ExternalPermissionDenied:
            self._forbidden()
        except UnsupportedExternalOperation as exc:
            self._unprocessable(str(exc))
        except ExternalLibraryError as exc:
            logger.exception("External stream resolution failed for track %s", track_id)
            self._bad_request(str(exc))
        return None

    async def _serve_payment_sample(
        self,
        session,
        track: Track,
        sale,
        storage_backend: StorageBackend,
    ) -> None:
        """Serve the configured sample for a ``sample``-gated track — never original bytes."""
        from ..services.payments import samples as samples_service

        storage = StorageService(storage_backend, self._config.storage)
        stored = await samples_service.sample_stored_file(
            session,
            track,
            sale,
            self._config,
            storage=storage,
            redis=self._redis,
        )
        if stored is None:
            self._not_found("sample not ready")
            return
        local_path = await self._require_local_path(storage_backend, stored)
        if local_path is None:
            return
        try:
            await self._serve_file(str(local_path), stored.content_type or "audio/mpeg")
        except tornado.iostream.StreamClosedError:
            return
