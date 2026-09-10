"""Cross-entity autocomplete search endpoint."""

from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.user import User
from ...services import music
from ...services.auth import list_public_users
from ...services.genres import list_genres
from ...services.storage import StorageService
from ...services.tags import list_tags
from ..deps import get_current_user_optional, get_db, get_storage_service

SearchEntity = Literal[
    "users",
    "tracks",
    "albums",
    "artists",
    "playlists",
    "libraries",
    "tags",
    "genres",
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
    **_,
) -> SearchResultSection:
    users, total = await list_public_users(db, q=term, limit=limit, offset=0)
    return SearchResultSection(
        entity="users",
        total=total,
        items=[
            SearchResultItem(
                type="user",
                id=user.username,
                name=user.username,
                title=user.display_name or user.username,
                subtitle=user.username,
                image_url=user.avatar_url,
                url=f"/@{user.username}",
            )
            for user in users
        ],
    )


async def _track_section(
    db: AsyncSession,
    storage: StorageService,
    term: str,
    limit: int,
    user: Optional[User],
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
) -> SearchResultSection:
    from ..responses import build_artist_summary

    total = await music.count_artists(db, query=term)
    rows = await music.list_artists(db, query=term, limit=limit, offset=0)
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
) -> SearchResultSection:
    summaries, total = await list_tags(
        db,
        user=user,
        query=term,
        limit=limit,
        offset=0,
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


_SECTION_FETCHERS = {
    "users": _user_section,
    "tracks": _track_section,
    "albums": _album_section,
    "artists": _artist_section,
    "playlists": _playlist_section,
    "libraries": _library_section,
    "tags": _tag_section,
    "genres": _genre_section,
}


@router.get("/", response_model=SearchResponse)
async def search(
    q: Optional[str] = Query(None, description="Search term"),
    entities: Optional[str] = Query(None, description="Comma-separated entity allowlist"),
    limit: int = Query(5, ge=1, le=10, description="Per-section result limit"),
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
):
    """Return a grouped, ACL-respecting preview for the requested entities."""
    term = (q or "").strip()
    if not term:
        return SearchResponse(query=term, sections=[])

    selected = _parse_entities(entities)
    sections: List[SearchResultSection] = []
    for entity in selected:
        sections.append(await _SECTION_FETCHERS[entity](db=db, storage=storage, term=term, limit=limit, user=user))
    return SearchResponse(query=term, sections=sections)
