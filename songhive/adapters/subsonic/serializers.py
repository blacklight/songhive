"""
Serializers mapping Songhive models onto Subsonic response dicts.

All functions are pure (no database access): callers load entities and any
aggregate data they need, then translate through here so the route layer
stays thin.

``coverArt`` values are *entity* identifiers prefixed with a type tag
(``tr-``, ``al-``, ``ar-``, ``pl-``) rather than raw ``StoredFile`` ids —
``getCoverArt`` resolves the entity, applies Songhive's art fallback chain
(track image → album cover), and can also redirect to remote cover URLs.
"""

from pathlib import PurePosixPath
from typing import Any, Dict, Optional

from ...models import Visibility
from ...models.album import Album
from ...models.artist import Artist
from ...models.playlist import Playlist
from ...models.track import Track
from .responses import iso

#: Prefix → entity kind used by ``coverArt`` ids and ``getCoverArt``.
COVER_ART_PREFIXES = {
    "tr": "track",
    "al": "album",
    "ar": "artist",
    "pl": "playlist",
}

_MIME_SUFFIX = {
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/flac": "flac",
    "audio/x-flac": "flac",
    "audio/ogg": "ogg",
    "audio/opus": "opus",
    "audio/vorbis": "ogg",
    "audio/aac": "aac",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/webm": "webm",
    "audio/aiff": "aiff",
    "audio/x-aiff": "aiff",
}


def cover_art_id(kind_prefix: str, entity_id: Any) -> str:
    """Return a prefixed cover-art identifier for ``getCoverArt``."""
    return f"{kind_prefix}-{entity_id}"


def parse_cover_art_id(value: str) -> tuple[Optional[str], str]:
    """Split a ``coverArt`` id into ``(kind, entity_id)``; kind may be ``None``."""
    if "-" in value:
        prefix, rest = value.split("-", 1)
        kind = COVER_ART_PREFIXES.get(prefix)
        if kind is not None:
            return kind, rest
    return None, value


def _is_loaded(obj: Any, attr: str) -> bool:
    """Return True when ``attr`` is already loaded on a SQLAlchemy instance."""
    try:
        from sqlalchemy import inspect as sa_inspect

        state = sa_inspect(obj)
        return not state.unloaded or attr not in state.unloaded
    except Exception:
        return getattr(obj, attr, None) is not None


def _track_album(track: Track) -> Optional[Album]:
    """Return the track's album when the relationship is already loaded."""
    if track.album_id is None or not _is_loaded(track, "album"):
        return None
    return track.album


def _track_external_ref(track: Track):
    """Return the active external reference (file- or entity-backed)."""
    external = getattr(track, "external_track", None)
    if external is not None and external.state == "active":
        return external
    entity = getattr(track, "external_item", None)
    if entity is not None and entity.state == "active":
        return entity
    return None


def _track_filename(track: Track) -> Optional[str]:
    """Return the best-known filename for a track's audio payload."""
    audio_file = track.audio_file if _is_loaded(track, "audio_file") else None
    if audio_file is not None and audio_file.original_filename:
        return audio_file.original_filename
    external = _track_external_ref(track)
    if external is not None:
        display_path = (external.raw_metadata or {}).get("display_path") or external.provider_key
        return PurePosixPath(display_path).name
    return None


def _track_mime(track: Track) -> str:
    """Return the track audio MIME type."""
    if track.audio_mime_type:
        return track.audio_mime_type
    audio_file = track.audio_file if _is_loaded(track, "audio_file") else None
    if audio_file is not None and audio_file.content_type:
        return audio_file.content_type
    external = _track_external_ref(track)
    if external is not None and getattr(external, "provider_mime_type", None):
        return external.provider_mime_type
    return "audio/mpeg"


def _track_suffix(track: Track) -> str:
    """Return the lowercase file suffix for a track."""
    filename = _track_filename(track)
    if filename:
        suffix = PurePosixPath(filename).suffix.lstrip(".")
        if suffix:
            return suffix.lower()
    return _MIME_SUFFIX.get(_track_mime(track).split(";")[0].strip(), "mp3")


def _track_size(track: Track) -> Optional[int]:
    """Return the byte size of a track's audio payload when known."""
    audio_file = track.audio_file if _is_loaded(track, "audio_file") else None
    if audio_file is not None:
        return audio_file.size
    external = _track_external_ref(track)
    if external is None:
        return None
    provider_size = getattr(external, "provider_size", None)
    if provider_size:
        return provider_size
    raw_size = (getattr(external, "raw_metadata", None) or {}).get("Size")
    try:
        return int(raw_size) if raw_size is not None else None
    except (TypeError, ValueError):
        return None


def _track_release_year(track: Track) -> Optional[int]:
    """Return the track's year, falling back to the loaded album's year."""
    if track.release_year is not None:
        return track.release_year
    album = _track_album(track)
    return album.release_year if album is not None else None


def _track_cover_art(track: Track) -> Optional[str]:
    """Return the prefixed cover-art id for a track, honouring album fallback."""
    if track.image_file_id:
        return cover_art_id("tr", track.id)
    album = _track_album(track)
    if album is not None and (album.cover_file_id or album.cover_url):
        return cover_art_id("tr", track.id)
    return None


