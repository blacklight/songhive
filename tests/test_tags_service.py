"""
Tests for the tag service.
"""

import pytest

from songhive.models._enums import Visibility
from songhive.models.activity import Activity
from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.library import Library
from songhive.models.playlist import Playlist
from songhive.models.track import Track
from songhive.models.user import User
from songhive.services.activities import create_local_activity
from songhive.services.metadata import AudioMetadata
from songhive.services.tags import (
    add_tags_to_entity,
    delete_tag_globally,
    extract_tags_from_metadata,
    extract_tags_from_track,
    get_items_for_tag,
    get_tags_for_entity,
    list_tags,
    remove_tag_from_entity,
    validate_tag_name,
)


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


async def _make_activity(
    session,
    entity_type: str,
    entity_id: str,
    author: User,
    content: str = "#rock",
    visibility: Visibility = Visibility.PUBLIC,
) -> Activity:
    """Create a local activity whose content carries ``content``'s hashtags."""
    return await create_local_activity(
        session,
        entity_type=entity_type,
        entity_id=entity_id,
        activity_type="create",
        author=author,
        visibility=visibility,
        content_source=content,
    )


class TestValidation:
    """Tests for tag validation and metadata extraction."""

    def test_validate_strips_leading_hash(self):
        assert validate_tag_name("#Rock") == "rock"

    def test_validate_lowercases(self):
        assert validate_tag_name("RoCk") == "rock"

    def test_validate_allows_underscores(self):
        assert validate_tag_name("chill_vibes") == "chill_vibes"

    def test_validate_rejects_only_digits(self):
        with pytest.raises(ValueError):
            validate_tag_name("123")

    def test_validate_rejects_invalid_chars(self):
        with pytest.raises(ValueError):
            validate_tag_name("rock&roll")

    def test_extract_from_genre(self):
        metadata = AudioMetadata(genre="Rock, Pop; Chill")
        assert extract_tags_from_metadata(metadata) == ["rock", "pop", "chill"]

    def test_extract_from_raw_tags(self):
        metadata = AudioMetadata(raw_tags={"TXXX:TAGS": ["#Synthwave", "Retrowave"]})
        assert extract_tags_from_metadata(metadata) == ["synthwave", "retrowave"]

    def test_extract_deduplicates(self):
        metadata = AudioMetadata(
            genre="Rock",
            raw_tags={"TAGS": ["Rock", "Indie"]},
        )
        assert extract_tags_from_metadata(metadata) == ["rock", "indie"]


class TestAddAndRemove:
    """Tests for adding and removing tags from entities."""

    async def test_add_tags_to_track(self, db_session, regular_user):
        artist = await _make_artist(db_session)
        track = await _make_track(db_session, artist, owner=regular_user)

        tags = await add_tags_to_entity(
            db_session,
            "track",
            track.id,
            ["#Rock", "Indie_Folk"],
            user_id=regular_user.id,
        )

        assert [t.name for t in tags] == ["rock", "indie_folk"]
        entity_tags = await get_tags_for_entity(db_session, "track", track.id)
        assert [t.name for t in entity_tags] == ["indie_folk", "rock"]

    async def test_add_is_idempotent(self, db_session, regular_user):
        artist = await _make_artist(db_session)
        track = await _make_track(db_session, artist, owner=regular_user)

        first = await add_tags_to_entity(db_session, "track", track.id, ["rock"], user_id=regular_user.id)
        second = await add_tags_to_entity(db_session, "track", track.id, ["rock"], user_id=regular_user.id)

        assert first[0].id == second[0].id
        assert len(await get_tags_for_entity(db_session, "track", track.id)) == 1

    async def test_remove_tag_from_track(self, db_session, regular_user):
        artist = await _make_artist(db_session)
        track = await _make_track(db_session, artist, owner=regular_user)
        await add_tags_to_entity(db_session, "track", track.id, ["rock"], user_id=regular_user.id)

        await remove_tag_from_entity(db_session, "track", track.id, "#Rock")

        assert await get_tags_for_entity(db_session, "track", track.id) == []

    async def test_add_to_unknown_entity_raises(self, db_session):
        with pytest.raises(ValueError, match="Unknown entity type"):
            await add_tags_to_entity(db_session, "widget", "abc", ["rock"])

    async def test_add_to_missing_entity_raises(self, db_session):
        with pytest.raises(ValueError, match="track not found"):
            await add_tags_to_entity(db_session, "track", "missing", ["rock"])


