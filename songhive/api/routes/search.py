"""Cross-entity autocomplete search endpoint."""

import asyncio
import logging
import re
from typing import List, Literal, Optional, cast
from urllib.parse import parse_qs, unquote, urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pubby import AttributionMismatch
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...config.schema import SonghiveConfig
from ...federation.actors import get_federation_storage
from ...federation.fetch import FetchError
from ...models.remote_object import RemoteObject
from ...models.user import User
from ...services import acl
from ...services import federation as federation_service
from ...services import moderation as moderation_service
from ...services import music, remote_content, sharing
from ...services.auth import get_user_by_username, list_public_users
from ...services.genres import list_genres
from ...services.storage import StorageService
from ...services.tags import list_tags
from ..deps import (
    _get_share_token,
    get_config,
    get_current_user,
    get_current_user_optional,
    get_db,
    get_storage_service,
)

logger = logging.getLogger(__name__)

SearchEntity = Literal[
    "users",
    "tracks",
    "albums",
    "artists",
    "playlists",
    "libraries",
    "tags",
    "genres",
    "remote",
]

_CANONICAL_ORDER: List[SearchEntity] = [
    "tracks",
    "albums",
    "artists",
    "playlists",
    "libraries",
    "users",
    "tags",
    "genres",
    "remote",
]

router = APIRouter(prefix="/search")


class SearchResultItem(BaseModel):
    """A single, normalized search result."""

    model_config = ConfigDict(from_attributes=True)

    type: str
    id: Optional[str] = None
    name: Optional[str] = None
    title: str
    subtitle: Optional[str] = None
    image_url: Optional[str] = None
    url: str


class SearchResultSection(BaseModel):
    """One entity section inside a search response."""

    model_config = ConfigDict(from_attributes=True)

    entity: SearchEntity
    total: int
    items: List[SearchResultItem]


class SearchResponse(BaseModel):
    """Aggregate search response for autocomplete widgets."""

    model_config = ConfigDict(from_attributes=True)

    query: str
    sections: List[SearchResultSection]
    # Whether this caller may run explicit remote lookups — the UI offers
    # the "search remotely" action only when true.
    remote_available: bool = False


def _parse_entities(value: Optional[str]) -> List[SearchEntity]:
    """Parse and validate the comma-separated ``entities`` allowlist."""
    if not value:
        return list(_CANONICAL_ORDER)  # type: ignore
    requested = {item.strip().lower() for item in value.split(",")}
    return [entity for entity in _CANONICAL_ORDER if entity in requested]


def _local_user_item(u: User) -> SearchResultItem:
    """Serialize a local user as a search result pointing at its SPA route."""
    return SearchResultItem(
        type="user",
        id=u.username,
        name=u.username,
        title=u.display_name or u.username,
        subtitle=u.username,
        image_url=u.avatar_url,
        url=f"/@{u.username}",
    )


async def _user_section(
    db: AsyncSession,
    term: str,
    limit: int,
    user: Optional[User] = None,
    config: Optional[SonghiveConfig] = None,
    remote_users: bool = False,
    **_,
) -> SearchResultSection:
    # A ``user@domain`` term searches local usernames on the ``user`` part;
    # the domain fragment can only narrow remote actor matches.
    local_term = term.split("@", 1)[0] or term
    users, total = await list_public_users(db, q=local_term, user=user, limit=limit, offset=0)
    items = [_local_user_item(u) for u in users]

    if remote_users and config is not None and config.federation.enabled and config.federation.instance_domain:
        try:
            # Refresh the policy snapshot so defederated domains are excluded.
            await moderation_service.load_instance_policies(db)
            fed_storage = await asyncio.to_thread(get_federation_storage, config.database)
            remote_matches = await asyncio.to_thread(federation_service.search_remote_actors, fed_storage, term, config)
        except Exception:
            # The users section must not fail when the federation storage is
            # unavailable or its tables do not exist yet.
            logger.warning("Remote actor search failed for %r", term, exc_info=True)
        else:
            total += len(remote_matches)
            items.extend(
                SearchResultItem(
                    type="user",
                    id=match.actor_url,
                    name=match.handle,
                    title=match.display_name or match.handle,
                    subtitle=f"@{match.handle}",
                    image_url=match.avatar_url,
                    url=match.profile_url or match.actor_url,
                )
                for match in remote_matches[:limit]
            )

    return SearchResultSection(entity="users", total=total, items=items)


