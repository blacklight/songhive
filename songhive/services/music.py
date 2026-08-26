"""
Music service: CRUD operations for artists, albums, tracks, playlists,
libraries, and radios.
"""

import contextlib
from typing import Any, Dict, List, Optional, Set, Tuple, cast

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models.album import Album
from ..models.artist import Artist
from ..models.library import Library
from ..models.library_track import LibraryTrack
from ..models.playlist import Playlist, PlaylistTrack
from ..models.radio import Radio
from ..models.track import Track
from ..models.user import User
from .acl import apply_access_filter


def _track_selectin_options(include: Optional[Set[str]]) -> List[Any]:
    """Return selectinload options for Track queries."""
    options: List[Any] = [
        selectinload(Track.audio_file),
        selectinload(Track.image_file),
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
    return options


def _build_artists_stmt(query: Optional[str] = None) -> Select[Any]:
    """Build a statement for listing/counting artists."""
    stmt = select(Artist)
    if query:
        stmt = stmt.where(Artist.name.ilike(f"%{query}%"))
    return stmt


async def list_artists(
    session: AsyncSession,
    query: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    include: Optional[Set[str]] = None,
) -> List[Artist]:
    """List artists with optional search."""
    stmt = _build_artists_stmt(query=query).options(*_artist_selectin_options(include))
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_artists(
    session: AsyncSession,
    query: Optional[str] = None,
) -> int:
    """Return the total number of artists matching the optional search."""
    stmt = select(func.count(Artist.id))
    if query:
        stmt = stmt.where(Artist.name.ilike(f"%{query}%"))
    result = await session.execute(stmt)
    return result.scalar() or 0


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
) -> Select[Any]:
    """Build a statement for listing/counting albums."""
    stmt = select(Album)
    if query:
        stmt = stmt.where(Album.title.ilike(f"%{query}%"))
    if artist_id:
        stmt = stmt.where(Album.artist_id == artist_id)
    if year_from is not None:
        stmt = stmt.where(Album.release_year >= year_from)
    if year_to is not None:
        stmt = stmt.where(Album.release_year <= year_to)
    return stmt


