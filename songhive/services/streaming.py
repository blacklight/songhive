"""
Streaming service: resolve track backing files, transcode cache, and listen history.
"""

import io
import os
import tempfile
from pathlib import Path
from typing import Any, AsyncIterator, Optional, Union, cast

import aiofiles
import httpx
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..config.schema import SonghiveConfig
from ..external.errors import ExternalItemNotFound, UnsupportedExternalOperation
from ..external.registry import get_external_adapter
from ..external.types import ExternalItemRef, ExternalStream
from ..models.external_track import ExternalTrack
from ..models.history import ListeningHistory
from ..models.stored_file import StoredFile
from ..models.track import Track
from ..models.transcoded_file import TranscodedFile
from ..models.upload import Upload
from .secrets import decrypt_json
from .storage import StorageService


async def get_upload_for_track(session: AsyncSession, track_id: str) -> Optional[Upload]:
    """Get the best available upload for a track."""
    result = await session.execute(select(Upload).where(Upload.track_id == track_id).limit(1))
    return cast(Optional[Upload], result.scalar_one_or_none())


async def resolve_track_file(session: AsyncSession, track_id: str) -> Optional[StoredFile]:
    """Return the StoredFile backing a track, falling back to an upload."""
    result = await session.execute(select(Track).where(Track.id == track_id).options(selectinload(Track.audio_file)))
    track = result.scalar_one_or_none()
    if track is None:
        return None

    if track.audio_file_id is not None:
        return track.audio_file

    upload = await get_upload_for_track(session, track_id)
    if upload is not None and upload.stored_file_id is not None:
        return upload.stored_file

    return None


async def get_cached_transcode(
    session: AsyncSession,
    track_id: str,
    format_: str,
    bitrate: str,
) -> Optional[StoredFile]:
    """Return a cached StoredFile for the given track/format/bitrate or None."""
    result = await session.execute(
        select(TranscodedFile)
        .where(
            TranscodedFile.track_id == track_id,
            TranscodedFile.format == format_,
            TranscodedFile.bitrate == bitrate,
        )
        .options(selectinload(TranscodedFile.stored_file))
    )
    transcoded_file = result.scalar_one_or_none()
    if transcoded_file is None:
        return None
    return transcoded_file.stored_file


async def cache_transcode(
    session: AsyncSession,
    storage_service: StorageService,
    track: Track,
    format_: str,
    bitrate: str,
    output_bytes: bytes,
    content_type: str,
) -> StoredFile:
    """
    Store transcoded bytes and create a TranscodedFile cache row.

    The ``StoredFile`` creation and ``TranscodedFile`` insert share a single
    savepoint so a duplicate ``TranscodedFile`` race does not leave an
    unreferenced (orphaned) ``StoredFile`` row behind.
    """
    file_like = io.BytesIO(output_bytes)

    try:
        async with session.begin_nested():
            existing = await get_cached_transcode(session, track.id, format_, bitrate)
            if existing is not None:
                return existing

            stored_file = await storage_service.store_file(
                session,
                file_like,
                content_type,
                prefix="transcoded",
                owner_id=track.owner_id,
                visibility=track.visibility,
            )

            transcoded_file = TranscodedFile(
                track_id=track.id,
                format=format_,
                bitrate=bitrate,
                stored_file_id=stored_file.id,
            )
            session.add(transcoded_file)
            await session.flush()
    except IntegrityError as exc:
        if not StorageService._is_unique_constraint_error(exc):
            raise

        # Another request cached the same transcode concurrently; reuse it.
        existing = await get_cached_transcode(session, track.id, format_, bitrate)
        if existing is not None:
            return existing
        raise

    return stored_file


async def record_listen(session: AsyncSession, user_id: str, track_id: str) -> None:
    """
    Record a listen: insert history and increment the track play count.

    The play count is incremented with an atomic ``UPDATE`` expression so
    concurrent listens do not silently under-count.
    """
    session.add(ListeningHistory(user_id=user_id, track_id=track_id))
    await session.execute(update(Track).where(Track.id == track_id).values(play_count=Track.play_count + 1))

    track = await session.get(Track, track_id)
    if track is not None:
        await session.flush()
        await session.refresh(track)


