"""
ffmpeg remux/download pipeline for TIDAL segmented media.

Follows the tidaldl approach: the DASH segment list is rewritten as a local
HLS (``.m3u8``) playlist and ffmpeg reads it with a restricted protocol
whitelist, stream-copying to a seekable container — no re-encode, so the
remux is I/O bound rather than CPU bound.
"""

import asyncio
import logging
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Optional

import httpx

from ..errors import ExternalItemNotFound, ExternalLibraryError
from ..types import ExternalStream, ExternalTrackMetadata
from .api import is_allowed_media_url
from .conf import download_format, downloads_allowed

logger = logging.getLogger(__name__)

_FETCH_CHUNK_BYTES = 256 * 1024


def _proxy_timeout() -> float:
    try:
        from ...config.loader import load_config

        return float(load_config([]).external_libraries.stream_proxy_timeout_seconds)
    except Exception:
        return 60.0


def _max_proxy_bytes() -> int:
    try:
        from ...config.loader import load_config

        raw = load_config([]).external_libraries.stream_max_proxy_bytes
        return int(raw) if raw else 0
    except Exception:
        return 0


def segments_m3u8(urls: list[str]) -> str:
    """Rewrite a DASH ``[init, seg1, seg2, ...]`` URL list as an HLS playlist."""
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:7",
        "#EXT-X-PLAYLIST-TYPE:VOD",
        "#EXT-X-TARGETDURATION:30",
        "#EXT-X-MEDIA-SEQUENCE:0",
    ]
    if urls:
        lines.append(f'#EXT-X-MAP:URI="{urls[0]}"')
    for url in urls[1:]:
        lines.append("#EXTINF:30.0,")
        lines.append(url)
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


def _ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