class TestListingAndVisibility:
    """Tests for listing tags and visibility-aware item queries."""

    async def test_list_tags_counts_public_items(self, db_session, regular_user):
        artist = await _make_artist(db_session)
        track = await _make_track(db_session, artist, owner=regular_user, visibility=Visibility.PUBLIC.value)
        await add_tags_to_entity(db_session, "track", track.id, ["rock"], user_id=regular_user.id)

        summaries, total = await list_tags(db_session)
        assert total == 1
        assert len(summaries) == 1
        assert summaries[0].name == "rock"
        assert summaries[0].item_count == 1

    async def test_list_tags_hides_private_items_from_anonymous(self, db_session, regular_user):
        artist = await _make_artist(db_session)
        track = await _make_track(db_session, artist, owner=regular_user, visibility=Visibility.PRIVATE.value)
        await add_tags_to_entity(db_session, "track", track.id, ["rock"], user_id=regular_user.id)

        summaries, total = await list_tags(db_session)
        assert total == 0

    async def test_list_tags_owner_sees_private(self, db_session, regular_user):
        artist = await _make_artist(db_session)
        track = await _make_track(db_session, artist, owner=regular_user, visibility=Visibility.PRIVATE.value)
        await add_tags_to_entity(db_session, "track", track.id, ["rock"], user_id=regular_user.id)

        summaries, total = await list_tags(db_session, user=regular_user)
        assert total == 1

    async def test_user_scoped_listing(self, db_session, regular_user, other_user):
        artist = await _make_artist(db_session)
        track1 = await _make_track(db_session, artist, owner=regular_user, visibility=Visibility.PUBLIC.value)
        track2 = await _make_track(db_session, artist, owner=other_user, visibility=Visibility.PUBLIC.value)
        await add_tags_to_entity(db_session, "track", track1.id, ["rock"], user_id=regular_user.id)
        await add_tags_to_entity(db_session, "track", track2.id, ["jazz"], user_id=other_user.id)

        summaries, total = await list_tags(db_session, user=regular_user, target_user_id=regular_user.id)
        assert total == 1
        assert summaries[0].name == "rock"

    async def test_get_items_for_tag(self, db_session, regular_user):
        artist = await _make_artist(db_session)
        track = await _make_track(db_session, artist, owner=regular_user, visibility=Visibility.PUBLIC.value)
        album = await _make_album(db_session, artist, owner=regular_user, visibility=Visibility.PUBLIC.value)
        await add_tags_to_entity(db_session, "track", track.id, ["rock"], user_id=regular_user.id)
        await add_tags_to_entity(db_session, "album", album.id, ["rock"], user_id=regular_user.id)

        items, total = await get_items_for_tag(db_session, "rock")
        assert total == 2
        assert sorted([(i.type, i.id) for i in items]) == [
            ("album", str(album.id)),
            ("track", str(track.id)),
        ]

    async def test_get_items_for_missing_tag(self, db_session):
        items, total = await get_items_for_tag(db_session, "nope")
        assert total == 0
        assert items == []

    async def test_get_items_for_invalid_tag_returns_empty(self, db_session):
        items, total = await get_items_for_tag(db_session, "foo bar")
        assert total == 0
        assert items == []

    async def test_get_items_for_tag_by_type(self, db_session, regular_user):
        artist = await _make_artist(db_session)
        track = await _make_track(db_session, artist, owner=regular_user, visibility=Visibility.PUBLIC.value)
        album = await _make_album(db_session, artist, owner=regular_user, visibility=Visibility.PUBLIC.value)
        await add_tags_to_entity(db_session, "track", track.id, ["rock"], user_id=regular_user.id)
        await add_tags_to_entity(db_session, "album", album.id, ["rock"], user_id=regular_user.id)
        await add_tags_to_entity(db_session, "artist", artist.id, ["rock"])

        track_items, total = await get_items_for_tag(db_session, "rock", item_type="track")
        assert total == 1
        assert [(i.type, i.id) for i in track_items] == [("track", str(track.id))]

        album_items, total = await get_items_for_tag(db_session, "rock", item_type="album")
        assert total == 1
        assert [(i.type, i.id) for i in album_items] == [("album", str(album.id))]

        artist_items, total = await get_items_for_tag(db_session, "rock", item_type="artist")
        assert total == 1
        assert [(i.type, i.id) for i in artist_items] == [("artist", str(artist.id))]

        unknown_items, total = await get_items_for_tag(db_session, "rock", item_type="library")
        assert total == 0
        assert unknown_items == []

    async def test_list_tags_includes_activity_only_tags(self, db_session, regular_user):
        """Tags used only in activity content appear in the listing."""
        await _make_activity(db_session, "user", regular_user.id, regular_user, content="#rock")

        summaries, total = await list_tags(db_session)
        assert total == 1
        assert summaries[0].name == "rock"
        assert summaries[0].item_count == 1

    async def test_list_tags_counts_entities_and_activities(self, db_session, regular_user):
        """A tag on a track and in an activity counts both usages."""
        artist = await _make_artist(db_session)
        track = await _make_track(db_session, artist, owner=regular_user, visibility=Visibility.PUBLIC.value)
        await add_tags_to_entity(db_session, "track", track.id, ["rock"], user_id=regular_user.id)
        await _make_activity(db_session, "track", track.id, regular_user, content="#rock")

        summaries, total = await list_tags(db_session)
        assert total == 1
        assert summaries[0].item_count == 2

    async def test_list_tags_hides_restricted_activities(self, db_session, regular_user, other_user):
        """Local-visibility activity hashtags are hidden from anonymous users."""
        artist = await _make_artist(db_session)
        track = await _make_track(db_session, artist, owner=regular_user, visibility=Visibility.PUBLIC.value)
        await _make_activity(db_session, "track", track.id, regular_user, content="#rock", visibility=Visibility.LOCAL)

        _, total = await list_tags(db_session)
        assert total == 0

        _, total = await list_tags(db_session, user=other_user)
        assert total == 1

    async def test_list_tags_hides_activities_on_inaccessible_entities(self, db_session, regular_user):
        """Hashtags on activities attached to private entities stay hidden."""
        artist = await _make_artist(db_session)
        track = await _make_track(db_session, artist, owner=regular_user, visibility=Visibility.PRIVATE.value)
        await _make_activity(
            db_session, "track", track.id, regular_user, content="#rock", visibility=Visibility.PRIVATE
        )

        _, total = await list_tags(db_session)
        assert total == 0

        _, total = await list_tags(db_session, user=regular_user)
        assert total == 1

    async def test_user_scoped_listing_includes_owned_activities(self, db_session, regular_user, other_user):
        """User tag pages count hashtags on the user's own activities."""
        await _make_activity(db_session, "user", regular_user.id, regular_user, content="#rock")

        summaries, total = await list_tags(db_session, user=other_user, target_user_id=regular_user.id)
        assert total == 1
        assert summaries[0].name == "rock"

        _, total = await list_tags(db_session, user=other_user, target_user_id=other_user.id)
        assert total == 0

    async def test_get_items_for_tag_includes_activities(self, db_session, regular_user):
        """Activities carrying the hashtag appear as ``activity`` items."""
        artist = await _make_artist(db_session)
        track = await _make_track(db_session, artist, owner=regular_user, visibility=Visibility.PUBLIC.value)
        await add_tags_to_entity(db_session, "track", track.id, ["rock"], user_id=regular_user.id)
        activity = await _make_activity(db_session, "user", regular_user.id, regular_user, content="#rock")

        items, total = await get_items_for_tag(db_session, "rock")
        assert total == 2
        assert sorted((i.type, i.id) for i in items) == [
            ("activity", str(activity.id)),
            ("track", str(track.id)),
        ]

        activity_items, total = await get_items_for_tag(db_session, "rock", item_type="activity")
        assert total == 1
        assert [(i.type, i.id) for i in activity_items] == [("activity", str(activity.id))]

    async def test_get_items_for_tag_hides_inaccessible_activities(self, db_session, regular_user):
        """Activity items respect entity and activity visibility."""
        artist = await _make_artist(db_session)
        track = await _make_track(db_session, artist, owner=regular_user, visibility=Visibility.PRIVATE.value)
        await _make_activity(
            db_session, "track", track.id, regular_user, content="#rock", visibility=Visibility.PRIVATE
        )

        items, total = await get_items_for_tag(db_session, "rock")
        assert total == 0
        assert items == []

        items, total = await get_items_for_tag(db_session, "rock", user=regular_user)
        assert total == 1
        assert items[0].type == "activity"


