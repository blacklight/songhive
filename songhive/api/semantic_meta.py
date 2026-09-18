"""
Semantic ``<head>`` tag injection for SPA object pages.

Social-media crawlers and other semantic Web consumers do not execute
JavaScript, so the backend annotates the SPA shell served for object routes
with OpenGraph ``<meta>`` tags and ``rel="tag"`` link elements derived from
the underlying entity.

Tags are only injected when the requester may access the entity: anonymous
requests therefore only describe ``public`` content, while authenticated
requests also cover content visible to them (including share-token access).
"""

import logging
from html import escape
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import quote, urlparse

from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import Receive, Scope, Send

from ..config.schema import SonghiveConfig
from ..models.base import get_session
from ..models.genre import Genre
from ..models.tag import Tag
from ..models.user import User
from ..services import acl
from ..services import feeds as feeds_service
from ..services import music
from ..services.auth import get_user_by_username
from ..services.genres import validate_genre_name
from ..services.storage import StorageService
from ..services.tags import validate_tag_name
from . import deps

logger = logging.getLogger(__name__)

# SPA path prefixes that render a single object, mapped to the internal
# entity kind used by the builders below.
_OBJECT_PREFIXES = {
    "tracks": "track",
    "albums": "album",
    "artists": "artist",
    "playlists": "playlist",
    "libraries": "library",
    "genres": "genre",
    "tags": "tag",
}

# Sub-paths that still render the parent object in the SPA.
_ENTITY_SUFFIXES = {"edit", "activities"}
_USER_SUFFIXES = {"posts", "activity", "tracks", "albums", "libraries", "playlists", "followers"}

_OG_TYPES = {
    "track": "music.song",
    "album": "music.album",
    "artist": "music.artist",
    "playlist": "music.playlist",
    "library": "music.playlist",
    "genre": "website",
    "tag": "website",
    "user": "profile",
}


def match_object_path(path: str) -> Optional[tuple[str, str]]:
    """Map an SPA path to ``(entity_kind, key)`` or return ``None``.

    Only paths that render a single Songhive object in the SPA match:
    ``/tracks/{id}``, ``/@{username}/followers``, ``/tags/{name}`` and so on.
    List pages and unknown sub-paths are ignored.
    """
    segments = [s for s in path.split("/") if s]
    if not segments:
        return None

    first = segments[0]
    if first.startswith("@"):
        if len(segments) > 2 or (len(segments) == 2 and segments[1] not in _USER_SUFFIXES):
            return None
        return "user", first[1:]

    if first not in _OBJECT_PREFIXES or len(segments) < 2:
        return None

    kind = _OBJECT_PREFIXES[first]
    if len(segments) > 2 and (kind in ("genre", "tag") or len(segments) > 3 or segments[2] not in _ENTITY_SUFFIXES):
        return None
    return kind, segments[1]


def _h(value: Optional[Any]) -> str:
    """Return a value as a single-line HTML-escaped string.

    Whitespace runs (including newlines) are collapsed: literal newlines are
    legal inside a quoted attribute, but line-oriented parsers and crawlers
    handle single-line ``content`` values more reliably, and card renderers
    collapse whitespace when displaying anyway.
    """
    if value is None:
        return ""
    return escape(" ".join(str(value).split()))


def public_base_url(request: Request, config: SonghiveConfig) -> str:
    """Return the public base URL, preferring the configured instance domain."""
    domain = config.federation.instance_domain
    if domain:
        return f"https://{domain}"
    return str(request.base_url).rstrip("/")


def _absolute(base_url: str, url: Optional[str]) -> Optional[str]:
    """Return ``url`` as an absolute URL rooted at ``base_url``."""
    if not url:
        return None
    if url.startswith(("http://", "https://")):
        return url
    if not url.startswith("/"):
        url = f"/{url}"
    return f"{base_url}{url}"