def _parse_external_range_header(
    range_header: Optional[str],
    size: Optional[int],
) -> Optional[tuple[int, int]]:
    """Parse a ``Range`` header into a closed byte interval for the adapter."""
    if not range_header:
        return None

    if not range_header.lower().startswith("bytes="):
        raise ValueError(f"Unsupported range unit: {range_header!r}")

    spec = range_header[6:].split(",")[0].strip()
    if "-" not in spec:
        raise ValueError(f"Invalid range syntax: {range_header!r}")

    start_str, end_str = spec.split("-", 1)
    try:
        start = int(start_str) if start_str else None
        end = int(end_str) if end_str else None
    except ValueError as exc:
        raise ValueError(f"Invalid range syntax: {range_header!r}") from exc

    if start is None and end is None:
        raise ValueError(f"Invalid range syntax: {range_header!r}")

    if start is None:
        if size is None or end is None or end <= 0:
            return None
        start = max(0, size - end)
        end = size - 1
    else:
        if end is None:
            if size is None:
                return None
            end = size - 1
        elif size is not None:
            end = min(end, size - 1)

    if start < 0 or end < 0 or start > end:
        raise ValueError(f"Invalid range: {range_header!r}")

    return start, end


async def _load_external_stream_request(
    session: AsyncSession,
    track_id: str,
    range_header: Optional[str],
) -> Optional[tuple[Track, ExternalTrack, dict, Any, ExternalItemRef, Optional[tuple[int, int]]]]:
    """Load the track, external item, adapter, and optional byte range."""
    result = await session.execute(
        select(Track)
        .where(Track.id == track_id)
        .options(selectinload(Track.external_track).selectinload(ExternalTrack.external_library))
    )
    track = cast(Optional[Track], result.scalar_one_or_none())
    if track is None:
        return None

    external_track = track.external_track
    if external_track is None:
        return None

    if external_track.state in ("shadowed", "tombstoned", "missing", "error"):
        raise ExternalItemNotFound(
            f"External track {track_id} is not available",
            provider_key=external_track.provider_key,
        )

    external_library = external_track.external_library
    if external_library is None or not external_library.enabled:
        raise ExternalItemNotFound(
            f"External library for track {track_id} is not available",
            provider_key=external_track.provider_key,
        )

    raw_config = external_library.config
    if isinstance(raw_config, str):
        try:
            config = decrypt_json(raw_config)
        except Exception as exc:
            raise ExternalItemNotFound(
                f"Could not decrypt external library config for track {track_id}",
                provider_key=external_track.provider_key,
            ) from exc
    else:
        config = dict(raw_config or {})

    adapter_cls = get_external_adapter(external_library.provider_type)
    adapter = adapter_cls()

    item = ExternalItemRef(
        provider_key=external_track.provider_key,
        display_path=external_track.provider_key,
        etag=external_track.provider_etag,
        mtime=external_track.provider_mtime,
        size=external_track.provider_size,
        mime_type=external_track.provider_mime_type or track.audio_mime_type,
        checksum=external_track.provider_checksum,
        sha256=external_track.sha256,
    )

    capabilities = external_library.capabilities or {}
    range_tuple: Optional[tuple[int, int]] = None
    if capabilities.get("range_read") is not False and range_header:
        try:
            range_tuple = _parse_external_range_header(range_header, external_track.provider_size)
        except ValueError:
            range_tuple = None

    return track, external_track, config, adapter, item, range_tuple


async def resolve_external_stream(
    session: AsyncSession,
    track_id: str,
    range_header: Optional[str] = None,
) -> Optional[ExternalStream]:
    """Resolve an external byte stream for the given track, or return None."""
    loaded = await _load_external_stream_request(session, track_id, range_header)
    if loaded is None:
        return None
    _, _, config, adapter, item, range_tuple = loaded
    return await adapter.open_stream(config, item, range=range_tuple)


