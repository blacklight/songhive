"""
Personal listening statistics.

Aggregates ``listening_history`` rows for a single user into the shapes the
``/api/v1/stats`` endpoints return. Top-N lists and release-year grouping run
in SQL (portable ``GROUP BY``/``ORDER BY``); calendar and hour-of-day
bucketing runs in Python over a ``created_at`` range scan so the same code
serves PostgreSQL and SQLite, and so buckets honor the caller's IANA
timezone.

Remote (federated) listens have no local ``Track`` row: they count towards
plays/clock/top-track/artist/album charts through the cached ``RemoteObject``
payload, but are excluded from genre and release-year charts, which have no
remote metadata source.
"""

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Literal, Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.album import Album
from ..models.artist import Artist
from ..models.genre import Genre, GenreTrack
from ..models.history import ListeningHistory
from ..models.remote_object import RemoteObject
from ..models.track import Track
from . import remote_content
from .genres import split_genre_string
from .storage import StorageService

PeriodGroupBy = Literal["day", "week", "month", "year"]
ReleaseGroupBy = Literal["decade", "year"]

UTC = timezone.utc


def _range_conditions(user_id: str, from_dt: datetime, to_dt: datetime):
    return (
        ListeningHistory.user_id == user_id,
        ListeningHistory.created_at >= from_dt,
        ListeningHistory.created_at <= to_dt,
    )


# ---------------------------------------------------------------------------
# Calendar bucketing (Python-side, timezone-aware)
# ---------------------------------------------------------------------------


def _bucket_key(day: date, group_by: PeriodGroupBy, week_start: int = 0) -> date:
    """Return the sortable bucket key (a date) containing ``day``.

    ``week_start`` is the first weekday in Python's convention (0 = Monday …
    6 = Sunday); the caller's locale decides which day that is.
    """
    if group_by == "week":
        return day - timedelta(days=(day.weekday() - week_start) % 7)
    if group_by == "month":
        return day.replace(day=1)
    if group_by == "year":
        return day.replace(month=1, day=1)
    return day


def _bucket_label(key: date, group_by: PeriodGroupBy) -> str:
    # Day and week buckets both serialize as the bucket's first date; the
    # frontend renders it in the caller's locale.
    if group_by == "month":
        return f"{key.year:04d}-{key.month:02d}"
    if group_by == "year":
        return f"{key.year:04d}"
    return key.isoformat()


def _next_bucket(key: date, group_by: PeriodGroupBy) -> date:
    if group_by == "week":
        return key + timedelta(days=7)
    if group_by == "month":
        year = key.year + (1 if key.month == 12 else 0)
        month = 1 if key.month == 12 else key.month + 1
        return key.replace(year=year, month=month)
    if group_by == "year":
        return key.replace(year=key.year + 1)
    return key + timedelta(days=1)


def _bucket_keys(
    from_dt: datetime,
    to_dt: datetime,
    tz: ZoneInfo,
    group_by: PeriodGroupBy,
    week_start: int = 0,
) -> list[date]:
    """Every bucket key between ``from_dt`` and ``to_dt`` in local time."""
    start = _bucket_key(from_dt.astimezone(tz).date(), group_by, week_start)
    end = _bucket_key(to_dt.astimezone(tz).date(), group_by, week_start)
    keys = []
    key = start
    while key <= end:
        keys.append(key)
        key = _next_bucket(key, group_by)
    return keys


async def _created_at_values(
    session: AsyncSession,
    user_id: str,
    from_dt: datetime,
    to_dt: datetime,
) -> list[datetime]:
    rows = await session.execute(
        select(ListeningHistory.created_at)
        .where(*_range_conditions(user_id, from_dt, to_dt))
        .order_by(ListeningHistory.created_at)
    )
    return list(rows.scalars().all())


async def plays_per_period(
    session: AsyncSession,
    user_id: str,
    from_dt: datetime,
    to_dt: datetime,
    group_by: PeriodGroupBy = "day",
    tz: ZoneInfo = ZoneInfo("UTC"),
    week_start: int = 0,
) -> list[dict]:
    """Histogram of plays per calendar bucket (day/week/month/year)."""
    counts: Counter[date] = Counter()
    for created_at in await _created_at_values(session, user_id, from_dt, to_dt):
        counts[_bucket_key(created_at.astimezone(tz).date(), group_by, week_start)] += 1

    return [
        {"bucket": _bucket_label(key, group_by), "count": counts.get(key, 0)}
        for key in _bucket_keys(from_dt, to_dt, tz, group_by, week_start)
    ]


