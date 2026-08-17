"""
Metadata extraction service using mutagen.
"""

from dataclasses import dataclass
from logging import getLogger
from pathlib import Path
from typing import Optional

logger = getLogger(__name__)


@dataclass
class AudioMetadata:
    """Extracted audio file metadata."""

    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    track_number: Optional[int] = None
    disc_number: Optional[int] = None
    duration: Optional[float] = None
    genre: Optional[str] = None
    year: Optional[int] = None
    bitrate: Optional[int] = None
    mimetype: Optional[str] = None


def extract_metadata(file_path: Path) -> AudioMetadata:
    """
    Extract metadata from an audio file using mutagen.

    :param file_path: Path to the audio file.
    :returns: Extracted metadata.
    """
    try:
        import mutagen
    except ImportError:
        return AudioMetadata()

    try:
        audio = mutagen.File(file_path, easy=True)
    except Exception as e:
        logger.error("Failed to extract metadata from %s: %s", file_path, e)
        return AudioMetadata()

    info = audio.info if hasattr(audio, "info") else None

    def _first(tag: str) -> Optional[str]:
        values = audio.get(tag)  # type: ignore
        return values[0] if values else None

    def _int(tag: str) -> Optional[int]:
        val = _first(tag)
        if val is None:
            return None
        try:
            # Handle "3/12" format (track_number/total)
            return int(val.split("/")[0])
        except (ValueError, IndexError):
            return None

    return AudioMetadata(
        title=_first("title"),
        artist=_first("artist"),
        album=_first("album"),
        track_number=_int("tracknumber"),
        disc_number=_int("discnumber"),
        duration=info.length if info else None,
        genre=_first("genre"),
        year=_int("date"),
        bitrate=getattr(info, "bitrate", None),
        mimetype=audio.mime[0] if hasattr(audio, "mime") and audio.mime else None,
    )
