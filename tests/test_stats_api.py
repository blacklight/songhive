"""
Tests for the personal listening statistics API.
"""

from datetime import datetime, timedelta, timezone

import pytest

from songhive.models._enums import Visibility
from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.genre import Genre, GenreTrack
from songhive.models.history import ListeningHistory
from songhive.models.remote_object import RemoteObject
from songhive.models.track import Track

NOW = datetime(2026, 9, 10, 15, 0, 0, tzinfo=timezone.utc)


async def _track(db_session, owner, *, title="Song", artist=None, album=None, genre=None, release_year=None):
    if artist is None:
        artist = Artist(name="Artist")
        db_session.add(artist)
        await db_session.flush()
    track = Track(
        title=title,
        artist_id=artist.id,
        album_id=album.id if album else None,
        genre=genre,
        release_year=release_year,
        owner_id=str(owner.id),
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(track)
    await db_session.flush()
    return track


async def _remote_track(db_session):
    remote = RemoteObject(
        canonical_url="https://remote.example/uploads/1",
        domain="remote.example",
        object_type="Audio",
        resource_type="track",
        actor_url="https://remote.example/users/bob",
        name="Remote Band - Remote Album - Remote Song",
        payload={
            "type": "Audio",
            "id": "https://remote.example/uploads/1",
            "track": {
                "type": "Track",
                "name": "Remote Song",
                "artists": [{"name": "Remote Band"}],
                "album": {"name": "Remote Album"},
            },
        },
    )
    db_session.add(remote)
    await db_session.flush()
    return remote


def _listen(db_session, user, *, track=None, remote=None, played_at=NOW):
    entry = ListeningHistory(
        user_id=str(user.id),
        track_id=str(track.id) if track is not None else None,
        remote_object_id=str(remote.id) if remote is not None else None,
        created_at=played_at,
    )
    db_session.add(entry)
    return entry


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["top", "plays", "genres-timeline", "releases", "clock"])
async def test_stats_endpoints_require_auth(client, path):
    """All stats endpoints require authentication."""
    response = client.get(f"/api/v1/stats/{path}")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_top_stats(client, db_session, regular_user, auth_headers):
    """GET /stats/top ranks artists, albums, tracks and genres by play count."""
    artist = Artist(name="Top Artist")
    other_artist = Artist(name="Other Artist")
    db_session.add_all([artist, other_artist])
    await db_session.flush()
    album = Album(title="Top Album", artist_id=artist.id)
    db_session.add(album)
    await db_session.flush()

    track = await _track(db_session, regular_user, title="Hit", artist=artist, album=album)
    other = await _track(db_session, regular_user, title="Flop", artist=other_artist)
    rock = Genre(name="rock")
    jazz = Genre(name="jazz")
    db_session.add_all([rock, jazz])
    await db_session.flush()
    db_session.add_all(
        [
            GenreTrack(genre_id=rock.id, track_id=track.id),
            GenreTrack(genre_id=jazz.id, track_id=track.id, inherited=True),
        ]
    )

    for _ in range(3):
        _listen(db_session, regular_user, track=track)
    _listen(db_session, regular_user, track=other)
    await db_session.commit()

    response = client.get("/api/v1/stats/top", headers=auth_headers(regular_user))
    assert response.status_code == 200
    data = response.json()

    assert [(a["name"], a["play_count"], a["url"]) for a in data["artists"]] == [
        ("Top Artist", 3, f"/artists/{artist.id}"),
        ("Other Artist", 1, f"/artists/{other_artist.id}"),
    ]
    assert [(a["name"], a["play_count"], a["url"], a["artist_name"]) for a in data["albums"]] == [
        ("Top Album", 3, f"/albums/{album.id}", "Top Artist"),
    ]
    assert [(t_["name"], t_["play_count"], t_["url"], t_["artist_url"]) for t_ in data["tracks"]] == [
        ("Hit", 3, f"/tracks/{track.id}", f"/artists/{artist.id}"),
        ("Flop", 1, f"/tracks/{other.id}", f"/artists/{other_artist.id}"),
    ]
    # Album-inherited genres count alongside direct associations.
    assert {g["name"]: (g["play_count"], g["url"]) for g in data["genres"]} == {
        "rock": (3, "/genres/rock"),
        "jazz": (3, "/genres/jazz"),
    }


