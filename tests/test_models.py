"""
Model tests - verify model instantiation and relationships.
"""

from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.favorite import Favorite
from songhive.models.history import ListeningHistory
from songhive.models.library import Library
from songhive.models.playlist import Playlist
from songhive.models.radio import Radio
from songhive.models.track import Track
from songhive.models.user import User


def test_user_model():
    """Test User model instantiation."""
    user = User(
        username="testuser",
        email="test@example.com",
        password_hash="hashed",
        is_admin=False,
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