async def listening_clock(
    session: AsyncSession,
    user_id: str,
    from_dt: datetime,
    to_dt: datetime,
    tz: ZoneInfo = ZoneInfo("UTC"),
) -> list[dict]:
    """Plays grouped by local hour of day — always 24 entries."""
    counts: Counter[int] = Counter()
    for created_at in await _created_at_values(session, user_id, from_dt, to_dt):
        counts[created_at.astimezone(tz).hour] += 1

    return [{"hour": hour, "count": counts.get(hour, 0)} for hour in range(24)]


# ---------------------------------------------------------------------------
# Genres
# ---------------------------------------------------------------------------


def _no_genre_association():
    """Filter selecting tracks without any normalized genre association."""
    return ~select(GenreTrack.id).where(GenreTrack.track_id == Track.id).exists()


def _genre_queries(user_id: str, from_dt: datetime, to_dt: datetime):
    """Return (normalized, free-text) ``(created_at, genre_name)`` queries."""
    normalized = (
        select(ListeningHistory.created_at, Genre.name)
        .select_from(ListeningHistory)
        .join(Track, Track.id == ListeningHistory.track_id)
        .join(GenreTrack, GenreTrack.track_id == Track.id)
        .join(Genre, Genre.id == GenreTrack.genre_id)
        .where(*_range_conditions(user_id, from_dt, to_dt))
    )
    free_text = (
        select(ListeningHistory.created_at, Track.genre)
        .select_from(ListeningHistory)
        .join(Track, Track.id == ListeningHistory.track_id)
        .where(
            *_range_conditions(user_id, from_dt, to_dt),
            Track.genre.is_not(None),
            _no_genre_association(),
        )
    )
    return normalized, free_text


def _genre_key(name: str) -> str:
    """Canonical grouping key so genre casings merge (``Rock`` ≡ ``rock``)."""
    return name.strip().casefold()


async def genres_timeline(
    session: AsyncSession,
    user_id: str,
    from_dt: datetime,
    to_dt: datetime,
    group_by: PeriodGroupBy = "week",
    tz: ZoneInfo = ZoneInfo("UTC"),
    week_start: int = 0,
) -> list[dict]:
    """Stacked timeline of plays per genre per calendar bucket."""
    normalized, free_text = _genre_queries(user_id, from_dt, to_dt)

    buckets: dict[date, Counter] = defaultdict(Counter)
    for created_at, name in await session.execute(normalized):
        if name:
            buckets[_bucket_key(created_at.astimezone(tz).date(), group_by, week_start)][_genre_key(name)] += 1
    for created_at, raw in await session.execute(free_text):
        key = _bucket_key(created_at.astimezone(tz).date(), group_by, week_start)
        for name in split_genre_string(raw):
            buckets[key][name] += 1

    return [
        {
            "bucket": _bucket_label(key, group_by),
            "genres": [{"name": name, "count": count} for name, count in buckets.get(key, Counter()).most_common()],
        }
        for key in _bucket_keys(from_dt, to_dt, tz, group_by)
    ]


# ---------------------------------------------------------------------------
# Release-year histogram
# ---------------------------------------------------------------------------


async def release_histogram(
    session: AsyncSession,
    user_id: str,
    from_dt: datetime,
    to_dt: datetime,
    group_by: ReleaseGroupBy = "decade",
) -> list[dict]:
    """Plays grouped by the played track's release decade or year."""
    rows = (
        await session.execute(
            select(Track.release_year, func.count().label("plays"))
            .select_from(ListeningHistory)
            .join(Track, Track.id == ListeningHistory.track_id)
            .where(*_range_conditions(user_id, from_dt, to_dt))
            .group_by(Track.release_year)
        )
    ).all()

    counts: Counter[str] = Counter()
    for year, plays in rows:
        if year is None:
            continue
        label = f"{(year // 10) * 10}s" if group_by == "decade" else str(year)
        counts[label] += plays

    def _sort_key(item) -> int:
        label = item[0]
        return int(label[:-1] if label.endswith("s") else label)

    return [{"bucket": label, "count": count} for label, count in sorted(counts.items(), key=_sort_key)]


# ---------------------------------------------------------------------------
# Top-N lists
# ---------------------------------------------------------------------------