async def _track_item(
    track,
    db: AsyncSession,
    storage: StorageService,
    user: Optional[User],
    policy_cache: Optional[dict] = None,
) -> SearchResultItem:
    """Serialize one track row as a search result."""
    from ..responses import build_track_summary

    summary = await build_track_summary(
        track, storage, user=user, session=db, policy_cache=policy_cache if policy_cache is not None else {}
    )
    artist_name = summary.artist.name if summary and summary.artist else None
    album_title = summary.album.title if summary and summary.album else None
    subtitle: Optional[str] = None
    if artist_name and album_title:
        subtitle = f"{artist_name} · {album_title}"
    elif artist_name:
        subtitle = artist_name
    return SearchResultItem(
        type="track",
        id=str(track.id),
        name=track.title,
        title=track.title,
        subtitle=subtitle,
        image_url=summary.image_url if summary else None,
        url=f"/tracks/{track.id}",
    )


async def _track_section(
    db: AsyncSession,
    storage: StorageService,
    term: str,
    limit: int,
    user: Optional[User],
    **_kwargs,
) -> SearchResultSection:
    total = await music.count_tracks(db, query=term, user=user)
    rows, _ = await music.list_tracks(
        db,
        query=term,
        user=user,
        limit=limit,
        offset=0,
        include={"artist", "album"},
    )
    items: List[SearchResultItem] = []
    policy_cache: dict = {}
    for track in rows:
        items.append(await _track_item(track, db, storage, user, policy_cache))
    return SearchResultSection(entity="tracks", total=total, items=items)


async def _album_item(album, storage: StorageService) -> SearchResultItem:
    """Serialize one album row as a search result."""
    from ..responses import build_album_summary

    summary = await build_album_summary(album, storage)
    return SearchResultItem(
        type="album",
        id=str(album.id),
        name=album.title,
        title=album.title,
        subtitle=summary.artist.name if summary and summary.artist else None,
        image_url=summary.cover_url if summary else None,
        url=f"/albums/{album.id}",
    )


async def _album_section(
    db: AsyncSession,
    storage: StorageService,
    term: str,
    limit: int,
    user: Optional[User],
    **_,
) -> SearchResultSection:
    total = await music.count_albums(db, query=term, user=user)
    rows = await music.list_albums(
        db,
        query=term,
        user=user,
        limit=limit,
        offset=0,
        include={"artist"},
    )
    items: List[SearchResultItem] = []
    for album in rows:
        items.append(await _album_item(album, storage))
    return SearchResultSection(entity="albums", total=total, items=items)


async def _artist_item(artist, storage: StorageService) -> SearchResultItem:
    """Serialize one artist row as a search result."""
    from ..responses import build_artist_summary

    summary = await build_artist_summary(artist, storage)
    return SearchResultItem(
        type="artist",
        id=str(artist.id),
        name=artist.name,
        title=artist.name,
        subtitle=None,
        image_url=summary.image_url if summary else None,
        url=f"/artists/{artist.id}",
    )


async def _artist_section(
    db: AsyncSession,
    storage: StorageService,
    term: str,
    limit: int,
    user: Optional[User],
    **_,
) -> SearchResultSection:
    total = await music.count_artists(db, query=term, user=user)
    rows = await music.list_artists(db, query=term, user=user, limit=limit, offset=0)
    items: List[SearchResultItem] = []
    for artist in rows:
        items.append(await _artist_item(artist, storage))
    return SearchResultSection(entity="artists", total=total, items=items)


async def _playlist_item(playlist, storage: StorageService) -> SearchResultItem:
    """Serialize one playlist row as a search result."""
    image_url = None
    if playlist.image_file_id and playlist.image_file:
        image_url = await storage.get_url(playlist.image_file)
    return SearchResultItem(
        type="playlist",
        id=str(playlist.id),
        name=playlist.name,
        title=playlist.name,
        subtitle=playlist.description,
        image_url=image_url,
        url=f"/playlists/{playlist.id}",
    )


