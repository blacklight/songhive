"""
Federation visibility tests.

Only public tracks should be serialized into ActivityPub objects and
activities. The audio stream URL must point to the public download endpoint.
"""

from songhive.federation.activities import create_audio_activity
from songhive.federation.serializers import track_to_audio_object
from songhive.models import Visibility
from songhive.models.artist import Artist
from songhive.models.track import Track


def _make_artist():
    artist = Artist(name="TestArtist")
    artist.id = "artist-1"
    return artist


def _make_track(visibility: str, audio_file_id: str = "file-1") -> Track:
    track = Track(
        title="TestTrack",
        artist_id="artist-1",
        audio_file_id=audio_file_id,
        duration=120.0,
        genre="Rock",
        visibility=visibility,
    )
    track.id = "track-1"
    return track


def test_track_to_audio_object_skips_private_track():
    """A private track produces no Audio object."""
    track = _make_track(Visibility.PRIVATE.value)
    artist = _make_artist()
    assert track_to_audio_object(track, artist, "music.example.com") is None


def test_track_to_audio_object_skips_local_track():
    """A local track produces no Audio object."""
    track = _make_track(Visibility.LOCAL.value)
    artist = _make_artist()
    assert track_to_audio_object(track, artist, "music.example.com") is None


def test_track_to_audio_object_includes_public_download_url():
    """A public track's stream URL points to the public file download."""
    track = _make_track(Visibility.PUBLIC.value)
    artist = _make_artist()
    obj = track_to_audio_object(track, artist, "music.example.com")

    assert obj is not None
    assert any(
        link["href"] == "https://music.example.com/api/v1/files/file-1/download" and link["mediaType"] == "audio/mpeg"
        for link in obj["url"]
    )


def test_track_to_audio_object_uses_explicit_stream_url():
    """When a stream URL is provided it is used in the serialized object."""
    track = _make_track(Visibility.PUBLIC.value)
    artist = _make_artist()
    stream_url = "https://music.example.com/api/v1/files/file-1/download?extra=1"
    obj = track_to_audio_object(track, artist, "music.example.com", stream_url=stream_url)

    assert obj is not None
    assert any(link["href"] == stream_url and link["mediaType"] == "audio/mpeg" for link in obj["url"])


def test_create_audio_activity_skips_private_track():
    """A private track produces no Create(Audio) activity."""
    track = _make_track(Visibility.PRIVATE.value)
    artist = _make_artist()
    assert (
        create_audio_activity(
            "https://music.example.com/users/alice",
            track,
            artist,
            "music.example.com",
        )
        is None
    )


def test_create_audio_activity_skips_local_track():
    """A local track produces no Create(Audio) activity."""
    track = _make_track(Visibility.LOCAL.value)
    artist = _make_artist()
    assert (
        create_audio_activity(
            "https://music.example.com/users/alice",
            track,
            artist,
            "music.example.com",
        )
        is None
    )


def test_create_audio_activity_includes_public_download_url():
    """A public track's activity links to the public file download."""
    track = _make_track(Visibility.PUBLIC.value)
    artist = _make_artist()
    activity = create_audio_activity(
        "https://music.example.com/users/alice",
        track,
        artist,
        "music.example.com",
        description="A great track",
    )

    assert activity is not None
    assert activity["type"] == "Create"
    assert activity["object"]["type"] == "Audio"
    assert activity["object"]["content"] == "A great track"
    assert any(
        link["href"] == "https://music.example.com/api/v1/files/file-1/download" and link["mediaType"] == "audio/mpeg"
        for link in activity["object"]["url"]
    )


def test_create_audio_activity_duration_override():
    """The optional duration parameter overrides the track's duration."""
    track = _make_track(Visibility.PUBLIC.value)
    artist = _make_artist()
    activity = create_audio_activity(
        "https://music.example.com/users/alice",
        track,
        artist,
        "music.example.com",
        duration=195.5,
    )

    assert activity is not None
    assert activity["object"]["duration"] == "PT3M15S"


def test_outbox_caller_skips_non_public_tracks():
    """A batch outbox helper only emits activities for public tracks."""
    artist = _make_artist()
    tracks = [
        _make_track(Visibility.PUBLIC.value),
        _make_track(Visibility.LOCAL.value),
        _make_track(Visibility.PRIVATE.value),
    ]

    activities = [
        create_audio_activity(
            "https://music.example.com/users/alice",
            track,
            artist,
            "music.example.com",
        )
        for track in tracks
    ]

    assert [a is not None for a in activities] == [True, False, False]
