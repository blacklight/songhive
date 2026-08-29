"""
Tests for the genre service.
"""

import pytest

from songhive.models._enums import Visibility
from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.track import Track
from songhive.models.user import User
from songhive.services.genres import (
    add_genres_to_entity,
    delete_genre_globally,
    extract_genres_from_metadata,
    extract_genres_from_track,
    genres_to_hashtags,
    get_genres_for_entity,
    get_items_for_genre,
    list_genres,
    propagate_album_genres,
    remove_genre_from_entity,
    set_genres_for_entity,
    split_genre_string,
    validate_genre_name,
)
from songhive.services.hashtags import get_hashtags_for_entity
from songhive.services.metadata import AudioMetadata


async def _make_artist(session, name: str = "Test Artist") -> Artist:
    """Create and persist a test artist."""
    artist = Artist(name=name)
    session.add(artist)
    await session.flush()
    return artist


async def _make_track(
    session,
    artist: Artist,
    title: str = "Test Track",
    owner: User | None = None,
    visibility: str = Visibility.PUBLIC.value,
    genre: str | None = None,
    album: Album | None = None,
) -> Track:
    """Create and persist a test track."""
    track = Track(
        title=title,
        artist_id=artist.id,
        owner_id=owner.id if owner is not None else None,
        visibility=visibility,
        genre=genre,
        album_id=album.id if album is not None else None,
    )
    session.add(track)
    await session.flush()
    return track


async def _make_album(
    session,
    artist: Artist,
    title: str = "Test Album",
    owner: User | None = None,
    visibility: str = Visibility.PUBLIC.value,
) -> Album:
    """Create and persist a test album."""
    album = Album(
        title=title,
        artist_id=artist.id,
        owner_id=owner.id if owner is not None else None,
        visibility=visibility,
    )
    session.add(album)
    await session.flush()
    return album


class TestValidation:
    """Tests for genre validation and splitting."""

    def test_validate_lowercases(self):
        assert validate_genre_name("RoCk") == "rock"

    def test_validate_allows_underscores(self):
        assert validate_genre_name("chill_vibes") == "chill_vibes"

    def test_validate_allows_spaces(self):
        assert validate_genre_name("Hip Hop") == "hip hop"

    def test_validate_strips_leading_hash(self):
        assert validate_genre_name("#Rock") == "rock"

    def test_validate_rejects_only_digits(self):
        with pytest.raises(ValueError):
            validate_genre_name("123")

    def test_validate_rejects_invalid_chars(self):
        with pytest.raises(ValueError):
            validate_genre_name("rock&roll")

    def test_validate_rejects_too_long(self):
        with pytest.raises(ValueError):
            validate_genre_name("x" * 129)

    def test_split_genre_string(self):
        assert split_genre_string("Rock, Pop; Chill") == ["rock", "pop", "chill"]

    def test_split_genre_string_allows_spaces(self):
        assert split_genre_string("Hip Hop, Drum and Bass") == ["hip hop", "drum and bass"]

    def test_split_genre_string_deduplicates(self):
        assert split_genre_string("Rock; rock, Indie") == ["rock", "indie"]


class TestExtraction:
    """Tests for automatic genre extraction from metadata and tracks."""

    def test_extract_from_metadata(self):
        metadata = AudioMetadata(genre="Rock, Pop; Chill")
        assert extract_genres_from_metadata(metadata) == ["rock", "pop", "chill"]

    def test_extract_from_track(self):
        track = Track(
            title="Test Track",
            artist_id="artist-id",
            genre="Rock, Pop; Chill",
            owner_id="user-id",
        )
        assert extract_genres_from_track(track) == ["rock", "pop", "chill"]

    def test_extract_from_track_with_no_genre(self):
        track = Track(title="Test Track", artist_id="artist-id")
        assert extract_genres_from_track(track) == []


class TestGenreToHashtag:
    """Tests for the genre-to-hashtag bridge."""

    def test_genres_to_hashtags_replaces_spaces(self):
        assert genres_to_hashtags(["hip hop", "drum and bass"]) == ["hip_hop", "drum_and_bass"]

    def test_genres_to_hashtags_skips_invalid(self):
        assert genres_to_hashtags(["rock", "r&b"]) == ["rock"]

    def test_genres_to_hashtags_deduplicates(self):
        assert genres_to_hashtags(["rock", "Rock", "hip hop"]) == ["rock", "hip_hop"]


