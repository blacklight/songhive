"""
Music service: CRUD operations for artists, albums, tracks, playlists,
libraries, and radios.
"""

import contextlib
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, cast

from sqlalchemy import Select, and_, exists, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models.album import Album
from ..models.artist import Artist
from ..models.external_library import ExternalLibrary
from ..models.external_track import ExternalTrack
from ..models.favorite import Favorite
from ..models.genre import Genre, GenreAlbum, GenreTrack
from ..models.library import Library
from ..models.library_track import LibraryTrack
from ..models.playlist import Playlist, PlaylistTrack
from ..models.podcast import Podcast, PodcastEpisode
from ..models.radio import Radio
from ..models.remote_object import RemoteObject
from ..models.tag import Tag, TagTrack
from ..models.track import Track
from ..models.user import User
from ._common import ilike_contains
from .acl import _list_access_predicate, apply_access_filter
from .collection import apply_collection_filter, in_collection_clause
from .genres import InvalidGenreName, validate_genre_name


class DuplicatePlaylistTrackError(ValueError):
    """Raised when items are already present in a playlist and duplicates are disallowed."""

    def __init__(
        self,
        track_ids: List[str],
        episode_ids: Optional[List[str]] = None,
        remote_object_ids: Optional[List[str]] = None,
    ):
        self.track_ids = track_ids
        self.episode_ids = episode_ids or []
        self.remote_object_ids = remote_object_ids or []
        super().__init__(
            "Duplicate playlist items: "
            f"tracks={track_ids} episodes={self.episode_ids} remote={self.remote_object_ids}"
        )


def _track_selectin_options(include: Optional[Set[str]]) -> List[Any]:
    """Return selectinload options for Track queries."""
    options: List[Any] = [
        selectinload(Track.audio_file),
        selectinload(Track.image_file),
        selectinload(Track.external_track).selectinload(ExternalTrack.external_library),
    ]
    if include:
        if "artist" in include:
            options.append(selectinload(Track.artist))
        if "album" in include:
            options.append(
                selectinload(Track.album).options(
                    selectinload(Album.artist),
                    selectinload(Album.cover_file),
                )
            )
        if "owner" in include:
            options.append(selectinload(Track.owner))
        if "tags" in include:
            options.append(selectinload(Track.tags))
        if "genres" in include:
            options.append(selectinload(Track.genres))
    return options


def _album_selectin_options(include: Optional[Set[str]]) -> List[Any]:
    """Return selectinload options for Album queries."""
    options: List[Any] = [
        selectinload(Album.artist),
        selectinload(Album.cover_file),
    ]
    if include:
        if "owner" in include:
            options.append(selectinload(Album.owner))
        if "tracks" in include:
            options.append(
                selectinload(Album.tracks).options(
                    selectinload(Track.artist),
                    selectinload(Track.album).options(
                        selectinload(Album.artist),
                        selectinload(Album.cover_file),
                    ),
                    selectinload(Track.audio_file),
                    selectinload(Track.image_file),
                )
            )
        if "tags" in include:
            options.append(selectinload(Album.tags))
        if "genres" in include:
            options.append(selectinload(Album.genres))
    return options


def _artist_selectin_options(include: Optional[Set[str]]) -> List[Any]:
    """Return selectinload options for Artist queries."""
    options: List[Any] = [
        selectinload(Artist.image_file),
        selectinload(Artist.cover_file),
    ]
    if include:
        if "albums" in include:
            options.append(
                selectinload(Artist.albums).options(
                    selectinload(Album.artist),
                    selectinload(Album.cover_file),
                )
            )
        if "tracks" in include:
            options.append(
                selectinload(Artist.tracks).options(
                    selectinload(Track.artist),
                    selectinload(Track.album).options(
                        selectinload(Album.artist),
                        selectinload(Album.cover_file),
                    ),
                    selectinload(Track.audio_file),
                    selectinload(Track.image_file),
                )
            )
        if "tags" in include:
            options.append(selectinload(Artist.tags))
    return options


def _library_selectin_options(include: Optional[Set[str]]) -> List[Any]:
    """Return selectinload options for Library queries."""
    options: List[Any] = [
        selectinload(Library.image_file),
        selectinload(Library.cover_file),
    ]
    if include:
        if "owner" in include:
            options.append(selectinload(Library.owner))
        if "tracks" in include:
            options.append(
                selectinload(Library.tracks).options(
                    selectinload(Track.artist),
                    selectinload(Track.album).options(
                        selectinload(Album.artist),
                        selectinload(Album.cover_file),
                    ),
                    selectinload(Track.audio_file),
                    selectinload(Track.image_file),
                )
            )
        if "tags" in include:
            options.append(selectinload(Library.tags))
    return options


def _playlist_selectin_options(include: Optional[Set[str]]) -> List[Any]:
    """Return selectinload options for Playlist queries."""
    options: List[Any] = [
        selectinload(Playlist.image_file),
        selectinload(Playlist.cover_file),
    ]
    if include:
        if "owner" in include:
            options.append(selectinload(Playlist.owner))
        if "tracks" in include:
            options.append(
                selectinload(Playlist.tracks).options(
                    selectinload(PlaylistTrack.track).options(
                        selectinload(Track.artist),
                        selectinload(Track.album).options(
                            selectinload(Album.artist),
                            selectinload(Album.cover_file),
                        ),
                        selectinload(Track.audio_file),
                        selectinload(Track.image_file),
                    )
                )
            )
        if "tags" in include:
            options.append(selectinload(Playlist.tags))
    return options


def _order_clause(expr: Any, direction: str, nulls_last: bool = False) -> Any:
    """Return an ascending or descending order clause for ``expr``."""
    clause = expr.asc() if direction == "asc" else expr.desc()
    if nulls_last:
        clause = clause.nulls_last()
    return clause


def _apply_sort(
    stmt: Select[Any],
    field: Any,
    direction: str,
    secondary: Any,
    nulls_last: bool = False,
) -> Select[Any]:
    """Apply a primary and secondary sort to ``stmt``."""
    return stmt.order_by(_order_clause(field, direction, nulls_last), _order_clause(secondary, direction))


def _build_artists_stmt(
    query: Optional[str] = None,
    user: Optional[User] = None,
    owner_id: Optional[str] = None,
    collection: Optional[bool] = None,
) -> Select[Any]:
    """Build a statement for listing/counting artists.

    Artists carry no ACL of their own, so non-admin requesters only see
    artists that have at least one track or album they can access. When
    ``owner_id`` is set, artists must additionally have at least one track
    or album owned by that user. When ``collection`` is set, artists must
    instead be saved to the requester's collection or have at least one
    accessible track or album in it.
    """
    stmt = select(Artist)
    if query:
        stmt = stmt.where(ilike_contains(Artist.name, query))
    if owner_id:
        owned_tracks = select(Track.id).where(
            Track.artist_id == Artist.id,
            Track.owner_id == owner_id,
        )
        owned_albums = select(Album.id).where(
            Album.artist_id == Artist.id,
            Album.owner_id == owner_id,
        )
        stmt = stmt.where(or_(exists(owned_tracks), exists(owned_albums)))
    if collection:
        if user is None:
            stmt = stmt.where(false())
        else:
            collected_tracks = apply_access_filter(
                select(Track.id).where(
                    Track.artist_id == Artist.id,
                    in_collection_clause(Track, "track", user),
                ),
                Track,
                user,
                "track",
            )
            collected_albums = apply_access_filter(
                select(Album.id).where(
                    Album.artist_id == Artist.id,
                    in_collection_clause(Album, "album", user),
                ),
                Album,
                user,
                "album",
            )
            stmt = stmt.where(
                or_(
                    exists(collected_tracks),
                    exists(collected_albums),
                    in_collection_clause(Artist, "artist", user),
                )
            )
    if user is None or not user.is_admin:
        accessible_tracks = apply_access_filter(
            select(Track.id).where(Track.artist_id == Artist.id),
            Track,
            user,
            "track",
        )
        accessible_albums = apply_access_filter(
            select(Album.id).where(Album.artist_id == Artist.id),
            Album,
            user,
            "album",
        )
        stmt = stmt.where(or_(exists(accessible_tracks), exists(accessible_albums)))
    return stmt


