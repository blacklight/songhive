"""
Tests for the hashtag service.
"""

import pytest

from songhive.models._enums import Visibility
from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.library import Library
from songhive.models.playlist import Playlist
from songhive.models.track import Track
from songhive.models.user import User
from songhive.services.hashtags import (
    add_hashtags_to_entity,
    delete_hashtag_globally,
    extract_hashtags_from_metadata,
    extract_hashtags_from_track,
    get_hashtags_for_entity,
    get_items_for_hashtag,
    list_hashtags,
    remove_hashtag_from_entity,
    validate_hashtag_name,
)
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
) -> Track:
    """Create and persist a test track."""
    track = Track(
        title=title,
        artist_id=artist.id,
        owner_id=owner.id if owner is not None else None,
        visibility=visibility,
        genre=genre,
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


async def _make_playlist(
    session,
    owner: User,
    name: str = "Test Playlist",
    visibility: str = Visibility.PUBLIC.value,
) -> Playlist:
    """Create and persist a test playlist."""
    playlist = Playlist(name=name, owner_id=owner.id, visibility=visibility)
    session.add(playlist)
    await session.flush()
    return playlist


async def _make_library(
    session,
    owner: User,
    name: str = "Test Library",
    visibility: str = Visibility.PUBLIC.value,
) -> Library:
    """Create and persist a test library."""
    library = Library(name=name, owner_id=owner.id, visibility=visibility)
    session.add(library)
    await session.flush()
    return library


class TestValidation:
    """Tests for hashtag validation and metadata extraction."""

    def test_validate_strips_leading_hash(self):
        assert validate_hashtag_name("#Rock") == "rock"

    def test_validate_lowercases(self):
        assert validate_hashtag_name("RoCk") == "rock"

    def test_validate_allows_underscores(self):
        assert validate_hashtag_name("chill_vibes") == "chill_vibes"

    def test_validate_rejects_only_digits(self):
        with pytest.raises(ValueError):
            validate_hashtag_name("123")

    def test_validate_rejects_invalid_chars(self):
        with pytest.raises(ValueError):
            validate_hashtag_name("rock&roll")

    def test_extract_from_genre(self):
        metadata = AudioMetadata(genre="Rock, Pop; Chill")
        assert extract_hashtags_from_metadata(metadata) == ["rock", "pop", "chill"]

    def test_extract_from_raw_tags(self):
        metadata = AudioMetadata(raw_tags={"TXXX:TAGS": ["#Synthwave", "Retrowave"]})
        assert extract_hashtags_from_metadata(metadata) == ["synthwave", "retrowave"]

    def test_extract_deduplicates(self):
        metadata = AudioMetadata(
            genre="Rock",
            raw_tags={"TAGS": ["Rock", "Indie"]},
        )
        assert extract_hashtags_from_metadata(metadata) == ["rock", "indie"]


class TestAddAndRemove:
    """Tests for adding and removing hashtags from entities."""

    async def test_add_hashtags_to_track(self, db_session, regular_user):
        artist = await _make_artist(db_session)
        track = await _make_track(db_session, artist, owner=regular_user)

        tags = await add_hashtags_to_entity(
            db_session,
            "track",
            track.id,
            ["#Rock", "Indie_Folk"],
            user_id=regular_user.id,
        )

        assert [t.name for t in tags] == ["rock", "indie_folk"]
        entity_tags = await get_hashtags_for_entity(db_session, "track", track.id)
        assert [t.name for t in entity_tags] == ["indie_folk", "rock"]

    async def test_add_is_idempotent(self, db_session, regular_user):
        artist = await _make_artist(db_session)
        track = await _make_track(db_session, artist, owner=regular_user)

        first = await add_hashtags_to_entity(db_session, "track", track.id, ["rock"], user_id=regular_user.id)
        second = await add_hashtags_to_entity(db_session, "track", track.id, ["rock"], user_id=regular_user.id)

        assert first[0].id == second[0].id
        assert len(await get_hashtags_for_entity(db_session, "track", track.id)) == 1

    async def test_remove_hashtag_from_track(self, db_session, regular_user):
        artist = await _make_artist(db_session)
        track = await _make_track(db_session, artist, owner=regular_user)
        await add_hashtags_to_entity(db_session, "track", track.id, ["rock"], user_id=regular_user.id)

        await remove_hashtag_from_entity(db_session, "track", track.id, "#Rock")

        assert await get_hashtags_for_entity(db_session, "track", track.id) == []

    async def test_add_to_unknown_entity_raises(self, db_session):
        with pytest.raises(ValueError, match="Unknown entity type"):
            await add_hashtags_to_entity(db_session, "widget", "abc", ["rock"])

    async def test_add_to_missing_entity_raises(self, db_session):
        with pytest.raises(ValueError, match="track not found"):
            await add_hashtags_to_entity(db_session, "track", "missing", ["rock"])


class TestListingAndVisibility:
    """Tests for listing hashtags and visibility-aware item queries."""

    async def test_list_hashtags_counts_public_items(self, db_session, regular_user):
        artist = await _make_artist(db_session)
        track = await _make_track(db_session, artist, owner=regular_user, visibility=Visibility.PUBLIC.value)
        await add_hashtags_to_entity(db_session, "track", track.id, ["rock"], user_id=regular_user.id)

        summaries, total = await list_hashtags(db_session)
        assert total == 1
        assert len(summaries) == 1
        assert summaries[0].name == "rock"
        assert summaries[0].item_count == 1

    async def test_list_hashtags_hides_private_items_from_anonymous(self, db_session, regular_user):
        artist = await _make_artist(db_session)
        track = await _make_track(db_session, artist, owner=regular_user, visibility=Visibility.PRIVATE.value)
        await add_hashtags_to_entity(db_session, "track", track.id, ["rock"], user_id=regular_user.id)

        summaries, total = await list_hashtags(db_session)
        assert total == 0

    async def test_list_hashtags_owner_sees_private(self, db_session, regular_user):
        artist = await _make_artist(db_session)
        track = await _make_track(db_session, artist, owner=regular_user, visibility=Visibility.PRIVATE.value)
        await add_hashtags_to_entity(db_session, "track", track.id, ["rock"], user_id=regular_user.id)

        summaries, total = await list_hashtags(db_session, user=regular_user)
        assert total == 1

    async def test_user_scoped_listing(self, db_session, regular_user, other_user):
        artist = await _make_artist(db_session)
        track1 = await _make_track(db_session, artist, owner=regular_user, visibility=Visibility.PUBLIC.value)
        track2 = await _make_track(db_session, artist, owner=other_user, visibility=Visibility.PUBLIC.value)
        await add_hashtags_to_entity(db_session, "track", track1.id, ["rock"], user_id=regular_user.id)
        await add_hashtags_to_entity(db_session, "track", track2.id, ["jazz"], user_id=other_user.id)

        summaries, total = await list_hashtags(db_session, user=regular_user, target_user_id=regular_user.id)
        assert total == 1
        assert summaries[0].name == "rock"

    async def test_get_items_for_hashtag(self, db_session, regular_user):
        artist = await _make_artist(db_session)
        track = await _make_track(db_session, artist, owner=regular_user, visibility=Visibility.PUBLIC.value)
        album = await _make_album(db_session, artist, owner=regular_user, visibility=Visibility.PUBLIC.value)
        await add_hashtags_to_entity(db_session, "track", track.id, ["rock"], user_id=regular_user.id)
        await add_hashtags_to_entity(db_session, "album", album.id, ["rock"], user_id=regular_user.id)

        items, total = await get_items_for_hashtag(db_session, "rock")
        assert total == 2
        assert sorted([(i.type, i.id) for i in items]) == [
            ("album", str(album.id)),
            ("track", str(track.id)),
        ]

    async def test_get_items_for_missing_hashtag(self, db_session):
        items, total = await get_items_for_hashtag(db_session, "nope")
        assert total == 0
        assert items == []

    async def test_get_items_for_invalid_hashtag_returns_empty(self, db_session):
        items, total = await get_items_for_hashtag(db_session, "foo bar")
        assert total == 0
        assert items == []


class TestDelete:
    """Tests for global hashtag deletion."""

    async def test_delete_hashtag_globally(self, db_session, regular_user):
        artist = await _make_artist(db_session)
        track = await _make_track(db_session, artist, owner=regular_user, visibility=Visibility.PUBLIC.value)
        await add_hashtags_to_entity(db_session, "track", track.id, ["rock"], user_id=regular_user.id)

        deleted = await delete_hashtag_globally(db_session, "rock")
        assert deleted is not None
        assert deleted.name == "rock"
        assert await get_hashtags_for_entity(db_session, "track", track.id) == []

    async def test_delete_missing_hashtag_returns_none(self, db_session):
        deleted = await delete_hashtag_globally(db_session, "nope")
        assert deleted is None


class TestExtraction:
    """Tests for automatic hashtag extraction from metadata and tracks."""

    def test_extract_from_track(self):
        track = Track(
            title="Test Track",
            artist_id="artist-id",
            genre="Rock, Pop; Chill",
            raw_metadata={"TXXX:TAGS": ["#Synthwave", "Retrowave"]},
            owner_id="user-id",
        )
        assert extract_hashtags_from_track(track) == ["rock", "pop", "chill", "synthwave", "retrowave"]

    def test_extract_from_track_with_no_tags(self):
        track = Track(title="Test Track", artist_id="artist-id")
        assert extract_hashtags_from_track(track) == []