def _og_tags(
    *,
    title: str,
    url: str,
    site_name: str,
    og_type: str,
    description: Optional[str] = None,
    image: Optional[str] = None,
    tag_names: tuple[str, ...] = (),
    extra: Iterable[str] = (),
) -> list[str]:
    """Build the OpenGraph ``<meta>`` and ``rel="tag"`` tags for an entity."""
    tags = [
        f'<meta property="og:title" content="{_h(title)}">',
        f'<meta property="og:type" content="{_h(og_type)}">',
        f'<meta property="og:url" content="{_h(url)}">',
        f'<meta property="og:site_name" content="{_h(site_name)}">',
    ]
    if description:
        tags.append(f'<meta property="og:description" content="{_h(description)}">')
    if image:
        tags.append(f'<meta property="og:image" content="{_h(image)}">')
    for name in tag_names:
        tags.append(f'<link rel="tag" href="/tags/{quote(name)}" title="{_h(name)}">')
    tags.extend(extra)
    tags.append('<meta name="twitter:card" content="summary_large_image">')
    return tags


def _profile_url(base_url: str, username: str) -> str:
    return f"{base_url}/@{quote(username)}"


def _music_relation(property_name: str, url: str) -> str:
    """Build an OpenGraph music relation tag (``music:musician`` etc.)."""
    return f'<meta property="{property_name}" content="{_h(url)}">'


def _uploader_tags(owner: Optional[User], base_url: str) -> list[str]:
    """Build authorship tags pointing at the user who uploaded the content.

    ``rel="author"``/``name="author"`` are the standard HTML authorship
    annotations; ``fediverse:creator`` is consumed by Mastodon and other
    Fediverse software for author attribution in link previews.
    """
    if owner is None:
        return []
    tags = [
        f'<link rel="author" href="{_h(_profile_url(base_url, owner.username))}" ' f'title="{_h(owner.username)}">',
        f'<meta name="author" content="{_h(owner.username)}">',
    ]
    host = urlparse(base_url).hostname
    if host:
        handle = f"@{owner.username}@{host}"
        tags.append(f'<meta name="fediverse:creator" content="{_h(handle)}">')
    return tags


def _tag_names(entity: Any) -> tuple[str, ...]:
    """Return the tag names attached to an entity (tags must be loaded)."""
    return tuple(t.name for t in (getattr(entity, "tags", None) or ()))


async def _file_url(storage: StorageService, stored_file: Any) -> Optional[str]:
    """Return the download URL for a stored file, or ``None``."""
    if stored_file is None:
        return None
    return await storage.get_url(stored_file)


async def _track_tags(
    session: AsyncSession,
    storage: StorageService,
    user: Optional[User],
    share_token: Optional[str],
    track_id: str,
    base_url: str,
    site_name: str,
) -> Optional[list[str]]:
    if not await acl.can_access(session, user, "track", track_id, share_token=share_token):
        return None
    track = await music.get_track(session, track_id, include={"artist", "album", "tags"})
    if track is None:
        return None

    artist_name = track.artist.name if track.artist else None
    title = f"{artist_name} - {track.title}" if artist_name else track.title
    description = (track.album.title if track.album else None) or track.description

    image = await _file_url(storage, track.image_file)
    if image is None and track.album is not None:
        image = await _file_url(storage, track.album.cover_file) or track.album.cover_url

    extra: list[str] = []
    if track.artist is not None:
        extra.append(_music_relation("music:musician", f"{base_url}/artists/{track.artist.id}"))
    if track.album is not None:
        extra.append(_music_relation("music:album", f"{base_url}/albums/{track.album.id}"))
    if track.duration:
        extra.append(f'<meta property="music:duration" content="{int(track.duration)}">')
    extra += _uploader_tags(track.owner, base_url)

    return _og_tags(
        title=title,
        description=description,
        url=f"{base_url}/tracks/{track.id}",
        site_name=site_name,
        og_type=_OG_TYPES["track"],
        image=_absolute(base_url, image),
        tag_names=_tag_names(track),
        extra=extra,
    )


async def _album_tags(
    session: AsyncSession,
    storage: StorageService,
    user: Optional[User],
    share_token: Optional[str],
    album_id: str,
    base_url: str,
    site_name: str,
) -> Optional[list[str]]:
    if not await acl.can_access(session, user, "album", album_id, share_token=share_token):
        return None
    album = await music.get_album(session, album_id, include={"tags"})
    if album is None:
        return None

    artist_name = album.artist.name if album.artist else None
    title = f"{artist_name} - {album.title}" if artist_name else album.title
    image = await _file_url(storage, album.cover_file) or album.cover_url

    extra: list[str] = []
    if album.artist is not None:
        extra.append(_music_relation("music:musician", f"{base_url}/artists/{album.artist.id}"))
    extra += _uploader_tags(album.owner, base_url)

    return _og_tags(
        title=title,
        description=album.description,
        url=f"{base_url}/albums/{album.id}",
        site_name=site_name,
        og_type=_OG_TYPES["album"],
        image=_absolute(base_url, image),
        tag_names=_tag_names(album),
        extra=extra,
    )