class TestDelete:
    """Tests for global tag deletion."""

    async def test_delete_tag_globally(self, db_session, regular_user):
        artist = await _make_artist(db_session)
        track = await _make_track(db_session, artist, owner=regular_user, visibility=Visibility.PUBLIC.value)
        await add_tags_to_entity(db_session, "track", track.id, ["rock"], user_id=regular_user.id)

        deleted = await delete_tag_globally(db_session, "rock")
        assert deleted is not None
        assert deleted.name == "rock"
        assert await get_tags_for_entity(db_session, "track", track.id) == []

    async def test_delete_missing_tag_returns_none(self, db_session):
        deleted = await delete_tag_globally(db_session, "nope")
        assert deleted is None


class TestExtraction:
    """Tests for automatic tag extraction from metadata and tracks."""

    def test_extract_from_track(self):
        track = Track(
            title="Test Track",
            artist_id="artist-id",
            genre="Rock, Pop; Chill",
            raw_metadata={"TXXX:TAGS": ["#Synthwave", "Retrowave"]},
            owner_id="user-id",
        )
        assert extract_tags_from_track(track) == ["rock", "pop", "chill", "synthwave", "retrowave"]

    def test_extract_from_track_with_no_tags(self):
        track = Track(title="Test Track", artist_id="artist-id")
        assert extract_tags_from_track(track) == []