async def _remote_object_map(session: AsyncSession, ids: Iterable[str]) -> dict[str, RemoteObject]:
    """
    Fetch remote objects by id, folding renditions onto their media entity.

    ``Audio``/``Video`` rendition rows point at their metadata entity through
    ``media_of_url``; resolving that link attributes stats to the track's
    embedded metadata instead of the rendition wrapper.
    """
    id_list = [str(i) for i in ids]
    if not id_list:
        return {}
    rows = list((await session.execute(select(RemoteObject).where(RemoteObject.id.in_(id_list)))).scalars().all())

    entities = {}
    entity_urls = [row.media_of_url for row in rows if row.media_of_url]
    if entity_urls:
        entity_rows = (
            (await session.execute(select(RemoteObject).where(RemoteObject.canonical_url.in_(entity_urls))))
            .scalars()
            .all()
        )
        entities = {row.canonical_url: row for row in entity_rows}

    return {str(row.id): entities.get(row.media_of_url, row) if row.media_of_url else row for row in rows}


def _top_entries(counts: Counter, limit: int, details: Optional[dict] = None) -> list[dict]:
    return [
        {"name": name, "play_count": count, **(details or {}).get(name, {})}
        for name, count in counts.most_common(limit)
    ]


async def _artist_image_url(artist: Artist, storage: StorageService) -> Optional[str]:
    if artist.image_file_id and artist.image_file:
        return await storage.get_url(artist.image_file)
    return artist.image_url


async def _album_cover_url(album: Album, storage: StorageService) -> Optional[str]:
    if album.cover_file_id and album.cover_file:
        return await storage.get_url(album.cover_file)
    return album.cover_url


async def _track_image_url(track: Track, storage: StorageService) -> Optional[str]:
    """Track artwork, falling back to the album cover (both selectin-loaded)."""
    if track.image_file_id and track.image_file:
        return await storage.get_url(track.image_file)
    if track.album is not None:
        return await _album_cover_url(track.album, storage)
    return None


