"""
Federation visibility tests.

Only public tracks should be serialized into ActivityPub objects and
activities. The audio stream URL must point to the public download endpoint.
"""

from songhive.federation.activities import create_audio_activity
from songhive.federation.serializers import track_to_audio_object
from songhive.models import Visibility
from songhive.models.artist import Artist
from songhive.models.stored_file import StoredFile
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


def test_track_to_audio_object_includes_attachment():
    """A public track includes a Document attachment for the stream."""
    track = _make_track(Visibility.PUBLIC.value)
    artist = _make_artist()
    obj = track_to_audio_object(track, artist, "music.example.com")

    assert obj is not None
    assert "attachment" in obj
    assert len(obj["attachment"]) == 1
    assert obj["attachment"][0]["type"] == "Document"
    assert obj["attachment"][0]["url"] == "https://music.example.com/api/v1/files/file-1/download"
    assert obj["attachment"][0]["name"] == track.title
    assert obj["attachment"][0]["mediaType"] == "audio/mpeg"


def test_track_to_audio_object_uses_explicit_mime_type():
    """A track's stored audio MIME type is used for links and attachments."""
    track = _make_track(Visibility.PUBLIC.value)
    track.audio_mime_type = "audio/ogg"
    artist = _make_artist()
    obj = track_to_audio_object(track, artist, "music.example.com")

    assert obj is not None
    assert any(
        link["href"] == "https://music.example.com/api/v1/files/file-1/download" and link["mediaType"] == "audio/ogg"
        for link in obj["url"]
    )
    assert obj["attachment"][0]["mediaType"] == "audio/ogg"


def test_track_to_audio_object_falls_back_to_stored_file_content_type():
    """When audio_mime_type is unset, the stored file content type is used."""
    track = _make_track(Visibility.PUBLIC.value)
    track.audio_file = StoredFile(
        content_type="audio/flac",
        storage_path="songs/test.flac",
        storage_backend="local",
        size=1234,
        sha256="a" * 64,
    )
    artist = _make_artist()
    obj = track_to_audio_object(track, artist, "music.example.com")

    assert obj is not None
    assert any(
        link["href"] == "https://music.example.com/api/v1/files/file-1/download" and link["mediaType"] == "audio/flac"
        for link in obj["url"]
    )
    assert obj["attachment"][0]["mediaType"] == "audio/flac"


def test_track_to_audio_object_attribution_with_actor_url():
    """When an actor_url is provided, attributedTo includes both artist and actor."""
    track = _make_track(Visibility.PUBLIC.value)
    artist = _make_artist()
    actor_url = "https://music.example.com/users/alice"
    obj = track_to_audio_object(track, artist, "music.example.com", actor_url=actor_url)

    assert obj is not None
    assert obj["attributedTo"] == [
        f"https://music.example.com/artists/{artist.id}",
        actor_url,
    ]


def test_track_to_audio_object_attribution_without_actor_url():
    """When no actor_url is provided, attributedTo is the artist URL only."""
    track = _make_track(Visibility.PUBLIC.value)
    artist = _make_artist()
    obj = track_to_audio_object(track, artist, "music.example.com")

    assert obj is not None
    assert obj["attributedTo"] == f"https://music.example.com/artists/{artist.id}"


def test_create_audio_activity_includes_actor_attribution():
    """A Create(Audio) activity attributes the object to artist and actor."""
    track = _make_track(Visibility.PUBLIC.value)
    artist = _make_artist()
    actor_url = "https://music.example.com/users/alice"
    activity = create_audio_activity(
        actor_url,
        track,
        artist,
        "music.example.com",
    )

    assert activity is not None
    assert activity["object"]["attributedTo"] == [
        f"https://music.example.com/artists/{artist.id}",
        actor_url,
    ]
    assert "attachment" in activity["object"]
    assert activity["object"]["attachment"][0]["type"] == "Document"
