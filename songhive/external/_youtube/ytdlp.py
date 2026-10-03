"""
yt-dlp integration: format resolution, stream descriptors and downloads.

YouTube's real playback URLs are signed ``googlevideo.com`` links obtained
by extracting each video's format list — an HTTP-heavy call to the YouTube
player. To keep playback latency sane, resolved ``StreamDescriptor``s are
cached in Redis keyed by
``(config fingerprint, video id, variant)`` for ``stream_url_ttl_seconds``.

Every extractor call injects the library's captured browser cookies (see
``cookies.py``) so member/age-restricted videos resolve the same way the
user's own session would.
"""

import asyncio
import json
import logging
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from redis.asyncio import Redis

from ..errors import (
    ExternalConfigError,
    ExternalItemNotFound,
    ExternalLibraryError,
    ExternalPermissionDenied,
)
from .conf import (
    DEFAULT_AUDIO_FORMAT,
    download_format,
    download_timeout_seconds,
    request_timeout_seconds,
    stream_url_ttl_seconds,
    video_format,
)
from .cookies import cookie_file_for_ytdlp
from .mapping import video_url
from .session import _config_fingerprint

logger = logging.getLogger(__name__)

_STREAM_CACHE_PREFIX = "songhive:youtube:stream:"

_MIME_BY_EXT = {
    "m4a": "audio/mp4",
    "mp3": "audio/mpeg",
    "opus": "audio/opus",
    "ogg": "audio/ogg",
    "webm": "audio/webm",
    "mp4": "video/mp4",
    "mkv": "video/x-matroska",
    "mov": "video/quicktime",
}


def _import_ytdlp() -> Any:
    """Import yt-dlp lazily so a broken install only disables this provider."""
    try:
        from yt_dlp import YoutubeDL

        return YoutubeDL
    except ImportError as exc:
        raise ExternalConfigError(
            "The 'yt-dlp' package is required for YouTube libraries",
        ) from exc


@dataclass
class StreamDescriptor:
    """A resolved direct-media URL plus the metadata needed to serve it."""

    url: str
    http_headers: dict[str, str] = field(default_factory=dict)
    ext: str = ""
    mime: str = ""
    size: Optional[int] = None
    expires_at: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "http_headers": self.http_headers,
            "ext": self.ext,
            "mime": self.mime,
            "size": self.size,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "StreamDescriptor":
        return cls(
            url=str(data.get("url") or ""),
            http_headers={str(k): str(v) for k, v in (data.get("http_headers") or {}).items()},
            ext=str(data.get("ext") or ""),
            mime=str(data.get("mime") or ""),
            size=data.get("size") if isinstance(data.get("size"), int) else None,
            expires_at=(float(data["expires_at"]) if isinstance(data.get("expires_at"), (int, float)) else None),
        )


def mime_for(ext: str, *, variant: str) -> str:
    """Best-effort MIME type for a yt-dlp format ext + rendition variant."""
    ext = ext.lower()
    if ext == "webm":
        return "video/webm" if variant == "video" else "audio/webm"
    mime = _MIME_BY_EXT.get(ext)
    if mime is None:
        return "video/mp4" if variant == "video" else "audio/mp4"
    if variant == "audio" and not mime.startswith("audio/"):
        return "audio/mp4"
    if variant == "video" and not mime.startswith("video/"):
        return "video/mp4"
    return mime


def _extract_url_token(params: dict, name: str) -> Optional[float]:
    raw = params.get(name)
    if isinstance(raw, list) and raw:
        try:
            return float(raw[0])
        except (TypeError, ValueError):
            return None
    return None


def _format_to_descriptor(fmt: dict, *, variant: str) -> Optional[StreamDescriptor]:
    """Build a ``StreamDescriptor`` from a resolved yt-dlp format dict."""
    url = fmt.get("url")
    if not isinstance(url, str) or not url:
        return None
    headers = {str(k): str(v) for k, v in (fmt.get("http_headers") or {}).items() if isinstance(v, str)}
    size = fmt.get("filesize")
    if not isinstance(size, int):
        size = fmt.get("filesize_approx")
        if not isinstance(size, int):
            size = None
    expires_at = None
    try:
        from urllib.parse import parse_qs, urlsplit

        query = parse_qs(urlsplit(url).query)
        expires_at = _extract_url_token(query, "expire")
    except Exception:
        expires_at = None
    ext = str(fmt.get("ext") or "")
    return StreamDescriptor(
        url=url,
        http_headers=headers,
        ext=ext,
        mime=mime_for(ext, variant=variant),
        size=size,
        expires_at=expires_at,
    )


def ydl_params(config: dict, *, base: Optional[dict] = None) -> dict:
    """Baseline ``YoutubeDL`` params with this library's cookies attached."""
    params: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": request_timeout_seconds(config),
        "ignore_no_formats_error": False,
    }
    if base:
        params.update(base)
    cookie_file = cookie_file_for_ytdlp(config)
    if cookie_file is not None:
        params["cookiefile"] = cookie_file
    return params


