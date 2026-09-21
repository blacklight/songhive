"""
RSS 2.0 and Atom feed generation for Songhive object pages.

Feeds are rendered server-side as XML and advertised through
``<link rel="alternate">`` tags injected into the SPA shell (see
``api.semantic_meta``), so feed readers and other non-browser clients can
discover them. All queries honour the requester's ACL: anonymous requests
receive only public content, while authenticated requests (browser sessions
or bearer tokens) also cover content visible to them.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import format_datetime
from typing import List, Optional
from urllib.parse import quote
from xml.sax.saxutils import escape, quoteattr

from sqlalchemy import exists, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..config.schema import SonghiveConfig
from ..models.activity import Activity
from ..models.album import Album
from ..models.artist import Artist
from ..models.library_track import LibraryTrack
from ..models.playlist import PlaylistTrack
from ..models.podcast import PodcastEpisode
from ..models.track import Track
from ..models.user import User
from . import activities as activity_service
from . import music as music_service
from . import tags as tags_service
from .acl import _list_access_predicate, apply_access_filter, get_item_plural
from .activities import ActorProfile

logger = logging.getLogger(__name__)

FEED_FORMATS = ("rss", "atom")
FEED_MEDIA_TYPES = {
    "rss": "application/rss+xml",
    "atom": "application/atom+xml",
}

_ATOM_NS = "http://www.w3.org/2005/Atom"
_DC_NS = "http://purl.org/dc/elements/1.1/"


def feed_path(kind: str, key: str, fmt: str) -> str:
    """Return the site-relative URL of an entity's main feed.

    ``kind`` is one of ``user``, ``artist``, ``playlist``, ``library``,
    ``tag`` or ``genre``. Tracks and albums have no dedicated feed — their
    pages advertise the activities feed instead (see
    :func:`activities_feed_path`).
    """
    if kind == "user":
        return f"/feeds/users/{quote(key)}.{fmt}"
    if kind == "tag":
        return f"/feeds/tags/{quote(key)}.{fmt}"
    if kind == "genre":
        return f"/feeds/genres/{quote(key)}.{fmt}"
    plural = get_item_plural(kind) or f"{kind}s"
    return f"/feeds/{plural}/{quote(key)}.{fmt}"


def activities_feed_path(entity_type: str, entity_id: str, fmt: str) -> str:
    """Return the site-relative URL of an entity's activities feed."""
    plural = get_item_plural(entity_type) or f"{entity_type}s"
    return f"/feeds/{plural}/{quote(entity_id)}/activities.{fmt}"


@dataclass
class FeedItem:
    """A single entry in a feed."""

    id: str
    title: str
    link: str
    published: datetime
    description: Optional[str] = None
    author: Optional[str] = None
    updated: Optional[datetime] = None
    enclosure_url: Optional[str] = None
    enclosure_type: Optional[str] = None
    enclosure_length: Optional[int] = None


@dataclass
class Feed:
    """A feed document ready for RSS or Atom serialisation."""

    id: str
    title: str
    link: str
    feed_url: str
    description: Optional[str] = None
    items: List[FeedItem] = field(default_factory=list)
    updated: Optional[datetime] = None


def _rss_date(dt: datetime) -> str:
    return format_datetime(dt.astimezone(timezone.utc), usegmt=True)