async def list_artists(
    session: AsyncSession,
    query: Optional[str] = None,
    user: Optional[User] = None,
    owner_id: Optional[str] = None,
    collection: Optional[bool] = None,
    limit: int = 20,
    offset: int = 0,
    include: Optional[Set[str]] = None,
    sort_by: str = "name",
    sort_dir: str = "asc",
) -> List[Artist]:
    """List artists with optional search and sorting, honouring the requester's ACL."""
    field = getattr(Artist, sort_by)
    stmt = _build_artists_stmt(query=query, user=user, owner_id=owner_id, collection=collection).options(
        *_artist_selectin_options(include)
    )
    stmt = _apply_sort(stmt, field, sort_dir, Artist.id)
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_artists(
    session: AsyncSession,
    query: Optional[str] = None,
    user: Optional[User] = None,
    owner_id: Optional[str] = None,
    collection: Optional[bool] = None,
) -> int:
    """Return the total number of artists matching the optional search and ACL."""
    stmt = _build_artists_stmt(query=query, user=user, owner_id=owner_id, collection=collection)
    result = await session.execute(select(func.count()).select_from(stmt.subquery()))
    return result.scalar() or 0


async def find_or_create_artist(session: AsyncSession, name: str) -> Artist:
    """Find an artist by case-insensitive name, or create one."""
    result = await session.execute(select(Artist).where(func.lower(Artist.name) == name.lower()).limit(1))
    artist = cast(Optional[Artist], result.scalar_one_or_none())
    if artist:
        return artist

    artist = Artist(name=name)
    session.add(artist)
    await session.flush()
    return artist


async def find_or_create_album(
    session: AsyncSession,
    *,
    title: str,
    artist_id: str,
    year: Optional[int] = None,
    owner_id: Optional[str] = None,
    visibility: Optional[str] = None,
) -> Album:
    """Find an album by title and artist, or create one."""
    result = await session.execute(
        select(Album)
        .where(
            func.lower(Album.title) == title.lower(),
            Album.artist_id == artist_id,
        )
        .limit(1)
    )
    album = cast(Optional[Album], result.scalar_one_or_none())
    if album:
        return album

    album = Album(
        title=title,
        artist_id=artist_id,
        release_year=year,
        owner_id=owner_id,
        visibility=visibility or "private",
    )
    session.add(album)
    await session.flush()
    return album


async def _refresh_include(session: AsyncSession, obj: Any, include: Optional[Set[str]]) -> None:
    """Eagerly load any requested relationships so they are populated for reads."""
    if obj is None or not include:
        return
    with contextlib.suppress(Exception):
        await session.refresh(obj, list(include))

    if "album" in include and getattr(obj, "album", None) is not None:
        with contextlib.suppress(Exception):
            await session.refresh(obj.album, ["cover_file", "artist"])
    if "artist" in include and getattr(obj, "artist", None) is not None:
        with contextlib.suppress(Exception):
            await session.refresh(obj.artist, ["image_file"])


async def get_artist(
    session: AsyncSession,
    artist_id: str,
    include: Optional[Set[str]] = None,
) -> Optional[Artist]:
    """Get an artist by ID."""
    stmt = (
        select(Artist)
        .options(*_artist_selectin_options(include))
        .where(Artist.id == artist_id)
        .execution_options(populate_existing=True)
    )
    result = await session.execute(stmt)
    artist = result.scalar_one_or_none()
    await _refresh_include(session, artist, include)
    return cast(Optional[Artist], artist)


def _build_albums_stmt(
    query: Optional[str] = None,
    artist_id: Optional[str] = None,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    genre: Optional[str] = None,
    owner_id: Optional[str] = None,
    collection: Optional[bool] = None,
    user: Optional[User] = None,
) -> Select[Any]:
    """Build a statement for listing/counting albums."""
    stmt = select(Album)
    if owner_id:
        stmt = stmt.where(Album.owner_id == owner_id)
    if query:
        stmt = stmt.where(ilike_contains(Album.title, query))
    if artist_id:
        stmt = stmt.where(Album.artist_id == artist_id)
    if year_from is not None:
        stmt = stmt.where(Album.release_year >= year_from)
    if year_to is not None:
        stmt = stmt.where(Album.release_year <= year_to)
    if genre:
        try:
            genre_name = validate_genre_name(genre)
        except InvalidGenreName:
            genre_name = None
        if genre_name is None:
            stmt = stmt.where(false())
        else:
            stmt = stmt.where(
                Album.id.in_(
                    select(GenreAlbum.album_id)
                    .join(Genre, GenreAlbum.genre_id == Genre.id)
                    .where(Genre.name == genre_name)
                )
            )
    stmt = apply_collection_filter(stmt, Album, user, "album", collection)
    return stmt


async def list_albums(
    session: AsyncSession,
    query: Optional[str] = None,
    artist_id: Optional[str] = None,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    genre: Optional[str] = None,
    owner_id: Optional[str] = None,
    collection: Optional[bool] = None,
    user: Optional[User] = None,
    limit: int = 20,
    offset: int = 0,
    include: Optional[Set[str]] = None,
    sort_by: str = "title",
    sort_dir: str = "asc",
) -> List[Album]:
    """List albums with optional filters, honouring the requester's ACL."""
    stmt = _build_albums_stmt(
        query=query,
        artist_id=artist_id,
        year_from=year_from,
        year_to=year_to,
        genre=genre,
        owner_id=owner_id,
        collection=collection,
        user=user,
    ).options(*_album_selectin_options(include))
    stmt = apply_access_filter(stmt, Album, user, "album")
    field = (
        select(Artist.name).where(Artist.id == Album.artist_id).scalar_subquery()
        if sort_by == "artist_name"
        else getattr(Album, sort_by)
    )

    nulls_last = sort_by in {"release_year"}
    stmt = _apply_sort(stmt, field, sort_dir, Album.id, nulls_last)
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_albums(
    session: AsyncSession,
    query: Optional[str] = None,
    artist_id: Optional[str] = None,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    genre: Optional[str] = None,
    owner_id: Optional[str] = None,
    collection: Optional[bool] = None,
    user: Optional[User] = None,
) -> int:
    """Return the total number of albums matching the filters and ACL."""
    stmt = _build_albums_stmt(
        query=query,
        artist_id=artist_id,
        year_from=year_from,
        year_to=year_to,
        genre=genre,
        owner_id=owner_id,
        collection=collection,
        user=user,
    )
    stmt = apply_access_filter(stmt, Album, user, "album")
    result = await session.execute(select(func.count()).select_from(stmt.subquery()))
    return result.scalar() or 0


async def get_album(
    session: AsyncSession,
    album_id: str,
    include: Optional[Set[str]] = None,
) -> Optional[Album]:
    """Get an album by ID."""
    stmt = (
        select(Album)
        .options(*_album_selectin_options(include))
        .where(Album.id == album_id)
        .execution_options(populate_existing=True)
    )
    result = await session.execute(stmt)
    album = result.scalar_one_or_none()
    await _refresh_include(session, album, include)
    return cast(Optional[Album], album)


