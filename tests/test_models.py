"""
Model tests - verify model instantiation and relationships.
"""

import pytest
from sqlalchemy import select

from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.favorite import Favorite
from songhive.models.history import ListeningHistory
from songhive.models.library import Library
from songhive.models.playlist import Playlist
from songhive.models.radio import Radio
from songhive.models.stored_file import StoredFile
from songhive.models.track import Track
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
    lib = Library(name="My Library", owner_id="user-1", is_public=False)
    assert lib.is_public is False


def test_playlist_model():
    """Test Playlist model instantiation."""
    pl = Playlist(name="My Playlist", owner_id="user-1", is_public=False)
    assert pl.is_public is False


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