def _iso_date(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _el(tag: str, text: Optional[str], attrs: Optional[dict] = None) -> str:
    """Return a ``<tag>text</tag>`` element with escaped content and attributes."""
    attr = "".join(f" {name}={quoteattr(value)}" for name, value in (attrs or {}).items())
    if text is None:
        return f"<{tag}{attr} />"
    return f"<{tag}{attr}>{escape(text)}</{tag}>"


def _enclosure_attrs(item: FeedItem, href_key: str) -> dict:
    attrs = {href_key: item.enclosure_url or ""}
    if item.enclosure_type:
        attrs["type"] = item.enclosure_type
    if item.enclosure_length is not None:
        attrs["length"] = str(item.enclosure_length)
    return attrs


def _document(body: str) -> str:
    return '<?xml version="1.0" encoding="utf-8"?>\n' + body


def render_rss(feed: Feed) -> str:
    """Serialise ``feed`` as an RSS 2.0 document."""
    channel = [
        _el("title", feed.title),
        _el("link", feed.link),
        _el("description", feed.description or feed.title),
        _el(
            "atom:link",
            None,
            {"href": feed.feed_url, "rel": "self", "type": FEED_MEDIA_TYPES["rss"]},
        ),
    ]
    if feed.updated is not None:
        channel.append(_el("lastBuildDate", _rss_date(feed.updated)))

    for item in feed.items:
        entry = [
            _el("title", item.title),
            _el("link", item.link),
            _el("guid", item.id, {"isPermaLink": "false"}),
            _el("pubDate", _rss_date(item.published)),
        ]
        if item.description:
            entry.append(_el("description", item.description))
        if item.author:
            entry.append(_el("dc:creator", item.author))
        if item.enclosure_url:
            entry.append(_el("enclosure", None, _enclosure_attrs(item, "url")))
        channel.append(f"<item>{''.join(entry)}</item>")

    return _document(
        f'<rss version="2.0" xmlns:atom="{_ATOM_NS}" xmlns:dc="{_DC_NS}">'
        f"<channel>{''.join(channel)}</channel></rss>"
    )


def render_atom(feed: Feed) -> str:
    """Serialise ``feed`` as an Atom 1.0 document."""
    updated = feed.updated or datetime.now(timezone.utc)
    entries = [
        _el("title", feed.title),
        _el("id", feed.id),
        _el("link", None, {"href": feed.link, "rel": "alternate"}),
        _el("link", None, {"href": feed.feed_url, "rel": "self", "type": FEED_MEDIA_TYPES["atom"]}),
        _el("updated", _iso_date(updated)),
    ]

    for item in feed.items:
        entry = [
            _el("title", item.title),
            _el("id", item.id),
            _el("link", None, {"href": item.link, "rel": "alternate"}),
        ]
        if item.enclosure_url:
            entry.append(_el("link", None, {**_enclosure_attrs(item, "href"), "rel": "enclosure"}))
        entry.append(_el("published", _iso_date(item.published)))
        entry.append(_el("updated", _iso_date(item.updated or item.published)))
        if item.description:
            entry.append(_el("summary", item.description, {"type": "html"}))
        if item.author:
            entry.append(f"<author>{_el('name', item.author)}</author>")
        entries.append(f"<entry>{''.join(entry)}</entry>")

    return _document(f'<feed xmlns="{_ATOM_NS}">{"".join(entries)}</feed>')


def _latest(items: List[FeedItem]) -> Optional[datetime]:
    """Return the most recent publication date among ``items``."""
    if not items:
        return None
    return max(item.published for item in items)


def _entity_title(entity: object) -> str:
    """Return a display title for a feed-owning entity."""
    return (
        getattr(entity, "title", None)
        or getattr(entity, "name", None)
        or getattr(entity, "display_name", None)
        or getattr(entity, "username", None)
        or ""
    )


def track_item(track: Track, base_url: str) -> FeedItem:
    """Build a feed item for a track, with a stream enclosure when playable."""
    artist_name = track.artist.name if track.artist else None
    title = f"{artist_name} - {track.title}" if artist_name else track.title
    link = f"{base_url}/tracks/{track.id}"
    item = FeedItem(
        id=link,
        title=title,
        link=link,
        published=track.created_at,
        description=track.description,
        author=artist_name,
        updated=track.updated_at,
    )
    if track.audio_file is not None:
        item.enclosure_url = f"{base_url}/api/v1/stream/{track.id}"
        item.enclosure_type = track.audio_mime_type or track.audio_file.content_type
        item.enclosure_length = track.audio_file.size
    return item


def episode_item(episode: PodcastEpisode, base_url: str) -> FeedItem:
    """Build a feed item for a podcast episode — the enclosure streams the remote source."""
    podcast = episode.podcast
    podcast_title = podcast.title if podcast is not None else None
    title = f"{podcast_title} - {episode.title}" if podcast_title else episode.title
    link = episode.link or f"{base_url}/podcasts/{episode.podcast_id}"
    return FeedItem(
        id=link,
        title=title,
        link=link,
        published=episode.published_at or episode.created_at,
        description=episode.description,
        author=podcast.author if podcast is not None else podcast_title,
        updated=episode.updated_at,
        enclosure_url=episode.audio_url,
        enclosure_type=episode.audio_type,
        enclosure_length=episode.audio_length,
    )


def album_item(album: Album, base_url: str) -> FeedItem:
    """Build a feed item for an album release."""
    artist_name = album.artist.name if album.artist else None
    title = f"{artist_name} - {album.title}" if artist_name else album.title
    link = f"{base_url}/albums/{album.id}"
    return FeedItem(
        id=link,
        title=title,
        link=link,
        published=album.created_at,
        description=album.description,
        author=artist_name,
        updated=album.updated_at,
    )


_ACTIVITY_VERBS = {
    "create": "posted",
    "announce": "boosted a post",
    "like": "liked a post",
    "reply": "replied to a post",
    "quote": "quoted a post",
    "mention": "mentioned a post",
    "update": "updated a post",
    "delete": "deleted a post",
    "webmention": "sent a Webmention",
}


def activity_item(
    activity: Activity,
    profile: Optional[ActorProfile],
    base_url: str,
) -> FeedItem:
    """Build a feed item for an activity."""
    actor = (profile.display_name if profile else None) or activity_service._remote_actor_handle(activity.source_actor)
    verb = _ACTIVITY_VERBS.get(activity.activity_type, activity.activity_type)
    link = f"{base_url}/activities/{activity.id}"
    return FeedItem(
        id=link,
        title=f"{actor} {verb}",
        link=link,
        published=activity.published_at,
        description=activity.content or None,
        author=actor,
    )


async def _activities_items(
    session: AsyncSession,
    activities: List[Activity],
    config: SonghiveConfig,
    base_url: str,
) -> List[FeedItem]:
    """Resolve actor profiles and serialise a page of activities."""
    profile_map = await activity_service.resolve_source_actor_profiles(session, activities, config)
    return [activity_item(a, profile_map.get(str(a.id)), base_url) for a in activities]


async def user_feed(
    session: AsyncSession,
    profile: User,
    requester: Optional[User],
    config: SonghiveConfig,
    base_url: str,
    feed_url: str,
    limit: int,
) -> Feed:
    """Build the feed of a user's latest posts (create/quote/announce)."""
    activities, _ = await activity_service.list_user_activities(
        session,
        owner_user_id=str(profile.id),
        user=requester,
        mode="posts",
        include_boosts=True,
        include_replies=False,
        limit=limit,
    )
    items = await _activities_items(session, activities, config, base_url)
    link = f"{base_url}/@{quote(profile.username)}"
    return Feed(
        id=link,
        title=profile.display_name or profile.username,
        link=link,
        feed_url=feed_url,
        description=profile.bio,
        items=items,
        updated=_latest(items),
    )


async def entity_activities_feed(
    session: AsyncSession,
    entity_type: str,
    entity: object,
    requester: Optional[User],
    config: SonghiveConfig,
    base_url: str,
    feed_url: str,
    limit: int,
) -> Feed:
    """Build the feed of activities attached to an entity."""
    activities, _ = await activity_service.list_activities(
        session,
        entity_type=entity_type,
        entity_id=str(entity.id),  # type: ignore[attr-defined]
        user=requester,
        limit=limit,
    )
    items = await _activities_items(session, activities, config, base_url)
    plural = get_item_plural(entity_type) or f"{entity_type}s"
    link = f"{base_url}/{plural}/{entity.id}"  # type: ignore[attr-defined]
    title = _entity_title(entity)
    return Feed(
        id=link,
        title=f"{title} — activities" if title else "Activities",
        link=link,
        feed_url=feed_url,
        items=items,
        updated=_latest(items),
    )


async def artist_feed(
    session: AsyncSession,
    artist: Artist,
    requester: Optional[User],
    base_url: str,
    feed_url: str,
    limit: int,
) -> Feed:
    """Build the feed of an artist's latest releases.

    Items are the artist's newest albums and standalone tracks, merged by
    creation date. A track that belongs to an album visible to the requester
    is not listed separately — the album entry already represents it.
    """
    albums = await music_service.list_albums(
        session,
        artist_id=str(artist.id),
        user=requester,
        limit=limit,
        sort_by="created_at",
        sort_dir="desc",
    )

    album_access = (
        true() if requester is not None and requester.is_admin else _list_access_predicate(Album, requester, "album")
    )
    stmt = (
        select(Track)
        .options(*music_service._track_selectin_options({"artist", "album"}))
        .where(
            Track.artist_id == artist.id,
            or_(
                Track.album_id.is_(None),
                ~exists().where(Album.id == Track.album_id, album_access),
            ),
        )
        .order_by(Track.created_at.desc(), Track.id.desc())
        .limit(limit)
    )
    stmt = apply_access_filter(stmt, Track, requester, "track")
    tracks = list((await session.execute(stmt)).scalars().all())

    releases = sorted([*albums, *tracks], key=lambda e: e.created_at, reverse=True)[:limit]
    items = [album_item(e, base_url) if isinstance(e, Album) else track_item(e, base_url) for e in releases]
    link = f"{base_url}/artists/{artist.id}"
    return Feed(
        id=link,
        title=f"{artist.name} — releases",
        link=link,
        feed_url=feed_url,
        description=artist.bio,
        items=items,
        updated=_latest(items),
    )


async def collection_feed(
    session: AsyncSession,
    kind: str,
    entity: object,
    requester: Optional[User],
    base_url: str,
    feed_url: str,
    limit: int,
) -> Feed:
    """Build the feed of items most recently added to a playlist or library.

    Playlist feeds include podcast episodes alongside tracks — episodes keep
    their remote enclosure URL so readers stream from the source.
    """
    if kind == "playlist":
        entries_stmt = (
            select(PlaylistTrack)
            .options(
                selectinload(PlaylistTrack.track).options(*music_service._track_selectin_options({"artist", "album"})),
                selectinload(PlaylistTrack.episode).selectinload(PodcastEpisode.podcast),
            )
            .outerjoin(Track, PlaylistTrack.track_id == Track.id)
            .where(PlaylistTrack.playlist_id == entity.id)  # type: ignore[attr-defined]
            .order_by(PlaylistTrack.created_at.desc(), PlaylistTrack.id.desc())
        )
        if requester is None or not requester.is_admin:
            entries_stmt = entries_stmt.where(
                or_(Track.id.is_(None), _list_access_predicate(Track, requester, "track"))
            )
        rows = list((await session.execute(entries_stmt.limit(limit))).scalars().all())
        items = []
        for row in rows:
            if row.track is not None:
                items.append(track_item(row.track, base_url))
            elif row.episode is not None:
                items.append(episode_item(row.episode, base_url))
    else:
        stmt = (
            select(Track)
            .join(LibraryTrack, LibraryTrack.track_id == Track.id)
            .where(LibraryTrack.library_id == entity.id)  # type: ignore[attr-defined]
            .order_by(LibraryTrack.created_at.desc(), LibraryTrack.id.desc())
        )
        stmt = stmt.options(*music_service._track_selectin_options({"artist", "album"}))
        stmt = apply_access_filter(stmt, Track, requester, "track").limit(limit)
        tracks = list((await session.execute(stmt)).scalars().all())
        items = [track_item(t, base_url) for t in tracks]
    plural = get_item_plural(kind) or f"{kind}s"
    link = f"{base_url}/{plural}/{entity.id}"  # type: ignore[attr-defined]
    return Feed(
        id=link,
        title=_entity_title(entity),
        link=link,
        feed_url=feed_url,
        description=getattr(entity, "description", None),
        items=items,
        updated=_latest(items),
    )


async def tag_feed(
    session: AsyncSession,
    tag_name: str,
    requester: Optional[User],
    config: SonghiveConfig,
    base_url: str,
    feed_url: str,
    limit: int,
) -> Feed:
    """Build the feed of recent entities and activities carrying ``tag_name``."""
    tagged, _ = await tags_service.get_items_for_tag(
        session,
        tag_name=tag_name,
        user=requester,
        limit=limit,
    )

    activity_ids = [item.id for item in tagged if item.type == "activity"]
    entity_ids = [item for item in tagged if item.type != "activity"]

    items: List[FeedItem] = []
    if activity_ids:
        result = await session.execute(select(Activity).where(Activity.id.in_(activity_ids)))
        activities = [a for a in result.scalars().all() if a.deleted_at is None]
        items += await _activities_items(session, activities, config, base_url)

    for item in entity_ids:
        entity = await activity_service.resolve_entity(session, item.type, item.id)
        if entity is None:
            continue
        plural = get_item_plural(item.type) or f"{item.type}s"
        link = f"{base_url}/{plural}/{entity.id}"  # type: ignore[attr-defined]
        title = _entity_title(entity)
        items.append(
            FeedItem(
                id=link,
                title=title or item.type,
                link=link,
                published=entity.created_at,  # type: ignore[attr-defined]
                description=getattr(entity, "description", None) or getattr(entity, "bio", None),
                updated=entity.updated_at,  # type: ignore[attr-defined]
            )
        )

    items.sort(key=lambda i: i.published, reverse=True)
    items = items[:limit]
    link = f"{base_url}/tags/{quote(tag_name)}"
    return Feed(
        id=link,
        title=f"#{tag_name}",
        link=link,
        feed_url=feed_url,
        items=items,
        updated=_latest(items),
    )


async def genre_feed(
    session: AsyncSession,
    genre_name: str,
    requester: Optional[User],
    base_url: str,
    feed_url: str,
    limit: int,
) -> Feed:
    """Build the feed of the most recent tracks and albums in ``genre_name``."""
    tracks, _ = await music_service.list_tracks(
        session,
        genre=genre_name,
        user=requester,
        limit=limit,
        sort_by="created_at",
        sort_dir="desc",
        include={"artist", "album"},
    )
    albums = await music_service.list_albums(
        session,
        genre=genre_name,
        user=requester,
        limit=limit,
        sort_by="created_at",
        sort_dir="desc",
    )

    merged = sorted([*tracks, *albums], key=lambda e: e.created_at, reverse=True)[:limit]
    items = []
    for entity in merged:
        if isinstance(entity, Album):
            items.append(album_item(entity, base_url))
        elif isinstance(entity, Track):
            items.append(track_item(entity, base_url))
    link = f"{base_url}/genres/{quote(genre_name)}"
    return Feed(
        id=link,
        title=genre_name,
        link=link,
        feed_url=feed_url,
        items=items,
        updated=_latest(items),
    )
