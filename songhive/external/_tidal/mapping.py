"""
Mapping helpers: TIDAL API JSON payloads → shared external dataclasses.

Everything here is a pure function — no I/O, no ORM — so the same mapping
feeds the sync pipeline (``iter_*``), the lazy-contents refresher and the
provider search, and unit tests can exercise payloads directly.
"""

import re
from datetime import datetime, timezone
from typing import Any, Optional

from ..types import (
    ExternalAlbumMetadata,
    ExternalArtistMetadata,
    ExternalItemRef,
    ExternalPlaylistEntry,
    ExternalPlaylistMetadata,
    ExternalTrackMetadata,
)

_IMAGE_BASE = "https://resources.tidal.com/images"
_BROWSE_BASE = "https://tidal.com/browse"

_SLUG_UNSAFE = re.compile(r"[/\\]+")


def image_url(uuid: Optional[str], *, width: int = 1280, height: int = 1280) -> Optional[str]:
    """Build a ``resources.tidal.com`` image URL from a dashed uuid."""
    if not uuid or not isinstance(uuid, str):
        return None
    return f"{_IMAGE_BASE}/{uuid.replace('-', '/')}/{width}x{height}.jpg"


def browse_url(kind: str, provider_key: str) -> str:
    """Return the public ``tidal.com/browse`` URL for an entity."""
    return f"{_BROWSE_BASE}/{kind}/{provider_key}"