async def resolve_external_download_stream(
    session: AsyncSession,
    track_id: str,
    range_header: Optional[str] = None,
) -> Optional[ExternalStream]:
    """Resolve an external download stream, preferring ``download`` over ``open_stream``."""
    loaded = await _load_external_stream_request(session, track_id, range_header)
    if loaded is None:
        return None
    track, external_track, config, adapter, item, range_tuple = loaded

    try:
        return await adapter.download(config, item)
    except UnsupportedExternalOperation:
        pass

    capabilities = (external_track.external_library.capabilities or {}) if external_track.external_library else {}
    if not (capabilities.get("read_bytes") or capabilities.get("stream_url")):
        raise UnsupportedExternalOperation("download is not supported by this adapter")

    return await adapter.open_stream(config, item, range=range_tuple)


async def _ensure_stream_temp_dir(config: SonghiveConfig) -> Path:
    """Return the configured temp directory, creating it if necessary."""
    temp_dir = config.external_libraries.stream_temp_dir
    if temp_dir is None:
        return Path(tempfile.gettempdir())
    path = Path(temp_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


async def _collect_from_iterator(
    iterator: AsyncIterator[bytes],
    max_bytes: Optional[int],
    temp_dir: Path,
    chunk_size: int,
) -> Union[bytes, Path]:
    """Collect an async byte iterator into memory or a temp file if it exceeds the limit."""
    buffer = bytearray()
    temp_path: Optional[Path] = None
    temp_file = None
    try:
        async for chunk in iterator:
            if not chunk:
                continue
            if temp_file is None:
                buffer.extend(chunk)
                if max_bytes is not None and len(buffer) > max_bytes:
                    fd, name = tempfile.mkstemp(dir=temp_dir, prefix="external-")
                    os.close(fd)
                    temp_path = Path(name)
                    temp_file = await aiofiles.open(temp_path, "wb")
                    await temp_file.write(buffer)
                    buffer = bytearray()
            else:
                await temp_file.write(chunk)
    finally:
        if temp_file is not None:
            await temp_file.close()

    if temp_path is not None:
        return temp_path
    return bytes(buffer)


async def _collect_from_url(
    stream: ExternalStream,
    max_bytes: Optional[int],
    temp_dir: Path,
    chunk_size: int,
    config: SonghiveConfig,
) -> Union[bytes, Path]:
    """Fetch an external URL and collect the bytes into memory or a temp file."""
    timeout = httpx.Timeout(config.external_libraries.stream_proxy_timeout_seconds)
    buffer = bytearray()
    temp_path: Optional[Path] = None
    temp_file = None
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async with client.stream("GET", cast(str, stream.url), headers=stream.headers) as resp:
                if resp.status_code == 404:
                    raise ExternalItemNotFound(
                        f"External URL not found: {stream.url}",
                        provider_key=cast(str, stream.url),
                    )
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes(chunk_size=chunk_size):
                    if not chunk:
                        continue
                    if temp_file is None:
                        buffer.extend(chunk)
                        if max_bytes is not None and len(buffer) > max_bytes:
                            fd, name = tempfile.mkstemp(dir=temp_dir, prefix="external-")
                            os.close(fd)
                            temp_path = Path(name)
                            temp_file = await aiofiles.open(temp_path, "wb")
                            await temp_file.write(buffer)
                            buffer = bytearray()
                    else:
                        await temp_file.write(chunk)
    finally:
        if temp_file is not None:
            await temp_file.close()

    if temp_path is not None:
        return temp_path
    return bytes(buffer)


async def collect_external_stream(
    stream: ExternalStream,
    config: SonghiveConfig,
) -> Union[bytes, Path]:
    """Materialize an external stream into bytes or a local temp file path."""
    if stream.kind == "path":
        return cast(Path, stream.path)

    temp_dir = await _ensure_stream_temp_dir(config)
    max_bytes = config.external_libraries.stream_max_proxy_bytes
    chunk_size = config.streaming.chunk_size

    if stream.kind == "iterator":
        return await _collect_from_iterator(
            cast(AsyncIterator[bytes], stream.iterator),
            max_bytes,
            temp_dir,
            chunk_size,
        )

    if stream.kind == "url":
        return await _collect_from_url(stream, max_bytes, temp_dir, chunk_size, config)

    raise ValueError(f"Unsupported external stream kind: {stream.kind}")