def _pick_format(info: dict, *, variant: str, selector: str) -> dict:
    """
    Run a yt-dlp format selector over ``info["formats"]`` in-process.

    Uses the extractor's own ``build_format_selector`` so the selection
    semantics (including ``a/b`` fallbacks and ``+`` merges) match yt-dlp.
    For merged ``+`` picks — which need ffmpeg post-processing and can't be
    served as a single URL — the best component carrying the wanted media
    kind is returned instead.
    """
    from yt_dlp import YoutubeDL

    formats = [f for f in info.get("formats") or [] if isinstance(f, dict)]
    if not formats:
        raise ExternalItemNotFound("No usable formats on the YouTube video; it may be removed or region-locked")
    # Strip formats that already failed upstream checks.
    formats = [f for f in formats if f.get("url")]
    if not formats:
        raise ExternalItemNotFound("No playable YouTube formats; the video may be sign-in or member restricted")

    params = ydl_params({}, base={"format": selector, "cookiefile": None})
    params.pop("cookiefile", None)
    ydl = YoutubeDL(params)
    pick = ydl.build_format_selector(selector)
    try:
        selections = pick({"formats": formats})
    except Exception as exc:
        raise ExternalItemNotFound(f"No YouTube format matched {selector!r}: {exc}") from exc
    selected = list(selections)
    if not selected:
        raise ExternalItemNotFound(f"No YouTube format matched {selector!r}")
    wanted = selected[-1]
    requested = wanted.get("requested_formats")
    if isinstance(requested, list) and requested:
        # A ``v+a`` merge cannot be served as one URL; prefer the component
        # that carries the requested media kind.
        def _part_score(fmt: dict) -> tuple[int, int]:
            if variant == "video":
                kind_ok = fmt.get("vcodec") not in (None, "none")
            else:
                kind_ok = fmt.get("acodec") not in (None, "none")
            return (1 if kind_ok else 0, int(fmt.get("tbr") or fmt.get("abr") or 0))

        return max(requested, key=_part_score)
    return wanted


def _descriptor_cache_key(fingerprint: str, video_id: str, variant: str) -> str:
    return f"{_STREAM_CACHE_PREFIX}{fingerprint}:{video_id}:{variant}"


async def resolve_stream_descriptor(
    config: dict,
    video_id: str,
    *,
    variant: str,
    redis: Optional[Redis] = None,
) -> StreamDescriptor:
    """
    Resolve a direct YouTube media URL for ``video_id`` + rendition variant.

    Cached in Redis for ``stream_url_ttl_seconds``; the googlevideo signed
    URL lives ~6h, so the cache window just smooths repeated playback.
    """
    fingerprint = _config_fingerprint(config)
    cache_key = _descriptor_cache_key(fingerprint, video_id, variant)
    if redis is not None:
        try:
            raw = await redis.get(cache_key)
        except Exception:
            raw = None
        if isinstance(raw, str) and raw:
            try:
                descriptor = StreamDescriptor.from_dict(json.loads(raw))
            except (TypeError, ValueError, KeyError):
                descriptor = None
            if descriptor is not None and descriptor.url:
                return descriptor

    descriptor = await asyncio.to_thread(_resolve_stream_descriptor_sync, config, video_id, variant)
    ttl = stream_url_ttl_seconds(config)
    if redis is not None and ttl > 0 and descriptor.url:
        try:
            # Never cache past the URL's own expiry.
            if descriptor.expires_at is not None:
                ttl = max(1, min(ttl, int(descriptor.expires_at - time.time()) - 10))
            await redis.set(cache_key, json.dumps(descriptor.to_dict()), ex=ttl)
        except Exception:
            logger.debug("Failed to cache YouTube stream descriptor", exc_info=True)
    return descriptor


def _resolve_stream_descriptor_sync(config: dict, video_id: str, variant: str) -> StreamDescriptor:
    """Extractor call; intended for ``asyncio.to_thread``."""
    YoutubeDL = _import_ytdlp()
    selector = video_format(config) if variant == "video" else DEFAULT_AUDIO_FORMAT
    params = ydl_params(config, base={"format": selector})
    ydl = YoutubeDL(params)
    try:
        info = ydl.extract_info(video_url(video_id), download=False)
    except Exception as exc:
        raise _translate_ydlp_error(exc, video_id) from exc
    if not isinstance(info, dict):
        raise ExternalLibraryError(f"Unexpected yt-dlp result for video {video_id!r}")

    fmt = _pick_format(info, variant=variant, selector=selector)
    descriptor = _format_to_descriptor(fmt, variant=variant)
    if descriptor is None or not descriptor.url:
        raise ExternalItemNotFound(f"No playable YouTube format resolved for {video_id!r}")
    return descriptor