class TestAddAndRemove:
    """Tests for adding and removing genres from entities."""

    async def test_add_genres_to_track(self, db_session, regular_user):
        artist = await _make_artist(db_session)
        track = await _make_track(db_session, artist, owner=regular_user)

        genres = await add_genres_to_entity(
            db_session,
            "track",
            track.id,
            ["Rock", "Indie Pop"],
        )

        assert [g.name for g in genres] == ["rock", "indie pop"]
        entity_genres = await get_genres_for_entity(db_session, "track", track.id)
        assert entity_genres == ["indie pop", "rock"]

    async def test_add_is_idempotent(self, db_session, regular_user):
        artist = await _make_artist(db_session)
        track = await _make_track(db_session, artist, owner=regular_user)

        first = await add_genres_to_entity(db_session, "track", track.id, ["rock"])
        second = await add_genres_to_entity(db_session, "track", track.id, ["rock"])

        assert first[0].id == second[0].id
        assert len(await get_genres_for_entity(db_session, "track", track.id)) == 1

    async def test_remove_genre_from_track(self, db_session, regular_user):
        artist = await _make_artist(db_session)
        track = await _make_track(db_session, artist, owner=regular_user)
        await add_genres_to_entity(db_session, "track", track.id, ["rock"])

        await remove_genre_from_entity(db_session, "track", track.id, "Rock")

        assert await get_genres_for_entity(db_session, "track", track.id) == []

    async def test_add_to_unknown_entity_raises(self, db_session):
        with pytest.raises(ValueError, match="Unknown entity type"):
            await add_genres_to_entity(db_session, "widget", "abc", ["rock"])

    async def test_add_to_missing_entity_raises(self, db_session):
        with pytest.raises(ValueError, match="track not found"):
            await add_genres_to_entity(db_session, "track", "missing", ["rock"])

    async def test_set_genres_replaces_existing(self, db_session, regular_user):
        artist = await _make_artist(db_session)
        track = await _make_track(db_session, artist, owner=regular_user, genre="rock; pop")
        await set_genres_for_entity(db_session, "track", track.id, ["rock", "pop", "indie"])

        entity_genres = await get_genres_for_entity(db_session, "track", track.id)
        assert entity_genres == ["indie", "pop", "rock"]

    async def test_set_genres_clears_existing(self, db_session, regular_user):
        artist = await _make_artist(db_session)
        track = await _make_track(db_session, artist, owner=regular_user, genre="rock")
        await set_genres_for_entity(db_session, "track", track.id, ["rock"])
        await set_genres_for_entity(db_session, "track", track.id, [])

        assert await get_genres_for_entity(db_session, "track", track.id) == []

    async def test_set_genres_creates_hashtags(self, db_session, regular_user):
        """Setting genres should also create the corresponding hashtags."""
        artist = await _make_artist(db_session)
        track = await _make_track(db_session, artist, owner=regular_user, genre="hip hop")

        # Caller is responsible for the hashtag bridge using genres_to_hashtags.
        from songhive.services.hashtags import add_hashtags_to_entity

        await set_genres_for_entity(db_session, "track", track.id, ["hip hop", "rock"])
        await add_hashtags_to_entity(
            db_session,
            "track",
            track.id,
            genres_to_hashtags(["hip hop", "rock"]),
        )

        entity_genres = await get_genres_for_entity(db_session, "track", track.id)
        assert entity_genres == ["hip hop", "rock"]
        hashtags = await get_hashtags_for_entity(db_session, "track", track.id)
        assert [h.name for h in hashtags] == ["hip_hop", "rock"]


class TestListingAndVisibility:
    """Tests for listing genres and visibility-aware item queries."""

    async def test_list_genres_counts_public_items(self, db_session, regular_user):
        artist = await _make_artist(db_session)
        track = await _make_track(db_session, artist, owner=regular_user, visibility=Visibility.PUBLIC.value)
        await set_genres_for_entity(db_session, "track", track.id, ["rock"])

        summaries, total = await list_genres(db_session)
        assert total == 1
        assert len(summaries) == 1
        assert summaries[0].name == "rock"
        assert summaries[0].item_count == 1

    async def test_list_genres_hides_private_items_from_anonymous(self, db_session, regular_user):
        artist = await _make_artist(db_session)
        track = await _make_track(db_session, artist, owner=regular_user, visibility=Visibility.PRIVATE.value)
        await set_genres_for_entity(db_session, "track", track.id, ["rock"])

        summaries, total = await list_genres(db_session)
        assert total == 0

    async def test_list_genres_owner_sees_private(self, db_session, regular_user):
        artist = await _make_artist(db_session)
        track = await _make_track(db_session, artist, owner=regular_user, visibility=Visibility.PRIVATE.value)
        await set_genres_for_entity(db_session, "track", track.id, ["rock"])

        summaries, total = await list_genres(db_session, user=regular_user)
        assert total == 1

    async def test_user_scoped_listing(self, db_session, regular_user, other_user):
        artist = await _make_artist(db_session)
        track1 = await _make_track(db_session, artist, owner=regular_user, visibility=Visibility.PUBLIC.value)
        track2 = await _make_track(db_session, artist, owner=other_user, visibility=Visibility.PUBLIC.value)
        await set_genres_for_entity(db_session, "track", track1.id, ["rock"])
        await set_genres_for_entity(db_session, "track", track2.id, ["jazz"])

        summaries, total = await list_genres(db_session, user=regular_user, target_user_id=regular_user.id)
        assert total == 1
        assert summaries[0].name == "rock"

    async def test_get_items_for_genre(self, db_session, regular_user):
        artist = await _make_artist(db_session)
        track = await _make_track(db_session, artist, owner=regular_user, visibility=Visibility.PUBLIC.value)
        album = await _make_album(db_session, artist, owner=regular_user, visibility=Visibility.PUBLIC.value)
        await set_genres_for_entity(db_session, "track", track.id, ["rock"])
        await set_genres_for_entity(db_session, "album", album.id, ["rock"])

        items, total = await get_items_for_genre(db_session, "rock")
        assert total == 2
        assert sorted([(i.type, i.id) for i in items]) == [
            ("album", str(album.id)),
            ("track", str(track.id)),
        ]

    async def test_get_items_for_missing_genre(self, db_session):
        items, total = await get_items_for_genre(db_session, "nope")
        assert total == 0
        assert items == []

    async def test_get_items_for_invalid_genre_returns_empty(self, db_session):
        items, total = await get_items_for_genre(db_session, "foo&bar")
        assert total == 0
        assert items == []


