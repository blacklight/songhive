"""MusicBrainz payload helpers — part of the ``musicbrainz`` service."""

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from ...models.album import Album
from ...models.artist import Artist

if TYPE_CHECKING:
    from ...models.track import Track

logger = logging.getLogger(__name__)

# The Cover Art Archive /front endpoint can return a chain of 307/302
# redirects before the actual image, so follow them manually up to this many.
_MAX_COVER_ART_REDIRECTS = 10

# Number of ranked MusicBrainz releases tried when the release linked to an
# album yields no cover art.
_MAX_COVER_RELEASE_CANDIDATES = 3

# MusicBrainz release statuses that describe unofficial or promotional
# products. A missing status is treated as neutral; unknown statuses rank
# between the two.
_RELEASE_STATUS_SCORE = {
    "official": 3,
    "promotion": -1,
    "bootleg": -3,
    "pseudo-release": -3,
}

# Release-group secondary types marking a derivative product rather than the
# canonical release (live versions, compilations, remixes, ...).
_NON_CANONICAL_SECONDARY_TYPES = {
    "live",
    "compilation",
    "remix",
    "dj-mix",
    "mixtape/street",
    "demo",
    "spokenword",
    "interview",
    "audiobook",
    "audio drama",
    "field recording",
}

# Disambiguation comments that strongly suggest the release is not the
# canonical edition.
_NON_CANONICAL_DISAMBIGUATION = ("bootleg", "unofficial", "promo")

# Disambiguation comments marking a special edition; the standard edition is
# preferred because it carries the canonical cover art.
_SPECIAL_EDITION_DISAMBIGUATION = (
    "limited",
    "deluxe",
    "collector",
    "box set",
    "fan club",
    "special edition",
    "expanded",
    "reissue",
    "remaster",
    "anniversary",
)

# Release countries preferred on ties: worldwide digital releases and the
# major physical markets tend to carry the canonical cover.
_PREFERRED_RELEASE_COUNTRIES = {"xe", "us", "gb", "jp", "de", "fr", "ca", "au"}


def _escape_query_term(term: str) -> str:
    """Quote a MusicBrainz query term so spaces and special chars are handled."""
    escaped = term.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _recording_title(recording: Dict[str, Any]) -> Optional[str]:
    """Return a recording title, if present."""
    title = recording.get("title")
    return title if isinstance(title, str) and title.strip() else None


def _recording_duration(recording: Dict[str, Any]) -> Optional[float]:
    """Return a recording duration in seconds, if present."""
    length = recording.get("length")
    if length is None:
        return None
    try:
        return float(length) / 1000.0
    except (TypeError, ValueError):
        return None


