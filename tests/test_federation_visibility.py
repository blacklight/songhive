"""
Federation visibility tests.

Only public tracks should be serialized into ActivityPub objects and
activities. The audio stream URL must point to the public download endpoint.
"""

from songhive.federation._common import get_stream_url
from songhive.federation.activities import create_audio_activity
from songhive.federation.serializers import track_to_attachment, track_to_audio_object, track_to_note_object
from songhive.models import Visibility
from songhive.models.artist import Artist
from songhive.models.external_track import ExternalTrack
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


def _make_external_track(visibility: str, state: str = "active") -> Track:
    """A track whose bytes live on an external provider (e.g. WebDAV)."""
    track = _make_track(visibility, audio_file_id=None)
    track.audio_mime_type = "audio/flac"
    track.external_track = ExternalTrack(
        external_library_id="lib-1",
        provider_key="music/song.flac",
        provider_mime_type="audio/flac",
        provider_size=30_000_000,
        state=state,
    )
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
    assert activity["object"]["content"] == (
        'A great track<p><a href="https://music.example.com/tracks/track-1">TestArtist - TestTrack</a></p>'
    )
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
    assert activity["object"]["duration"] == 195


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
    """When an actor_url is provided, attributedTo leads with the actor then the artist."""
    track = _make_track(Visibility.PUBLIC.value)
    artist = _make_artist()
    actor_url = "https://music.example.com/users/alice"
    obj = track_to_audio_object(track, artist, "music.example.com", actor_url=actor_url)

    assert obj is not None
    assert obj["attributedTo"] == [
        actor_url,
        f"https://music.example.com/artists/{artist.id}",
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
        actor_url,
        f"https://music.example.com/artists/{artist.id}",
    ]
    assert "attachment" in activity["object"]
    assert activity["object"]["attachment"][0]["type"] == "Document"


def test_get_stream_url_external_track():
    """External tracks stream through the track download endpoint."""
    track = _make_external_track(Visibility.PUBLIC.value)
    assert get_stream_url(track, "music.example.com") == "https://music.example.com/api/v1/tracks/track-1/download"


def test_get_stream_url_inactive_external_track():
    """A non-active external backing has no playable media URL."""
    track = _make_external_track(Visibility.PUBLIC.value, state="missing")
    assert get_stream_url(track, "music.example.com") is None


def test_get_stream_url_without_media():
    """A track with no stored file and no external backing has no media URL."""
    track = _make_track(Visibility.PUBLIC.value, audio_file_id=None)
    assert get_stream_url(track, "music.example.com") is None


def test_track_to_audio_object_external_track():
    """An external track advertises the track download endpoint, not the page."""
    track = _make_external_track(Visibility.PUBLIC.value)
    artist = _make_artist()
    obj = track_to_audio_object(track, artist, "music.example.com")

    assert obj is not None
    assert obj["url"][0] == {
        "type": "Link",
        "href": "https://music.example.com/api/v1/tracks/track-1/download",
        "mediaType": "audio/flac",
        "mimeType": "audio/flac",
    }
    assert obj["url"][1] == {
        "type": "Link",
        "href": "https://music.example.com/tracks/track-1",
        "mediaType": "text/html",
        "mimeType": "text/html",
    }
    # Provider-reported size stands in for the missing StoredFile.
    assert obj["size"] == 30_000_000
    assert obj["bitrate"] == 2_000_000
    assert obj["attachment"] == [
        {
            "type": "Document",
            "mediaType": "audio/flac",
            "url": "https://music.example.com/api/v1/tracks/track-1/download",
            "name": "TestTrack",
            "songhive:trackTitle": "TestTrack",
            "songhive:artistName": "TestArtist",
            "songhive:trackUrl": "https://music.example.com/tracks/track-1",
        }
    ]


def test_track_to_audio_object_inactive_external_track():
    """A non-active external track emits only the HTML page link."""
    track = _make_external_track(Visibility.PUBLIC.value, state="missing")
    artist = _make_artist()
    obj = track_to_audio_object(track, artist, "music.example.com")

    assert obj is not None
    assert obj["url"] == [
        {
            "type": "Link",
            "href": "https://music.example.com/tracks/track-1",
            "mediaType": "text/html",
            "mimeType": "text/html",
        }
    ]
    assert "attachment" not in obj


def test_track_to_audio_object_no_media_emits_no_audio_link():
    """A track with no playable media does not label its page as audio."""
    track = _make_track(Visibility.PUBLIC.value, audio_file_id=None)
    artist = _make_artist()
    obj = track_to_audio_object(track, artist, "music.example.com")

    assert obj is not None
    assert all(link["mediaType"] == "text/html" for link in obj["url"])
    assert "attachment" not in obj


def test_track_to_attachment_external_track():
    """External tracks produce an Audio status attachment, not a page Document."""
    track = _make_external_track(Visibility.PUBLIC.value)
    attachment = track_to_attachment(track, _make_artist(), "music.example.com")

    assert attachment["type"] == "Audio"
    assert attachment["mediaType"] == "audio/flac"
    assert attachment["url"] == "https://music.example.com/api/v1/tracks/track-1/download"
    assert attachment["name"] == "TestArtist - TestTrack"
    assert attachment["songhive:trackUrl"] == "https://music.example.com/tracks/track-1"


def test_track_to_attachment_inactive_external_track_falls_back_to_page():
    """A non-active external track degrades to the track-page Document."""
    track = _make_external_track(Visibility.PUBLIC.value, state="missing")
    attachment = track_to_attachment(track, _make_artist(), "music.example.com")

    assert attachment["type"] == "Document"
    assert attachment["mediaType"] == "text/html"
    assert attachment["url"] == "https://music.example.com/tracks/track-1"


def test_create_audio_activity_external_track():
    """Create(Audio) for an external track links the download endpoint."""
    track = _make_external_track(Visibility.PUBLIC.value)
    activity = create_audio_activity(
        "https://music.example.com/users/alice",
        track,
        _make_artist(),
        "music.example.com",
    )

    assert activity is not None
    obj = activity["object"]
    assert obj["url"][0]["href"] == "https://music.example.com/api/v1/tracks/track-1/download"
    assert obj["attachment"][0]["url"] == "https://music.example.com/api/v1/tracks/track-1/download"


def test_track_to_note_object_external_track_attachment():
    """A Note share of an external track embeds the playable attachment."""
    track = _make_external_track(Visibility.PUBLIC.value)
    obj = track_to_note_object(track, _make_artist(), "music.example.com")

    assert obj is not None
    assert obj["attachment"][0]["type"] == "Audio"
    assert obj["attachment"][0]["url"] == "https://music.example.com/api/v1/tracks/track-1/download"