async def top_listens(
    session: AsyncSession,
    user_id: str,
    from_dt: datetime,
    to_dt: datetime,
    storage: StorageService,
    limit: int = 10,
) -> dict:
    """Top played artists, albums, tracks and genres over the period.

    Entries carry a ``url`` for the entity page plus artwork (``image_url``)
    when the entities live locally; remote-only names render without links.
    ``storage`` resolves stored-file artwork URLs.
    """
    conditions = _range_conditions(user_id, from_dt, to_dt)

    play_rows = (
        await session.execute(
            select(
                ListeningHistory.track_id,
                ListeningHistory.remote_object_id,
                func.count().label("plays"),
            )
            .where(*conditions)
            .group_by(ListeningHistory.track_id, ListeningHistory.remote_object_id)
        )
    ).all()
    artist_rows = (
        await session.execute(
            select(Track.artist_id, func.count().label("plays"))
            .select_from(ListeningHistory)
            .join(Track, Track.id == ListeningHistory.track_id)
            .where(*conditions)
            .group_by(Track.artist_id)
        )
    ).all()
    album_rows = (
        await session.execute(
            select(Track.album_id, func.count().label("plays"))
            .select_from(ListeningHistory)
            .join(Track, Track.id == ListeningHistory.track_id)
            .where(*conditions, Track.album_id.is_not(None))
            .group_by(Track.album_id)
        )
    ).all()
    remote_rows = (
        await session.execute(
            select(ListeningHistory.remote_object_id, func.count().label("plays"))
            .where(*conditions, ListeningHistory.remote_object_id.is_not(None))
            .group_by(ListeningHistory.remote_object_id)
        )
    ).all()

    genre_rows = (
        await session.execute(
            select(Genre.name, func.count().label("plays"))
            .select_from(ListeningHistory)
            .join(Track, Track.id == ListeningHistory.track_id)
            .join(GenreTrack, GenreTrack.track_id == Track.id)
            .join(Genre, Genre.id == GenreTrack.genre_id)
            .where(*conditions)
            .group_by(Genre.name)
        )
    ).all()
    free_genre_rows = (
        await session.execute(
            select(Track.genre, func.count().label("plays"))
            .select_from(ListeningHistory)
            .join(Track, Track.id == ListeningHistory.track_id)
            .where(*conditions, Track.genre.is_not(None), _no_genre_association())
            .group_by(Track.genre)
        )
    ).all()

    track_ids = [row.track_id for row in play_rows if row.track_id is not None]
    tracks_by_id = (
        {t.id: t for t in (await session.execute(select(Track).where(Track.id.in_(track_ids)))).scalars()}
        if track_ids
        else {}
    )
    album_ids = {row[0] for row in album_rows} | {t.album_id for t in tracks_by_id.values() if t.album_id}
    albums_by_id = (
        {a.id: a for a in (await session.execute(select(Album).where(Album.id.in_(album_ids)))).scalars()}
        if album_ids
        else {}
    )
    artist_ids = (
        {row[0] for row in artist_rows}
        | {t.artist_id for t in tracks_by_id.values()}
        | {a.artist_id for a in albums_by_id.values() if a.artist_id}
    )
    artists_by_id = (
        {a.id: a for a in (await session.execute(select(Artist).where(Artist.id.in_(artist_ids)))).scalars()}
        if artist_ids
        else {}
    )
    remote_map = await _remote_object_map(
        session,
        {row.remote_object_id for row in play_rows if row.remote_object_id is not None}
        | {row[0] for row in remote_rows},
    )

    tracks: Counter = Counter()
    track_details: dict[str, dict] = {}
    for row in play_rows:
        name: Optional[str] = None
        if row.track_id is not None:
            track = tracks_by_id.get(row.track_id)
            if track is not None:
                name = track.title
                if name not in track_details:
                    artist = artists_by_id.get(track.artist_id)
                    track_details[name] = {
                        "url": f"/tracks/{track.id}",
                        "image_url": await _track_image_url(track, storage),
                        "artist_name": artist.name if artist else None,
                        "artist_url": f"/artists/{artist.id}" if artist else None,
                    }
        else:
            remote = remote_map.get(str(row.remote_object_id))
            if remote is not None:
                name = remote_content.remote_object_display_name(remote)
                if name and name not in track_details:
                    fields = remote_content.remote_object_music_fields(remote)
                    track_details[name] = {
                        "url": f"/remote/{remote.resource_type}/{remote.id}" if remote.resource_type else None,
                        "image_url": remote.image_url,
                        "artist_name": fields["artist_name"],
                    }
        if name:
            tracks[name] += row.plays

    artists: Counter = Counter()
    artist_details: dict[str, dict] = {}
    for artist_id, plays in artist_rows:
        artist = artists_by_id.get(artist_id)
        if artist is None:
            continue
        artists[artist.name] += plays
        artist_details.setdefault(
            artist.name,
            {"url": f"/artists/{artist.id}", "image_url": await _artist_image_url(artist, storage)},
        )

    albums: Counter = Counter()
    album_details: dict[str, dict] = {}
    for album_id, plays in album_rows:
        album = albums_by_id.get(album_id)
        if album is None:
            continue
        albums[album.title] += plays
        if album.title not in album_details:
            artist = artists_by_id.get(album.artist_id)
            album_details[album.title] = {
                "url": f"/albums/{album.id}",
                "image_url": await _album_cover_url(album, storage),
                "artist_name": artist.name if artist else None,
                "artist_url": f"/artists/{artist.id}" if artist else None,
            }

    for remote_id, plays in remote_rows:
        remote = remote_map.get(str(remote_id))
        if remote is None:
            continue
        fields = remote_content.remote_object_music_fields(remote)
        if fields["artist_name"]:
            artists[fields["artist_name"]] += plays
        if fields["album_name"]:
            albums[fields["album_name"]] += plays
            album_details.setdefault(fields["album_name"], {"image_url": remote.image_url})

    genres: Counter = Counter()
    for name, plays in genre_rows:
        if name:
            genres[_genre_key(name)] += plays
    for raw, plays in free_genre_rows:
        for name in split_genre_string(raw):
            genres[name] += plays

    top_genre_names = [name for name, _ in genres.most_common(limit)]
    genre_urls: dict[str, dict] = {}
    if top_genre_names:
        existing = {
            _genre_key(row[0])
            for row in (
                await session.execute(select(Genre.name).where(func.lower(Genre.name).in_(top_genre_names)))
            ).all()
        }
        genre_urls = {name: {"url": f"/genres/{quote(name, safe='')}"} for name in top_genre_names if name in existing}

    return {
        "artists": _top_entries(artists, limit, artist_details),
        "albums": _top_entries(albums, limit, album_details),
        "tracks": _top_entries(tracks, limit, track_details),
        "genres": _top_entries(genres, limit, genre_urls),
    }