class TestDelete:
    """Tests for global genre deletion."""

    async def test_delete_genre_globally(self, db_session, regular_user):
        artist = await _make_artist(db_session)
        track = await _make_track(db_session, artist, owner=regular_user, visibility=Visibility.PUBLIC.value)
        await set_genres_for_entity(db_session, "track", track.id, ["rock"])

        deleted = await delete_genre_globally(db_session, "rock")
        assert deleted is not None
        assert deleted.name == "rock"
        assert await get_genres_for_entity(db_session, "track", track.id) == []

    async def test_delete_missing_genre_returns_none(self, db_session):
        deleted = await delete_genre_globally(db_session, "nope")
        assert deleted is None


class TestAlbumGenrePropagation:
    """Tests for deriving album genres from the intersection of track genres."""

    async def test_all_tracks_same_genre_propagates(self, db_session, regular_user):
        artist = await _make_artist(db_session)
        album = await _make_album(db_session, artist, owner=regular_user)

        for _ in range(3):
            track = await _make_track(db_session, artist, owner=regular_user, album=album, genre="rock")
            await set_genres_for_entity(db_session, "track", track.id, ["rock"])

        genres = await propagate_album_genres(db_session, album)
        assert genres == ["rock"]
        assert album.genre == "rock"

    async def test_unanimous_only_propagates(self, db_session, regular_user):
        artist = await _make_artist(db_session)
        album = await _make_album(db_session, artist, owner=regular_user)

        track1 = await _make_track(db_session, artist, owner=regular_user, album=album, genre="rock")
        track2 = await _make_track(db_session, artist, owner=regular_user, album=album, genre="rock")
        track3 = await _make_track(db_session, artist, owner=regular_user, album=album, genre="pop")
        await set_genres_for_entity(db_session, "track", track1.id, ["rock"])
        await set_genres_for_entity(db_session, "track", track2.id, ["rock"])
        await set_genres_for_entity(db_session, "track", track3.id, ["pop"])

        genres = await propagate_album_genres(db_session, album)
        assert genres == []
        assert album.genre is None

    async def test_multiple_unanimous_genres(self, db_session, regular_user):
        artist = await _make_artist(db_session)
        album = await _make_album(db_session, artist, owner=regular_user)

        for _ in range(3):
            track = await _make_track(db_session, artist, owner=regular_user, album=album, genre="rock; indie")
            await set_genres_for_entity(db_session, "track", track.id, ["rock", "indie"])

        genres = await propagate_album_genres(db_session, album)
        assert genres == ["indie", "rock"]
        assert album.genre == "indie; rock"

    async def test_new_track_without_shared_genre_removes_it(self, db_session, regular_user):
        artist = await _make_artist(db_session)
        album = await _make_album(db_session, artist, owner=regular_user)

        track1 = await _make_track(db_session, artist, owner=regular_user, album=album, genre="rock")
        track2 = await _make_track(db_session, artist, owner=regular_user, album=album, genre="rock")
        await set_genres_for_entity(db_session, "track", track1.id, ["rock"])
        await set_genres_for_entity(db_session, "track", track2.id, ["rock"])
        await propagate_album_genres(db_session, album)
        assert album.genre == "rock"

        track3 = await _make_track(db_session, artist, owner=regular_user, album=album, genre="pop")
        await set_genres_for_entity(db_session, "track", track3.id, ["pop"])
        await propagate_album_genres(db_session, album)
        assert album.genre is None
        assert await get_genres_for_entity(db_session, "album", album.id) == []

    async def test_editing_track_genre_triggers_repropagation(self, db_session, regular_user):
        artist = await _make_artist(db_session)
        album = await _make_album(db_session, artist, owner=regular_user)

        track = await _make_track(db_session, artist, owner=regular_user, album=album, genre="rock")
        await set_genres_for_entity(db_session, "track", track.id, ["rock"])
        await propagate_album_genres(db_session, album)
        assert album.genre == "rock"

        track.genre = "pop"
        await set_genres_for_entity(db_session, "track", track.id, ["pop"])
        await propagate_album_genres(db_session, album)
        assert album.genre == "pop"