def _apply_tracks_query(
    session: AsyncSession,
    stmt: Select[Any],
    *,
    query: str,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
) -> Select[Any]:
    """
    Apply a search query to a tracks statement, using PostgreSQL full-text search if available.
    """
    dialect = getattr(getattr(session, "bind", None), "dialect", None)
    if dialect is not None and getattr(dialect, "name", None) == "postgresql" and hasattr(Track, "search_vector"):
        ts_query = func.plainto_tsquery("english", query)
        track_search_vector = Track.search_vector  # type: ignore
        stmt = stmt.where(track_search_vector.op("@@")(ts_query))
    else:
        stmt = stmt.join(Artist, Track.artist_id == Artist.id)
        if year_from is None and year_to is None:
            stmt = stmt.outerjoin(Album, Track.album_id == Album.id)
        stmt = stmt.where(
            or_(
                ilike_contains(Track.title, query),
                ilike_contains(Artist.name, query),
                ilike_contains(Album.title, query),
            )
        )

    return stmt


def _apply_collection_tracks_query(stmt: Select[Any], query: str) -> Select[Any]:
    """Filter collection member tracks by title, artist, album, tag, or genre."""
    return stmt.where(
        or_(
            ilike_contains(Track.title, query),
            Track.artist.has(ilike_contains(Artist.name, query)),
            Track.album.has(ilike_contains(Album.title, query)),
            ilike_contains(Track.genre, query),
            Track.tags.any(ilike_contains(Tag.name, query)),
            Track.genres.any(ilike_contains(Genre.name, query)),
        )
    )


def _track_sort_clause(sort_by: str, sort_dir: str) -> Tuple[Any, ...]:
    """Return ORDER BY clauses for a track list based on the requested field."""
    artist_name = select(Artist.name).where(Artist.id == Track.artist_id).scalar_subquery()
    album_title = select(Album.title).where(Album.id == Track.album_id).scalar_subquery()
    album_release_year = select(Album.release_year).where(Album.id == Track.album_id).scalar_subquery()

    field_map = {
        "created_at": Track.created_at,
        "title": Track.title,
        "artist_name": artist_name,
        "album_title": album_title,
        "updated_at": Track.updated_at,
        "release_year": func.coalesce(Track.release_year, album_release_year),
    }
    field = field_map.get(sort_by, Track.created_at)
    nulls_last = sort_by in {"release_year", "album_title"}
    primary = _order_clause(field, sort_dir, nulls_last)
    secondary = _order_clause(Track.id, sort_dir)
    return (primary, secondary)


def _playlist_track_sort_clause(sort_by: str, sort_dir: str) -> Tuple[Any, ...]:
    """Return ORDER BY clauses for a playlist track list."""
    if sort_by == "position":
        primary = _order_clause(PlaylistTrack.position, sort_dir)
        secondary = _order_clause(Track.id, sort_dir)
    else:
        return _track_sort_clause(sort_by, sort_dir)
    return (primary, secondary)


def _build_tracks_stmt(
    session: AsyncSession,
    *,
    query: Optional[str] = None,
    artist_id: Optional[str] = None,
    album_id: Optional[str] = None,
    genre: Optional[str] = None,
    tag: Optional[str] = None,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    library_id: Optional[str] = None,
    favorited: Optional[bool] = None,
    collection: Optional[bool] = None,
    owner_id: Optional[str] = None,
    user: Optional[User] = None,
    file_id: Optional[str] = None,
) -> Select[Any]:
    """Build a statement for listing/counting tracks."""
    stmt = select(Track)
    if owner_id:
        stmt = stmt.where(Track.owner_id == owner_id)
    if artist_id:
        stmt = stmt.where(Track.artist_id == artist_id)
    if album_id:
        stmt = stmt.where(Track.album_id == album_id)
    if genre:
        try:
            genre_name = validate_genre_name(genre)
        except InvalidGenreName:
            genre_name = None
        if genre_name is None:
            stmt = stmt.where(false())
        else:
            stmt = stmt.where(
                Track.id.in_(
                    select(GenreTrack.track_id)
                    .join(Genre, GenreTrack.genre_id == Genre.id)
                    .where(Genre.name == genre_name)
                )
            )
    if tag:
        stmt = (
            stmt.join(TagTrack, TagTrack.track_id == Track.id)
            .join(Tag, Tag.id == TagTrack.tag_id)
            .where(Tag.name == tag)
        )
    if file_id:
        stmt = stmt.where(
            or_(
                Track.audio_file_id == file_id,
                Track.image_file_id == file_id,
                Track.album_id.in_(select(Album.id).where(Album.cover_file_id == file_id)),
            )
        )

    if year_from is not None or year_to is not None:
        stmt = stmt.outerjoin(Album, Track.album_id == Album.id)
        range_conditions = []
        if year_from is not None:
            range_conditions.append(Album.release_year >= year_from)
        if year_to is not None:
            range_conditions.append(Album.release_year <= year_to)
        stmt = stmt.where(or_(Album.release_year.is_(None), and_(*range_conditions)))

    if library_id:
        stmt = stmt.join(LibraryTrack, LibraryTrack.track_id == Track.id).where(LibraryTrack.library_id == library_id)

    if favorited:
        if user is None:
            stmt = stmt.where(false())
        else:
            stmt = stmt.join(Favorite, and_(Favorite.track_id == Track.id, Favorite.user_id == user.id))

    stmt = apply_collection_filter(stmt, Track, user, "track", collection)

    if query:
        stmt = _apply_tracks_query(session, stmt, query=query, year_from=year_from, year_to=year_to)

    stmt = stmt.where(
        or_(
            ~exists().where(ExternalTrack.track_id == Track.id),
            exists().where(
                ExternalTrack.track_id == Track.id,
                ExternalTrack.state == "active",
            ),
        )
    )

    return stmt