def _num(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _year(item: dict) -> Optional[int]:
    """Extract a release year from ``streamStartDate``/``releaseDate``."""
    for key in ("streamStartDate", "releaseDate"):
        raw = item.get(key)
        if isinstance(raw, str) and len(raw) >= 4 and raw[:4].isdigit():
            return int(raw[:4])
    return None


def _artist_names(item: dict) -> tuple[str, ...]:
    """Return ordered display names from a TIDAL ``artists`` list."""
    artists = item.get("artists")
    if not isinstance(artists, list):
        return ()
    names = []
    for entry in artists:
        if isinstance(entry, dict) and entry.get("name"):
            names.append(str(entry["name"]))
    return tuple(names)


def _primary_artist(item: dict) -> str:
    """Return the primary (first ``MAIN``) artist name for a track/album."""
    artists = item.get("artists")
    if isinstance(artists, list):
        for entry in artists:
            if isinstance(entry, dict) and entry.get("type") == "MAIN" and entry.get("name"):
                return str(entry["name"])
    single = item.get("artist")
    if isinstance(single, dict) and single.get("name"):
        return str(single["name"])
    names = _artist_names(item)
    return names[0] if names else ""


def _title(item: dict) -> str:
    """Title with the TIDAL ``version`` suffix folded in (``Title (Version)``)."""
    title = str(item.get("title") or "Unknown")
    version = item.get("version")
    if isinstance(version, str) and version.strip() and version.strip() not in title:
        title = f"{title} ({version.strip()})"
    return title


def _clean_segment(value: Any) -> str:
    text = _SLUG_UNSAFE.sub("_", str(value)).strip()
    return text or "Unknown"


def track_display_path(item: dict) -> str:
    """Read-only pseudo-filename ``{artist}/{album}/{disc:02d}-{track:02d} {title}.flac``."""
    title = _clean_segment(_title(item))
    track_no = _num(item.get("trackNumber"))
    disc_no = _num(item.get("volumeNumber"))
    if track_no is not None:
        leaf = f"{disc_no:02d}-{track_no:02d} {title}" if disc_no and disc_no > 1 else f"{track_no:02d} {title}"
    else:
        leaf = title
    leaf = f"{leaf}.flac"

    artist = _clean_segment(_primary_artist(item) or "Unknown Artist")
    album = item.get("album")
    album_title = _clean_segment(album.get("title")) if isinstance(album, dict) and album.get("title") else None
    if album_title:
        return f"{artist}/{album_title}/{leaf}"
    return f"{artist}/{leaf}"


def track_provider_ids(item: dict) -> dict[str, str]:
    """Provider-id map for a track: the TIDAL id plus ISRC when present."""
    ids: dict[str, str] = {}
    if item.get("id") is not None:
        ids["tidal"] = str(item["id"])
    isrc = item.get("isrc")
    if isinstance(isrc, str) and isrc:
        ids["isrc"] = isrc
    return ids


def map_track(item: dict) -> ExternalTrackMetadata:
    """Map a TIDAL track JSON object onto ``ExternalTrackMetadata``."""
    artists = _artist_names(item)
    raw_album = item.get("album")
    album: dict = raw_album if isinstance(raw_album, dict) else {}
    album_title = str(album.get("title") or "")
    album_artists = _artist_names(album)
    if not album_artists:
        album_artist = album.get("artist")
        if isinstance(album_artist, dict) and album_artist.get("name"):
            album_artists = (str(album_artist["name"]),)
    if not album_artists:
        album_artists = artists[:1]

    cover = album.get("cover")
    provider_ids = track_provider_ids(item)
    if album.get("id") is not None:
        provider_ids["tidal_album"] = str(album["id"])

    raw = dict(item)
    return ExternalTrackMetadata(
        title=_title(item),
        artist=_primary_artist(item) or "Unknown Artist",
        album=album_title,
        album_artist=album_artists[0] if album_artists else "",
        track_number=_num(item.get("trackNumber")),
        disc_number=_num(item.get("volumeNumber")),
        duration=_float(item.get("duration")),
        release_year=_year(item),
        cover_url=image_url(cover) if isinstance(cover, str) else None,
        artists=artists,
        album_artists=album_artists,
        provider_ids=provider_ids,
        raw_metadata=raw,
    )


def map_track_ref(item: dict) -> Optional[ExternalItemRef]:
    """Map a TIDAL track JSON object onto an ``ExternalItemRef`` (None for non-tracks)."""
    if item.get("id") is None:
        return None
    return ExternalItemRef(
        provider_key=str(item["id"]),
        display_path=track_display_path(item),
        etag=None,
        mime_type="audio/flac",
        metadata=map_track(item),
    )


def map_album(item: dict) -> Optional[ExternalAlbumMetadata]:
    """Map a TIDAL album JSON object onto ``ExternalAlbumMetadata``."""
    if item.get("id") is None:
        return None
    artists = _artist_names(item)
    artist_keys: list[str] = []
    for entry in item.get("artists") or []:
        if isinstance(entry, dict) and entry.get("id") is not None:
            artist_keys.append(str(entry["id"]))
    if not artists:
        single = item.get("artist")
        if isinstance(single, dict) and single.get("name"):
            artists = (str(single["name"]),)
            if single.get("id") is not None:
                artist_keys.append(str(single["id"]))

    provider_ids: dict[str, str] = {"tidal": str(item["id"])}
    upc = item.get("universal_product_number") or item.get("upc")
    if isinstance(upc, str) and upc:
        provider_ids["upc"] = upc

    return ExternalAlbumMetadata(
        provider_key=str(item["id"]),
        title=_title(item),
        artist_names=artists,
        artist_provider_keys=tuple(artist_keys),
        release_year=_year(item),
        cover_url=image_url(item.get("cover")),
        provider_ids=provider_ids,
        raw_metadata=dict(item),
    )


def map_artist(item: dict) -> Optional[ExternalArtistMetadata]:
    """Map a TIDAL artist JSON object onto ``ExternalArtistMetadata``."""
    if item.get("id") is None:
        return None
    picture = item.get("picture")
    provider_ids: dict[str, str] = {"tidal": str(item["id"])}
    return ExternalArtistMetadata(
        provider_key=str(item["id"]),
        name=str(item.get("name") or "Unknown"),
        image_url=image_url(picture, width=750, height=750) if isinstance(picture, str) else None,
        provider_ids=provider_ids,
        raw_metadata=dict(item),
    )


def _parse_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def map_playlist(item: dict) -> Optional[ExternalPlaylistMetadata]:
    """Map a TIDAL playlist JSON object onto ``ExternalPlaylistMetadata`` (metadata only)."""
    uuid = item.get("uuid") or item.get("id")
    if uuid is None:
        return None
    cover = item.get("squareImage") or item.get("image")
    creator = item.get("creator")
    owner_name = None
    if isinstance(creator, dict):
        owner_name = creator.get("name") or creator.get("id")
    return ExternalPlaylistMetadata(
        provider_key=str(uuid),
        title=str(item.get("title") or "Untitled playlist"),
        description=item.get("description") if isinstance(item.get("description"), str) else None,
        cover_url=image_url(cover, width=750, height=750) if isinstance(cover, str) else None,
        owner_name=str(owner_name) if owner_name is not None else None,
        entries=(),
        mtime=_parse_datetime(item.get("lastUpdated")),
        raw_metadata=dict(item),
    )


def map_playlist_items(payload: dict) -> tuple[tuple[int, str, Optional[dict]], ...]:
    """
    Map a ``playlists/{uuid}/items`` page into ``(position, provider_key, track_json)``.

    Video entries are skipped; positions stay dense (re-sequenced after
    filtering) so ``PlaylistTrack.position`` is gapless.
    """
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return ()
    entries: list[tuple[int, str, Optional[dict]]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        entry_type = raw.get("type")
        item = raw.get("item") if isinstance(raw.get("item"), dict) else raw
        if entry_type == "video" or not isinstance(item, dict):
            continue
        track_id = item.get("id")
        if track_id is None:
            continue
        entries.append((len(entries), str(track_id), item))
    return tuple(entries)


def playlist_entries_from_payload(payload: dict) -> tuple[ExternalPlaylistEntry, ...]:
    """Map playlist items JSON to plain provider-key entries (positions dense)."""
    return tuple(
        ExternalPlaylistEntry(position=pos, track_provider_key=key) for pos, key, _ in map_playlist_items(payload)
    )
