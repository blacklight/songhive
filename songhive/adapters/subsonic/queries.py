"""
Database helpers shared by the Subsonic routes and serializers.

These exist to keep list endpoints free of N+1 queries: aggregate stats
(song counts, durations, play counts, album counts, favorite timestamps)
are batched here and passed into the pure serializers.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.album import Album
from ...models.artist import Artist
from ...models.favorite import Favorite
from ...models.genre import Genre, GenreAlbum, GenreTrack
from ...models.library_track import LibraryTrack
from ...models.playlist import PlaylistTrack
from ...models.track import Track
from ...models.user import User
from ...services._common import ilike_contains
from ...services.acl import apply_access_filter
from ...services.music import renormalize_playlist_track_positions


async def favorite_map(
    session: AsyncSession,
    user: User,
    track_ids: Sequence[str],
) -> Dict[str, datetime]:
    """Return ``{track_id: favorited_at}`` for the user's favorite tracks."""
    if not track_ids:
        return {}
    result = await session.execute(
        select(Favorite.track_id, Favorite.created_at).where(
            Favorite.user_id == user.id,
            Favorite.track_id.in_(track_ids),
        )
    )
    return {str(row[0]): row[1] for row in result.all()}


async def album_stats_map(
    session: AsyncSession,
    user: Optional[User],
    album_ids: Sequence[str],
) -> Dict[str, Dict[str, Any]]:
    """Return ``{album_id: {"song_count", "duration", "play_count"}}`` for accessible tracks."""
    if not album_ids:
        return {}
    stmt = select(
        Track.album_id,
        func.count(Track.id),
        func.coalesce(func.sum(Track.duration), 0.0),
        func.coalesce(func.sum(Track.play_count), 0),
    ).where(Track.album_id.in_(album_ids))
    stmt = apply_access_filter(stmt, Track, user, "track")
    stmt = stmt.group_by(Track.album_id)

    result = await session.execute(stmt)
    return {
        str(album_id): {
            "song_count": int(count),
            "duration": float(duration),
            "play_count": int(play_count),
        }
        for album_id, count, duration, play_count in result.all()
    }


async def artist_album_count_map(
    session: AsyncSession,
    user: Optional[User],
    artist_ids: Sequence[str],
) -> Dict[str, int]:
    """Return ``{artist_id: accessible_album_count}`` for the given artists."""
    if not artist_ids:
        return {}
    stmt = select(Album.artist_id, func.count(Album.id)).where(Album.artist_id.in_(artist_ids))
    stmt = apply_access_filter(stmt, Album, user, "album")
    stmt = stmt.group_by(Album.artist_id)

    result = await session.execute(stmt)
    return {str(artist_id): int(count) for artist_id, count in result.all()}


async def genre_counts(
    session: AsyncSession,
    user: Optional[User],
) -> List[Tuple[str, int, int]]:
    """Return ``(name, song_count, album_count)`` rows for every visible genre."""
    track_stmt = (
        select(Genre.name, func.count(GenreTrack.track_id))
        .select_from(GenreTrack)
        .join(Genre, GenreTrack.genre_id == Genre.id)
        .join(Track, GenreTrack.track_id == Track.id)
    )
    track_stmt = apply_access_filter(track_stmt, Track, user, "track")
    track_stmt = track_stmt.group_by(Genre.name)

    album_stmt = (
        select(Genre.name, func.count(GenreAlbum.album_id))
        .select_from(GenreAlbum)
        .join(Genre, GenreAlbum.genre_id == Genre.id)
        .join(Album, GenreAlbum.album_id == Album.id)
    )
    album_stmt = apply_access_filter(album_stmt, Album, user, "album")
    album_stmt = album_stmt.group_by(Genre.name)

    counts: Dict[str, List[int]] = {}
    for name, count in (await session.execute(track_stmt)).all():
        counts.setdefault(name, [0, 0])[0] = int(count)
    for name, count in (await session.execute(album_stmt)).all():
        counts.setdefault(name, [0, 0])[1] = int(count)

    return sorted(((name, pair[0], pair[1]) for name, pair in counts.items()), key=lambda row: row[0])


def library_track_ids_query(library_id: str):
    """Return a ``(track_id,)`` select limited to a library's members."""
    return select(LibraryTrack.track_id).where(LibraryTrack.library_id == library_id)


async def artist_ids_in_library(session: AsyncSession, library_id: str) -> List[str]:
    """Return ids of artists that have at least one track in ``library_id``."""
    result = await session.execute(
        select(Track.artist_id).where(Track.id.in_(library_track_ids_query(library_id))).group_by(Track.artist_id)
    )
    return [str(row) for row in result.scalars().all()]


async def album_ids_in_library(session: AsyncSession, library_id: str) -> List[str]:
    """Return ids of albums that have at least one track in ``library_id``."""
    result = await session.execute(
        select(Track.album_id)
        .where(Track.id.in_(library_track_ids_query(library_id)), Track.album_id.isnot(None))
        .group_by(Track.album_id)
    )
    return [str(row) for row in result.scalars().all()]


async def accessible_artist_map(
    session: AsyncSession,
    user: Optional[User],
    *,
    artist_ids: Optional[List[str]] = None,
    query: Optional[str] = None,
) -> List[Artist]:
    """Return accessible artists, optionally restricted to explicit ids or a name query."""
    stmt = select(Artist).order_by(Artist.name)
    if artist_ids is not None:
        if not artist_ids:
            return []
        stmt = stmt.where(Artist.id.in_(artist_ids))
    if query:
        stmt = stmt.where(ilike_contains(Artist.name, query))
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
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def remove_playlist_tracks_at_indexes(
    session: AsyncSession,
    playlist_id: str,
    indexes: Sequence[int],
) -> int:
    """Remove playlist entries at the given 0-based positions; returns the removal count."""
    if not indexes:
        return 0
    result = await session.execute(
        select(PlaylistTrack)
        .where(PlaylistTrack.playlist_id == playlist_id)
        .order_by(PlaylistTrack.position, PlaylistTrack.id)
    )
    rows = list(result.scalars().all())

    removed = 0
    for index in sorted(set(indexes)):
        if 0 <= index < len(rows):
            await session.delete(rows[index])
            removed += 1

    if removed:
        await session.flush()
        await renormalize_playlist_track_positions(session, playlist_id)
    return removed


def index_letter(name: str) -> str:
    """Return the A-Z index letter for an artist name (``#`` for non-letters)."""
    for char in name.strip():
        if char.isalpha():
            return char.upper()
        if char.isdigit():
            continue
        break
    return "#"


def group_artists_by_index(artists: Sequence[Artist]) -> List[Dict[str, Any]]:
    """Group artists into Subsonic ``index`` entries ordered A-Z then ``#``."""
    groups: Dict[str, List[Artist]] = {}
    for artist in artists:
        groups.setdefault(index_letter(artist.name), []).append(artist)

    def _sort_key(letter: str) -> Tuple[int, str]:
        return (0, letter) if letter != "#" else (1, "#")

    return [{"name": letter, "artist": groups[letter]} for letter in sorted(groups, key=_sort_key)]
