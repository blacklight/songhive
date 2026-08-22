"""
Model tests - verify model instantiation and relationships.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from songhive.models._enums import Visibility
from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.favorite import Favorite
from songhive.models.history import ListeningHistory
from songhive.models.library import Library
from songhive.models.playlist import Playlist
from songhive.models.radio import Radio
from songhive.models.share_grant import ShareGrant
from songhive.models.share_token import ShareToken
from songhive.models.stored_file import StoredFile
from songhive.models.track import Track
from songhive.models.transcoded_file import TranscodedFile
from songhive.models.upload import Upload
from songhive.models.user import User


def test_user_model():
    """Test User model instantiation."""
    user = User(
        username="testuser",
        email="test@example.com",
        password_hash="hashed",
        role="user",
    )
    assert user.username == "testuser"
    assert user.is_admin is False


def test_artist_model():
    """Test Artist model instantiation."""
    artist = Artist(name="Test Artist")
    assert artist.name == "Test Artist"


def test_album_model():
    """Test Album model instantiation."""
    album = Album(title="Test Album", artist_id="artist-1")
    assert album.title == "Test Album"


def test_track_model():
    """Test Track model instantiation."""
    track = Track(title="Test Track", artist_id="artist-1")
    assert track.title == "Test Track"
    assert track.album_id is None


def test_library_model():
    """Test Library model instantiation."""
    lib = Library(name="My Library", owner_id="user-1", visibility=Visibility.LOCAL.value)
    assert lib.visibility == Visibility.LOCAL.value


@pytest.mark.asyncio
async def test_library_default_visibility(db_session):
    """A new library defaults to private visibility at flush."""
    lib = Library(name="My Library", owner_id="user-1")
    db_session.add(lib)
    await db_session.flush()
    assert lib.visibility == Visibility.PRIVATE.value


def test_playlist_model():
    """Test Playlist model instantiation."""
    pl = Playlist(name="My Playlist", owner_id="user-1", visibility=Visibility.PUBLIC.value)
    assert pl.visibility == Visibility.PUBLIC.value


@pytest.mark.asyncio
async def test_playlist_default_visibility(db_session):
    """A new playlist defaults to private visibility at flush."""
    pl = Playlist(name="My Playlist", owner_id="user-1")
    db_session.add(pl)
    await db_session.flush()
    assert pl.visibility == Visibility.PRIVATE.value


def test_favorite_model():
    """Test Favorite model instantiation."""
    fav = Favorite(user_id="user-1", track_id="track-1")
    assert fav.user_id == "user-1"


def test_history_model():
    """Test ListeningHistory model instantiation."""
    entry = ListeningHistory(user_id="user-1", track_id="track-1")
    assert entry.track_id == "track-1"


def test_radio_model():
    """Test Radio model instantiation."""
    radio = Radio(name="My Radio", owner_id="user-1")
    assert radio.name == "My Radio"


def test_stored_file_model():
    """Test StoredFile model instantiation."""
    stored_file = StoredFile(
        storage_path="files/aa/bb/cc",
        storage_backend="local",
        content_type="audio/mpeg",
        size=12345,
        sha256="a" * 64,
        original_filename="song.mp3",
    )
    assert stored_file.storage_path == "files/aa/bb/cc"
    assert stored_file.storage_backend == "local"
    assert stored_file.content_type == "audio/mpeg"
    assert stored_file.size == 12345
    assert stored_file.sha256 == "a" * 64
    assert stored_file.original_filename == "song.mp3"


def test_track_audio_file_id():
    """Test Track accepts the optional audio_file_id foreign key."""
    track = Track(title="Test Track", artist_id="artist-1", audio_file_id="file-1")
    assert track.audio_file_id == "file-1"


def test_album_cover_file_id():
    """Test Album accepts the optional cover_file_id foreign key."""
    album = Album(title="Test Album", artist_id="artist-1", cover_file_id="file-1")
    assert album.cover_file_id == "file-1"


def test_upload_stored_file_id():
    """Test Upload accepts the optional stored_file_id foreign key."""
    upload = Upload(
        track_id="track-1",
        library_id="library-1",
        storage_path="tracks/track-1/audio.mp3",
        storage_backend="local",
        mimetype="audio/mpeg",
        stored_file_id="file-1",
    )
    assert upload.stored_file_id == "file-1"


@pytest.mark.asyncio
async def test_stored_file_persistence(db_session):
    """Test StoredFile can be persisted and queried."""
    stored_file = StoredFile(
        storage_path="files/aa/bb/cc",
        storage_backend="local",
        content_type="audio/mpeg",
        size=12345,
        sha256="a" * 64,
        original_filename="song.mp3",
    )
    db_session.add(stored_file)
    await db_session.flush()

    assert stored_file.id is not None
    result = await db_session.execute(select(StoredFile).where(StoredFile.sha256 == "a" * 64))
    assert result.scalar_one() is stored_file


def test_visibility_enum():
    """Visibility values are the expected strings."""
    assert Visibility.PRIVATE.value == "private"
    assert Visibility.LOCAL.value == "local"
    assert Visibility.PUBLIC.value == "public"


def test_stored_file_owner_and_visibility():
    """StoredFile accepts an optional owner and a visibility value."""
    stored_file = StoredFile(
        storage_path="files/aa/bb/cc",
        storage_backend="local",
        content_type="audio/mpeg",
        size=12345,
        sha256="a" * 64,
        original_filename="song.mp3",
        owner_id="user-1",
        visibility=Visibility.PUBLIC.value,
    )
    assert stored_file.owner_id == "user-1"
    assert stored_file.visibility == Visibility.PUBLIC.value


def test_track_owner_and_visibility():
    """Track accepts an optional owner and a visibility value."""
    track = Track(title="Test Track", artist_id="artist-1", visibility=Visibility.LOCAL.value)
    assert track.visibility == Visibility.LOCAL.value


def test_album_owner_and_visibility():
    """Album accepts an optional owner and a visibility value."""
    album = Album(
        title="Test Album",
        artist_id="artist-1",
        owner_id="user-1",
        visibility=Visibility.PRIVATE.value,
    )
    assert album.owner_id == "user-1"
    assert album.visibility == Visibility.PRIVATE.value


@pytest.mark.asyncio
async def test_album_default_visibility(db_session):
    """A new album defaults to private visibility at flush."""
    album = Album(title="Test Album", artist_id="artist-1")
    db_session.add(album)
    await db_session.flush()
    assert album.visibility == Visibility.PRIVATE.value


@pytest.mark.asyncio
async def test_radio_visibility(db_session):
    """Radio has a visibility value defaulting to private at flush."""
    radio = Radio(name="My Radio", owner_id="user-1")
    db_session.add(radio)
    await db_session.flush()
    assert radio.visibility == Visibility.PRIVATE.value


def test_share_grant_model():
    """ShareGrant stores an item reference and two user references."""
    grant = ShareGrant(
        item_type="track",
        item_id="track-1",
        user_id="user-1",
        created_by="user-2",
    )
    assert grant.item_type == "track"
    assert grant.user_id == "user-1"
    assert grant.created_by == "user-2"


def test_share_token_model():
    """ShareToken stores a token hash and optional expiry/revocation."""
    token = ShareToken(
        item_type="file",
        item_id="file-1",
        token_hash="a" * 64,
        created_by="user-1",
    )
    assert token.token_hash == "a" * 64
    assert token.revoked_at is None
    assert token.expires_at is None


@pytest.mark.asyncio
async def test_share_models_persistence(db_session):
    """ShareGrant and ShareToken tables are created and can persist rows."""
    grant = ShareGrant(
        item_type="track",
        item_id="track-1",
        user_id="user-1",
        created_by="user-2",
    )
    token = ShareToken(
        item_type="file",
        item_id="file-1",
        token_hash="b" * 64,
        created_by="user-1",
    )
    db_session.add_all([grant, token])
    await db_session.flush()

    assert grant.id is not None
    assert token.id is not None

    result = await db_session.execute(select(ShareToken).where(ShareToken.token_hash == "b" * 64))
    assert result.scalar_one() is token


@pytest.mark.asyncio
async def test_transcoded_file_unique_constraint(db_session, regular_user):
    """TranscodedFile enforces a unique (track_id, format, bitrate) triple."""
    artist = Artist(name="Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(
        title="Track",
        artist_id=artist.id,
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(track)
    await db_session.flush()

    stored1 = StoredFile(
        storage_path="transcoded/aa/bb/01",
        storage_backend="local",
        content_type="audio/opus",
        size=10,
        sha256="a" * 64,
    )
    stored2 = StoredFile(
        storage_path="transcoded/aa/bb/02",
        storage_backend="local",
        content_type="audio/opus",
        size=10,
        sha256="b" * 64,
    )
    db_session.add_all([stored1, stored2])
    await db_session.flush()

    first = TranscodedFile(
        track_id=track.id,
        format="opus",
        bitrate="128k",
        stored_file_id=stored1.id,
    )
    db_session.add(first)
    await db_session.flush()

    second = TranscodedFile(
        track_id=track.id,
        format="opus",
        bitrate="128k",
        stored_file_id=stored2.id,
    )
    db_session.add(second)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_track_play_count_defaults_to_zero(db_session, regular_user):
    """A new track has play_count defaulted to 0."""
    artist = Artist(name="Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(
        title="Track",
        artist_id=artist.id,
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(track)
    await db_session.flush()

    assert track.play_count == 0