async def list_tracks(
    session: AsyncSession,
    query: Optional[str] = None,
    artist_id: Optional[str] = None,
    album_id: Optional[str] = None,
    genre: Optional[str] = None,
    tag: Optional[str] = None,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    library_id: Optional[str] = None,
    user: Optional[User] = None,
    limit: int = 20,
    offset: int = 0,
    include: Optional[Set[str]] = None,
    around_track_id: Optional[str] = None,
    sort_by: str = "created_at",
    sort_dir: str = "desc",
    favorited: Optional[bool] = None,
    collection: Optional[bool] = None,
    owner_id: Optional[str] = None,
    file_id: Optional[str] = None,
) -> Tuple[List[Track], int]:
    """List tracks with optional filters, honouring the requester's ACL.

    If ``around_track_id`` is given and the default sort order is active, the
    returned chunk is centered on that track (when it matches the filters and
    is accessible to the requester). The second return value is the effective
    offset of the returned chunk.
    """
    base_stmt = _build_tracks_stmt(
        session,
        query=query,
        artist_id=artist_id,
        album_id=album_id,
        genre=genre,
        tag=tag,
        year_from=year_from,
        year_to=year_to,
        library_id=library_id,
        favorited=favorited,
        collection=collection,
        user=user,
        file_id=file_id,
        owner_id=owner_id,
    )
    base_stmt = apply_access_filter(base_stmt, Track, user, "track")

    # Album tracks are always ordered by disc and track number.
    if album_id:
        base_stmt = base_stmt.order_by(
            _order_clause(Track.disc_number, "asc", nulls_last=True),
            _order_clause(Track.track_number, "asc", nulls_last=True),
            Track.id,
        )
    else:
        base_stmt = base_stmt.order_by(*_track_sort_clause(sort_by, sort_dir))

    effective_offset = max(0, offset)
    if around_track_id and not album_id and not query and sort_by == "created_at" and sort_dir == "desc":
        around = await get_track(session, around_track_id, include=None)
        if around is not None:
            exists_stmt = base_stmt.where(Track.id == around.id).limit(1)
            exists = (await session.execute(exists_stmt)).scalar_one_or_none()
            if exists is not None:
                before_stmt = select(func.count()).select_from(
                    base_stmt.where(
                        or_(
                            Track.created_at > around.created_at,
                            and_(
                                Track.created_at == around.created_at,
                                Track.id > around.id,
                            ),
                        )
                    ).subquery()
                )
                count_before = (await session.execute(before_stmt)).scalar() or 0
                effective_offset = max(0, count_before - limit // 2)

    stmt = base_stmt.options(*_track_selectin_options(include)).offset(effective_offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all()), effective_offset


async def count_tracks(
    session: AsyncSession,
    *,
    query: Optional[str] = None,
    artist_id: Optional[str] = None,
    album_id: Optional[str] = None,
    genre: Optional[str] = None,
    tag: Optional[str] = None,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    library_id: Optional[str] = None,
    user: Optional[User] = None,
    favorited: Optional[bool] = None,
    collection: Optional[bool] = None,
    owner_id: Optional[str] = None,
    file_id: Optional[str] = None,
) -> int:
    """Return the total number of tracks matching the filters and ACL."""
    stmt = _build_tracks_stmt(
        session,
        query=query,
        artist_id=artist_id,
        album_id=album_id,
        genre=genre,
        tag=tag,
        year_from=year_from,
        year_to=year_to,
        library_id=library_id,
        favorited=favorited,
        collection=collection,
        user=user,
        file_id=file_id,
        owner_id=owner_id,
    )
    stmt = apply_access_filter(stmt, Track, user, "track")
    result = await session.execute(select(func.count()).select_from(stmt.subquery()))
    return result.scalar() or 0


async def get_track(
    session: AsyncSession,
    track_id: str,
    include: Optional[Set[str]] = None,
) -> Optional[Track]:
    """Get a track by ID."""
    stmt = (
        select(Track)
        .options(*_track_selectin_options(include))
        .where(Track.id == track_id)
        .execution_options(populate_existing=True)
    )
    result = await session.execute(stmt)
    track = result.scalar_one_or_none()
    await _refresh_include(session, track, include)
    return cast(Optional[Track], track)


async def get_favorited_track_ids(
    session: AsyncSession,
    user: Optional[User],
    track_ids: Set[str],
) -> Set[str]:
    """Return the subset of ``track_ids`` that the given user has favorited."""
    if user is None or not track_ids:
        return set()
    result = await session.execute(
        select(Favorite.track_id).where(
            Favorite.user_id == user.id,
            Favorite.track_id.in_(track_ids),
        )
    )
    return {str(row) for row in result.scalars().all()}


async def list_library_tracks(
    session: AsyncSession,
    library_id: str,
    user: Optional[User] = None,
    limit: int = 20,
    offset: int = 0,
    include: Optional[Set[str]] = None,
    sort_by: str = "created_at",
    sort_dir: str = "desc",
    query: Optional[str] = None,
) -> List[Track]:
    """List tracks that are members of ``library_id``."""
    stmt = (
        select(Track)
        .options(*_track_selectin_options(include))
        .join(LibraryTrack, LibraryTrack.track_id == Track.id)
        .where(LibraryTrack.library_id == library_id)
    )
    if query:
        stmt = _apply_collection_tracks_query(stmt, query)
    stmt = apply_access_filter(stmt, Track, user, "track")
    stmt = stmt.order_by(*_track_sort_clause(sort_by, sort_dir))
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_library_tracks(
    session: AsyncSession,
    library_id: str,
    user: Optional[User] = None,
    query: Optional[str] = None,
) -> int:
    """Return the total number of tracks in ``library_id`` visible to ``user``."""
    stmt = (
        select(Track).join(LibraryTrack, LibraryTrack.track_id == Track.id).where(LibraryTrack.library_id == library_id)
    )
    if query:
        stmt = _apply_collection_tracks_query(stmt, query)
    stmt = apply_access_filter(stmt, Track, user, "track")
    result = await session.execute(select(func.count()).select_from(stmt.subquery()))
    return result.scalar() or 0


async def list_playlists(
    session: AsyncSession,
    user: Optional[User] = None,
    owner_id: Optional[str] = None,
    query: Optional[str] = None,
    collection: Optional[bool] = None,
    limit: int = 20,
    offset: int = 0,
    include: Optional[Set[str]] = None,
    sort_by: str = "name",
    sort_dir: str = "asc",
) -> List[Playlist]:
    """List playlists visible to ``user``, optionally filtered by name/description."""
    field = getattr(Playlist, sort_by)
    stmt = select(Playlist).options(*_playlist_selectin_options(include))
    if owner_id:
        stmt = stmt.where(Playlist.owner_id == owner_id)
    if query:
        stmt = stmt.where(or_(ilike_contains(Playlist.name, query), ilike_contains(Playlist.description, query)))
    stmt = apply_collection_filter(stmt, Playlist, user, "playlist", collection)
    stmt = apply_access_filter(stmt, Playlist, user, "playlist")
    stmt = _apply_sort(stmt, field, sort_dir, Playlist.id)
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_playlists(
    session: AsyncSession,
    user: Optional[User] = None,
    owner_id: Optional[str] = None,
    query: Optional[str] = None,
    collection: Optional[bool] = None,
) -> int:
    """Return the total number of playlists visible to ``user``."""
    stmt = select(Playlist)
    if owner_id:
        stmt = stmt.where(Playlist.owner_id == owner_id)
    if query:
        stmt = stmt.where(or_(ilike_contains(Playlist.name, query), ilike_contains(Playlist.description, query)))
    stmt = apply_collection_filter(stmt, Playlist, user, "playlist", collection)
    stmt = apply_access_filter(stmt, Playlist, user, "playlist")
    result = await session.execute(select(func.count()).select_from(stmt.subquery()))
    return result.scalar() or 0


async def get_playlist(
    session: AsyncSession,
    playlist_id: str,
    include: Optional[Set[str]] = None,
) -> Optional[Playlist]:
    """Get a playlist by ID."""
    stmt = (
        select(Playlist)
        .options(*_playlist_selectin_options(include))
        .where(Playlist.id == playlist_id)
        .execution_options(populate_existing=True)
    )
    result = await session.execute(stmt)
    playlist = result.scalar_one_or_none()
    await _refresh_include(session, playlist, include)
    return cast(Optional[Playlist], playlist)


async def list_libraries(
    session: AsyncSession,
    user: Optional[User] = None,
    owner_id: Optional[str] = None,
    query: Optional[str] = None,
    collection: Optional[bool] = None,
    limit: int = 20,
    offset: int = 0,
    include: Optional[Set[str]] = None,
    sort_by: str = "name",
    sort_dir: str = "asc",
    include_external: bool = False,
) -> List[Library]:
    """List libraries visible to ``user``, optionally filtered by name/description."""
    field = getattr(Library, sort_by)
    stmt = select(Library).options(*_library_selectin_options(include))
    if owner_id:
        stmt = stmt.where(Library.owner_id == owner_id)
    if query:
        stmt = stmt.where(or_(ilike_contains(Library.name, query), ilike_contains(Library.description, query)))
    if not include_external:
        stmt = stmt.where(
            ~exists().where(
                ExternalLibrary.library_id == Library.id,
                or_(
                    ExternalLibrary.scope == "user",
                    and_(
                        ExternalLibrary.scope == "admin",
                        ExternalLibrary.include_in_library_index == false(),
                    ),
                ),
            )
        )
    stmt = apply_collection_filter(stmt, Library, user, "library", collection)
    stmt = apply_access_filter(stmt, Library, user, "library")
    stmt = _apply_sort(stmt, field, sort_dir, Library.id)
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_libraries(
    session: AsyncSession,
    user: Optional[User] = None,
    owner_id: Optional[str] = None,
    query: Optional[str] = None,
    collection: Optional[bool] = None,
    include_external: bool = False,
) -> int:
    """Return the total number of libraries visible to ``user``."""
    stmt = select(Library)
    if owner_id:
        stmt = stmt.where(Library.owner_id == owner_id)
    if query:
        stmt = stmt.where(or_(ilike_contains(Library.name, query), ilike_contains(Library.description, query)))
    if not include_external:
        stmt = stmt.where(
            ~exists().where(
                ExternalLibrary.library_id == Library.id,
                or_(
                    ExternalLibrary.scope == "user",
                    and_(
                        ExternalLibrary.scope == "admin",
                        ExternalLibrary.include_in_library_index == false(),
                    ),
                ),
            )
        )
    stmt = apply_collection_filter(stmt, Library, user, "library", collection)
    stmt = apply_access_filter(stmt, Library, user, "library")
    result = await session.execute(select(func.count()).select_from(stmt.subquery()))
    return result.scalar() or 0


async def get_library(
    session: AsyncSession,
    library_id: str,
    include: Optional[Set[str]] = None,
) -> Optional[Library]:
    """Get a library by ID."""
    stmt = (
        select(Library)
        .options(*_library_selectin_options(include))
        .where(Library.id == library_id)
        .execution_options(populate_existing=True)
    )
    result = await session.execute(stmt)
    library = result.scalar_one_or_none()
    await _refresh_include(session, library, include)
    return cast(Optional[Library], library)


async def list_radios(
    session: AsyncSession,
    user: Optional[User] = None,
    collection: Optional[bool] = None,
    limit: int = 20,
    offset: int = 0,
) -> List[Radio]:
    """List radios visible to ``user``."""
    stmt = select(Radio)
    stmt = apply_collection_filter(stmt, Radio, user, "radio", collection)
    stmt = apply_access_filter(stmt, Radio, user, "radio")
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_radios(
    session: AsyncSession,
    user: Optional[User] = None,
    collection: Optional[bool] = None,
) -> int:
    """Return the total number of radios visible to ``user``."""
    stmt = select(Radio)
    stmt = apply_collection_filter(stmt, Radio, user, "radio", collection)
    stmt = apply_access_filter(stmt, Radio, user, "radio")
    result = await session.execute(select(func.count()).select_from(stmt.subquery()))
    return result.scalar() or 0


async def get_radio(session: AsyncSession, radio_id: str) -> Optional[Radio]:
    """Get a radio by ID."""
    return cast(Optional[Radio], await session.get(Radio, radio_id))


async def get_track_ids_for_album(
    session: AsyncSession,
    album_id: str,
    user: Optional[User] = None,
    without_image: bool = False,
) -> List[str]:
    """Return the IDs of accessible tracks that belong to ``album_id``.

    When ``without_image`` is True, only tracks without a track-level image
    override are returned.
    """
    stmt = (
        select(Track.id)
        .where(Track.album_id == album_id)
        .order_by(Track.track_number, Track.disc_number, Track.created_at)
    )
    if without_image:
        stmt = stmt.where(Track.image_file_id.is_(None))
    stmt = apply_access_filter(stmt, Track, user, "track")
    result = await session.execute(stmt)
    return [str(row) for row in result.scalars().all()]


async def propagate_album_visibility(
    session: AsyncSession,
    album: Album,
    user: User,
) -> List[Tuple[Track, str]]:
    """Copy ``album.visibility`` onto the album's tracks that ``user`` manages.

    Non-admin users only affect tracks they own; admins propagate to every
    track in the album, mirroring the ownership semantics of
    ``deletion.delete_album``.  Returns ``(track, previous_visibility)`` pairs
    for the tracks whose visibility actually changed so callers can run
    post-change side effects such as federation publish/unpublish.
    """
    stmt = select(Track).where(
        Track.album_id == album.id,
        Track.visibility != album.visibility,
    )
    if not user.is_admin:
        stmt = stmt.where(Track.owner_id == user.id)
    result = await session.execute(stmt)
    changed = [(track, track.visibility) for track in result.scalars().all()]
    for track, _ in changed:
        track.visibility = album.visibility
    if changed:
        await session.flush()
    return changed


async def propagate_external_library_visibility(
    session: AsyncSession,
    external_library: ExternalLibrary,
    user: User,
) -> List[Tuple[Track, str]]:
    """Copy the backing ``Library.visibility`` onto the library's synced tracks.

    External-library tracks are seeded with the library's visibility at sync
    time; propagating keeps list queries and single-item ACL consistent when
    the owner later makes the library public/local or locks it back down.
    Non-admin callers only affect tracks they own, mirroring
    ``propagate_album_visibility``.  Returns ``(track, previous_visibility)``
    pairs for the tracks whose visibility actually changed.
    """
    library = external_library.library
    if library is None:
        return []

    external_track_ids = select(ExternalTrack.track_id).where(
        ExternalTrack.external_library_id == str(external_library.id),
        ExternalTrack.track_id.isnot(None),
    )
    stmt = select(Track).where(
        Track.id.in_(external_track_ids),
        Track.visibility != library.visibility,
    )
    if not user.is_admin:
        stmt = stmt.where(Track.owner_id == user.id)
    result = await session.execute(stmt)
    changed = [(track, track.visibility) for track in result.scalars().all()]
    for track, _ in changed:
        track.visibility = library.visibility
    if changed:
        await session.flush()
    return changed


async def get_track_ids_for_artist(
    session: AsyncSession,
    artist_id: str,
    user: Optional[User] = None,
) -> List[str]:
    """Return the IDs of accessible tracks that belong to ``artist_id``."""
    stmt = (
        select(Track.id)
        .where(Track.artist_id == artist_id)
        .order_by(Track.album_id, Track.track_number, Track.disc_number, Track.created_at)
    )
    stmt = apply_access_filter(stmt, Track, user, "track")
    result = await session.execute(stmt)
    return [str(row) for row in result.scalars().all()]


async def add_library_tracks(
    session: AsyncSession,
    library_id: str,
    track_ids: List[str],
    remote_object_ids: Sequence[str] = (),
    added_by_id: Optional[str] = None,
) -> Tuple[List[str], List[str]]:
    """
    Insert ``LibraryTrack`` rows for ``track_ids``/``remote_object_ids`` into ``library_id``.

    Duplicate rows are skipped because of the unique constraints. The returned
    tuple ``(track_ids, remote_object_ids)`` preserves the input order and
    contains only the IDs that were actually added.
    """
    if not track_ids and not remote_object_ids:
        return [], []

    existing_tracks: Set[str] = set()
    if track_ids:
        result = await session.execute(
            select(LibraryTrack.track_id).where(
                LibraryTrack.library_id == library_id,
                LibraryTrack.track_id.in_(track_ids),
            )
        )
        existing_tracks = {str(row) for row in result.scalars().all()}
    existing_remote: Set[str] = set()
    if remote_object_ids:
        result = await session.execute(
            select(LibraryTrack.remote_object_id).where(
                LibraryTrack.library_id == library_id,
                LibraryTrack.remote_object_id.in_(remote_object_ids),
            )
        )
        existing_remote = {str(row) for row in result.scalars().all()}

    added_tracks: List[str] = []
    added_remote: List[str] = []
    rows: List[LibraryTrack] = []
    seen: set[str] = set()
    for track_id in track_ids:
        if track_id in existing_tracks or track_id in seen:
            continue
        seen.add(track_id)
        rows.append(LibraryTrack(library_id=library_id, track_id=track_id, added_by_id=added_by_id))
        added_tracks.append(track_id)
    seen.clear()
    for object_id in remote_object_ids:
        if object_id in existing_remote or object_id in seen:
            continue
        seen.add(object_id)
        rows.append(LibraryTrack(library_id=library_id, remote_object_id=object_id, added_by_id=added_by_id))
        added_remote.append(object_id)

    if rows:
        session.add_all(rows)
        await session.flush()

    return added_tracks, added_remote


async def remove_library_tracks(
    session: AsyncSession,
    library_id: str,
    track_ids: List[str],
    remote_object_ids: Sequence[str] = (),
) -> Tuple[List[str], List[str]]:
    """
    Remove ``track_ids``/``remote_object_ids`` from ``library_id``.

    Returns the ``(track_ids, remote_object_ids)`` that were actually removed,
    preserving input order.
    """
    if not track_ids and not remote_object_ids:
        return [], []

    clauses = []
    if track_ids:
        clauses.append(LibraryTrack.track_id.in_(track_ids))
    if remote_object_ids:
        clauses.append(LibraryTrack.remote_object_id.in_(remote_object_ids))
    result = await session.execute(
        select(LibraryTrack).where(
            LibraryTrack.library_id == library_id,
            or_(*clauses),
        )
    )
    rows = list(result.scalars().all())
    removed_track_ids = {str(row.track_id) for row in rows if row.track_id is not None}
    removed_remote_ids = {str(row.remote_object_id) for row in rows if row.remote_object_id is not None}

    for row in rows:
        await session.delete(row)

    removed_tracks: List[str] = []
    removed_remote: List[str] = []
    seen: Set[str] = set()
    for track_id in track_ids:
        if track_id in removed_track_ids and track_id not in seen:
            seen.add(track_id)
            removed_tracks.append(track_id)
    seen.clear()
    for object_id in remote_object_ids:
        if object_id in removed_remote_ids and object_id not in seen:
            seen.add(object_id)
            removed_remote.append(object_id)

    if rows:
        await session.flush()

    return removed_tracks, removed_remote


async def add_playlist_items(
    session: AsyncSession,
    playlist_id: str,
    *,
    track_ids: Sequence[str] = (),
    episode_ids: Sequence[str] = (),
    remote_object_ids: Sequence[str] = (),
    allow_duplicates: bool = False,
) -> Tuple[List[str], List[str], List[str]]:
    """
    Append ``track_ids``/``episode_ids``/``remote_object_ids`` to ``playlist_id`` at the end.

    Tracks are appended first, then podcast episodes, then remote objects;
    each new ``PlaylistTrack`` row is assigned an increasing ``position``.
    The returned tuple contains the appended ``(track_ids, episode_ids,
    remote_object_ids)`` in input order.

    If ``allow_duplicates`` is ``False`` (the default) and any of the items are
    already in the playlist, a ``DuplicatePlaylistTrackError`` carrying the
    offending ids is raised.
    """
    if not track_ids and not episode_ids and not remote_object_ids:
        return [], [], []

    if not allow_duplicates:
        existing_tracks: Set[str] = set()
        existing_episodes: Set[str] = set()
        existing_remote: Set[str] = set()
        if track_ids:
            result = await session.execute(
                select(PlaylistTrack.track_id).where(
                    PlaylistTrack.playlist_id == playlist_id,
                    PlaylistTrack.track_id.in_(track_ids),
                )
            )
            existing_tracks = {str(row) for row in result.scalars().all()}
        if episode_ids:
            result = await session.execute(
                select(PlaylistTrack.podcast_episode_id).where(
                    PlaylistTrack.playlist_id == playlist_id,
                    PlaylistTrack.podcast_episode_id.in_(episode_ids),
                )
            )
            existing_episodes = {str(row) for row in result.scalars().all()}
        if remote_object_ids:
            result = await session.execute(
                select(PlaylistTrack.remote_object_id).where(
                    PlaylistTrack.playlist_id == playlist_id,
                    PlaylistTrack.remote_object_id.in_(remote_object_ids),
                )
            )
            existing_remote = {str(row) for row in result.scalars().all()}
        duplicate_tracks: List[str] = []
        duplicate_episodes: List[str] = []
        duplicate_remote: List[str] = []
        seen: Set[str] = set()
        for track_id in track_ids:
            if track_id in existing_tracks and track_id not in seen:
                seen.add(track_id)
                duplicate_tracks.append(track_id)
        for episode_id in episode_ids:
            if episode_id in existing_episodes and episode_id not in seen:
                seen.add(episode_id)
                duplicate_episodes.append(episode_id)
        for object_id in remote_object_ids:
            if object_id in existing_remote and object_id not in seen:
                seen.add(object_id)
                duplicate_remote.append(object_id)
        if duplicate_tracks or duplicate_episodes or duplicate_remote:
            raise DuplicatePlaylistTrackError(duplicate_tracks, duplicate_episodes, duplicate_remote)

    result = await session.execute(
        select(func.max(PlaylistTrack.position)).where(PlaylistTrack.playlist_id == playlist_id)
    )
    max_position = cast(int, result.scalar() or 0)

    added_tracks: List[str] = []
    added_episodes: List[str] = []
    added_remote: List[str] = []
    rows: List[PlaylistTrack] = []
    for offset, track_id in enumerate(track_ids, start=1):
        rows.append(PlaylistTrack(playlist_id=playlist_id, track_id=track_id, position=max_position + offset))
        added_tracks.append(track_id)
    for offset, episode_id in enumerate(episode_ids, start=len(rows) + 1):
        rows.append(
            PlaylistTrack(
                playlist_id=playlist_id,
                podcast_episode_id=episode_id,
                position=max_position + offset,
            )
        )
        added_episodes.append(episode_id)
    for offset, object_id in enumerate(remote_object_ids, start=len(rows) + 1):
        rows.append(
            PlaylistTrack(
                playlist_id=playlist_id,
                remote_object_id=object_id,
                position=max_position + offset,
            )
        )
        added_remote.append(object_id)

    if rows:
        session.add_all(rows)
        await session.flush()

    return added_tracks, added_episodes, added_remote


async def add_playlist_tracks(
    session: AsyncSession,
    playlist_id: str,
    track_ids: List[str],
    allow_duplicates: bool = False,
) -> List[str]:
    """
    Append ``track_ids`` to ``playlist_id`` at the end of the playlist.

    Thin wrapper over ``add_playlist_items`` kept for track-only callers
    (Subsonic). Returns the appended track IDs in input order and raises
    ``DuplicatePlaylistTrackError`` when duplicates are disallowed.
    """
    added, _, _ = await add_playlist_items(
        session,
        playlist_id,
        track_ids=track_ids,
        allow_duplicates=allow_duplicates,
    )
    return added


async def remove_playlist_items(
    session: AsyncSession,
    playlist_id: str,
    *,
    track_ids: Sequence[str] = (),
    episode_ids: Sequence[str] = (),
    remote_object_ids: Sequence[str] = (),
) -> Tuple[int, List[str], List[str], List[str]]:
    """
    Remove all occurrences of ``track_ids``/``episode_ids``/``remote_object_ids`` from ``playlist_id``.

    Returns the number of rows removed and the distinct track/episode/remote
    object IDs that were removed.
    """
    if not track_ids and not episode_ids and not remote_object_ids:
        return 0, [], [], []

    clauses = []
    if track_ids:
        clauses.append(PlaylistTrack.track_id.in_(track_ids))
    if episode_ids:
        clauses.append(PlaylistTrack.podcast_episode_id.in_(episode_ids))
    if remote_object_ids:
        clauses.append(PlaylistTrack.remote_object_id.in_(remote_object_ids))
    result = await session.execute(
        select(PlaylistTrack).where(
            PlaylistTrack.playlist_id == playlist_id,
            or_(*clauses),
        )
    )
    rows = list(result.scalars().all())
    removed_track_counts: Dict[str, int] = {}
    removed_episode_counts: Dict[str, int] = {}
    removed_remote_counts: Dict[str, int] = {}
    for row in rows:
        await session.delete(row)
        if row.track_id is not None:
            track_id = str(row.track_id)
            removed_track_counts[track_id] = removed_track_counts.get(track_id, 0) + 1
        if row.podcast_episode_id is not None:
            episode_id = str(row.podcast_episode_id)
            removed_episode_counts[episode_id] = removed_episode_counts.get(episode_id, 0) + 1
        if row.remote_object_id is not None:
            object_id = str(row.remote_object_id)
            removed_remote_counts[object_id] = removed_remote_counts.get(object_id, 0) + 1

    removed_track_ids: List[str] = []
    removed_episode_ids: List[str] = []
    removed_remote_ids: List[str] = []
    seen: Set[str] = set()
    for track_id in track_ids:
        if track_id in removed_track_counts and track_id not in seen:
            seen.add(track_id)
            removed_track_ids.append(track_id)
    for episode_id in episode_ids:
        if episode_id in removed_episode_counts and episode_id not in seen:
            seen.add(episode_id)
            removed_episode_ids.append(episode_id)
    for object_id in remote_object_ids:
        if object_id in removed_remote_counts and object_id not in seen:
            seen.add(object_id)
            removed_remote_ids.append(object_id)

    if rows:
        await session.flush()
        await renormalize_playlist_track_positions(session, playlist_id)

    return len(rows), removed_track_ids, removed_episode_ids, removed_remote_ids


async def remove_playlist_tracks(
    session: AsyncSession,
    playlist_id: str,
    track_ids: List[str],
) -> Tuple[int, List[str]]:
    """Remove all occurrences of ``track_ids`` from ``playlist_id``."""
    removed, track_ids_out, _, _ = await remove_playlist_items(session, playlist_id, track_ids=track_ids)
    return removed, track_ids_out


async def renormalize_playlist_track_positions(session: AsyncSession, playlist_id: str) -> None:
    """
    Reassign contiguous 1-based ``position`` values to a playlist's tracks.

    Existing order is preserved; only rows whose position actually changes are
    written.
    """
    result = await session.execute(
        select(PlaylistTrack)
        .where(PlaylistTrack.playlist_id == playlist_id)
        .order_by(PlaylistTrack.position, PlaylistTrack.id)
    )
    rows = list(result.scalars().all())

    changed = False
    for i, row in enumerate(rows, start=1):
        if row.position != i:
            row.position = i
            changed = True

    if changed:
        await session.flush()


def _playlist_item_entity_id(row: PlaylistTrack) -> Optional[str]:
    """Return the entity id (track, podcast episode, or remote object) carried by a playlist row."""
    if row.track_id is not None:
        return str(row.track_id)
    if row.podcast_episode_id is not None:
        return str(row.podcast_episode_id)
    if row.remote_object_id is not None:
        return str(row.remote_object_id)
    return None


async def reorder_playlist_items(
    session: AsyncSession,
    playlist_id: str,
    item_ids: List[str],
    position: Optional[int] = None,
) -> List[str]:
    """
    Move all occurrences of ``item_ids`` to ``position`` in the playlist.

    ``item_ids`` are entity ids — track ids or podcast episode ids. The block
    of items is taken from its current locations, preserving its internal
    order, and inserted at the 1-based rank ``position`` (``None`` means the
    end). Unknown item IDs raise ``ValueError``.
    """
    if not item_ids:
        return []

    seen: Set[str] = set()
    deduped: List[str] = []
    for item_id in item_ids:
        if item_id not in seen:
            seen.add(item_id)
            deduped.append(item_id)

    result = await session.execute(
        select(PlaylistTrack)
        .where(PlaylistTrack.playlist_id == playlist_id)
        .order_by(PlaylistTrack.position, PlaylistTrack.id)
    )
    rows = list(result.scalars().all())
    present = {entity_id for row in rows if (entity_id := _playlist_item_entity_id(row)) is not None}

    for item_id in deduped:
        if item_id not in present:
            raise ValueError(f"Item {item_id} is not in the playlist")

    move_set = set(deduped)
    moving = [row for row in rows if _playlist_item_entity_id(row) in move_set]
    rest = [row for row in rows if _playlist_item_entity_id(row) not in move_set]
    insertion_index = max(1, min(position, len(rest) + 1)) - 1 if position is not None else len(rest)

    final = rest[:insertion_index] + moving + rest[insertion_index:]
    for i, row in enumerate(final, start=1):
        if row.position != i:
            row.position = i

    await session.flush()
    return deduped


async def reorder_playlist_tracks(
    session: AsyncSession,
    playlist_id: str,
    track_ids: List[str],
    position: Optional[int] = None,
) -> List[str]:
    """Move all occurrences of ``track_ids`` to ``position`` in the playlist."""
    return await reorder_playlist_items(session, playlist_id, track_ids, position)


async def list_playlist_tracks(
    session: AsyncSession,
    playlist_id: str,
    user: Optional[User] = None,
    limit: int = 20,
    offset: int = 0,
    include: Optional[Set[str]] = None,
    sort_by: str = "position",
    sort_dir: str = "asc",
    query: Optional[str] = None,
) -> List[Track]:
    """List tracks that are members of ``playlist_id``."""
    stmt = (
        select(Track)
        .options(*_track_selectin_options(include))
        .join(PlaylistTrack, PlaylistTrack.track_id == Track.id)
        .where(PlaylistTrack.playlist_id == playlist_id)
    )
    if query:
        stmt = _apply_collection_tracks_query(stmt, query)
    stmt = apply_access_filter(stmt, Track, user, "track")
    stmt = stmt.order_by(*_playlist_track_sort_clause(sort_by, sort_dir))
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_playlist_tracks(
    session: AsyncSession,
    playlist_id: str,
    user: Optional[User] = None,
    query: Optional[str] = None,
) -> int:
    """Return the total number of tracks in ``playlist_id`` visible to ``user``."""
    stmt = (
        select(Track)
        .join(PlaylistTrack, PlaylistTrack.track_id == Track.id)
        .where(PlaylistTrack.playlist_id == playlist_id)
    )
    if query:
        stmt = _apply_collection_tracks_query(stmt, query)
    stmt = apply_access_filter(stmt, Track, user, "track")
    result = await session.execute(select(func.count()).select_from(stmt.subquery()))
    return result.scalar() or 0


def _playlist_items_stmt(playlist_id: str, user: Optional[User], query: Optional[str]) -> Select[Any]:
    """Build the base statement selecting a playlist's ``PlaylistTrack`` rows.

    Track rows are filtered by the track access predicate (inaccessible tracks
    drop out of listings, as on the tracks endpoint); podcast episode rows are
    always included — the playlist's own visibility is the gate, and episode
    metadata/enclosures are public feed data.
    """
    stmt = (
        select(PlaylistTrack)
        .outerjoin(Track, PlaylistTrack.track_id == Track.id)
        .outerjoin(PodcastEpisode, PlaylistTrack.podcast_episode_id == PodcastEpisode.id)
        .outerjoin(Podcast, PodcastEpisode.podcast_id == Podcast.id)
        .outerjoin(RemoteObject, PlaylistTrack.remote_object_id == RemoteObject.id)
        .where(PlaylistTrack.playlist_id == playlist_id)
    )
    if user is None or not user.is_admin:
        stmt = stmt.where(
            or_(
                Track.id.is_(None),
                _list_access_predicate(Track, user, "track"),
            )
        )
    if user is None:
        # Anonymous callers only see public remote rows (mirrors acl for
        # remote objects); authenticated users see all cached rows.
        stmt = stmt.where(
            or_(
                RemoteObject.id.is_(None),
                RemoteObject.visibility == "public",
            )
        )
    if query:
        stmt = stmt.where(
            or_(
                and_(
                    Track.id.isnot(None),
                    or_(
                        ilike_contains(Track.title, query),
                        Track.artist.has(ilike_contains(Artist.name, query)),
                        Track.album.has(ilike_contains(Album.title, query)),
                        ilike_contains(Track.genre, query),
                        Track.tags.any(ilike_contains(Tag.name, query)),
                        Track.genres.any(ilike_contains(Genre.name, query)),
                    ),
                ),
                and_(
                    PodcastEpisode.id.isnot(None),
                    or_(
                        ilike_contains(PodcastEpisode.title, query),
                        ilike_contains(Podcast.title, query),
                        ilike_contains(Podcast.author, query),
                    ),
                ),
                and_(
                    RemoteObject.id.isnot(None),
                    or_(
                        ilike_contains(RemoteObject.name, query),
                        ilike_contains(RemoteObject.summary, query),
                    ),
                ),
            )
        )
    return stmt


def _playlist_item_sort_clause(sort_by: str, sort_dir: str) -> Tuple[Any, ...]:
    """Return ORDER BY clauses for a mixed playlist item list.

    Episode rows participate in track-oriented sorts via ``coalesce`` so a
    mixed list stays meaningfully ordered (episode title, podcast author as
    artist name, podcast title as album title, publish year as release year).
    """
    if sort_by == "position":
        primary = _order_clause(PlaylistTrack.position, sort_dir)
        secondary = _order_clause(PlaylistTrack.id, sort_dir)
        return (primary, secondary)

    artist_name = select(Artist.name).where(Artist.id == Track.artist_id).scalar_subquery()
    album_title = select(Album.title).where(Album.id == Track.album_id).scalar_subquery()
    album_release_year = select(Album.release_year).where(Album.id == Track.album_id).scalar_subquery()

    field_map = {
        "created_at": PlaylistTrack.created_at,
        "updated_at": PlaylistTrack.updated_at,
        "title": func.coalesce(Track.title, PodcastEpisode.title, RemoteObject.name),
        "artist_name": func.coalesce(artist_name, Podcast.author, Podcast.title),
        "album_title": func.coalesce(album_title, Podcast.title),
        "release_year": func.coalesce(
            Track.release_year,
            album_release_year,
            func.extract("year", PodcastEpisode.published_at),
        ),
    }
    field = field_map.get(sort_by, PlaylistTrack.position)
    nulls_last = sort_by in {"release_year", "album_title", "artist_name", "title"}
    primary = _order_clause(field, sort_dir, nulls_last)
    secondary = _order_clause(PlaylistTrack.position, sort_dir)
    return (primary, secondary)


async def list_playlist_items(
    session: AsyncSession,
    playlist_id: str,
    user: Optional[User] = None,
    limit: int = 20,
    offset: int = 0,
    include: Optional[Set[str]] = None,
    sort_by: str = "position",
    sort_dir: str = "asc",
    query: Optional[str] = None,
) -> List[PlaylistTrack]:
    """List a playlist's membership rows (tracks and podcast episodes) in order."""
    stmt = _playlist_items_stmt(playlist_id, user, query).options(
        selectinload(PlaylistTrack.track).options(*_track_selectin_options(include)),
        selectinload(PlaylistTrack.episode).selectinload(PodcastEpisode.podcast),
        selectinload(PlaylistTrack.remote_object),
    )
    stmt = stmt.order_by(*_playlist_item_sort_clause(sort_by, sort_dir))
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_playlist_items(
    session: AsyncSession,
    playlist_id: str,
    user: Optional[User] = None,
    query: Optional[str] = None,
) -> int:
    """Return the number of playlist items (tracks + episodes) visible to ``user``."""
    stmt = _playlist_items_stmt(playlist_id, user, query)
    result = await session.execute(select(func.count()).select_from(stmt.subquery()))
    return result.scalar() or 0


async def _count_and_duration(session: AsyncSession, stmt: Select[Any]) -> Dict[str, Any]:
    """Return ``track_count``/``total_duration`` aggregates over a tracks statement."""
    sub = stmt.subquery()
    row = (await session.execute(select(func.count(), func.coalesce(func.sum(sub.c.duration), 0.0)))).one()
    return {"track_count": int(row[0]), "total_duration": float(row[1])}


async def get_artist_stats(
    session: AsyncSession,
    artist_id: str,
    user: Optional[User] = None,
) -> Dict[str, Any]:
    """Return aggregate track/album counts for ``artist_id`` visible to ``user``."""
    tracks_stmt = _build_tracks_stmt(session, artist_id=artist_id)
    tracks_stmt = apply_access_filter(tracks_stmt, Track, user, "track")
    track_count = int((await session.execute(select(func.count()).select_from(tracks_stmt.subquery()))).scalar() or 0)
    albums_stmt = apply_access_filter(_build_albums_stmt(artist_id=artist_id), Album, user, "album")
    album_count = int((await session.execute(select(func.count()).select_from(albums_stmt.subquery()))).scalar() or 0)
    return {"track_count": track_count, "album_count": album_count}


async def get_album_stats(
    session: AsyncSession,
    album_id: str,
    user: Optional[User] = None,
) -> Dict[str, Any]:
    """Return aggregate track count and total duration for ``album_id`` visible to ``user``."""
    stmt = _build_tracks_stmt(session, album_id=album_id)
    stmt = apply_access_filter(stmt, Track, user, "track")
    return await _count_and_duration(session, stmt)


async def get_playlist_stats(
    session: AsyncSession,
    playlist_id: str,
    user: Optional[User] = None,
) -> Dict[str, Any]:
    """Return aggregate item counts and total duration for ``playlist_id`` visible to ``user``."""
    stmt = (
        select(Track)
        .join(PlaylistTrack, PlaylistTrack.track_id == Track.id)
        .where(PlaylistTrack.playlist_id == playlist_id)
    )
    stmt = apply_access_filter(stmt, Track, user, "track")
    stats = await _count_and_duration(session, stmt)

    episode_stmt = (
        select(func.count(), func.coalesce(func.sum(PodcastEpisode.duration_seconds), 0.0))
        .select_from(PlaylistTrack)
        .join(PodcastEpisode, PlaylistTrack.podcast_episode_id == PodcastEpisode.id)
        .where(PlaylistTrack.playlist_id == playlist_id)
    )
    row = (await session.execute(episode_stmt)).one()
    stats["episode_count"] = int(row[0])
    stats["total_duration"] += float(row[1])

    remote_count = int(
        (
            await session.execute(
                select(func.count())
                .select_from(PlaylistTrack)
                .where(
                    PlaylistTrack.playlist_id == playlist_id,
                    PlaylistTrack.remote_object_id.is_not(None),
                )
            )
        ).scalar()
        or 0
    )
    stats["track_count"] += remote_count
    return stats


async def get_library_stats(
    session: AsyncSession,
    library_id: str,
    user: Optional[User] = None,
) -> Dict[str, Any]:
    """Return the number of tracks in ``library_id`` visible to ``user``."""
    stmt = (
        select(Track).join(LibraryTrack, LibraryTrack.track_id == Track.id).where(LibraryTrack.library_id == library_id)
    )
    stmt = apply_access_filter(stmt, Track, user, "track")
    track_count = int((await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0)
    remote_count = int(
        (
            await session.execute(
                select(func.count())
                .select_from(LibraryTrack)
                .where(
                    LibraryTrack.library_id == library_id,
                    LibraryTrack.remote_object_id.is_not(None),
                )
            )
        ).scalar()
        or 0
    )
    return {"track_count": track_count + remote_count}


async def resolve_track_ids_for_sync(
    session: AsyncSession,
    *,
    track_id: Optional[str] = None,
    album_id: Optional[str] = None,
    artist_id: Optional[str] = None,
    library_id: Optional[str] = None,
    all_: bool = False,
    user: Optional[User] = None,
    batch_size: int = 1000,
) -> List[str]:
    """Resolve track IDs for a tag sync operation based on the provided scope."""
    if track_id:
        stmt = select(Track.id).where(Track.id == track_id)
        stmt = apply_access_filter(stmt, Track, user, "track")
        result = await session.execute(stmt)
        row = result.scalar_one_or_none()
        return [str(row)] if row is not None else []
    if album_id:
        return await get_track_ids_for_album(session, album_id, user=user)
    if artist_id:
        return await get_track_ids_for_artist(session, artist_id, user=user)

    if library_id is not None or all_:
        track_ids: List[str] = []
        total = await count_tracks(session, library_id=library_id, user=user)
        for offset in range(0, total, batch_size):
            tracks, _ = await list_tracks(
                session,
                library_id=library_id,
                user=user,
                limit=batch_size,
                offset=offset,
                include=None,
            )
            track_ids.extend(str(track.id) for track in tracks)
        return track_ids

    return []
