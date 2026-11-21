"""
Shared helpers for reading embedded audio tags from local files.

Adapters that can materialize an item as a local path (filesystem adapters,
or object-storage adapters that download to a temp file) reuse this to map
``services.metadata.AudioMetadata`` onto ``ExternalTrackMetadata``.
"""

from pathlib import Path
from typing import Any

from .types import ExternalTrackMetadata


def track_metadata_from_file(path: Path, display_path: str) -> ExternalTrackMetadata:
    """Extract embedded tags from a local audio file into provider metadata.

    Embedded tags win; fields they leave empty (or fill with placeholders
    like ``Unknown Artist``) fall back to hints parsed from ``display_path``
    (``Artist/Album/NN - Title.ext`` layouts), and finally to the path
    itself for the title.
    """
    from ..services.metadata import (
        extract_metadata,
        is_placeholder_album_name,
        is_placeholder_artist_name,
        parse_audio_path,
    )

    meta = extract_metadata(path)
    path_meta = parse_audio_path(display_path)

    artist = (meta.artist or "").strip()
    if is_placeholder_artist_name(artist):
        artist = ""
    if not artist:
        artist = path_meta.artist or ""

    album = (meta.album or "").strip()
    if is_placeholder_album_name(album):
        album = ""
    if not album:
        album = path_meta.album or ""

    album_artist = ""
    raw_tags = meta.raw_tags or {}
    if isinstance(raw_tags, dict):
        for key, values in raw_tags.items():
            if key.lower() in {"albumartist", "album artist", "talb"} and values:
                album_artist = str(values[0])
                break
    if not album_artist:
        album_artist = artist

    raw_metadata: dict[str, Any] = {
        "display_path": display_path,
        "mimetype": meta.mimetype,
        "raw_tags": raw_tags,
    }

    return ExternalTrackMetadata(
        title=(meta.title or "").strip() or path_meta.title or display_path,
        artist=artist,
        album=album,
        album_artist=album_artist,
        track_number=meta.track_number if meta.track_number is not None else path_meta.track_number,
        disc_number=meta.disc_number if meta.disc_number is not None else path_meta.disc_number,
        duration=meta.duration,
        release_year=meta.year,
        genre=meta.genre,
        musicbrainz_id=None,
        cover_art=meta.cover_art,
        cover_art_mime=meta.cover_art_mime,
        raw_metadata=raw_metadata,
    )