async def _playlist_section(
    db: AsyncSession,
    storage: StorageService,
    term: str,
    limit: int,
    user: Optional[User],
    **_,
) -> SearchResultSection:
    total = await music.count_playlists(db, query=term, user=user)
    rows = await music.list_playlists(
        db,
        query=term,
        user=user,
        limit=limit,
        offset=0,
    )
    items: List[SearchResultItem] = []
    for playlist in rows:
        items.append(await _playlist_item(playlist, storage))
    return SearchResultSection(entity="playlists", total=total, items=items)


async def _library_item(library, storage: StorageService) -> SearchResultItem:
    """Serialize one library row as a search result."""
    image_url = None
    if library.image_file_id and library.image_file:
        image_url = await storage.get_url(library.image_file)
    return SearchResultItem(
        type="library",
        id=str(library.id),
        name=library.name,
        title=library.name,
        subtitle=library.description,
        image_url=image_url,
        url=f"/libraries/{library.id}",
    )


async def _library_section(
    db: AsyncSession,
    storage: StorageService,
    term: str,
    limit: int,
    user: Optional[User],
    **_,
) -> SearchResultSection:
    total = await music.count_libraries(db, query=term, user=user)
    rows = await music.list_libraries(
        db,
        query=term,
        user=user,
        limit=limit,
        offset=0,
    )
    items: List[SearchResultItem] = []
    for library in rows:
        items.append(await _library_item(library, storage))
    return SearchResultSection(entity="libraries", total=total, items=items)