def _recording_artist(recording: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Return the artist name and MusicBrainz ID from a recording."""
    phrase = recording.get("artist-credit-phrase")
    if isinstance(phrase, str) and phrase.strip():
        return phrase.strip(), _first_artist_id(recording)

    credit = recording.get("artist-credit")
    if not isinstance(credit, list):
        return None, None

    parts: List[str] = []
    mbid: Optional[str] = None
    for entry in credit:
        if not isinstance(entry, dict):
            continue
        if mbid is None:
            artist = entry.get("artist", {})
            if isinstance(artist, dict):
                mbid = artist.get("id")
        name = entry.get("name")
        if not name and isinstance(entry.get("artist"), dict):
            name = entry["artist"].get("name")
        if name:
            parts.append(str(name))
            join = entry.get("joinphrase")
            if join:
                parts.append(str(join))

    return ("".join(parts).strip() or None), mbid


def _first_artist_id(recording: Dict[str, Any]) -> Optional[str]:
    """Return the first artist ID from a recording search result."""
    credit = recording.get("artist-credit", [])
    if credit and isinstance(credit, list):
        first = credit[0]
        if isinstance(first, dict):
            artist = first.get("artist", {})
            if isinstance(artist, dict):
                return artist.get("id")
    return None


def _release_title(release: Optional[Dict[str, Any]]) -> Optional[str]:
    """Return a release title, if present."""
    if not release:
        return None
    title = release.get("title")
    return title if isinstance(title, str) and title.strip() else None


def _release_id(release: Optional[Dict[str, Any]]) -> Optional[str]:
    """Return a release MusicBrainz ID, if present."""
    if not release:
        return None
    return release.get("id")


def _release_year(release: Optional[Dict[str, Any]]) -> Optional[int]:
    """Return the release year, if present."""
    if not release:
        return None

    date = release.get("date") or release.get("first-release-date")
    if not date and isinstance(release.get("release-events"), list):
        events = release["release-events"]
        if events:
            date = events[0].get("date")

    if not date:
        return None

    match = re.search(r"\d{4}", str(date))
    return int(match.group(0)) if match else None


def _release_track_number(release: Optional[Dict[str, Any]]) -> Optional[int]:
    """Return the track number on the first medium, if present and numeric."""
    if not release:
        return None

    media_list = release.get("medium-list") or release.get("media") or []
    for medium in media_list:
        tracks = medium.get("track-list") or medium.get("track") or []
        if not isinstance(tracks, list):
            tracks = [tracks]
        for track in tracks:
            number = track.get("number")
            if number is None:
                continue
            match = re.match(r"(\d+)", str(number))
            if match:
                return int(match.group(1))

    return None


def _release_disc_number(release: Optional[Dict[str, Any]]) -> Optional[int]:
    """Return the medium/disc number, if present."""
    if not release:
        return None

    media_list = release.get("medium-list") or release.get("media") or []
    for medium in media_list:
        tracks = medium.get("track-list") or medium.get("track") or []
        if not isinstance(tracks, list):
            tracks = [tracks]
        if tracks:
            position = medium.get("position")
            if position is not None:
                try:
                    return int(position)
                except (TypeError, ValueError):
                    pass
            return 1

    return None


def _release_group_id(release: Optional[Dict[str, Any]]) -> Optional[str]:
    """Return the release-group MusicBrainz ID for a release, if present."""
    if not release:
        return None
    group = release.get("release-group")
    if isinstance(group, dict):
        group_id = group.get("id")
        if isinstance(group_id, str) and group_id:
            return group_id
    return None


def _release_rank(release: Dict[str, Any], album_title: Optional[str]) -> Tuple[int, ...]:
    """
    Return a ranking tuple for a release; higher ranks are more canonical.

    Title matches come first, then unofficial statuses (bootleg, promotion),
    special-edition disambiguation comments and secondary release-group types
    are penalized, and releases whose group is a plain album, which report
    front artwork and which have the earliest release date are preferred —
    i.e. the original press of an album rather than a bootleg, promo or
    limited reissue.
    """
    return (
        _release_title_score(release, album_title),
        _release_status_score(release),
        _release_disambiguation_score(release),
        _release_primary_type_score(release),
        _release_secondary_type_score(release),
        _release_cover_art_score(release),
        -_release_date_value(release),
        _release_country_score(release),
    )


def _release_title_score(release: Dict[str, Any], album_title: Optional[str]) -> int:
    """Score how closely a release title matches the local album title."""
    title = _release_title(release)
    if not title or not album_title:
        return 0
    if title.strip().lower() == album_title.strip().lower():
        return 2
    normalized_title = _normalize_release_title(title)
    if normalized_title and normalized_title == _normalize_release_title(album_title):
        return 1
    return 0


_NORMALIZED_TITLE_STRIP_RE = re.compile(r"[(\[][^)\]]*[)\]]")
_NORMALIZED_TITLE_CHARS_RE = re.compile(r"[^a-z0-9]+")


def _normalize_release_title(title: str) -> str:
    """Normalize a title for fuzzy release/album comparison."""
    normalized = _NORMALIZED_TITLE_STRIP_RE.sub(" ", title.lower())
    return _NORMALIZED_TITLE_CHARS_RE.sub(" ", normalized).strip()


def _release_status_score(release: Dict[str, Any]) -> int:
    """Score a release status; unofficial products rank below official ones."""
    status = release.get("status")
    if not isinstance(status, str) or not status.strip():
        return 1
    return _RELEASE_STATUS_SCORE.get(status.strip().lower(), 0)


def _release_disambiguation_score(release: Dict[str, Any]) -> int:
    """Penalize releases whose disambiguation marks a non-canonical edition."""
    parts = [release.get("disambiguation")]
    group = release.get("release-group")
    if isinstance(group, dict):
        parts.append(group.get("disambiguation"))
    text = " ".join(p.lower() for p in parts if isinstance(p, str) and p)
    if not text:
        return 0
    if any(marker in text for marker in _NON_CANONICAL_DISAMBIGUATION):
        return -3
    if any(marker in text for marker in _SPECIAL_EDITION_DISAMBIGUATION):
        return -1
    return 0


def _release_primary_type_score(release: Dict[str, Any]) -> int:
    """Prefer releases whose group is a plain album over singles, EPs, etc."""
    group = release.get("release-group")
    if not isinstance(group, dict):
        return 1
    primary = group.get("primary-type") or group.get("type")
    if not isinstance(primary, str) or not primary.strip():
        return 1
    return 2 if primary.strip().lower() == "album" else 0


def _release_secondary_type_score(release: Dict[str, Any]) -> int:
    """Penalize releases whose group is a live, compilation or remix variant."""
    group = release.get("release-group")
    if not isinstance(group, dict):
        return 0
    secondary = group.get("secondary-type-list") or group.get("secondary-types") or []
    types = {str(entry).strip().lower() for entry in secondary if entry}
    return -2 if types & _NON_CANONICAL_SECONDARY_TYPES else 0


def _release_cover_art_score(release: Dict[str, Any]) -> int:
    """Prefer releases the Cover Art Archive reports artwork for."""
    archive = release.get("cover-art-archive") or release.get("cover_art_archive")
    if not isinstance(archive, dict):
        return 0
    for key in ("front", "artwork"):
        if str(archive.get(key)).strip().lower() in {"true", "1"}:
            return 1
    count = archive.get("count")
    try:
        if count is not None and int(count) > 0:
            return 1
    except (TypeError, ValueError):
        pass
    return -1


def _release_date_value(release: Dict[str, Any]) -> int:
    """Return a sortable ordinal for the release date; missing dates sort last."""
    date = release.get("date")
    events = release.get("release-events")
    if not date and isinstance(events, list):
        for event in events:
            if isinstance(event, dict) and event.get("date"):
                date = event["date"]
                break
    if not date:
        group = release.get("release-group")
        if isinstance(group, dict):
            date = group.get("first-release-date")

    match = re.match(r"(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", str(date or ""))
    if not match:
        return 99999999
    year = int(match.group(1))
    month = int(match.group(2) or 0)
    day = int(match.group(3) or 0)
    return year * 10000 + month * 100 + day


def _release_country_score(release: Dict[str, Any]) -> int:
    """Prefer worldwide and major-market releases on ties."""
    codes = set()
    country = release.get("country")
    if isinstance(country, str) and country.strip():
        codes.add(country.strip().lower())
    events = release.get("release-events")
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, dict):
                continue
            area = event.get("area")
            if not isinstance(area, dict):
                continue
            for code in area.get("iso-3166-1-code-list") or []:
                if isinstance(code, str):
                    codes.add(code.lower())
    if "xw" in codes:
        return 2
    if codes & _PREFERRED_RELEASE_COUNTRIES:
        return 1
    return 0


def _ranked_cover_art_image_urls(payload: Dict[str, Any]) -> List[str]:
    """Return front-cover image URLs from a Cover Art Archive listing, best first."""
    images = payload.get("images")
    if not isinstance(images, list):
        return []

    fronts = [image for image in images if isinstance(image, dict) and _cover_art_is_front(image)]
    # Approved edits first; the archive's own ordering is preserved otherwise.
    fronts.sort(key=lambda image: image.get("approved") is not True)

    urls = []
    for image in fronts:
        url = image.get("image")
        if isinstance(url, str) and url.startswith("http"):
            urls.append(url)
    return urls


def _cover_art_is_front(image: Dict[str, Any]) -> bool:
    """Return ``True`` when a Cover Art Archive image is marked as a front."""
    if image.get("front") is True:
        return True
    types = image.get("types")
    if isinstance(types, list):
        return any(str(entry).strip().lower() == "front" for entry in types)
    return False


def _is_logo_relation(relation: Dict[str, Any]) -> bool:
    """Return ``True`` when a URL relation points to a logo rather than a photo."""
    return str(relation.get("type") or "").strip().lower() == "logo"


def _is_missing_title(track: Track) -> bool:
    """Return ``True`` when the track title is missing or derived from the filename."""
    if not track.title or not track.title.strip():
        return True

    if track.audio_file and track.audio_file.original_filename:
        return track.title == Path(track.audio_file.original_filename).stem

    return False


def _is_missing_artist(artist: Optional[Artist]) -> bool:
    """Return ``True`` when the artist name is missing or a generic placeholder."""
    if artist is None:
        return True
    name = (artist.name or "").strip()
    return not name or name == "Unknown Artist"


def _is_missing_album(album: Optional[Album]) -> bool:
    """Return ``True`` when the album title is missing or a generic placeholder."""
    if album is None:
        return True
    title = (album.title or "").strip()
    return not title or title == "Unknown Album"


def _normalize_name(name: Optional[str]) -> str:
    """Return a lowercased, stripped name for comparison."""
    return (name or "").strip().lower()


def _guess_image_mime(data: bytes) -> str:
    """Guess an image MIME type from the first few bytes."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


_IMAGE_URL_RE = re.compile(r"\.(?:png|jpe?g|gif|webp)(?:\?|#|$)", re.IGNORECASE)


def _looks_like_image_url(url: str) -> bool:
    """Return ``True`` when a URL looks like a direct image link."""
    return _IMAGE_URL_RE.search(url) is not None


def _is_valid_image(data: bytes) -> bool:
    """Return ``True`` when the bytes start with a known image signature."""
    if not data:
        return False
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if data.startswith(b"\xff\xd8"):
        return True
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return True
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return True
    return False


def _is_image_relation(relation: Dict[str, Any]) -> bool:
    """Return ``True`` when a MusicBrainz URL relation may point to an image."""
    relation_type = relation.get("type") or ""
    if not isinstance(relation_type, str):
        return False
    relation_type = relation_type.lower()
    if relation_type in {"image", "logo", "wikimedia"}:
        return True
    if "image" in relation_type or "photo" in relation_type or "picture" in relation_type:
        return True
    return False
