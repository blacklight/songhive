"""
Instance- and library-level configuration helpers for the YouTube provider.

Kept separate from ``__init__.py`` so submodules (stream, api) can import
them without dragging the adapter class in.
"""

from typing import Any, Optional

_DEFAULT_PLAYLIST_TTL = 21600
_DEFAULT_STREAM_URL_TTL = 300
_DEFAULT_REQUEST_TIMEOUT = 30.0
_DEFAULT_DOWNLOAD_TIMEOUT = 600.0

API_MODES = ("auto", "music", "youtube")
DOWNLOAD_FORMATS = ("audio", "video")
DEFAULT_VIDEO_FORMAT = "best[height<=720][acodec!=none][vcodec!=none]/best"
DEFAULT_AUDIO_FORMAT = "bestaudio[ext=m4a]/bestaudio/best"


def instance_youtube_config() -> Any:
    """Return ``config.external_libraries.youtube`` (or a default instance)."""
    try:
        from ...config.loader import load_config

        return load_config([]).external_libraries.youtube
    except Exception:
        return None


def _instance_attr(name: str, default: Any) -> Any:
    instance = instance_youtube_config()
    return getattr(instance, name, default) if instance is not None else default


def client_id(config: dict) -> Optional[str]:
    """Google OAuth client id used for the device-authorization flow."""
    raw = config.get("client_id") or _instance_attr("client_id", "")
    return str(raw).strip() or None


def client_secret(config: dict) -> Optional[str]:
    """Google OAuth client secret paired with :func:`client_id`."""
    raw = config.get("client_secret") or _instance_attr("client_secret", "")
    return str(raw).strip() or None


def api_mode(config: dict) -> str:
    """Requested API surface: ``auto``, ``music`` (YouTube Music) or ``youtube``."""
    raw = str(config.get("api_mode") or "auto").lower()
    return raw if raw in API_MODES else "auto"


def max_rps(config: dict) -> float:
    """Return the per-account request rate limit for this library."""
    raw = config.get("max_requests_per_second")
    if isinstance(raw, (int, float)) and raw > 0:
        return float(raw)
    return float(_instance_attr("max_requests_per_second", 5.0))


def request_timeout_seconds(config: dict) -> float:
    """HTTP timeout for YouTube API calls, including token refresh."""
    raw = config.get("request_timeout_seconds")
    if isinstance(raw, (int, float)) and raw > 0:
        return float(raw)
    return float(_instance_attr("request_timeout_seconds", _DEFAULT_REQUEST_TIMEOUT))


def playlist_ttl(config: dict) -> int:
    """Per-library playlist contents TTL, clamped to the instance minimum."""
    minimum = int(_instance_attr("minimum_playlist_ttl_seconds", 300))
    default = int(_instance_attr("playlist_ttl_seconds", _DEFAULT_PLAYLIST_TTL))
    raw = config.get("playlist_ttl_seconds")
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def stream_url_ttl_seconds(config: dict) -> int:
    """How long a resolved yt-dlp stream URL stays usable in the cache."""
    raw = config.get("stream_url_ttl_seconds")
    try:
        value = int(raw) if raw is not None else int(_instance_attr("stream_url_ttl_seconds", _DEFAULT_STREAM_URL_TTL))
    except (TypeError, ValueError):
        value = _DEFAULT_STREAM_URL_TTL
    return max(0, value)


def download_timeout_seconds(config: dict) -> float:
    """Timeout for yt-dlp format resolution and media downloads."""
    raw = config.get("download_timeout_seconds")
    try:
        value = (
            float(raw)
            if raw is not None
            else float(_instance_attr("download_timeout_seconds", _DEFAULT_DOWNLOAD_TIMEOUT))
        )
    except (TypeError, ValueError):
        value = _DEFAULT_DOWNLOAD_TIMEOUT
    return max(1.0, value)


def downloads_allowed() -> bool:
    """Whether the instance permits YouTube downloads at all (ToS guard)."""
    return bool(_instance_attr("allow_downloads", False))


def download_format(config: dict) -> str:
    """Per-library download mode: ``audio`` (default) or ``video``."""
    raw = config.get("download_format")
    if raw in DOWNLOAD_FORMATS:
        return str(raw)
    instance_raw = _instance_attr("download_format", "audio")
    return str(instance_raw) if instance_raw in DOWNLOAD_FORMATS else "audio"


def video_playback_allowed() -> bool:
    """Whether the instance permits video+audio playback renditions."""
    return bool(_instance_attr("video_playback", True))


def video_format(config: dict) -> str:
    """yt-dlp format selector for video+audio playback."""
    raw = config.get("video_format")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return str(_instance_attr("video_format", DEFAULT_VIDEO_FORMAT))