async def _artist_tags(
    session: AsyncSession,
    storage: StorageService,
    artist_id: str,
    base_url: str,
    site_name: str,
) -> Optional[list[str]]:
    # Artists carry no visibility of their own; the artist JSON endpoint is
    # world-readable, so the page metadata is too.
    artist = await music.get_artist(session, artist_id, include={"tags"})
    if artist is None:
        return None

    image = await _file_url(storage, artist.image_file) or artist.image_url

    return _og_tags(
        title=artist.name,
        description=artist.bio,
        url=f"{base_url}/artists/{artist.id}",
        site_name=site_name,
        og_type=_OG_TYPES["artist"],
        image=_absolute(base_url, image),
        tag_names=_tag_names(artist),
    )


async def _collection_tags(
    session: AsyncSession,
    storage: StorageService,
    user: Optional[User],
    share_token: Optional[str],
    kind: str,
    entity_id: str,
    base_url: str,
    site_name: str,
) -> Optional[list[str]]:
    """Build head tags for a playlist or library page."""
    if not await acl.can_access(session, user, kind, entity_id, share_token=share_token):
        return None
    if kind == "playlist":
        entity: Optional[Any] = await music.get_playlist(session, entity_id, include={"owner", "tags"})
    else:
        entity = await music.get_library(session, entity_id, include={"owner", "tags"})
    if entity is None:
        return None

    owner_name = entity.owner.username if entity.owner else None
    image = await _file_url(storage, entity.image_file) or await _file_url(storage, entity.cover_file)
    plural = "playlists" if kind == "playlist" else "libraries"

    extra: list[str] = []
    if entity.owner is not None:
        extra.append(_music_relation("music:creator", _profile_url(base_url, entity.owner.username)))
    extra += _uploader_tags(entity.owner, base_url)

    return _og_tags(
        title=entity.name,
        description=owner_name or entity.description,
        url=f"{base_url}/{plural}/{entity.id}",
        site_name=site_name,
        og_type=_OG_TYPES[kind],
        image=_absolute(base_url, image),
        tag_names=_tag_names(entity),
        extra=extra,
    )


async def _genre_tags(
    session: AsyncSession,
    name: str,
    base_url: str,
    site_name: str,
) -> Optional[list[str]]:
    try:
        normalised = validate_genre_name(name)
    except ValueError:
        return None
    result = await session.execute(select(Genre).where(Genre.name == normalised).limit(1))
    if result.scalar_one_or_none() is None:
        return None

    return _og_tags(
        title=normalised,
        url=f"{base_url}/genres/{quote(normalised)}",
        site_name=site_name,
        og_type=_OG_TYPES["genre"],
    )


async def _tag_tags(
    session: AsyncSession,
    name: str,
    base_url: str,
    site_name: str,
) -> Optional[list[str]]:
    try:
        normalised = validate_tag_name(name)
    except ValueError:
        return None
    result = await session.execute(select(Tag).where(Tag.name == normalised).limit(1))
    if result.scalar_one_or_none() is None:
        return None

    return _og_tags(
        title=f"#{normalised}",
        url=f"{base_url}/tags/{quote(normalised)}",
        site_name=site_name,
        og_type=_OG_TYPES["tag"],
        tag_names=(normalised,),
    )


def user_head_tags(user: User, base_url: str, site_name: str) -> list[str]:
    """Build the OpenGraph tags for a user profile page."""
    description = user.bio.strip() if user.bio else None
    return _og_tags(
        title=user.display_name or user.username,
        description=description,
        url=_profile_url(base_url, user.username),
        site_name=site_name,
        og_type=_OG_TYPES["user"],
        image=_absolute(base_url, user.avatar_url),
        extra=[f'<meta property="profile:username" content="{_h(user.username)}">'],
    )