async def _tag_section(
    db: AsyncSession,
    storage: StorageService,
    term: str,
    limit: int,
    user: Optional[User],
    sort_by: str = "name",
    sort_dir: str = "asc",
    **_,
) -> SearchResultSection:
    summaries, total = await list_tags(
        db,
        user=user,
        query=term or None,
        limit=limit,
        offset=0,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
    return SearchResultSection(
        entity="tags",
        total=total,
        items=[
            SearchResultItem(
                type="tag",
                id=summary.name,
                name=summary.name,
                title=summary.name,
                subtitle=f"{summary.item_count} items",
                image_url=None,
                url=f"/tags/{summary.name}",
            )
            for summary in summaries
        ],
    )


async def _genre_section(
    db: AsyncSession,
    storage: StorageService,
    term: str,
    limit: int,
    user: Optional[User],
    **_,
) -> SearchResultSection:
    summaries, total = await list_genres(
        db,
        user=user,
        query=term,
        limit=limit,
        offset=0,
    )
    return SearchResultSection(
        entity="genres",
        total=total,
        items=[
            SearchResultItem(
                type="genre",
                id=summary.name,
                name=summary.name,
                title=summary.name,
                subtitle=f"{summary.item_count} items",
                image_url=None,
                url=f"/genres/{summary.name}",
            )
            for summary in summaries
        ],
    )


async def _remote_section(
    db: AsyncSession,
    term: str,
    limit: int,
    user: Optional[User] = None,
    config: Optional[SonghiveConfig] = None,
    **_,
) -> SearchResultSection:
    """
    Match cached remote objects — never fetches remotely.

    Rows surface only when the ``remote_search_access`` policy lets this
    caller perform remote lookups; items carry internal ``/remote/…`` and
    ``/activities/@user@domain/…`` routes, never the remote URL itself.
    """
    if config is None or not remote_content.remote_lookup_allowed(user, config):
        return SearchResultSection(entity="remote", total=0, items=[])

    rows = await remote_content.search_cached_remote_objects(db, config, term, user=user, limit=limit)
    actor_handles = await remote_content.resolve_actor_handle_map(config, [row.actor_url for row in rows])
    items = []
    for row in rows:
        actor_handle = actor_handles.get(row.actor_url) or remote_content.actor_handle_from_url(row.actor_url)
        items.append(
            SearchResultItem(
                type=row.resource_type or "remote_object",
                id=str(row.id),
                name=row.name or row.domain,
                title=row.name or row.domain,
                subtitle=f"{actor_handle} · {row.domain}",
                image_url=row.image_url,
                url=remote_content.remote_object_page_url(row, actor_handle=actor_handles.get(row.actor_url)),
            )
        )
    return SearchResultSection(entity="remote", total=len(items), items=items)


# ---------------------------------------------------------------------------
# Direct URL lookups
# ---------------------------------------------------------------------------
#
# A pasted URL is a direct lookup, not a text query: it references exactly one
# entity, so the response carries only that entity's section — populated when
# the caller may see it, empty otherwise. Local URLs are resolved from the
# database under the caller's ACL; a ``?token=`` query parameter and the
# ``/{api/v1/}share/{token}`` short links carry their own access grant. Remote
# URLs are dereferenced through the same bounded, policy-gated path as
# ``/remote/lookup`` — a remote object does not need to be federated already.

_LOCAL_SHARE_RE = re.compile(r"^/(?:api/v1/)?share/(?P<token>[^/?#]+)/?$")
_LOCAL_TAGGED_RE = re.compile(r"^/(?:api/v1/)?(?P<kind>tags|genres)/(?P<name>[^/?#]+)/?$")
# Local SPA routes for cached remote objects — ``/remote/{type}/{id}`` and
# ``/activities/@{handle}/{id}`` as produced by ``remote_object_page_url``.
_LOCAL_REMOTE_RE = re.compile(r"^/(?:remote/[^/?#]+|activities/@[^/?#]+)/(?P<rid>[^/?#]+)/?$")


async def _local_entity_item(
    db: AsyncSession,
    storage: StorageService,
    item_type: str,
    item_id: str,
    user: Optional[User],
) -> Optional[SearchResultItem]:
    """Build the result item for a directly addressed local entity."""
    if item_type == "track":
        track = await music.get_track(db, item_id, include={"artist", "album"})
        return await _track_item(track, db, storage, user) if track is not None else None
    if item_type == "album":
        album = await music.get_album(db, item_id, include={"artist"})
        return await _album_item(album, storage) if album is not None else None
    if item_type == "artist":
        artist = await music.get_artist(db, item_id)
        return await _artist_item(artist, storage) if artist is not None else None
    if item_type == "playlist":
        playlist = await music.get_playlist(db, item_id)
        return await _playlist_item(playlist, storage) if playlist is not None else None
    if item_type == "library":
        library = await music.get_library(db, item_id)
        return await _library_item(library, storage) if library is not None else None
    return None


async def _entity_url_section(
    db: AsyncSession,
    storage: StorageService,
    plural: str,
    item_id: str,
    user: Optional[User],
    share_token: Optional[str] = None,
) -> SearchResultSection:
    """
    Build the single-item section a local resource URL (or share token)
    addresses.

    ``can_access`` doubles as the existence check: missing rows and
    ACL-denied items both produce the empty section.
    """
    entity = cast(SearchEntity, plural)
    item_type = remote_content._PLURAL_TO_RESOURCE_KIND.get(plural)
    item: Optional[SearchResultItem] = None
    if item_type is not None and await acl.can_access(db, user, item_type, item_id, share_token=share_token):
        item = await _local_entity_item(db, storage, item_type, item_id, user)
        if item is not None and share_token and not await acl.can_access(db, user, item_type, item_id):
            # The share token is the only grant — link the share page, which
            # establishes the ``share_token`` cookie the entity API checks.
            item.url = f"/share/{share_token}"
    return SearchResultSection(entity=entity, total=1 if item else 0, items=[item] if item else [])


async def _share_url_sections(
    db: AsyncSession,
    storage: StorageService,
    raw_token: str,
    user: Optional[User],
) -> List[SearchResultSection]:
    """
    Resolve a share short link to the item it grants access to.

    The valid token is itself the access grant; when the caller could not
    otherwise see the entity the result links back to the share page (which
    establishes the ``share_token`` cookie) instead of the entity route.
    """
    token = await sharing.get_valid_share_token(db, raw_token)
    if token is None:
        return []
    plural = acl.get_item_plural(token.item_type)
    if plural is None or plural not in ("tracks", "albums", "artists", "playlists", "libraries"):
        # Radios and files have no search section of their own.
        return []
    section = await _entity_url_section(db, storage, plural, token.item_id, user, share_token=raw_token)
    return [section]


async def _user_url_section(db: AsyncSession, username: str, user: Optional[User]) -> SearchResultSection:
    """Build the users section for a local profile URL (``/@name``)."""
    row = await get_user_by_username(db, username)
    item = _local_user_item(row) if row is not None and await acl.can_access(db, user, "user", str(row.id)) else None
    return SearchResultSection(entity="users", total=1 if item else 0, items=[item] if item else [])


async def _tagged_url_section(db: AsyncSession, kind: str, name: str, user: Optional[User]) -> SearchResultSection:
    """Build the tags/genres section for a ``/tags/{name}``-style URL."""
    entity: SearchEntity = "tags" if kind == "tags" else "genres"
    summaries, _ = await (list_tags if kind == "tags" else list_genres)(db, user=user, query=name, limit=10, offset=0)
    # ``query`` is an ilike filter — keep only the exact, case-insensitive hit.
    match = next((s for s in summaries if s.name.lower() == name.lower()), None)
    items = (
        [
            SearchResultItem(
                type=kind[:-1],
                id=match.name,
                name=match.name,
                title=match.name,
                subtitle=f"{match.item_count} items",
                image_url=None,
                url=f"/{kind}/{match.name}",
            )
        ]
        if match is not None
        else []
    )
    return SearchResultSection(entity=entity, total=len(items), items=items)


async def _local_remote_url_section(
    db: AsyncSession, config: SonghiveConfig, rid: str, user: Optional[User]
) -> SearchResultSection:
    """Build the remote section for a local ``/remote/…`` page URL."""
    item: Optional[SearchResultItem] = None
    if await acl.can_access(db, user, "remote", rid):
        row = await db.get(RemoteObject, rid)
        if row is not None and row.unavailable_at is None:
            item = await _remote_object_item(db, config, row)
    return SearchResultSection(entity="remote", total=1 if item else 0, items=[item] if item else [])


async def _local_url_sections(
    db: AsyncSession,
    storage: StorageService,
    url: str,
    user: Optional[User],
    config: SonghiveConfig,
    request_token: Optional[str] = None,
) -> List[SearchResultSection]:
    """
    Map a local-instance URL to the single entity section it references.

    Resource pages (``/tracks/{id}`` and friends, with or without the
    ``/api/v1`` prefix), share links, profile pages, tag/genre pages and
    object permalinks are recognized; anything else is not an entity URL and
    yields no results. A ``?token=`` query parameter acts as the share-token
    grant for non-public items; ``request_token`` carries the caller's
    ambient share grant (``X-Share-Token`` header or ``share_token``
    cookie).
    """
    parsed = urlparse(url)
    path = parsed.path or "/"
    url_token = next(iter(parse_qs(parsed.query).get("token") or []), None)
    share_token = url_token or request_token

    match = _LOCAL_SHARE_RE.match(path)
    if match:
        return await _share_url_sections(db, storage, unquote(match.group("token")), user)

    match = remote_content._SONGHIVE_RESOURCE_RE.match(path)
    if match:
        return [
            await _entity_url_section(db, storage, match.group("kind"), unquote(match.group("rid")), user, share_token)
        ]

    match = _LOCAL_TAGGED_RE.match(path)
    if match:
        return [await _tagged_url_section(db, match.group("kind"), unquote(match.group("name")), user)]

    match = remote_content._LOCAL_PERMALINK_RE.match(path)
    if match:
        # ``/users/{u}/objects|statuses/{id}`` permalinks resolve to the
        # underlying track or activity page.
        resolved = await remote_content._resolve_local_object_page(db, match.group("object_id"))
        if resolved is not None:
            resolved_match = remote_content._SONGHIVE_RESOURCE_RE.match(urlparse(resolved).path)
            if resolved_match:
                return [
                    await _entity_url_section(
                        db,
                        storage,
                        resolved_match.group("kind"),
                        resolved_match.group("rid"),
                        user,
                        share_token,
                    )
                ]
        return []

    match = remote_content._ACTOR_PATH_RE.match(path)
    if match:
        name = match.group("name") or match.group("name2") or ""
        return [await _user_url_section(db, unquote(name).lstrip("@"), user)]

    match = _LOCAL_REMOTE_RE.match(path)
    if match:
        # ``/remote/{type}/{id}`` / ``/activities/@{handle}/{id}`` SPA pages
        # reference a cached remote object by its local row id.
        return [await _local_remote_url_section(db, config, unquote(match.group("rid")), user)]

    return []


def _remote_actor_item(actor: remote_content.RemoteActorResult) -> SearchResultItem:
    """Serialize a resolved remote actor, linking the internal profile route."""
    return SearchResultItem(
        type="user",
        id=actor.actor_url,
        name=actor.handle,
        title=actor.display_name or actor.handle,
        subtitle=f"@{actor.handle}",
        image_url=actor.avatar_url,
        url=f"/@{actor.handle}",
    )


async def _remote_object_item(db: AsyncSession, config: SonghiveConfig, row) -> SearchResultItem:
    """Serialize a dereferenced remote object, linking its internal page."""
    actor_handles = await remote_content.resolve_actor_handle_map(config, [row.actor_url])
    actor_handle = actor_handles.get(row.actor_url) or remote_content.actor_handle_from_url(row.actor_url)
    return SearchResultItem(
        type=row.resource_type or "remote_object",
        id=str(row.id),
        name=remote_content.remote_object_display_name(row) or row.domain,
        title=remote_content.remote_object_display_name(row) or row.domain,
        subtitle=f"{actor_handle} · {row.domain}",
        image_url=row.image_url,
        url=remote_content.remote_object_page_url(row, actor_handle=actor_handles.get(row.actor_url)),
    )


async def _remote_url_sections(
    db: AsyncSession,
    target: remote_content.RemoteTarget,
    url: str,
    user: Optional[User],
    config: SonghiveConfig,
) -> List[SearchResultSection]:
    """
    Resolve a remote URL to the single section it references.

    Mirrors ``/remote/lookup``: actor-shaped URLs resolve through WebFinger +
    the actor cache first (falling back to object dereference when the
    document is not an actor), everything else dereferences into the
    ``remote_objects`` cache. The fetch is bounded and runs under the
    ``remote_search_access`` policy; a denied caller or a failed lookup
    produces the empty ``remote`` section rather than an error.
    """
    empty = SearchResultSection(entity="remote", total=0, items=[])
    if not remote_content.remote_lookup_allowed(user, config):
        return [empty]

    try:
        if target.kind == remote_content.RemoteTargetKind.ACTOR_URL:
            try:
                actor = await remote_content.lookup_remote_actor(db, config, url)
            except FetchError as exc:
                # A non-actor document may still be a content object.
                if exc.status_code != 422:
                    raise
            else:
                return [SearchResultSection(entity="users", total=1, items=[_remote_actor_item(actor)])]

        result = await remote_content.dereference_remote_object(db, config, url)
        await db.commit()
    except (FetchError, HTTPException, AttributionMismatch):
        logger.info("Remote URL lookup failed for %r", url, exc_info=True)
        await db.rollback()
        return [empty]
    except Exception:
        logger.warning("Remote URL lookup errored for %r", url, exc_info=True)
        await db.rollback()
        return [empty]

    row = result.remote_object
    if result.status == "gone" or not await acl.can_access(db, user, "remote", str(row.id)):
        return [empty]
    item = await _remote_object_item(db, config, row)
    return [SearchResultSection(entity="remote", total=1, items=[item])]


async def _url_lookup_sections(
    db: AsyncSession,
    storage: StorageService,
    request: Request,
    target: remote_content.RemoteTarget,
    user: Optional[User],
    config: SonghiveConfig,
    request_token: Optional[str] = None,
) -> List[SearchResultSection]:
    """
    Dispatch a URL-shaped query to the direct-lookup resolvers.

    The URL is local when its host matches the configured instance domain or
    the host the request itself was made on; everything else goes through the
    remote dereference path.
    """
    url = target.url or target.raw
    parsed = urlparse(url)
    request_host = (request.url.hostname or "").lower()
    is_local = target.kind == remote_content.RemoteTargetKind.LOCAL or (
        bool(request_host) and (parsed.hostname or "").lower() == request_host
    )
    if is_local:
        return await _local_url_sections(db, storage, url, user, config, request_token)
    return await _remote_url_sections(db, target, url, user, config)


_SECTION_FETCHERS = {
    "users": _user_section,
    "tracks": _track_section,
    "albums": _album_section,
    "artists": _artist_section,
    "playlists": _playlist_section,
    "libraries": _library_section,
    "tags": _tag_section,
    "genres": _genre_section,
    "remote": _remote_section,
}


@router.get("/", response_model=SearchResponse)
async def search(
    request: Request,
    q: Optional[str] = Query(None, description="Search term"),
    entities: Optional[str] = Query(None, description="Comma-separated entity allowlist"),
    limit: int = Query(5, ge=1, le=10, description="Per-section result limit"),
    remote_users: bool = Query(
        False,
        description="Also match cached remote actors (followers, actor cache) in the users section",
    ),
    include_remote: bool = Query(
        True,
        description="Include the cached remote objects section (never triggers remote fetches)",
    ),
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    config: SonghiveConfig = Depends(get_config),
    share_token: Optional[str] = Depends(_get_share_token),
):
    """
    Return a grouped, ACL-respecting preview for the requested entities.

    A ``q`` starting with ``#`` is a hashtag lookup: the prefix is stripped
    and only the tags section is returned, sorted by popularity
    (``item_count`` descending). A bare ``#`` lists the most used tags.

    An http(s) ``q`` is a direct URL lookup, not a text search: the URL
    references exactly one entity, so the response carries only that
    entity's section — populated when the caller may see it, empty
    otherwise. Local-instance URLs (matching the configured instance domain
    or the host the request was made on) resolve from the database under
    the caller's ACL, honoring ``?token=`` share grants and
    ``/share/{token}`` links; remote URLs are dereferenced through the same
    bounded path as ``/remote/lookup``.

    With ``remote_users`` the users section additionally lists remote
    ActivityPub actors cached on the instance — followers and resolved actor
    documents — so mention completion can offer ``user@domain`` handles.

    The ``remote`` section matches only *cached* remote objects — it never
    performs network fetches, and it is empty when the
    ``remote_search_access`` policy denies remote lookups to this caller.
    ``remote_available`` reports whether the caller may run explicit remote
    lookups at all (via ``/remote/lookup``).
    """
    term = (q or "").strip()
    hashtag = term.startswith("#")
    if hashtag:
        section = await _tag_section(
            db=db,
            storage=storage,
            term=term.lstrip("#").strip(),
            limit=limit,
            user=user,
            sort_by="item_count",
            sort_dir="desc",
        )
        return SearchResponse(query=term, sections=[section])

    if not term:
        return SearchResponse(query=term, sections=[])

    # An http(s) URL is a direct entity lookup — never a text search. Only
    # URL-shaped targets dispatch here; handles (``@user@domain``), text
    # containing a URL, and unsupported input keep the regular per-entity
    # search path.
    url_target = remote_content.parse_remote_target(term, config)
    if url_target.url is not None and not re.search(r"\s", term):
        url_sections = await _url_lookup_sections(
            db, storage, request, url_target, user, config, request_token=share_token
        )
        return SearchResponse(
            query=term,
            sections=url_sections,
            remote_available=remote_content.remote_lookup_allowed(user, config),
        )

    selected = _parse_entities(entities)
    if not include_remote:
        selected = [entity for entity in selected if entity != "remote"]
    sections: List[SearchResultSection] = []
    for entity in selected:
        sections.append(
            await _SECTION_FETCHERS[entity](  # type: ignore
                db=db,
                storage=storage,
                term=term,
                limit=limit,
                user=user,
                config=config,
                remote_users=remote_users,
            )
        )
    return SearchResponse(
        query=term,
        sections=sections,
        remote_available=remote_content.remote_lookup_allowed(user, config),
    )


# --------------------------------------------------------------------------
# External-provider search
# --------------------------------------------------------------------------
#
# Provider results are transient metadata only — nothing is persisted until
# the caller explicitly imports an entity via
# ``POST /api/v1/external-libraries/{id}/import``. This endpoint is separate
# from the local search so provider latency can never delay local results.


class ProviderSearchResultItem(BaseModel):
    """One transient provider-search result."""

    model_config = ConfigDict(from_attributes=True)

    kind: str
    provider_key: str
    title: str
    subtitle: Optional[str] = None
    image_url: Optional[str] = None
    external_url: Optional[str] = None


class ProviderSearchGroup(BaseModel):
    """Results from one connected external library."""

    model_config = ConfigDict(from_attributes=True)

    external_library_id: str
    provider_type: str
    library_name: Optional[str] = None
    results: List[ProviderSearchResultItem]
    error: Optional[str] = None


class ProviderSearchResponse(BaseModel):
    """Provider-side search grouped by connected library."""

    model_config = ConfigDict(from_attributes=True)

    query: str
    providers: List[ProviderSearchGroup]


_PROVIDER_SEARCH_TIMEOUT = 3.0
# URL lookups are single-entity resolutions the caller explicitly asked
# for; providers may need several upstream round-trips (or a yt-dlp
# extraction fallback) to answer them, so they get a wider budget.
_PROVIDER_SEARCH_URL_TIMEOUT = 12.0


@router.get("/providers", response_model=ProviderSearchResponse)
async def search_providers(
    q: Optional[str] = Query(None, description="Search term"),
    limit: int = Query(10, ge=1, le=50, description="Per-library result limit"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Fan out the query to the caller's own connected external libraries.

    Each library is searched with an individual timeout; a slow or failing
    provider surfaces as ``error`` on its group and never fails the whole
    request. Results are transient — no rows are created.
    """
    from ...external.base import ExternalLibraryAdapter
    from ...external.registry import get_external_adapter
    from ...models.external_library import ExternalLibrary
    from .external_libraries import _decrypt_external_config, _sanitize_error

    term = (q or "").strip()
    if not term:
        return ProviderSearchResponse(query=term, providers=[])

    timeout = _PROVIDER_SEARCH_URL_TIMEOUT if re.match(r"^https?://\S+$", term) else _PROVIDER_SEARCH_TIMEOUT

    rows = (
        (
            await db.execute(
                select(ExternalLibrary)
                .where(
                    ExternalLibrary.enabled.is_(True),
                    ExternalLibrary.created_by_id == str(user.id),
                )
                .options(selectinload(ExternalLibrary.library))
            )
        )
        .scalars()
        .all()
    )

    searchable = []
    for row in rows:
        try:
            adapter_cls = get_external_adapter(row.provider_type)
        except KeyError:
            continue
        if adapter_cls.search is not ExternalLibraryAdapter.search:
            searchable.append((row, adapter_cls))

    async def _fan_out(row, adapter_cls) -> ProviderSearchGroup:
        library_name = row.name or (row.library.name if row.library is not None else None)
        group = ProviderSearchGroup(
            external_library_id=str(row.id),
            provider_type=row.provider_type,
            library_name=library_name,
            results=[],
        )
        try:
            config = _decrypt_external_config(row.config)
            adapter = adapter_cls()
            raw = await asyncio.wait_for(
                adapter.search(config, term, limit=limit),
                timeout=timeout,
            )
            for item in raw or []:
                if not isinstance(item, dict) or not item.get("provider_key"):
                    continue
                group.results.append(
                    ProviderSearchResultItem(
                        kind=str(item.get("kind") or "track"),
                        provider_key=str(item["provider_key"]),
                        title=str(item.get("title") or item["provider_key"]),
                        subtitle=item.get("subtitle"),
                        image_url=item.get("image_url"),
                        external_url=item.get("external_url"),
                    )
                )
        except asyncio.TimeoutError:
            group.error = "provider search timed out"
        except Exception as exc:
            logger.warning(
                "Provider search failed for library %s (%s)",
                row.id,
                row.provider_type,
                exc_info=True,
            )
            group.error = _sanitize_error(exc)
        return group

    groups = await asyncio.gather(*[_fan_out(row, cls) for row, cls in searchable])
    return ProviderSearchResponse(query=term, providers=list(groups))