def _track_path(track: Track, artist_name: str, album_title: Optional[str]) -> str:
    """Build a Subsonic-style virtual path for a track."""
    parts = [artist_name]
    if album_title:
        parts.append(album_title)
    name = track.title
    if track.track_number is not None:
        name = f"{track.track_number:02d} - {name}"
    parts.append(f"{name}.{_track_suffix(track)}")
    return "/".join(parts)


def song_dict(track: Track, *, starred_at: Optional[Any] = None) -> Dict[str, Any]:
    """Serialize a Track to a Subsonic ``song``/``child`` dict."""
    artist = track.artist if _is_loaded(track, "artist") else None
    artist_name = artist.name if artist is not None else ""
    album = _track_album(track)
    album_title = album.title if album is not None else None

    size = _track_size(track)
    bit_rate = None
    if size is not None and track.duration:
        bit_rate = int(size * 8 / track.duration / 1000)

    song: Dict[str, Any] = {
        "id": str(track.id),
        "parent": str(track.album_id) if track.album_id else None,
        "isDir": False,
        "title": track.title,
        "album": album_title,
        "artist": artist_name,
        "track": track.track_number,
        "discNumber": track.disc_number,
        "year": _track_release_year(track),
        "genre": track.genre,
        "coverArt": _track_cover_art(track),
        "size": size,
        "contentType": _track_mime(track),
        "suffix": _track_suffix(track),
        "duration": int(track.duration) if track.duration is not None else None,
        "bitRate": bit_rate,
        "path": _track_path(track, artist_name, album_title),
        "albumId": str(track.album_id) if track.album_id else None,
        "artistId": str(track.artist_id) if track.artist_id else None,
        "type": "music",
        "isVideo": False,
        "created": iso(track.created_at),
        "playCount": track.play_count,
        "starred": iso(starred_at),
    }
    return {key: value for key, value in song.items() if value is not None}


def album_dict(
    album: Album,
    *,
    song_count: Optional[int] = None,
    duration: Optional[float] = None,
    play_count: Optional[int] = None,
) -> Dict[str, Any]:
    """Serialize an Album to a Subsonic ``album`` dict (ID3 shape)."""
    artist = album.artist if _is_loaded(album, "artist") else None
    cover_art = cover_art_id("al", album.id) if (album.cover_file_id or album.cover_url) else None

    out: Dict[str, Any] = {
        "id": str(album.id),
        "name": album.title,
        "artist": artist.name if artist is not None else None,
        "artistId": str(album.artist_id),
        "coverArt": cover_art,
        "songCount": song_count,
        "duration": int(duration) if duration is not None else None,
        "playCount": play_count,
        "created": iso(album.created_at),
        "year": album.release_year,
        "genre": album.genre,
    }
    return {key: value for key, value in out.items() if value is not None}


def album_dir_dict(album: Album) -> Dict[str, Any]:
    """Serialize an Album as a directory-style ``child`` entry (``isDir``)."""
    artist = album.artist if _is_loaded(album, "artist") else None
    out: Dict[str, Any] = {
        "id": str(album.id),
        "parent": str(album.artist_id),
        "isDir": True,
        "title": album.title,
        "album": album.title,
        "artist": artist.name if artist is not None else None,
        "year": album.release_year,
        "genre": album.genre,
        "coverArt": cover_art_id("al", album.id) if (album.cover_file_id or album.cover_url) else None,
    }
    return {key: value for key, value in out.items() if value is not None}


def artist_dict(artist: Artist, *, album_count: Optional[int] = None) -> Dict[str, Any]:
    """Serialize an Artist to a Subsonic ``artist`` dict (ID3 shape)."""
    has_art = artist.image_file_id or artist.cover_file_id or artist.image_url
    out: Dict[str, Any] = {
        "id": str(artist.id),
        "name": artist.name,
        "coverArt": cover_art_id("ar", artist.id) if has_art else None,
        "albumCount": album_count,
        "artistImageUrl": artist.image_url,
    }
    return {key: value for key, value in out.items() if value is not None}


def playlist_dict(
    playlist: Playlist,
    *,
    owner_name: Optional[str] = None,
    song_count: Optional[int] = None,
    duration: Optional[float] = None,
    songs: Optional[list] = None,
) -> Dict[str, Any]:
    """Serialize a Playlist to a Subsonic ``playlist`` dict."""
    has_art = playlist.cover_file_id or playlist.image_file_id
    owner = playlist.owner if _is_loaded(playlist, "owner") else None
    out: Dict[str, Any] = {
        "id": str(playlist.id),
        "name": playlist.name,
        "comment": playlist.description,
        "owner": owner_name or (owner.username if owner is not None else None),
        "public": playlist.visibility == Visibility.PUBLIC.value,
        "songCount": song_count,
        "duration": int(duration) if duration is not None else None,
        "created": iso(playlist.created_at),
        "changed": iso(playlist.updated_at),
        "coverArt": cover_art_id("pl", playlist.id) if has_art else None,
    }
    if songs is not None:
        out["entry"] = songs
    return {key: value for key, value in out.items() if value is not None}
