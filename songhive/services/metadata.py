"""
Metadata extraction service using mutagen.
"""

import base64
import re
from dataclasses import dataclass, field
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
    cover_art: Optional[bytes] = None
    cover_art_mime: Optional[str] = None
    raw_tags: dict = field(default_factory=dict)


def _first(values) -> Optional[str]:
    """Return the first string value from a tag list."""
    if not values:
        return None
    value = values[0]
    if isinstance(value, str):
        return value
    return str(value)


def _int(values) -> Optional[int]:
    """Parse a numeric tag, ignoring slash-separated totals."""
    val = _first(values)
    if val is None:
        return None
    try:
        return int(val.split("/")[0])
    except (ValueError, IndexError):
        return None


def _year(values) -> Optional[int]:
    """Parse a year from a date tag, falling back to a four-digit prefix."""
    val = _first(values)
    if val is None:
        return None
    try:
        return int(val.split("/")[0])
    except (ValueError, IndexError):
        pass
    match = re.search(r"\d{4}", val)
    if match:
        return int(match.group(0))
    return None


def _guess_image_mime(data: bytes) -> str:
    """Best-effort MIME type from image magic bytes."""
    if data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return "image/jpeg"


def _extract_pictures_attr(audio) -> Optional[tuple[Optional[bytes], Optional[str]]]:
    """FLAC / Ogg FLAC store pictures in the ``pictures`` attribute."""
    pictures = getattr(audio, "pictures", None)
    if pictures:
        pic = pictures[0]
        return pic.data, pic.mime

    return None


def _extract_metadata_block_picture(audio) -> Optional[tuple[Optional[bytes], Optional[str]]]:
    """Ogg Vorbis / Opus store pictures in the ``METADATA_BLOCK_PICTURE`` tag."""
    tags = getattr(audio, "tags", None)
    if tags is not None and "METADATA_BLOCK_PICTURE" in tags:
        try:
            from mutagen.flac import Picture

            raw = base64.b64decode(tags["METADATA_BLOCK_PICTURE"][0])
            pic = Picture(raw)
            return pic.data, pic.mime
        except Exception:
            pass

    return None


def _extract_covr_atom(audio) -> Optional[tuple[Optional[bytes], Optional[str]]]:
    """MP4 cover atom."""
    tags = getattr(audio, "tags", None)
    if tags is not None and "covr" in tags:
        data = bytes(tags["covr"][0])
        return data, _guess_image_mime(data)

    return None


def _extract_apic_frame(audio) -> Optional[tuple[Optional[bytes], Optional[str]]]:
    """MP3 ID3 APIC frame."""
    tags = getattr(audio, "tags", None)
    try:
        from mutagen.id3 import ID3

        if isinstance(tags, ID3):
            for frame in tags.values():
                if frame.FrameID == "APIC":
                    return frame.data, frame.mime
    except Exception:
        pass

    return None


def _extract_cover_art(audio) -> tuple[Optional[bytes], Optional[str]]:
    """Return ``(data, mime)`` for the first embedded cover art, if any."""
    if audio is None:
        return None, None
    if pictures := _extract_pictures_attr(audio):
        return pictures
    if metadata_block_picture := _extract_metadata_block_picture(audio):
        return metadata_block_picture
    if covr_atom := _extract_covr_atom(audio):
        return covr_atom
    if apic_frame := _extract_apic_frame(audio):
        return apic_frame
    return None, None


def _stringify(value) -> Optional[str]:
    """Convert a tag value to a JSON-safe string, encoding bytes as base64."""
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value if v is not None)
    if value is None:
        return None
    return str(value)


def _extract_id3_tags(raw_tags) -> dict:
    """MP3 ID3 tags: iterate frames and convert text fields."""
    tags: dict = {}
    if hasattr(raw_tags, "getall"):
        for key in raw_tags.keys():
            for frame in raw_tags.getall(key):
                if frame.FrameID in ("APIC", "PRIV", "GEOB", "SYLT"):
                    continue
                if hasattr(frame, "text") and frame.text:
                    tags[key] = [str(t) for t in frame.text]
                else:
                    s = _stringify(frame)
                    if s:
                        tags[key] = [s]

    return tags


def _extract_vorbis_tags(raw_tags) -> dict:
    """Vorbis/FLAC comments and MP4 atoms: values are lists."""
    tags: dict = {}
    for key, values in raw_tags.items():
        if key == "covr":
            continue
        out: list[str] = []
        for value in values:
            s = _stringify(value)
            if s:
                out.append(s)
        if out:
            tags[str(key)] = out

    return tags


def _extract_raw_tags(audio) -> dict:
    """Build a JSON-safe ``{tag: [values]}`` dictionary from raw tags."""
    tags: dict = {}
    if audio is None:
        return tags

    raw_tags = getattr(audio, "tags", None)
    if raw_tags is None:
        return tags

    if tags := _extract_id3_tags(raw_tags):
        return tags

    return _extract_vorbis_tags(raw_tags)


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
        audio_easy = mutagen.File(file_path, easy=True)
    except Exception as e:
        logger.error("Failed to extract metadata from %s: %s", file_path, e)
        return AudioMetadata()

    try:
        audio_raw = mutagen.File(file_path, easy=False)
    except Exception as e:
        logger.debug("Could not load raw tags for %s: %s", file_path, e)
        audio_raw = None

    cover_art, cover_art_mime = _extract_cover_art(audio_raw)
    raw_tags = _extract_raw_tags(audio_raw)

    if audio_easy is None:
        return AudioMetadata(
            duration=getattr(getattr(audio_raw, "info", None), "length", None),
            mimetype=audio_raw.mime[0] if audio_raw and getattr(audio_raw, "mime", None) else None,
            cover_art=cover_art,
            cover_art_mime=cover_art_mime,
            raw_tags=raw_tags,
        )

    info = audio_easy.info if hasattr(audio_easy, "info") else None

    return AudioMetadata(
        title=_first(audio_easy.get("title")),
        artist=_first(audio_easy.get("artist")),
        album=_first(audio_easy.get("album")),
        track_number=_int(audio_easy.get("tracknumber")),
        disc_number=_int(audio_easy.get("discnumber")),
        duration=info.length if info else None,
        genre=_first(audio_easy.get("genre")),
        year=_year(audio_easy.get("date")),
        bitrate=getattr(info, "bitrate", None),
        mimetype=audio_easy.mime[0] if hasattr(audio_easy, "mime") and audio_easy.mime else None,
        cover_art=cover_art,
        cover_art_mime=cover_art_mime,
        raw_tags=raw_tags,
    )