async def _run_ffmpeg(args: list[str], *, timeout: float) -> None:
    proc = await asyncio.create_subprocess_exec(
        _ffmpeg_bin(),
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise ExternalLibraryError("ffmpeg remux timed out") from None
    if proc.returncode != 0:
        detail = stderr.decode(errors="replace").strip()[:300]
        raise ExternalLibraryError(f"ffmpeg exited with code {proc.returncode}: {detail}")


def _metadata_args(metadata: dict) -> list[str]:
    args: list[str] = []
    for key, value in metadata.items():
        if value is None or str(value) == "":
            continue
        args += ["-metadata", f"{key}={value}"]
    return args


async def remux_playlist_to_file(
    m3u8_text: str,
    output: Path,
    *,
    timeout: float,
    metadata: Optional[dict] = None,
    cover_path: Optional[Path] = None,
    container: str = "flac",
) -> None:
    """Remux an HLS playlist into ``output`` via ffmpeg stream copy."""
    playlist_path = Path(tempfile.mkstemp(prefix="songhive_tidal_", suffix=".m3u8")[1])
    try:
        playlist_path.write_text(m3u8_text, encoding="utf-8")
        args = [
            "-y",
            "-loglevel",
            "error",
            "-protocol_whitelist",
            "file,http,https,tcp,tls,crypto",
            "-allowed_extensions",
            "ALL",
            "-i",
            str(playlist_path),
        ]
        if cover_path is not None:
            args += ["-i", str(cover_path)]
        args += ["-map", "0:a"]
        if cover_path is not None:
            args += ["-map", "1", "-c:v", "copy", "-disposition:v:0", "attached_pic"]
        args += _metadata_args(metadata or {})
        args += ["-f", "mp4" if container == "aac" else "flac", "-c", "copy", str(output)]
        await _run_ffmpeg(args, timeout=timeout)
    finally:
        playlist_path.unlink(missing_ok=True)


async def remux_stream(
    track_id: str,
    quality: str,
    urls: list[str],
    *,
    codec: Optional[str] = None,
) -> ExternalStream:
    """Remux a segmented manifest into the remote-audio cache and stream it."""
    from ...config.loader import load_config
    from ...services.remote_audio_cache import get_remote_audio_cache

    config = load_config([])
    cache = get_remote_audio_cache(config)
    cache_key = f"tidal:{track_id}:{quality}"
    m3u8 = segments_m3u8(urls)
    timeout = max(300.0, _proxy_timeout())

    # The FLAC muxer only accepts a FLAC stream; AAC (and any other codec
    # TIDAL may serve in fMP4 segments) must land in an mp4 container.
    container = "flac" if codec == "flac" else "aac"
    ext = ".flac" if codec == "flac" else ".m4a"

    async def build(tmp: Path) -> None:
        await remux_playlist_to_file(m3u8, tmp, timeout=timeout, container=container)

    path = await cache.get_or_build(cache_key, build, ext=ext)
    return ExternalStream(
        kind="path",
        path=path,
        content_type="audio/flac" if codec == "flac" else "audio/mp4",
        size=path.stat().st_size,
        supports_range=True,
        temporary=False,
    )


async def _fetch_url_to_file(url: str, output: Path, *, timeout: float, max_bytes: int) -> int:
    """Download ``url`` to ``output``, enforcing the proxy byte cap."""
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        async with client.stream("GET", url) as response:
            if response.status_code >= 400:
                raise ExternalItemNotFound(f"TIDAL media fetch failed ({response.status_code})")
            total = 0
            with open(output, "wb") as fh:
                async for chunk in response.aiter_bytes(_FETCH_CHUNK_BYTES):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if max_bytes and total > max_bytes:
                        raise ExternalLibraryError("TIDAL download exceeds the size cap")
                    fh.write(chunk)
    return total


async def _fetch_cover(url: Optional[str], workdir: Path, *, timeout: float) -> Optional[Path]:
    if not url or not is_allowed_media_url(url):
        return None
    cover = workdir / f"cover_{uuid.uuid4().hex}.jpg"
    try:
        await _fetch_url_to_file(url, cover, timeout=timeout, max_bytes=8 * 1024 * 1024)
    except Exception:
        logger.debug("TIDAL cover fetch failed; continuing without it", exc_info=True)
        return None
    return cover


def download_tags(metadata: Optional[ExternalTrackMetadata]) -> dict:
    """Map provider metadata onto ffmpeg ``-metadata`` tag pairs."""
    if metadata is None:
        return {}
    raw = metadata.raw_metadata or {}
    return {
        "title": metadata.title,
        "artist": metadata.artist,
        "album": metadata.album,
        "album_artist": metadata.album_artist,
        "track": metadata.track_number,
        "disc": metadata.disc_number,
        "date": metadata.release_year,
        "ISRC": raw.get("isrc"),
        "copyright": raw.get("copyright"),
    }


def _manifest_codec(manifest: dict) -> Optional[str]:
    """Best-effort codec for a manifest descriptor (MPD carries it, BTS infers it)."""
    codec = manifest.get("codec")
    if codec:
        return str(codec)
    mime = str(manifest.get("mime_type") or "").lower()
    if "flac" in mime:
        return "flac"
    if "mp4" in mime or "m4a" in mime or "aac" in mime:
        return "aac"
    return None


def _container_for(fmt: str, codec: Optional[str]) -> str:
    """
    Pick the output container compatible with the source codec.

    ``fmt`` is the requested ``flac``|``aac`` preference. A FLAC stream
    fits either container, but an AAC (or other mp4-only) stream cannot be
    remuxed into ``.flac`` — ffmpeg's FLAC muxer requires exactly one FLAC
    stream — so the mp4 container is used instead.
    """
    if codec in (None, "flac"):
        return fmt
    return "aac"


async def download_track(config: dict, item, *, fmt: Optional[str] = None) -> ExternalStream:
    """
    Download a TIDAL track to a tagged FLAC/M4A temp file.

    ``item.metadata`` carries the catalog payload (title/artist/album/ISRC)
    used for the ffmpeg ``-metadata`` pass; the album cover is attached when
    fetchable. Gated by the instance ``allow_downloads`` setting.
    """
    from .session import effective_quality
    from .stream import _resolve_manifest

    if not downloads_allowed():
        raise ExternalLibraryError("TIDAL downloads are disabled on this instance")

    quality = effective_quality(config)
    manifest = await _resolve_manifest(config, item.provider_key, quality, None)
    container = _container_for(fmt or download_format(config), _manifest_codec(manifest))

    urls = manifest.get("urls") or []
    if manifest.get("encrypted") or not urls:
        raise ExternalItemNotFound(
            "TIDAL stream is not available for this track",
            provider_key=item.provider_key,
        )
    for url in urls:
        if not is_allowed_media_url(url):
            raise ExternalLibraryError("TIDAL returned a media URL outside the allowed CDN")

    timeout = max(300.0, _proxy_timeout())
    max_bytes = _max_proxy_bytes()
    ext = "m4a" if container == "aac" else "flac"

    stream_temp = None
    try:
        from ...config.loader import load_config

        stream_temp = load_config([]).external_libraries.stream_temp_dir
    except Exception:
        pass
    workdir = Path(tempfile.mkdtemp(prefix="songhive_tidal_dl_", dir=stream_temp))

    metadata = getattr(item, "metadata", None)
    tags = download_tags(metadata)
    cover_path = await _fetch_cover(getattr(metadata, "cover_url", None), workdir, timeout=min(timeout, 60.0))

    output = workdir / f"track{os.getpid()}.{ext}"
    if manifest.get("mode") == "mpd":
        m3u8 = segments_m3u8(urls)
        await remux_playlist_to_file(
            m3u8,
            output,
            timeout=timeout,
            metadata=tags,
            cover_path=cover_path,
            container=container,
        )
    else:
        raw = workdir / f"raw{os.getpid()}.bin"
        await _fetch_url_to_file(urls[0], raw, timeout=timeout, max_bytes=max_bytes)
        await _tag_file(
            raw,
            output,
            timeout=timeout,
            metadata=tags,
            cover_path=cover_path,
            container=container,
        )
        raw.unlink(missing_ok=True)

    return ExternalStream(
        kind="path",
        path=output,
        content_type="audio/mp4" if container == "aac" else "audio/flac",
        size=output.stat().st_size,
        supports_range=True,
        temporary=True,
    )


async def _tag_file(
    source: Path,
    output: Path,
    *,
    timeout: float,
    metadata: Optional[dict],
    cover_path: Optional[Path],
    container: str,
) -> None:
    """Stream-copy ``source`` into ``output`` while writing tags + cover."""
    args = ["-y", "-loglevel", "error", "-i", str(source)]
    if cover_path is not None:
        args += ["-i", str(cover_path)]
    args += ["-map", "0:a"]
    if cover_path is not None:
        args += ["-map", "1", "-c:v", "copy", "-disposition:v:0", "attached_pic"]
    args += _metadata_args(metadata or {})
    args += ["-f", "mp4" if container == "aac" else "flac", "-c", "copy", str(output)]
    await _run_ffmpeg(args, timeout=timeout)
