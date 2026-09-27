"""
Instance- and library-level configuration helpers for the TIDAL provider.

Kept separate from ``__init__.py`` so submodules (stream, api) can import
them without dragging the adapter class in.
"""

from typing import Any

_DEFAULT_PLAYLIST_TTL = 21600


def instance_tidal_config() -> Any:
    """Return ``config.external_libraries.tidal`` (or a default instance)."""
    try:
        from ...config.loader import load_config

        return load_config([]).external_libraries.tidal
    except Exception:
        return None


def max_rps(config: dict) -> float:
    """Return the per-account request rate limit for this library."""
    raw = config.get("max_requests_per_second")
    if isinstance(raw, (int, float)) and raw > 0:
        return float(raw)
    instance = instance_tidal_config()
    if instance is not None:
        return float(instance.max_requests_per_second)
    return 5.0


def playlist_ttl(config: dict) -> int:
    """Per-library playlist contents TTL, clamped to the instance minimum."""
    instance = instance_tidal_config()
    minimum = instance.minimum_playlist_ttl_seconds if instance is not None else 300
    default = instance.playlist_ttl_seconds if instance is not None else _DEFAULT_PLAYLIST_TTL
    raw = config.get("playlist_ttl_seconds")
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def mpd_mode(config: dict) -> str:
    """How segmented (MPD/DASH) tracks are served: ``segments`` or ``remux``."""
    raw = config.get("mpd_mode")
    return str(raw) if raw in ("segments", "remux") else "segments"


def downloads_allowed() -> bool:
    """Whether the instance permits TIDAL downloads at all (ToS guard)."""
    instance = instance_tidal_config()
    return bool(instance.allow_downloads) if instance is not None else False


def download_format(config: dict) -> str:
    """Per-library download container: ``flac`` (default) or ``aac``."""
    raw = config.get("download_format")
    if raw in ("flac", "aac"):
        return str(raw)
    instance = instance_tidal_config()
    if instance is not None and instance.download_format in ("flac", "aac"):
        return str(instance.download_format)
    return "flac"