def feed_link_tags(kind: str, key: str, base_url: str, *, activities: bool = False) -> list[str]:
    """Build ``<link rel="alternate">`` tags advertising the entity's feeds.

    ``kind``/``key`` follow ``match_object_path``. ``activities`` selects the
    activities feed for kinds that also have a content feed (artists,
    playlists, libraries); track and album pages always advertise their
    activities feed since they have no feed of their own.
    """
    if kind in ("user", "tag", "genre") or (kind in ("artist", "playlist", "library") and not activities):
        build = lambda fmt: feeds_service.feed_path(kind, key, fmt)  # noqa: E731
    else:
        build = lambda fmt: feeds_service.activities_feed_path(kind, key, fmt)  # noqa: E731
    return [
        f'<link rel="alternate" type="application/rss+xml" href="{_h(base_url + build("rss"))}" title="RSS feed">',
        f'<link rel="alternate" type="application/atom+xml" href="{_h(base_url + build("atom"))}" title="Atom feed">',
    ]


async def _user_tags(
    session: AsyncSession,
    user: Optional[User],
    username: str,
    base_url: str,
    site_name: str,
) -> Optional[list[str]]:
    profile = await get_user_by_username(session, username)
    if profile is None or not await acl.can_access(session, user, "user", str(profile.id)):
        return None
    return user_head_tags(profile, base_url, site_name)


def _share_token(request: Request) -> Optional[str]:
    """Read a share token from the header, cookie, or query string."""
    token = request.headers.get("X-Share-Token")
    if token:
        return token
    token = request.cookies.get("share_token")
    if token:
        return token
    return request.query_params.get("token")


async def entity_head_tags(
    request: Request,
    session: AsyncSession,
    user: Optional[User],
    storage: StorageService,
    kind: str,
    key: str,
) -> list[str]:
    """Return the semantic ``<head>`` tags for a single object.

    ``kind`` is one of the keys produced by ``match_object_path`` and ``key``
    the entity id, name or username. An empty list is returned when the
    object does not exist or is not visible to ``user``.
    """
    config: SonghiveConfig = request.app.state.config
    base_url = public_base_url(request, config)
    site_name = config.federation.instance_name
    share_token = _share_token(request)

    if kind == "track":
        tags = await _track_tags(session, storage, user, share_token, key, base_url, site_name)
    elif kind == "album":
        tags = await _album_tags(session, storage, user, share_token, key, base_url, site_name)
    elif kind == "artist":
        tags = await _artist_tags(session, storage, key, base_url, site_name)
    elif kind in ("playlist", "library"):
        tags = await _collection_tags(session, storage, user, share_token, kind, key, base_url, site_name)
    elif kind == "genre":
        tags = await _genre_tags(session, key, base_url, site_name)
    elif kind == "tag":
        tags = await _tag_tags(session, key, base_url, site_name)
    elif kind == "user":
        tags = await _user_tags(session, user, key, base_url, site_name)
    else:
        tags = None
    if tags and config.feeds.enabled:
        activities = request.url.path.rstrip("/").endswith("/activities")
        tags += feed_link_tags(kind, key, base_url, activities=activities)
    return tags or []


async def object_head_tags(request: Request) -> list[str]:
    """Return the semantic ``<head>`` tags for the SPA page being served.

    The request path is mapped to a Songhive object; when the object exists
    and is visible to the requester, OpenGraph and ``rel="tag"`` tags are
    returned. Any lookup or rendering failure yields an empty list so the SPA
    shell is still served.
    """
    match = match_object_path(request.url.path)
    if match is None:
        return []

    kind, key = match
    try:
        storage = deps.get_storage_service(request)
        async with get_session() as session:
            user = await deps._get_current_user(request, session, None)
            return await entity_head_tags(request, session, user, storage, kind, key)
    except Exception:
        logger.exception("Failed to build semantic head tags for %s", request.url.path)
        return []


def inject_head_tags(body: str, tags: list[str]) -> str:
    """Insert ``tags`` at the end of the document ``<head>``."""
    injected = "".join(tags)
    if "</head>" in body:
        return body.replace("</head>", f"{injected}</head>", 1)
    return f"{body}{injected}"


async def serve_spa_index(
    scope: Scope,
    receive: Receive,
    send: Send,
    index_path: Path,
) -> None:
    """Serve the SPA shell, injecting semantic tags for object pages."""
    request = Request(scope, receive)
    body = index_path.read_text(encoding="utf-8")
    tags = await object_head_tags(request)
    if tags:
        body = inject_head_tags(body, tags)
    await HTMLResponse(content=body)(scope, receive, send)