def _translate_ydlp_error(exc: Exception, video_id: str) -> Exception:
    """Map yt-dlp extractor failures onto external.* exceptions."""
    text = str(exc)
    lowered = text.lower()
    if "sign in" in lowered or "login" in lowered or "cookies" in lowered:
        return ExternalPermissionDenied(
            "YouTube requires sign-in for this video; reconnect the library " "with fresh browser cookies",
            operation="resolve_stream",
        )
    if "private" in lowered or "not available" in lowered or "removed" in lowered:
        return ExternalItemNotFound(f"YouTube video {video_id!r} is unavailable")
    return ExternalLibraryError(f"yt-dlp failed for video {video_id!r}: {text[:200]}")


def download_media(config: dict, video_id: str, *, dest_dir: Path) -> Path:
    """
    Download ``video_id`` via yt-dlp into ``dest_dir``; returns the file.

    The rendition follows the library's ``download_format`` (audio-only
    ``m4a`` or merged ``mp4`` video+audio). Intended for
    ``asyncio.to_thread`` — extraction plus download can take a while.
    """
    YoutubeDL = _import_ytdlp()
    mode = download_format(config)
    if mode == "video":
        params = ydl_params(
            config,
            base={
                "format": "bv*+ba/b",
                "merge_output_format": "mp4",
            },
        )
    else:
        params = ydl_params(
            config,
            base={"format": "bestaudio[ext=m4a]/bestaudio/best"},
        )
    params["socket_timeout"] = download_timeout_seconds(config)
    params["outtmpl"] = str(dest_dir / "%(id)s.%(ext)s")
    params["overwrites"] = True
    ydl = YoutubeDL(params)
    try:
        info = ydl.extract_info(video_url(video_id), download=True)
    except Exception as exc:
        raise _translate_ydlp_error(exc, video_id) from exc
    video_id_out = str((info or {}).get("id") or video_id)
    matches = sorted(dest_dir.glob(f"{video_id_out}.*"))
    if not matches:
        raise ExternalLibraryError(f"yt-dlp finished but produced no file for {video_id_out!r}")
    return matches[0]


def extract_video_metadata(config: dict, video_id: str) -> dict:
    """
    Single-video metadata via yt-dlp (no download); yt-dlp payload shape.

    Used for URL lookups where no API surface is configured, and as a
    metadata fallback. Blocking — call via ``asyncio.to_thread``.
    """
    YoutubeDL = _import_ytdlp()
    params = ydl_params(config)
    ydl = YoutubeDL(params)
    try:
        info = ydl.extract_info(video_url(video_id), download=False)
    except Exception as exc:
        raise _translate_ydlp_error(exc, video_id) from exc
    if not isinstance(info, dict):
        raise ExternalItemNotFound(f"YouTube video {video_id!r} not found")
    return {**info, "_source": "ytdlp"}


def extract_playlist_metadata(config: dict, playlist_id: str) -> dict:
    """Flat playlist metadata via yt-dlp (yt-dlp payload shape). Blocking."""
    YoutubeDL = _import_ytdlp()
    url = f"https://www.youtube.com/playlist?list={playlist_id}"
    params = ydl_params(config, base={"extract_flat": True})
    ydl = YoutubeDL(params)
    try:
        info = ydl.extract_info(url, download=False)
    except Exception as exc:
        raise _translate_ydlp_error(exc, playlist_id) from exc
    if not isinstance(info, dict):
        raise ExternalItemNotFound(f"YouTube playlist {playlist_id!r} not found")
    return {**info, "_source": "ytdlp"}


def extract_channel_metadata(config: dict, handle_or_id: str) -> dict:
    """Channel metadata via yt-dlp (handles ``@name`` and ``UC…`` ids)."""
    YoutubeDL = _import_ytdlp()
    if handle_or_id.startswith("@"):
        url = f"https://www.youtube.com/{handle_or_id}"
    else:
        url = f"https://www.youtube.com/channel/{handle_or_id}"
    params = ydl_params(config, base={"extract_flat": True, "playlistend": 1})
    ydl = YoutubeDL(params)
    try:
        info = ydl.extract_info(url, download=False)
    except Exception as exc:
        raise _translate_ydlp_error(exc, handle_or_id) from exc
    if not isinstance(info, dict):
        raise ExternalItemNotFound(f"YouTube channel {handle_or_id!r} not found")
    return {**info, "_source": "ytdlp"}


def temp_download_dir() -> Path:
    """
    Download scratch dir — the configured external stream temp dir (or the
    system temp). Files land here flat so the stream handler's
    ``temporary`` unlink fully cleans up after the download is served.
    """
    try:
        from ...config.loader import load_config

        configured = load_config([]).external_libraries.stream_temp_dir
    except Exception:
        configured = None
    path = Path(configured) if configured else Path(tempfile.gettempdir())
    path.mkdir(parents=True, exist_ok=True)
    return path
