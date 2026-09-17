"""Cross-entity autocomplete search endpoint."""

import asyncio
import logging
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.schema import SonghiveConfig
from ...federation.actors import get_federation_storage
from ...models.user import User
from ...services import federation as federation_service
from ...services import music, remote_content
from ...services.auth import list_public_users
from ...services.genres import list_genres
from ...services.storage import StorageService
from ...services.tags import list_tags
from ..deps import get_config, get_current_user_optional, get_db, get_storage_service

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
    items = [
        SearchResultItem(
            type="user",
            id=u.username,
            name=u.username,
            title=u.display_name or u.username,
            subtitle=u.username,
            image_url=u.avatar_url,
            url=f"/@{u.username}",
        )
        for u in users
    ]

    if remote_users and config is not None and config.federation.enabled and config.federation.instance_domain:
        try:
            fed_storage = await asyncio.to_thread(get_federation_storage, config.database.url)
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


async def _track_section(
    db: AsyncSession,
    storage: StorageService,
    term: str,
    limit: int,
    user: Optional[User],
    **_kwargs,
) -> SearchResultSection:
    from ..responses import build_track_summary

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
    for track in rows:
        summary = await build_track_summary(track, storage)
        artist_name = summary.artist.name if summary and summary.artist else None
        album_title = summary.album.title if summary and summary.album else None
        if artist_name and album_title:
            subtitle = f"{artist_name} · {album_title}"
        elif artist_name:
            subtitle = artist_name
        else:
            subtitle = None
        items.append(
            SearchResultItem(
                type="track",
                id=str(track.id),
                name=track.title,
                title=track.title,
                subtitle=subtitle,
                image_url=summary.image_url if summary else None,
                url=f"/tracks/{track.id}",
            )
        )
    return SearchResultSection(entity="tracks", total=total, items=items)


async def _album_section(
    db: AsyncSession,
    storage: StorageService,
    term: str,
    limit: int,
    user: Optional[User],
    **_,
) -> SearchResultSection:
    from ..responses import build_album_summary

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
        summary = await build_album_summary(album, storage)
        items.append(
            SearchResultItem(
                type="album",
                id=str(album.id),
                name=album.title,
                title=album.title,
                subtitle=summary.artist.name if summary and summary.artist else None,
                image_url=summary.cover_url if summary else None,
                url=f"/albums/{album.id}",
            )
        )
    return SearchResultSection(entity="albums", total=total, items=items)


async def _artist_section(
    db: AsyncSession,
    storage: StorageService,
    term: str,
    limit: int,
    user: Optional[User],
    **_,
) -> SearchResultSection:
    from ..responses import build_artist_summary

    total = await music.count_artists(db, query=term, user=user)
    rows = await music.list_artists(db, query=term, user=user, limit=limit, offset=0)
    items: List[SearchResultItem] = []
    for artist in rows:
        summary = await build_artist_summary(artist, storage)
        items.append(
            SearchResultItem(
                type="artist",
                id=str(artist.id),
                name=artist.name,
                title=artist.name,
                subtitle=None,
                image_url=summary.image_url if summary else None,
                url=f"/artists/{artist.id}",
            )
        )
    return SearchResultSection(entity="artists", total=total, items=items)


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
        image_url = None
        if playlist.image_file_id and playlist.image_file:
            image_url = await storage.get_url(playlist.image_file)
        items.append(
            SearchResultItem(
                type="playlist",
                id=str(playlist.id),
                name=playlist.name,
                title=playlist.name,
                subtitle=playlist.description,
                image_url=image_url,
                url=f"/playlists/{playlist.id}",
            )
        )
    return SearchResultSection(entity="playlists", total=total, items=items)


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
        image_url = None
        if library.image_file_id and library.image_file:
            image_url = await storage.get_url(library.image_file)
        items.append(
            SearchResultItem(
                type="library",
                id=str(library.id),
                name=library.name,
                title=library.name,
                subtitle=library.description,
                image_url=image_url,
                url=f"/libraries/{library.id}",
            )
        )
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
    items = [
        SearchResultItem(
            type=row.resource_type or "remote_object",
            id=str(row.id),
            name=row.name or row.domain,
            title=row.name or row.domain,
            subtitle=f"{remote_content.actor_handle_from_url(row.actor_url)} · {row.domain}",
            image_url=row.image_url,
            url=remote_content.remote_object_page_url(row),
        )
        for row in rows
    ]
    return SearchResultSection(entity="remote", total=len(items), items=items)


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
):
    """
    Return a grouped, ACL-respecting preview for the requested entities.

    A ``q`` starting with ``#`` is a hashtag lookup: the prefix is stripped
    and only the tags section is returned, sorted by popularity
    (``item_count`` descending). A bare ``#`` lists the most used tags.

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