async def list_albums(
    session: AsyncSession,
    query: Optional[str] = None,
    artist_id: Optional[str] = None,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    user: Optional[User] = None,
    limit: int = 20,
    offset: int = 0,
    include: Optional[Set[str]] = None,
) -> List[Album]:
    """List albums with optional filters, honouring the requester's ACL."""
    stmt = _build_albums_stmt(
        query=query,
        artist_id=artist_id,
        year_from=year_from,
        year_to=year_to,
    ).options(*_album_selectin_options(include))
    stmt = apply_access_filter(stmt, Album, user, "album")
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_albums(
    session: AsyncSession,
    query: Optional[str] = None,
    artist_id: Optional[str] = None,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    user: Optional[User] = None,
) -> int:
    """Return the total number of albums matching the filters and ACL."""
    stmt = _build_albums_stmt(
        query=query,
        artist_id=artist_id,
        year_from=year_from,
        year_to=year_to,
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
        stmt = stmt.order_by(func.ts_rank(track_search_vector, ts_query).desc())
    else:
        stmt = stmt.join(Artist, Track.artist_id == Artist.id)
        if year_from is None and year_to is None:
            stmt = stmt.outerjoin(Album, Track.album_id == Album.id)
        pattern = f"%{query}%"
        stmt = stmt.where(
            or_(
                Track.title.ilike(pattern),
                Artist.name.ilike(pattern),
                Album.title.ilike(pattern),
            )
        )

    return stmt


def _build_tracks_stmt(
    session: AsyncSession,
    *,
    query: Optional[str] = None,
    artist_id: Optional[str] = None,
    album_id: Optional[str] = None,
    genre: Optional[str] = None,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    library_id: Optional[str] = None,
) -> Select[Any]:
    """Build a statement for listing/counting tracks."""
    stmt = select(Track).order_by(Track.created_at, Track.id)
    if artist_id:
        stmt = stmt.where(Track.artist_id == artist_id)
    if album_id:
        stmt = stmt.where(Track.album_id == album_id)
    if genre:
        stmt = stmt.where(Track.genre == genre)

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

    if query:
        stmt = _apply_tracks_query(session, stmt, query=query, year_from=year_from, year_to=year_to)

    return stmt


async def list_tracks(
    session: AsyncSession,
    query: Optional[str] = None,
    artist_id: Optional[str] = None,
    album_id: Optional[str] = None,
    genre: Optional[str] = None,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    library_id: Optional[str] = None,
    user: Optional[User] = None,
    limit: int = 20,
    offset: int = 0,
    include: Optional[Set[str]] = None,
    around_track_id: Optional[str] = None,
) -> Tuple[List[Track], int]:
    """List tracks with optional filters, honouring the requester's ACL.

    If ``around_track_id`` is given, the returned chunk is centered on that
    track (when it matches the filters and is accessible to the requester).
    The second return value is the effective offset of the returned chunk.
    """
    base_stmt = _build_tracks_stmt(
        session,
        query=query,
        artist_id=artist_id,
        album_id=album_id,
        genre=genre,
        year_from=year_from,
        year_to=year_to,
        library_id=library_id,
    )
    base_stmt = apply_access_filter(base_stmt, Track, user, "track")

    effective_offset = max(0, offset)
    if around_track_id:
        around = await get_track(session, around_track_id, include=None)
        if around is not None:
            exists_stmt = base_stmt.where(Track.id == around.id).limit(1)
            exists = (await session.execute(exists_stmt)).scalar_one_or_none()
            if exists is not None:
                before_stmt = select(func.count()).select_from(
                    base_stmt.where(
                        or_(
                            Track.created_at < around.created_at,
                            and_(
                                Track.created_at == around.created_at,
                                Track.id < around.id,
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
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    library_id: Optional[str] = None,
    user: Optional[User] = None,
) -> int:
    """Return the total number of tracks matching the filters and ACL."""
    stmt = _build_tracks_stmt(
        session,
        query=query,
        artist_id=artist_id,
        album_id=album_id,
        genre=genre,
        year_from=year_from,
        year_to=year_to,
        library_id=library_id,
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


async def list_library_tracks(
    session: AsyncSession,
    library_id: str,
    user: Optional[User] = None,
    limit: int = 20,
    offset: int = 0,
    include: Optional[Set[str]] = None,
) -> List[Track]:
    """List tracks that are members of ``library_id``."""
    stmt = (
        select(Track)
        .options(*_track_selectin_options(include))
        .join(LibraryTrack, LibraryTrack.track_id == Track.id)
        .where(LibraryTrack.library_id == library_id)
        .order_by(Track.created_at)
    )
    stmt = apply_access_filter(stmt, Track, user, "track")
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_library_tracks(
    session: AsyncSession,
    library_id: str,
    user: Optional[User] = None,
) -> int:
    """Return the total number of tracks in ``library_id`` visible to ``user``."""
    stmt = (
        select(Track).join(LibraryTrack, LibraryTrack.track_id == Track.id).where(LibraryTrack.library_id == library_id)
    )
    stmt = apply_access_filter(stmt, Track, user, "track")
    result = await session.execute(select(func.count()).select_from(stmt.subquery()))
    return result.scalar() or 0


async def list_playlists(
    session: AsyncSession,
    user: Optional[User] = None,
    limit: int = 20,
    offset: int = 0,
    include: Optional[Set[str]] = None,
) -> List[Playlist]:
    """List playlists visible to ``user``."""
    stmt = select(Playlist).options(*_playlist_selectin_options(include))
    stmt = apply_access_filter(stmt, Playlist, user, "playlist")
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_playlists(
    session: AsyncSession,
    user: Optional[User] = None,
) -> int:
    """Return the total number of playlists visible to ``user``."""
    stmt = select(Playlist)
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
    limit: int = 20,
    offset: int = 0,
    include: Optional[Set[str]] = None,
) -> List[Library]:
    """List libraries visible to ``user``."""
    stmt = select(Library).options(*_library_selectin_options(include))
    stmt = apply_access_filter(stmt, Library, user, "library")
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_libraries(
    session: AsyncSession,
    user: Optional[User] = None,
) -> int:
    """Return the total number of libraries visible to ``user``."""
    stmt = select(Library)
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
    limit: int = 20,
    offset: int = 0,
) -> List[Radio]:
    """List radios visible to ``user``."""
    stmt = select(Radio)
    stmt = apply_access_filter(stmt, Radio, user, "radio")
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_radios(
    session: AsyncSession,
    user: Optional[User] = None,
) -> int:
    """Return the total number of radios visible to ``user``."""
    stmt = select(Radio)
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
) -> List[str]:
    """Return the IDs of accessible tracks that belong to ``album_id``."""
    stmt = (
        select(Track.id)
        .where(Track.album_id == album_id)
        .order_by(Track.track_number, Track.disc_number, Track.created_at)
    )
    stmt = apply_access_filter(stmt, Track, user, "track")
    result = await session.execute(stmt)
    return [str(row) for row in result.scalars().all()]


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
    added_by_id: Optional[str] = None,
) -> List[str]:
    """
    Insert ``LibraryTrack`` rows for ``track_ids`` into ``library_id``.

    Duplicate rows are skipped because of the unique constraint. The returned
    list preserves the input order and contains only the IDs that were actually
    added.
    """
    if not track_ids:
        return []

    result = await session.execute(
        select(LibraryTrack.track_id).where(
            LibraryTrack.library_id == library_id,
            LibraryTrack.track_id.in_(track_ids),
        )
    )
    existing = {str(row) for row in result.scalars().all()}

    added: List[str] = []
    rows: List[LibraryTrack] = []
    seen: set[str] = set()
    for track_id in track_ids:
        if track_id in existing or track_id in seen:
            continue
        seen.add(track_id)
        rows.append(LibraryTrack(library_id=library_id, track_id=track_id, added_by_id=added_by_id))
        added.append(track_id)

    if rows:
        session.add_all(rows)
        await session.flush()

    return added


async def remove_library_tracks(
    session: AsyncSession,
    library_id: str,
    track_ids: List[str],
) -> List[str]:
    """
    Remove ``track_ids`` from ``library_id``.

    Returns the IDs that were actually removed, preserving input order.
    """
    if not track_ids:
        return []

    result = await session.execute(
        select(LibraryTrack).where(
            LibraryTrack.library_id == library_id,
            LibraryTrack.track_id.in_(track_ids),
        )
    )
    rows = list(result.scalars().all())
    removed_track_ids = {str(row.track_id) for row in rows}

    for row in rows:
        await session.delete(row)

    removed: List[str] = []
    seen: Set[str] = set()
    for track_id in track_ids:
        if track_id in removed_track_ids and track_id not in seen:
            seen.add(track_id)
            removed.append(track_id)

    if rows:
        await session.flush()

    return removed


async def add_playlist_tracks(
    session: AsyncSession,
    playlist_id: str,
    track_ids: List[str],
) -> List[str]:
    """
    Append ``track_ids`` to ``playlist_id`` at the end of the playlist.

    New ``PlaylistTrack`` rows are assigned increasing ``position`` values. The
    returned list contains the IDs that were appended, in input order.
    """
    if not track_ids:
        return []

    result = await session.execute(
        select(func.max(PlaylistTrack.position)).where(PlaylistTrack.playlist_id == playlist_id)
    )
    max_position = result.scalar() or 0

    added: List[str] = []
    rows: List[PlaylistTrack] = []
    for offset, track_id in enumerate(track_ids, start=1):
        rows.append(PlaylistTrack(playlist_id=playlist_id, track_id=track_id, position=max_position + offset))
        added.append(track_id)

    if rows:
        session.add_all(rows)
        await session.flush()

    return added


async def remove_playlist_tracks(
    session: AsyncSession,
    playlist_id: str,
    track_ids: List[str],
) -> Tuple[int, List[str]]:
    """
    Remove all occurrences of ``track_ids`` from ``playlist_id``.

    Returns the number of rows removed and the distinct track IDs that were
    removed.
    """
    if not track_ids:
        return 0, []

    result = await session.execute(
        select(PlaylistTrack).where(
            PlaylistTrack.playlist_id == playlist_id,
            PlaylistTrack.track_id.in_(track_ids),
        )
    )
    rows = list(result.scalars().all())
    removed_track_counts: Dict[str, int] = {}
    for row in rows:
        await session.delete(row)
        track_id = str(row.track_id)
        removed_track_counts[track_id] = removed_track_counts.get(track_id, 0) + 1

    removed_ids: List[str] = []
    seen: Set[str] = set()
    for track_id in track_ids:
        if track_id in removed_track_counts and track_id not in seen:
            seen.add(track_id)
            removed_ids.append(track_id)

    if rows:
        await session.flush()

    return len(rows), removed_ids


async def list_playlist_tracks(
    session: AsyncSession,
    playlist_id: str,
    user: Optional[User] = None,
    limit: int = 20,
    offset: int = 0,
    include: Optional[Set[str]] = None,
) -> List[Track]:
    """List tracks that are members of ``playlist_id`` in playlist order."""
    stmt = (
        select(Track)
        .options(*_track_selectin_options(include))
        .join(PlaylistTrack, PlaylistTrack.track_id == Track.id)
        .where(PlaylistTrack.playlist_id == playlist_id)
        .order_by(PlaylistTrack.position)
    )
    stmt = apply_access_filter(stmt, Track, user, "track")
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_playlist_tracks(
    session: AsyncSession,
    playlist_id: str,
    user: Optional[User] = None,
) -> int:
    """Return the total number of tracks in ``playlist_id`` visible to ``user``."""
    stmt = (
        select(Track)
        .join(PlaylistTrack, PlaylistTrack.track_id == Track.id)
        .where(PlaylistTrack.playlist_id == playlist_id)
    )
    stmt = apply_access_filter(stmt, Track, user, "track")
    result = await session.execute(select(func.count()).select_from(stmt.subquery()))
    return result.scalar() or 0