@pytest.mark.asyncio
async def test_top_stats_limit_and_period_filter(client, db_session, regular_user, auth_headers):
    """`limit` caps each list and `from`/`to` narrow the counted plays."""
    artist = Artist(name="Artist")
    db_session.add(artist)
    await db_session.flush()
    first = await _track(db_session, regular_user, title="First", artist=artist)
    second = await _track(db_session, regular_user, title="Second", artist=artist)

    old = NOW - timedelta(days=90)
    _listen(db_session, regular_user, track=first, played_at=old)
    _listen(db_session, regular_user, track=first)
    _listen(db_session, regular_user, track=first)
    _listen(db_session, regular_user, track=second)
    await db_session.commit()

    headers = auth_headers(regular_user)
    response = client.get(
        "/api/v1/stats/top",
        params={
            "from": (NOW - timedelta(days=1)).isoformat(),
            "to": (NOW + timedelta(days=1)).isoformat(),
            "limit": 1,
        },
        headers=headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert [(t_["name"], t_["play_count"]) for t_ in data["tracks"]] == [("First", 2)]


@pytest.mark.asyncio
async def test_top_stats_free_text_genre_fallback(client, db_session, regular_user, auth_headers):
    """Tracks without normalized genres fall back to the free-text genre."""
    tagged = await _track(db_session, regular_user, title="Tagged", genre="Ignored")
    free = await _track(db_session, regular_user, title="Free", genre="Synthwave")
    rock = Genre(name="rock")
    db_session.add(rock)
    await db_session.flush()
    db_session.add(GenreTrack(genre_id=rock.id, track_id=tagged.id))

    _listen(db_session, regular_user, track=tagged)
    _listen(db_session, regular_user, track=free)
    await db_session.commit()

    response = client.get("/api/v1/stats/top", headers=auth_headers(regular_user))
    assert response.status_code == 200
    genres = {g["name"]: g["play_count"] for g in response.json()["genres"]}
    assert genres == {"rock": 1, "synthwave": 1}


@pytest.mark.asyncio
async def test_plays_histogram_fills_empty_days(client, db_session, regular_user, auth_headers):
    """GET /stats/plays returns one bucket per day, including zero-count days."""
    track = await _track(db_session, regular_user)
    _listen(db_session, regular_user, track=track, played_at=NOW)
    _listen(db_session, regular_user, track=track, played_at=NOW + timedelta(hours=2))
    _listen(db_session, regular_user, track=track, played_at=NOW + timedelta(days=2))
    await db_session.commit()

    response = client.get(
        "/api/v1/stats/plays",
        params={
            "from": "2026-09-10T00:00:00Z",
            "to": "2026-09-12T23:59:59Z",
            "group_by": "day",
        },
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    assert response.json() == [
        {"bucket": "2026-09-10", "count": 2},
        {"bucket": "2026-09-11", "count": 0},
        {"bucket": "2026-09-12", "count": 1},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "group_by, expected",
    [
        # Week buckets are labelled by their first day (Monday, 2026-09-07).
        ("week", [{"bucket": "2026-09-07", "count": 3}]),
        ("month", [{"bucket": "2026-09", "count": 3}]),
        ("year", [{"bucket": "2026", "count": 3}]),
    ],
)
async def test_plays_histogram_group_by(client, db_session, regular_user, auth_headers, group_by, expected):
    """Week/month/year groupings produce calendar labels."""
    track = await _track(db_session, regular_user)
    for _ in range(3):
        _listen(db_session, regular_user, track=track, played_at=NOW)
    await db_session.commit()

    response = client.get(
        "/api/v1/stats/plays",
        params={
            "from": "2026-09-10T00:00:00Z",
            "to": "2026-09-10T23:59:59Z",
            "group_by": group_by,
        },
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    assert response.json() == expected


@pytest.mark.asyncio
async def test_plays_histogram_week_start(client, db_session, regular_user, auth_headers):
    """`week_start` selects which weekday begins a week bucket (0=Mon..6=Sun)."""
    track = await _track(db_session, regular_user)
    _listen(db_session, regular_user, track=track, played_at=NOW)  # Thu 2026-09-10
    await db_session.commit()

    headers = auth_headers(regular_user)
    params = {"from": "2026-09-10T00:00:00Z", "to": "2026-09-10T23:59:59Z", "group_by": "week"}

    response = client.get("/api/v1/stats/plays", params=params, headers=headers)
    assert response.status_code == 200
    assert response.json() == [{"bucket": "2026-09-07", "count": 1}]  # Monday

    response = client.get("/api/v1/stats/plays", params={**params, "week_start": 6}, headers=headers)
    assert response.status_code == 200
    assert response.json() == [{"bucket": "2026-09-06", "count": 1}]  # Sunday


@pytest.mark.asyncio
async def test_genres_merge_case_insensitively(client, db_session, regular_user, auth_headers):
    """Free-text genre casing folds into the canonical (lowercase) genre."""
    tagged = await _track(db_session, regular_user, title="Tagged")
    free = await _track(db_session, regular_user, title="Free", genre="Alternative Rock")
    genre = Genre(name="alternative rock")
    db_session.add(genre)
    await db_session.flush()
    db_session.add(GenreTrack(genre_id=genre.id, track_id=tagged.id))

    _listen(db_session, regular_user, track=tagged)
    _listen(db_session, regular_user, track=free)
    await db_session.commit()

    headers = auth_headers(regular_user)
    top = client.get("/api/v1/stats/top", headers=headers).json()
    assert top["genres"] == [
        {
            "name": "alternative rock",
            "play_count": 2,
            "url": "/genres/alternative%20rock",
            "image_url": None,
            "artist_name": None,
            "artist_url": None,
        },
    ]

    timeline = client.get("/api/v1/stats/genres-timeline", headers=headers).json()
    counts = {g["name"]: g["count"] for bucket in timeline for g in bucket["genres"]}
    assert counts == {"alternative rock": 2}


@pytest.mark.asyncio
async def test_genres_timeline(client, db_session, regular_user, auth_headers):
    """GET /stats/genres-timeline stacks genre counts per bucket."""
    track = await _track(db_session, regular_user, title="Tagged")
    free = await _track(db_session, regular_user, title="Free", genre="Synthwave")
    rock = Genre(name="rock")
    db_session.add(rock)
    await db_session.flush()
    db_session.add(GenreTrack(genre_id=rock.id, track_id=track.id))

    _listen(db_session, regular_user, track=track, played_at=NOW)
    _listen(db_session, regular_user, track=free, played_at=NOW + timedelta(days=7))
    await db_session.commit()

    response = client.get(
        "/api/v1/stats/genres-timeline",
        params={"from": "2026-09-07T00:00:00Z", "to": "2026-09-20T23:59:59Z"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    # Week buckets are labelled by their first day (Monday by default).
    assert response.json() == [
        {"bucket": "2026-09-07", "genres": [{"name": "rock", "count": 1}]},
        {"bucket": "2026-09-14", "genres": [{"name": "synthwave", "count": 1}]},
    ]


@pytest.mark.asyncio
async def test_releases_histogram(client, db_session, regular_user, auth_headers):
    """GET /stats/releases groups by decade/year; NULL years are excluded."""
    eighties = await _track(db_session, regular_user, title="Old", release_year=1985)
    nineties = await _track(db_session, regular_user, title="Newer", release_year=1994)
    untagged = await _track(db_session, regular_user, title="Mystery")

    _listen(db_session, regular_user, track=eighties)
    _listen(db_session, regular_user, track=nineties)
    _listen(db_session, regular_user, track=nineties)
    _listen(db_session, regular_user, track=untagged)
    await db_session.commit()

    headers = auth_headers(regular_user)
    response = client.get("/api/v1/stats/releases", headers=headers)
    assert response.status_code == 200
    assert response.json() == [
        {"bucket": "1980s", "count": 1},
        {"bucket": "1990s", "count": 2},
    ]

    response = client.get("/api/v1/stats/releases", params={"group_by": "year"}, headers=headers)
    assert response.status_code == 200
    assert response.json() == [
        {"bucket": "1985", "count": 1},
        {"bucket": "1994", "count": 2},
    ]


@pytest.mark.asyncio
async def test_clock_timezone_shifts_hours(client, db_session, regular_user, auth_headers):
    """GET /stats/clock buckets by local hour in the requested timezone."""
    track = await _track(db_session, regular_user)
    _listen(db_session, regular_user, track=track, played_at=NOW)  # 15:00 UTC
    await db_session.commit()

    headers = auth_headers(regular_user)
    response = client.get("/api/v1/stats/clock", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 24
    assert data[15] == {"hour": 15, "count": 1}
    assert sum(entry["count"] for entry in data) == 1

    # 15:00 UTC is 03:00 the next day in Pacific/Auckland (UTC+12).
    response = client.get("/api/v1/stats/clock", params={"tz": "Pacific/Auckland"}, headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data[3] == {"hour": 3, "count": 1}
    assert data[15]["count"] == 0


@pytest.mark.asyncio
async def test_remote_listens_attribution(client, db_session, regular_user, auth_headers):
    """Remote listens count in plays/clock/top charts but not genres/releases."""
    remote = await _remote_track(db_session)
    _listen(db_session, regular_user, remote=remote, played_at=NOW)
    await db_session.commit()

    headers = auth_headers(regular_user)

    top = client.get("/api/v1/stats/top", headers=headers).json()
    assert [(t_["name"], t_["play_count"], t_["url"], t_["artist_name"]) for t_ in top["tracks"]] == [
        ("Remote Song", 1, f"/remote/track/{remote.id}", "Remote Band"),
    ]
    # Remote artists/albums have no local entity page — names stay unlinked.
    assert [(a["name"], a["play_count"], a["url"]) for a in top["artists"]] == [
        ("Remote Band", 1, None),
    ]
    assert [(a["name"], a["play_count"], a["url"]) for a in top["albums"]] == [
        ("Remote Album", 1, None),
    ]
    assert top["genres"] == []

    plays = client.get("/api/v1/stats/plays", headers=headers).json()
    assert sum(bucket["count"] for bucket in plays) == 1

    clock = client.get("/api/v1/stats/clock", headers=headers).json()
    assert clock[NOW.hour]["count"] == 1

    timeline = client.get("/api/v1/stats/genres-timeline", headers=headers).json()
    assert all(bucket["genres"] == [] for bucket in timeline)

    releases = client.get("/api/v1/stats/releases", headers=headers).json()
    assert releases == []


@pytest.mark.asyncio
async def test_stats_empty_range(client, regular_user, auth_headers):
    """An empty period returns zero-filled buckets and empty top lists."""
    headers = auth_headers(regular_user)
    params = {"from": "2026-09-08T00:00:00Z", "to": "2026-09-10T00:00:00Z"}

    top = client.get("/api/v1/stats/top", params=params, headers=headers)
    assert top.status_code == 200
    assert top.json() == {"artists": [], "albums": [], "tracks": [], "genres": []}

    plays = client.get("/api/v1/stats/plays", params=params, headers=headers).json()
    assert plays == [
        {"bucket": "2026-09-08", "count": 0},
        {"bucket": "2026-09-09", "count": 0},
        {"bucket": "2026-09-10", "count": 0},
    ]

    clock = client.get("/api/v1/stats/clock", params=params, headers=headers).json()
    assert [entry["count"] for entry in clock] == [0] * 24


@pytest.mark.asyncio
async def test_stats_scoped_to_current_user(client, db_session, regular_user, other_user, auth_headers):
    """Stats only count the requesting user's listens."""
    track = await _track(db_session, regular_user)
    _listen(db_session, regular_user, track=track)
    await db_session.commit()

    response = client.get("/api/v1/stats/top", headers=auth_headers(other_user))
    assert response.status_code == 200
    assert response.json()["tracks"] == []


@pytest.mark.asyncio
async def test_stats_reject_invalid_params(client, regular_user, auth_headers):
    """Invalid timezone, inverted range and bad group_by are rejected."""
    headers = auth_headers(regular_user)

    response = client.get("/api/v1/stats/clock", params={"tz": "Not/AZone"}, headers=headers)
    assert response.status_code == 422

    response = client.get(
        "/api/v1/stats/plays",
        params={"from": "2026-09-10T00:00:00Z", "to": "2026-09-01T00:00:00Z"},
        headers=headers,
    )
    assert response.status_code == 422

    response = client.get("/api/v1/stats/plays", params={"group_by": "hour"}, headers=headers)
    assert response.status_code == 422

    response = client.get("/api/v1/stats/plays", params={"week_start": 7}, headers=headers)
    assert response.status_code == 422

    response = client.get("/api/v1/stats/releases", params={"group_by": "month"}, headers=headers)
    assert response.status_code == 422
