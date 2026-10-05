"""
Mapping helpers: YouTube payload shapes → shared external dataclasses.

Three provider payload sources converge here, distinguished by the
``_source`` marker the adapter stamps onto the raw dict before it reaches
the catalog:

- ``"ytmusic"`` — YouTube Music internals (ytmusicapi): song/video dicts,
  library playlists, subscriptions, albums.
- ``"youtube_api"`` — YouTube Data API v3 resources: ``videos``,
  ``playlistItems``, ``playlists``, ``subscriptions``, ``channels``.
- ``"ytdlp"`` — yt-dlp ``extract_info`` dicts, used for URL resolution and
  single-entity fetches.

Everything here is a pure function — no I/O, no ORM — so the same mapping
feeds the sync pipeline (``iter_*``), the lazy-contents refresher, provider
search, and unit tests can exercise payloads directly.
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

_WATCH_URL = "https://www.youtube.com/watch?v={id}"
_CHANNEL_URL = "https://www.youtube.com/channel/{id}"
_PLAYLIST_URL = "https://www.youtube.com/playlist?list={id}"

_SLUG_UNSAFE = re.compile(r"[/\\]+")
_ISO8601_DURATION = re.compile(
    r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?)?$",
)
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def video_url(video_id: str) -> str:
    """Public watch URL for a video id."""
    return _WATCH_URL.format(id=video_id)


def browse_url(kind: str, provider_key: str) -> Optional[str]:
    """Public youtube.com URL for an entity kind."""
    if kind == "track":
        return video_url(provider_key)
    if kind == "playlist":
        return _PLAYLIST_URL.format(id=provider_key)
    if kind == "artist":
        if provider_key.startswith("@"):
            return f"https://www.youtube.com/{provider_key}"
        return _CHANNEL_URL.format(id=provider_key)
    if kind == "album":
        # YouTube Music album browse ids resolve on music.youtube.com.
        return f"https://music.youtube.com/browse/{provider_key}"
    return None


def parse_youtube_url(text: str) -> Optional[tuple[str, str]]:
    """
    Parse a YouTube URL into ``(kind, provider_key)``.

    ``kind`` is ``track`` for video URLs, ``playlist`` for playlist URLs and
    ``artist`` for channel/handle URLs. Returns ``None`` for non-YouTube or
    unparseable input.
    """
    if not isinstance(text, str):
        return None
    text = text.strip()
    if not text:
        return None

    from urllib.parse import parse_qs, urlsplit

    parts = urlsplit(text)
    host = (parts.hostname or "").lower()
    if not (host in ("youtube.com", "youtu.be", "music.youtube.com", "m.youtube.com") or host.endswith(".youtube.com")):
        # Bare video ids are also accepted as lookup keys.
        return ("track", text) if _VIDEO_ID.match(text) else None

    path = parts.path or "/"
    if host == "youtu.be":
        video_id = path.strip("/").split("/")[0]
        return ("track", video_id) if video_id else None

    if path == "/watch" or path.startswith("/watch/"):
        video_id = (parse_qs(parts.query).get("v") or [""])[0]
        if video_id:
            return ("track", video_id)
    for prefix in ("/shorts/", "/live/", "/embed/", "/v/"):
        if path.startswith(prefix):
            video_id = path[len(prefix) :].split("/")[0]
            if video_id:
                return ("track", video_id)
    if path == "/playlist":
        playlist_id = (parse_qs(parts.query).get("list") or [""])[0]
        if playlist_id:
            return ("playlist", playlist_id)
    if path.startswith("/channel/"):
        channel_id = path[len("/channel/") :].split("/")[0]
        if channel_id:
            return ("artist", channel_id)
    if path.startswith("/@"):
        handle = path.split("/")[1]
        if handle:
            return ("artist", handle)
    return None


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


def parse_hms_duration(value: Any) -> Optional[float]:
    """Parse a ``H:MM:SS``/``MM:SS`` duration string into seconds."""
    if not isinstance(value, str) or not value.strip():
        return None
    parts = value.strip().split(":")
    if not all(p.isdigit() for p in parts):
        return None
    try:
        seconds = 0
        for part in parts:
            seconds = seconds * 60 + int(part)
        return float(seconds)
    except ValueError:
        return None


def parse_iso8601_duration(value: Any) -> Optional[float]:
    """Parse an ISO-8601 ``PnDTnHnMnS`` duration into seconds."""
    if not isinstance(value, str):
        return None
    match = _ISO8601_DURATION.match(value.strip())
    if match is None:
        return None
    days, hours, minutes, seconds = match.groups()
    return float(int(days or 0) * 86400 + int(hours or 0) * 3600 + int(minutes or 0) * 60 + float(seconds or 0))


def pick_thumbnail(thumbnails: Any, *, min_width: int = 0) -> Optional[str]:
    """Pick the largest thumbnail URL from a provider thumbnails list."""
    if not isinstance(thumbnails, list):
        return None
    best_url, best_area = None, -1
    for thumb in thumbnails:
        if not isinstance(thumb, dict):
            continue
        url = thumb.get("url")
        if not isinstance(url, str) or not url.startswith("http"):
            continue
        width = _num(thumb.get("width")) or 0
        height = _num(thumb.get("height")) or 0
        area = width * height
        if area >= best_area and width >= min_width:
            best_url, best_area = url, area
    return best_url


def _snippet_thumbnail(snippet: dict) -> Optional[str]:
    """Pick the best thumbnail from a Data API ``snippet.thumbnails`` map."""
    thumbs = snippet.get("thumbnails")
    if not isinstance(thumbs, dict):
        return None
    for name in ("maxres", "standard", "high", "medium", "default"):
        entry = thumbs.get(name)
        if isinstance(entry, dict) and isinstance(entry.get("url"), str):
            return entry["url"]
    return None


def _year_from(value: Any) -> Optional[int]:
    """Extract a year from an ISO timestamp/date or YYYYMMDD upload string."""
    if isinstance(value, str) and len(value) >= 4 and value[:4].isdigit():
        return int(value[:4])
    return None


def _source(payload: dict) -> str:
    raw = payload.get("_source")
    if isinstance(raw, str) and raw:
        return raw
    # Shape sniffing for unmarked payloads.
    if "videoId" in payload or "playlistId" in payload or "browseId" in payload:
        return "ytmusic"
    if "snippet" in payload and isinstance(payload.get("snippet"), dict):
        return "youtube_api"
    if "webpage_url" in payload or "extractor" in payload:
        return "ytdlp"
    return "unknown"


def _clean_segment(value: Any) -> str:
    text = _SLUG_UNSAFE.sub("_", str(value)).strip()
    return text or "Unknown"


def _truncate(text: Optional[str], limit: int = 5000) -> Optional[str]:
    if text is None:
        return None
    text = str(text)
    return text if len(text) <= limit else text[:limit]


# ---------------------------------------------------------------------------
# Tracks (videos)
# ---------------------------------------------------------------------------


def _map_ytmusic_track(item: dict) -> Optional[ExternalTrackMetadata]:
    """Map a ytmusicapi song/video dict onto ``ExternalTrackMetadata``."""
    video_id = item.get("videoId")
    if not isinstance(video_id, str) or not video_id:
        return None

    artists_raw = item.get("artists")
    artists: list[str] = []
    artist_keys: list[str] = []
    if isinstance(artists_raw, list):
        for entry in artists_raw:
            if isinstance(entry, dict) and entry.get("name"):
                artists.append(str(entry["name"]))
                if entry.get("id") is not None:
                    artist_keys.append(str(entry["id"]))
    channel = item.get("channel")
    if not artists and isinstance(channel, dict) and channel.get("name"):
        artists.append(str(channel["name"]))
        if channel.get("id") is not None:
            artist_keys.append(str(channel["id"]))

    album_raw = item.get("album")
    album: dict = album_raw if isinstance(album_raw, dict) else {}
    album_title = str(album.get("name") or "")
    album_artist_key = None
    album_artists: list[str] = []
    if album_title:
        album_artists = artists[:1]
        album_artist_key = str(album["id"]) if album.get("id") is not None else None

    duration = _float(item.get("duration_seconds"))
    if duration is None:
        duration = parse_hms_duration(item.get("duration"))

    return ExternalTrackMetadata(
        title=str(item.get("title") or video_id),
        artist=artists[0] if artists else "Unknown Artist",
        album=album_title,
        album_artist=album_artists[0] if album_artists else "",
        duration=duration,
        release_year=_num(item.get("year")),
        cover_url=pick_thumbnail(item.get("thumbnails")),
        description=_truncate(item.get("description")),
        artists=tuple(artists),
        album_artists=tuple(album_artists),
        provider_ids={"youtube": video_id},
        artist_provider_key=artist_keys[0] if artist_keys else None,
        album_artist_provider_key=album_artist_key,
        raw_metadata={**item, "_source": "ytmusic"},
    )


def _map_data_api_video(item: dict) -> Optional[ExternalTrackMetadata]:
    """Map a Data API ``videos`` (or ``playlistItems``) resource."""
    video_id = item.get("id")
    snippet = item.get("snippet") or {}
    details = item.get("contentDetails") or {}
    if not isinstance(video_id, str) or not video_id:
        # playlistItems nest the video id under contentDetails/resourceId.
        if isinstance(details, dict) and details.get("videoId"):
            video_id = str(details["videoId"])
        else:
            resource = snippet.get("resourceId") or {}
            if isinstance(resource, dict) and resource.get("videoId"):
                video_id = str(resource["videoId"])
    if not video_id:
        return None
    if not isinstance(snippet, dict):
        snippet = {}
    if not isinstance(details, dict):
        details = {}

    # ``playlistItems`` resources identify the channel that added the item
    # under ``channelTitle``/``channelId`` — for the user's own playlists
    # that's the playlist owner, not the video's uploader. The uploader
    # channel lives in ``videoOwnerChannelTitle``/``videoOwnerChannelId``.
    # ``videos`` resources lack the owner fields, so the plain fields stay
    # the fallback.
    channel_title = snippet.get("videoOwnerChannelTitle") or snippet.get("channelTitle")
    channel_id = snippet.get("videoOwnerChannelId") or snippet.get("channelId")
    artists = (str(channel_title),) if channel_title else ()

    tags = snippet.get("tags")
    genres = tuple(str(t) for t in tags if isinstance(t, str))[:10] if isinstance(tags, list) else ()

    return ExternalTrackMetadata(
        title=str(snippet.get("title") or video_id),
        artist=artists[0] if artists else "Unknown Artist",
        album="",
        album_artist="",
        duration=parse_iso8601_duration(details.get("duration")),
        release_year=_year_from(snippet.get("publishedAt")),
        cover_url=_snippet_thumbnail(snippet),
        description=_truncate(snippet.get("description")),
        artists=artists,
        genres=genres,
        provider_ids={"youtube": str(video_id)},
        artist_provider_key=str(channel_id) if channel_id else None,
        raw_metadata={**item, "_source": "youtube_api", "id": video_id},
    )


def _map_ytdlp_track(info: dict) -> Optional[ExternalTrackMetadata]:
    """Map a yt-dlp ``extract_info`` result onto ``ExternalTrackMetadata``."""
    video_id = info.get("id")
    if not isinstance(video_id, str) or not video_id:
        return None
    channel = info.get("channel") or info.get("uploader")
    channel_id = info.get("channel_id") or info.get("uploader_id")
    artists = (str(channel),) if channel else ()
    return ExternalTrackMetadata(
        title=str(info.get("title") or video_id),
        artist=artists[0] if artists else "Unknown Artist",
        album="",
        album_artist="",
        duration=_float(info.get("duration")),
        release_year=_year_from(info.get("upload_date")),
        cover_url=(
            info.get("thumbnail") if isinstance(info.get("thumbnail"), str) else pick_thumbnail(info.get("thumbnails"))
        ),
        description=_truncate(info.get("description")),
        artists=artists,
        genres=tuple(str(c) for c in info.get("categories") or [] if isinstance(c, str)),
        provider_ids={"youtube": video_id},
        artist_provider_key=str(channel_id) if channel_id else None,
        raw_metadata={**info, "_source": "ytdlp"},
    )


def map_track(payload: dict) -> Optional[ExternalTrackMetadata]:
    """Map any supported video payload onto ``ExternalTrackMetadata``."""
    if not isinstance(payload, dict):
        return None
    source = _source(payload)
    if source == "ytmusic":
        return _map_ytmusic_track(payload)
    if source == "youtube_api":
        return _map_data_api_video(payload)
    if source == "ytdlp":
        return _map_ytdlp_track(payload)
    return None


def track_display_path(metadata: ExternalTrackMetadata, provider_key: str) -> str:
    """Read-only pseudo-filename ``{channel}/{title}.m4a``."""
    channel = _clean_segment(metadata.artist or "Unknown Channel")
    title = _clean_segment(metadata.title or provider_key)
    return f"{channel}/{title}.m4a"


def map_track_ref(payload: dict) -> Optional[ExternalItemRef]:
    """Map a video payload onto an ``ExternalItemRef`` (None for non-videos)."""
    metadata = map_track(payload)
    if metadata is None:
        return None
    provider_key = metadata.provider_ids.get("youtube")
    if not provider_key:
        return None
    return ExternalItemRef(
        provider_key=provider_key,
        display_path=track_display_path(metadata, provider_key),
        etag=None,
        mime_type="audio/mp4",
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Channels → artists
# ---------------------------------------------------------------------------


def _map_ytmusic_channel(item: dict) -> Optional[ExternalArtistMetadata]:
    """Map a ytmusicapi subscription/channel entry."""
    key = item.get("browseId") or item.get("channelId") or item.get("id")
    if key is None:
        return None
    name = item.get("artist") or item.get("title") or item.get("name") or key
    return ExternalArtistMetadata(
        provider_key=str(key),
        name=str(name),
        image_url=pick_thumbnail(item.get("thumbnails")),
        bio=_truncate(item.get("description")),
        provider_ids={"youtube": str(key)},
        raw_metadata={**item, "_source": "ytmusic"},
    )


def _map_data_api_channel(item: dict) -> Optional[ExternalArtistMetadata]:
    """Map a Data API ``subscriptions``/``channels`` resource."""
    snippet = item.get("snippet") or {}
    if not isinstance(snippet, dict):
        snippet = {}
    channel_id = item.get("id")
    if not channel_id:
        resource = snippet.get("resourceId") or {}
        if isinstance(resource, dict):
            channel_id = resource.get("channelId")
    if channel_id is None:
        return None
    name = snippet.get("title") or channel_id
    return ExternalArtistMetadata(
        provider_key=str(channel_id),
        name=str(name),
        image_url=_snippet_thumbnail(snippet),
        bio=_truncate(snippet.get("description")),
        provider_ids={"youtube": str(channel_id)},
        raw_metadata={**item, "_source": "youtube_api", "id": channel_id},
    )


def _map_ytdlp_channel(info: dict) -> Optional[ExternalArtistMetadata]:
    """Map a yt-dlp channel/handle extraction result."""
    key = info.get("channel_id") or info.get("id") or info.get("uploader_id")
    if key is None:
        return None
    name = info.get("channel") or info.get("title") or info.get("uploader") or key
    return ExternalArtistMetadata(
        provider_key=str(key),
        name=str(name),
        image_url=(
            info.get("thumbnail") if isinstance(info.get("thumbnail"), str) else pick_thumbnail(info.get("thumbnails"))
        ),
        bio=_truncate(info.get("description")),
        provider_ids={"youtube": str(key)},
        raw_metadata={**info, "_source": "ytdlp"},
    )


def map_artist(payload: dict) -> Optional[ExternalArtistMetadata]:
    """Map any supported channel payload onto ``ExternalArtistMetadata``."""
    if not isinstance(payload, dict):
        return None
    source = _source(payload)
    if source == "ytmusic":
        return _map_ytmusic_channel(payload)
    if source == "youtube_api":
        return _map_data_api_channel(payload)
    if source == "ytdlp":
        return _map_ytdlp_channel(payload)
    return None


# ---------------------------------------------------------------------------
# Albums (YouTube Music only)
# ---------------------------------------------------------------------------


def _map_ytmusic_album(item: dict) -> Optional[ExternalAlbumMetadata]:
    """Map a ytmusicapi library-album or ``get_album`` payload."""
    key = item.get("browseId") or item.get("id") or item.get("audioPlaylistId")
    if key is None:
        return None

    artist_names: list[str] = []
    artist_keys: list[str] = []
    for entry in item.get("artists") or []:
        if isinstance(entry, dict) and entry.get("name"):
            artist_names.append(str(entry["name"]))
            if entry.get("id") is not None:
                artist_keys.append(str(entry["id"]))
    if not artist_names and item.get("channel"):
        channel = item["channel"]
        if isinstance(channel, dict) and channel.get("name"):
            artist_names.append(str(channel["name"]))

    year = item.get("year")
    return ExternalAlbumMetadata(
        provider_key=str(key),
        title=str(item.get("title") or key),
        artist_names=tuple(artist_names),
        artist_provider_keys=tuple(artist_keys),
        release_year=_num(year) if year is not None else None,
        cover_url=pick_thumbnail(item.get("thumbnails")),
        description=_truncate(item.get("description")),
        provider_ids={"youtube": str(key)},
        raw_metadata={**item, "_source": "ytmusic"},
    )


def map_album(payload: dict) -> Optional[ExternalAlbumMetadata]:
    """Map an album payload onto ``ExternalAlbumMetadata`` (ytmusic source)."""
    if not isinstance(payload, dict):
        return None
    if _source(payload) != "ytmusic":
        return None
    return _map_ytmusic_album(payload)


# ---------------------------------------------------------------------------
# Playlists
# ---------------------------------------------------------------------------


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


def _map_ytmusic_playlist(item: dict) -> Optional[ExternalPlaylistMetadata]:
    """Map a ytmusicapi library playlist or ``get_playlist`` payload."""
    key = item.get("playlistId") or item.get("id")
    if key is None:
        return None
    author = item.get("author")
    owner_name = None
    if isinstance(author, dict):
        owner_name = author.get("name")
    elif isinstance(author, str):
        owner_name = author
    return ExternalPlaylistMetadata(
        provider_key=str(key),
        title=str(item.get("title") or "Untitled playlist"),
        description=item.get("description") if isinstance(item.get("description"), str) else None,
        cover_url=pick_thumbnail(item.get("thumbnails")),
        owner_name=str(owner_name) if owner_name else None,
        entries=(),
        mtime=_parse_datetime(item.get("lastUpdated")),
        raw_metadata={**item, "_source": "ytmusic"},
    )


def _map_data_api_playlist(item: dict) -> Optional[ExternalPlaylistMetadata]:
    """Map a Data API ``playlists`` resource."""
    key = item.get("id")
    snippet = item.get("snippet") or {}
    if key is None:
        return None
    if not isinstance(snippet, dict):
        snippet = {}
    return ExternalPlaylistMetadata(
        provider_key=str(key),
        title=str(snippet.get("title") or "Untitled playlist"),
        description=snippet.get("description") if isinstance(snippet.get("description"), str) else None,
        cover_url=_snippet_thumbnail(snippet),
        owner_name=str(snippet.get("channelTitle")) if snippet.get("channelTitle") else None,
        entries=(),
        etag=item.get("etag") if isinstance(item.get("etag"), str) else None,
        mtime=_parse_datetime(snippet.get("publishedAt")),
        raw_metadata={**item, "_source": "youtube_api"},
    )


def _map_ytdlp_playlist(info: dict) -> Optional[ExternalPlaylistMetadata]:
    """Map a yt-dlp playlist extraction result."""
    key = info.get("id")
    if key is None:
        return None
    return ExternalPlaylistMetadata(
        provider_key=str(key),
        title=str(info.get("title") or "Untitled playlist"),
        description=info.get("description") if isinstance(info.get("description"), str) else None,
        cover_url=info.get("thumbnail") if isinstance(info.get("thumbnail"), str) else None,
        owner_name=info.get("channel") or info.get("uploader"),
        entries=(),
        raw_metadata={**info, "_source": "ytdlp"},
    )


def map_playlist(payload: dict) -> Optional[ExternalPlaylistMetadata]:
    """Map any supported playlist payload onto ``ExternalPlaylistMetadata``."""
    if not isinstance(payload, dict):
        return None
    source = _source(payload)
    if source == "ytmusic":
        return _map_ytmusic_playlist(payload)
    if source == "youtube_api":
        return _map_data_api_playlist(payload)
    if source == "ytdlp":
        return _map_ytdlp_playlist(payload)
    return None


def map_playlist_items(payload: dict) -> tuple[tuple[int, str, Optional[dict]], ...]:
    """
    Map a playlist-contents payload into ``(position, provider_key, video_json)``.

    Handles ``get_playlist`` results (``tracks`` list of ytmusic dicts) and
    Data API ``playlistItems`` pages (``items`` list). Entries without a
    video id are skipped; positions stay dense after filtering.
    """
    items: list[Any]
    source = _source(payload)
    if source == "ytmusic":
        items = payload.get("tracks") or []
    elif source == "youtube_api":
        items = payload.get("items") or []
    else:
        items = []
    if not isinstance(items, list):
        return ()

    entries: list[tuple[int, str, Optional[dict]]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        video_id: Optional[str] = None
        if source == "ytmusic":
            candidate = raw.get("videoId")
            video_id = str(candidate) if candidate else None
            if raw.get("isAvailable") is False:
                continue
        else:
            details = raw.get("contentDetails") or {}
            resource = (raw.get("snippet") or {}).get("resourceId") or {}
            candidate = details.get("videoId") if isinstance(details, dict) else None
            candidate = candidate or (resource.get("videoId") if isinstance(resource, dict) else None)
            video_id = str(candidate) if candidate else None
        if not video_id:
            continue
        entries.append((len(entries), video_id, raw))
    return tuple(entries)


def playlist_entries_from_payload(payload: dict) -> tuple[ExternalPlaylistEntry, ...]:
    """Map playlist items JSON to plain provider-key entries (positions dense)."""
    return tuple(
        ExternalPlaylistEntry(position=pos, track_provider_key=key) for pos, key, _ in map_playlist_items(payload)
    )


def account_menu_is_premium(payload: Any) -> bool:
    """
    Best-effort YouTube Music Premium detection from the ``account_menu``
    innertube response — Premium accounts carry a membership/benefits entry.
    """
    import json

    try:
        text = json.dumps(payload)
    except (TypeError, ValueError):
        return False
    lowered = text.lower()
    return "music premium" in lowered or "youtube premium" in lowered or '"premium benefits"' in lowered
