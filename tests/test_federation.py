"""
Federation module tests.
"""

import asyncio
from contextlib import asynccontextmanager
from functools import partial
from unittest.mock import AsyncMock, MagicMock

import pytest
from celery.exceptions import Retry
from pubby.content import render_bio_html, render_post_html, render_verified_link
from sqlalchemy import select

from songhive.config.schema import SonghiveConfig
from songhive.federation._common import get_hashtag_url
from songhive.federation.activities import create_audio_activity, create_update_actor_activity
from songhive.federation.actors import (
    get_actor_url,
    get_federation_storage,
    get_inbox_url,
    sync_user_actor,
    user_to_actor_document,
)
from songhive.federation.serializers import track_to_audio_object
from songhive.models import Visibility
from songhive.models.activity import ActivityTarget
from songhive.models.album import Album  # noqa: F401
from songhive.models.artist import Artist
from songhive.models.track import Track
from songhive.models.user import User
from songhive.models.user_link import UserLink
from songhive.services.activities import record_track_publication
from songhive.services.federation import publish_actor_update
from songhive.tasks.federation import _load_user_actor, deliver_activity, process_incoming


def test_get_actor_url():
    """Test actor URL generation."""
    assert get_actor_url("music.example.com", "alice") == "https://music.example.com/users/alice"


def test_get_inbox_url():
    """Test inbox URL generation."""
    assert get_inbox_url("music.example.com", "alice") == "https://music.example.com/users/alice/inbox"


def test_user_to_actor_document():
    """Test converting a User to an AP actor document."""
    user = User(
        username="alice",
        email="alice@example.com",
        password_hash="x",
        display_name="Alice",
        public_key_pem="-----BEGIN PUBLIC KEY-----\ntest\n-----END PUBLIC KEY-----",
    )
    doc = user_to_actor_document(user, "music.example.com")
    assert doc["type"] == "Person"
    assert doc["preferredUsername"] == "alice"
    assert doc["name"] == "Alice"
    assert "inbox" in doc
    assert "publicKey" in doc


def test_create_audio_activity():
    """Test creating a Create(Audio) activity for a public track."""
    artist = Artist(name="TestArtist")
    artist.id = "artist-1"
    track = Track(
        title="My Song",
        artist_id="artist-1",
        audio_file_id="file-1",
        duration=195.5,
        visibility=Visibility.PUBLIC.value,
    )
    track.id = "track-123"

    activity = create_audio_activity(
        actor_url="https://music.example.com/users/alice",
        track=track,
        artist=artist,
        domain="music.example.com",
        description="A great track",
    )
    assert activity is not None
    assert activity["type"] == "Create"
    assert activity["object"]["type"] == "Audio"
    # ``name`` carries an "{artist} - {title}" anchor to the track page so
    # Mastodon-family servers render the post header as a link.
    assert activity["object"]["name"] == '<a href="https://music.example.com/tracks/track-123">TestArtist - My Song</a>'
    assert activity["object"]["content"] == "A great track"
    assert activity["object"]["summary"] == "A great track"
    assert "PT3M15S" in activity["object"]["duration"]
    assert any(
        link["href"] == "https://music.example.com/api/v1/files/file-1/download" and link["mediaType"] == "audio/mpeg"
        for link in activity["object"]["url"]
    )


def test_track_to_audio_object():
    """Test serializing a public Track to an AP Audio object."""
    artist = Artist(name="TestArtist")
    artist.id = "artist-1"
    track = Track(
        title="TestTrack",
        artist_id="artist-1",
        audio_file_id="file-1",
        duration=120.0,
        genre="Rock",
        visibility=Visibility.PUBLIC.value,
    )
    track.id = "track-1"

    obj = track_to_audio_object(track, artist, "music.example.com")
    assert obj is not None
    assert obj["type"] == "Audio"
    assert obj["name"] == '<a href="https://music.example.com/tracks/track-1">TestArtist - TestTrack</a>'
    assert obj["duration"] == "PT2M"
    # ``mimeType`` mirrors ``mediaType`` so Mastodon's ``url_to_href`` selects
    # the ``text/html`` track page for display instead of the audio download.
    assert all(link["mimeType"] == link["mediaType"] for link in obj["url"])
    html_link = next(link for link in obj["url"] if link["mimeType"] == "text/html")
    assert html_link["href"] == "https://music.example.com/tracks/track-1"
    assert obj["tag"] == [{"type": "Hashtag", "name": "#rock", "href": "https://music.example.com/hashtags/rock"}]
    assert any(
        link["href"] == "https://music.example.com/api/v1/files/file-1/download" and link["mediaType"] == "audio/mpeg"
        for link in obj["url"]
    )


def test_track_to_audio_object_emits_multiple_genre_hashtags():
    """Multi-genre tracks emit one ActivityPub Hashtag tag per genre."""
    artist = Artist(name="TestArtist")
    artist.id = "artist-1"
    track = Track(
        title="TestTrack",
        artist_id="artist-1",
        audio_file_id="file-1",
        duration=120.0,
        genre="Rock; Pop",
        visibility=Visibility.PUBLIC.value,
    )
    track.id = "track-1"

    obj = track_to_audio_object(track, artist, "music.example.com")
    assert obj is not None
    assert obj["tag"] == [
        {"type": "Hashtag", "name": "#rock", "href": "https://music.example.com/hashtags/rock"},
        {"type": "Hashtag", "name": "#pop", "href": "https://music.example.com/hashtags/pop"},
    ]


def test_track_to_audio_object_converts_spaces_to_underscores():
    """Genre names with spaces are emitted as underscore-separated hashtags."""
    artist = Artist(name="TestArtist")
    artist.id = "artist-1"
    track = Track(
        title="TestTrack",
        artist_id="artist-1",
        audio_file_id="file-1",
        duration=120.0,
        genre="Hip Hop",
        visibility=Visibility.PUBLIC.value,
    )
    track.id = "track-1"

    obj = track_to_audio_object(track, artist, "music.example.com")
    assert obj is not None
    assert obj["tag"] == [{"type": "Hashtag", "name": "#hip_hop", "href": "https://music.example.com/hashtags/hip_hop"}]


def test_render_post_html_linkifies_urls_and_hashtags():
    """Post text renders URLs as anchors and hashtags as tag links."""
    rendered = render_post_html(
        "New track #LoFi out now: https://band.example.com/song #chill",
        partial(get_hashtag_url, "music.example.com"),
    )
    assert rendered.html == (
        'New track <a href="https://music.example.com/hashtags/lofi" rel="tag">#LoFi</a> '
        'out now: <a href="https://band.example.com/song">band.example.com/song</a> '
        '<a href="https://music.example.com/hashtags/chill" rel="tag">#chill</a>'
    )
    assert rendered.hashtags == ["lofi", "chill"]


def test_render_post_html_escapes_text():
    """Description text is HTML-escaped; markup cannot be injected."""
    rendered = render_post_html('<b>bold</b> #tag "quotes"', partial(get_hashtag_url, "music.example.com"))
    assert rendered.html == (
        '&lt;b&gt;bold&lt;/b&gt; <a href="https://music.example.com/hashtags/tag" rel="tag">#tag</a> '
        "&quot;quotes&quot;"
    )
    assert rendered.hashtags == ["tag"]


def test_render_post_html_skips_invalid_tokens():
    """Numeric-only hashtags and hostless URLs stay as escaped text."""
    rendered = render_post_html("code #123 and https:// here a#b", partial(get_hashtag_url, "music.example.com"))
    assert rendered.html == "code #123 and https:// here a#b"
    assert rendered.hashtags == []


def test_render_post_html_dedupes_hashtags():
    """Repeated hashtags produce a single tag entry."""
    rendered = render_post_html("#rock #Rock #ROCK", partial(get_hashtag_url, "music.example.com"))
    assert rendered.hashtags == ["rock"]
    assert rendered.html.count('href="https://music.example.com/hashtags/rock"') == 3


def test_track_to_audio_object_renders_description():
    """A public track's description becomes linkified ``content``."""
    artist = Artist(name="TestArtist")
    artist.id = "artist-1"
    track = Track(
        title="TestTrack",
        artist_id="artist-1",
        audio_file_id="file-1",
        duration=120.0,
        genre="Rock",
        description="My new #LoFi song: https://band.example.com/song",
        visibility=Visibility.PUBLIC.value,
    )
    track.id = "track-1"

    obj = track_to_audio_object(track, artist, "music.example.com")
    assert obj is not None
    assert obj["content"] == (
        'My new <a href="https://music.example.com/hashtags/lofi" rel="tag">#LoFi</a> '
        'song: <a href="https://band.example.com/song">band.example.com/song</a>'
    )
    # Genre hashtag and description hashtag are merged, deduplicated by name.
    assert obj["tag"] == [
        {"type": "Hashtag", "name": "#rock", "href": "https://music.example.com/hashtags/rock"},
        {"type": "Hashtag", "name": "#lofi", "href": "https://music.example.com/hashtags/lofi"},
    ]


def test_track_to_audio_object_description_dedupes_genre():
    """A description hashtag matching a genre does not duplicate the tag."""
    artist = Artist(name="TestArtist")
    artist.id = "artist-1"
    track = Track(
        title="TestTrack",
        artist_id="artist-1",
        audio_file_id="file-1",
        genre="Rock",
        description="loud #rock anthem",
        visibility=Visibility.PUBLIC.value,
    )
    track.id = "track-1"

    obj = track_to_audio_object(track, artist, "music.example.com")
    assert obj is not None
    assert obj["tag"] == [{"type": "Hashtag", "name": "#rock", "href": "https://music.example.com/hashtags/rock"}]
    assert "#rock" in obj["content"]


def test_track_to_audio_object_escapes_description():
    """Markup in a description is escaped in the Audio object content."""
    artist = Artist(name="TestArtist")
    artist.id = "artist-1"
    track = Track(
        title="TestTrack",
        artist_id="artist-1",
        audio_file_id="file-1",
        description="<script>alert(1)</script>",
        visibility=Visibility.PUBLIC.value,
    )
    track.id = "track-1"

    obj = track_to_audio_object(track, artist, "music.example.com")
    assert obj is not None
    assert obj["content"] == "&lt;script&gt;alert(1)&lt;/script&gt;"


def test_track_to_audio_object_media_attachment():
    """The public download URL is attached as a Document on the Audio object."""
    artist = Artist(name="TestArtist")
    artist.id = "artist-1"
    track = Track(
        title="TestTrack",
        artist_id="artist-1",
        audio_file_id="file-1",
        audio_mime_type="audio/flac",
        visibility=Visibility.PUBLIC.value,
    )
    track.id = "track-1"

    obj = track_to_audio_object(track, artist, "music.example.com")
    assert obj is not None
    assert obj["attachment"] == [
        {
            "type": "Document",
            "mediaType": "audio/flac",
            "url": "https://music.example.com/api/v1/files/file-1/download",
            "name": "TestTrack",
        }
    ]


def test_create_audio_activity_renders_track_description():
    """Create(Audio) content comes from the track description."""
    artist = Artist(name="TestArtist")
    artist.id = "artist-1"
    track = Track(
        title="My Song",
        artist_id="artist-1",
        audio_file_id="file-1",
        description="Fresh #beats at https://band.example.com",
        visibility=Visibility.PUBLIC.value,
    )
    track.id = "track-123"

    activity = create_audio_activity(
        actor_url="https://music.example.com/users/alice",
        track=track,
        artist=artist,
        domain="music.example.com",
    )
    assert activity is not None
    assert activity["object"]["content"] == (
        'Fresh <a href="https://music.example.com/hashtags/beats" rel="tag">#beats</a> '
        'at <a href="https://band.example.com">band.example.com</a>'
    )
    # Mirrored to ``summary`` so Mastodon-style "converted" Audio objects
    # render the description instead of dropping ``content``.
    assert activity["object"]["summary"] == activity["object"]["content"]


def test_track_to_audio_object_without_description_has_no_content_or_summary():
    """Audio objects without a description carry neither content nor summary."""
    artist = Artist(name="TestArtist")
    artist.id = "artist-1"
    track = Track(
        title="TestTrack",
        artist_id="artist-1",
        audio_file_id="file-1",
        visibility=Visibility.PUBLIC.value,
    )
    track.id = "track-1"

    obj = track_to_audio_object(track, artist, "music.example.com")
    assert obj is not None
    assert "content" not in obj
    assert "summary" not in obj


def test_federation_app_setup(tmp_path):
    """Test that the FastAPI app can set up ActivityPub federation."""
    from fastapi.testclient import TestClient

    from songhive.api.app import create_app
    from songhive.config.schema import SonghiveConfig

    key_path = tmp_path / "actor.pem"
    config = SonghiveConfig(
        database={"url": f"sqlite+aiosqlite:///{tmp_path / 'songhive.db'}"},
        federation={
            "enabled": True,
            "instance_domain": "music.example.com",
            "instance_name": "Songhive",
            "instance_description": "A federated music instance",
            "private_key_path": key_path,
        },
    )

    app = create_app(config)

    with TestClient(app) as client:
        webfinger = client.get("/.well-known/webfinger?resource=acct:songhive@music.example.com")
        assert webfinger.status_code == 200
        assert webfinger.json()["subject"] == "acct:songhive@music.example.com"

        actor = client.get("/ap/actor")
        assert actor.status_code == 200
        data = actor.json()
        assert data["type"] == "Application"
        assert data["preferredUsername"] == "songhive"
        assert "publicKey" in data

        # A second create_app call should reuse the existing persisted key.
        assert key_path.exists()
        assert key_path.stat().st_size > 0


def test_federation_app_inbox_drops_blocked_domain(tmp_path):
    """The instance /ap/inbox silently drops activities from blocked domains."""
    from fastapi.testclient import TestClient

    from songhive.api.app import create_app
    from songhive.config.schema import SonghiveConfig

    config = SonghiveConfig(
        database={"url": f"sqlite+aiosqlite:///{tmp_path / 'songhive.db'}"},
        federation={
            "enabled": True,
            "instance_domain": "music.example.com",
            "instance_name": "Songhive",
            "private_key_path": tmp_path / "actor.pem",
            "blocked_instances": ["evil.example"],
        },
    )

    app = create_app(config)

    with TestClient(app) as client:
        # Blocked actors are dropped before signature verification.
        blocked = client.post(
            "/ap/inbox",
            json={
                "type": "Follow",
                "actor": "https://evil.example/users/bob",
                "object": "https://music.example.com/ap/actor",
            },
        )
        assert blocked.status_code == 202

        # Non-blocked actors reach signature verification and are rejected
        # for the missing Signature header instead.
        allowed = client.post(
            "/ap/inbox",
            json={
                "type": "Follow",
                "actor": "https://friend.example/users/carol",
                "object": "https://music.example.com/ap/actor",
            },
        )
        assert allowed.status_code == 401


def test_user_to_actor_document_includes_avatar_and_links():
    """Test that the actor document exposes avatar and profile links."""
    user = User(
        username="alice",
        email="alice@example.com",
        password_hash="x",
        display_name="Alice",
        bio="Hello fediverse",
        avatar_url="https://example.com/avatar.png",
        links=[
            UserLink(name="Website", url="https://example.com"),
            UserLink(name="Mastodon", url="https://mastodon.example.com/@alice"),
        ],
    )
    doc = user_to_actor_document(user, "music.example.com")
    assert doc["url"] == "https://music.example.com/users/alice"
    assert doc["icon"] == {"type": "Image", "url": "https://example.com/avatar.png"}
    assert doc["attachment"] == [
        {
            "type": "PropertyValue",
            "name": "Website",
            "value": '<a href="https://example.com" rel="me">example.com</a>',
        },
        {
            "type": "PropertyValue",
            "name": "Mastodon",
            "value": '<a href="https://mastodon.example.com/@alice" rel="me">mastodon.example.com/@alice</a>',
        },
    ]


def test_user_to_actor_document_escapes_invalid_link_values():
    """Malformed-but-prefixed URLs are emitted as escaped text, not anchors."""
    user = User(
        username="alice",
        email="alice@example.com",
        password_hash="x",
        links=[
            UserLink(name="Query", url="https://example.com/?a=1&b=2"),
            UserLink(name="No host", url="https://"),
            UserLink(name="Space", url="https://exa mple.com"),
            UserLink(name="Quote", url='https://example.com/" onmouseover="alert(1)'),
        ],
    )
    doc = user_to_actor_document(user, "music.example.com")
    assert doc["attachment"] == [
        {
            "type": "PropertyValue",
            "name": "Query",
            "value": '<a href="https://example.com/?a=1&amp;b=2" rel="me">example.com/?a=1&amp;b=2</a>',
        },
        {"type": "PropertyValue", "name": "No host", "value": "https://"},
        {"type": "PropertyValue", "name": "Space", "value": "https://exa mple.com"},
        {
            "type": "PropertyValue",
            "name": "Quote",
            "value": "https://example.com/&quot; onmouseover=&quot;alert(1)",
        },
    ]


def test_render_verified_link_rejects_non_http_schemes():
    """Non-http(s) values are never linkified, even if they reach the model."""
    assert render_verified_link("javascript:alert(1)") == "javascript:alert(1)"
    assert render_verified_link("ftp://example.com") == "ftp://example.com"
    assert render_verified_link("just some text") == "just some text"
    assert render_verified_link("") == ""


def test_render_bio_html_linkifies_urls():
    """http(s) URLs in the bio become anchors with scheme-less link text."""
    bio = "Find me at https://blog.example.com or http://old.example.net/page."
    assert render_bio_html(bio) == (
        'Find me at <a href="https://blog.example.com">blog.example.com</a> '
        'or <a href="http://old.example.net/page">old.example.net/page</a>.'
    )


def test_render_bio_html_escapes_non_url_text():
    """Bio text is HTML-escaped; angle brackets cannot inject markup."""
    bio = '<b>not bold</b> see https://a.example/?q="x"'
    assert render_bio_html(bio) == (
        "&lt;b&gt;not bold&lt;/b&gt; see " '<a href="https://a.example/?q=">a.example/?q=</a>&quot;x&quot;'
    )


def test_render_bio_html_strips_wrapping_punctuation():
    """Sentence punctuation around a URL stays outside the anchor."""
    assert render_bio_html("(see https://example.com/path)") == (
        '(see <a href="https://example.com/path">example.com/path</a>)'
    )
    # Balanced brackets inside the URL are kept.
    assert render_bio_html("https://example.com/a_(b)") == ('<a href="https://example.com/a_(b)">example.com/a_(b)</a>')


def test_render_bio_html_leaves_invalid_urls_as_text():
    """URL-looking text without a host is emitted as escaped text."""
    assert render_bio_html("visit https:// now") == "visit https:// now"
    assert render_bio_html("plain text") == "plain text"
    assert render_bio_html("") == ""


def test_user_to_actor_document_linkifies_bio():
    """The actor summary contains linkified, escaped bio HTML."""
    user = User(
        username="alice",
        email="alice@example.com",
        password_hash="x",
        bio="Music: https://band.example.com/alice",
    )
    doc = user_to_actor_document(user, "music.example.com")
    assert doc["summary"] == ('Music: <a href="https://band.example.com/alice">band.example.com/alice</a>')


def test_user_to_actor_document_omits_optional_fields():
    """Test that the actor document omits icon and attachment when not set."""
    user = User(
        username="alice",
        email="alice@example.com",
        password_hash="x",
    )
    doc = user_to_actor_document(user, "music.example.com")
    assert "icon" not in doc
    assert "attachment" not in doc


@pytest.mark.asyncio
async def test_sync_user_actor_skips_when_federation_disabled(db_session, config):
    """Test that sync is a no-op when federation is disabled."""
    user = User(username="alice", email="alice@example.com", password_hash="x")
    db_session.add(user)
    await db_session.flush()

    result = await sync_user_actor(user, config)
    assert result is False


@pytest.mark.asyncio
async def test_sync_user_actor_caches_document(db_session, config):
    """Test that sync stores the user's actor document in pubby storage."""
    user = User(
        username="alice",
        email="alice@example.com",
        password_hash="x",
        display_name="Alice",
        bio="Hello fediverse",
        avatar_url="https://example.com/avatar.png",
        links=[UserLink(name="Website", url="https://example.com")],
    )
    db_session.add(user)
    await db_session.commit()

    fed_config = SonghiveConfig(
        database={"url": config.database.url},
        federation={"enabled": True, "instance_domain": "music.example.com"},
        auth={"secret_key": config.auth.secret_key},
    )

    result = await sync_user_actor(user, fed_config)
    assert result is True

    storage = get_federation_storage(config.database.url)
    actor_url = get_actor_url("music.example.com", "alice")
    cached = await asyncio.to_thread(storage.get_cached_actor, actor_url)

    assert cached is not None
    assert cached["name"] == "Alice"
    assert cached["summary"] == "Hello fediverse"
    assert cached["icon"] == {"type": "Image", "url": "https://example.com/avatar.png"}
    assert cached["attachment"] == [
        {
            "type": "PropertyValue",
            "name": "Website",
            "value": '<a href="https://example.com" rel="me">example.com</a>',
        },
    ]


def test_create_update_actor_activity():
    """An Update activity wraps the full actor document."""
    actor_url = "https://music.example.com/users/alice"
    doc = {"id": actor_url, "type": "Person", "name": "Alice"}

    activity = create_update_actor_activity(actor_url, doc)

    assert activity["type"] == "Update"
    assert activity["actor"] == actor_url
    assert activity["id"].startswith(f"{actor_url}/activities/")
    assert activity["to"] == ["https://www.w3.org/ns/activitystreams#Public"]
    assert activity["cc"] == [f"{actor_url}/followers"]
    assert activity["object"] is doc
    assert "published" in activity


def _make_federated_user(username: str = "alice", **kwargs) -> User:
    """Build a user with actor credentials already provisioned."""
    return User(
        username=username,
        email=f"{username}@example.com",
        password_hash="x",
        actor_url=f"https://music.example.com/users/{username}",
        private_key_pem="private",
        public_key_pem="public",
        **kwargs,
    )


def test_publish_actor_update_noops_when_federation_disabled(monkeypatch):
    """publish_actor_update does nothing when federation is disabled."""
    user = _make_federated_user()
    deliver_mock = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver_mock)

    result = publish_actor_update(user, _fed_config(enabled=False))

    assert result == 0
    deliver_mock.delay.assert_not_called()


def test_publish_actor_update_noops_without_actor_credentials(monkeypatch):
    """publish_actor_update does nothing when the user has no actor keys."""
    user = User(username="alice", email="alice@example.com", password_hash="x")
    deliver_mock = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver_mock)

    result = publish_actor_update(user, _fed_config())

    assert result == 0
    deliver_mock.delay.assert_not_called()


def test_publish_actor_update_enqueues_update_to_followers(monkeypatch):
    """publish_actor_update sends one Update activity per follower inbox."""
    user = _make_federated_user(display_name="Alice")
    inboxes = ["https://a.example/inbox", "https://b.example/inbox"]
    monkeypatch.setattr(
        "songhive.services.federation.get_follower_inboxes",
        lambda *a, **k: inboxes,
    )
    deliver_mock = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver_mock)

    result = publish_actor_update(user, _fed_config())

    assert result == 2
    assert deliver_mock.delay.call_count == 2
    calls = [call.args for call in deliver_mock.delay.call_args_list]
    assert calls[0][1] == "https://a.example/inbox"
    assert calls[1][1] == "https://b.example/inbox"
    activity, _, key_id, pem = calls[0]
    assert activity["type"] == "Update"
    assert activity["actor"] == user.actor_url
    assert activity["object"]["id"] == user.actor_url
    assert activity["object"]["name"] == "Alice"
    assert key_id == f"{user.actor_url}#main-key"
    assert pem == "private"


@pytest.mark.asyncio
async def test_record_track_publication_uses_status_as_content(db_session, monkeypatch):
    """record_track_publication stores the one-off status as the object content."""
    user = _make_federated_user()
    db_session.add(user)
    await db_session.flush()
    artist = Artist(name="TestArtist")
    artist.id = "artist-1"
    track = Track(
        title="My Song",
        artist_id="artist-1",
        audio_file_id="file-1",
        description="stored description",
        visibility=Visibility.PUBLIC.value,
    )
    track.id = "track-1"
    track.federation_object_id = "obj-1"

    inboxes = ["https://a.example/inbox"]
    monkeypatch.setattr(
        "songhive.services.federation.get_follower_inboxes",
        lambda *a, **k: inboxes,
    )
    deliver_mock = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver_mock)

    activity = await record_track_publication(
        db_session,
        track=track,
        artist=artist,
        owner=user,
        config=_fed_config(),
        status="Fresh post about #beats",
    )

    assert activity is not None
    assert activity.activity_type == "create"
    assert activity.source_type == "local"
    assert activity.entity_type == "track"
    assert activity.entity_id == "track-1"
    assert activity.visibility == "public"
    assert activity.source_id == f"{user.actor_url}/objects/obj-1"
    assert activity.local_object_id == "obj-1"
    assert activity.content_source == "Fresh post about #beats"
    assert activity.content is not None and activity.content.startswith("Fresh post about")

    payload = activity.payload
    assert payload["type"] == "Create"
    assert payload["object"]["id"] == f"{user.actor_url}/objects/obj-1"
    assert payload["object"]["content"].startswith("Fresh post about")
    assert payload["object"]["summary"] == payload["object"]["content"]
    assert "stored description" not in payload["object"]["content"]
    assert {"type": "Hashtag", "name": "#beats", "href": "https://music.example.com/hashtags/beats"} in payload[
        "object"
    ]["tag"]

    deliver_mock.delay.assert_called_once()
    assert deliver_mock.delay.call_args[0][0] is payload
    assert deliver_mock.delay.call_args[0][1] == "https://a.example/inbox"

    # The delivered inbox is tracked so a later retraction can reach it.
    targets = [
        t
        for t in (await db_session.execute(select(ActivityTarget))).scalars().all()
        if t.activity_id == str(activity.id)
    ]
    assert [(t.inbox_url, t.state) for t in targets] == [("https://a.example/inbox", "sent")]


@pytest.mark.asyncio
async def test_record_track_publication_without_status_uses_track_description(db_session, monkeypatch):
    """record_track_publication falls back to the stored description content."""
    user = _make_federated_user()
    db_session.add(user)
    await db_session.flush()
    artist = Artist(name="TestArtist")
    artist.id = "artist-1"
    track = Track(
        title="My Song",
        artist_id="artist-1",
        audio_file_id="file-1",
        description="stored description",
        visibility=Visibility.PUBLIC.value,
    )
    track.id = "track-1"
    track.federation_object_id = "obj-1"

    monkeypatch.setattr(
        "songhive.services.federation.get_follower_inboxes",
        lambda *a, **k: ["https://a.example/inbox"],
    )
    deliver_mock = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver_mock)

    activity = await record_track_publication(
        db_session,
        track=track,
        artist=artist,
        owner=user,
        config=_fed_config(),
    )

    assert activity is not None
    assert activity.payload["object"]["content"] == "stored description"
    deliver_mock.delay.assert_called_once()


@pytest.mark.asyncio
async def test_record_track_publication_noops_when_not_public(db_session):
    """Non-public tracks record no activity and enqueue no deliveries."""
    user = _make_federated_user()
    db_session.add(user)
    await db_session.flush()
    artist = Artist(name="TestArtist")
    artist.id = "artist-1"
    track = Track(
        title="My Song",
        artist_id="artist-1",
        audio_file_id="file-1",
        visibility=Visibility.PRIVATE.value,
    )
    track.id = "track-1"
    track.federation_object_id = "obj-1"

    activity = await record_track_publication(
        db_session,
        track=track,
        artist=artist,
        owner=user,
        config=_fed_config(),
    )
    assert activity is None


@pytest.mark.asyncio
async def test_sync_user_actor_delivers_update_to_followers(db_session, config, monkeypatch):
    """sync_user_actor fans an Update activity out to follower inboxes."""
    user = User(
        username="alice",
        email="alice@example.com",
        password_hash="x",
        display_name="Alice",
        links=[],
    )
    db_session.add(user)
    await db_session.commit()

    fed_config = SonghiveConfig(
        database={"url": config.database.url},
        federation={"enabled": True, "instance_domain": "music.example.com"},
        auth={"secret_key": config.auth.secret_key},
    )

    inboxes = ["https://a.example/inbox"]
    monkeypatch.setattr(
        "songhive.services.federation.get_follower_inboxes",
        lambda *a, **k: inboxes,
    )
    deliver_mock = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver_mock)

    result = await sync_user_actor(user, fed_config)

    assert result is True
    deliver_mock.delay.assert_called_once()
    activity, inbox, key_id, pem = deliver_mock.delay.call_args.args
    assert inbox == "https://a.example/inbox"
    assert activity["type"] == "Update"
    assert activity["actor"] == user.actor_url
    assert activity["object"]["id"] == user.actor_url
    assert activity["object"]["name"] == "Alice"
    assert key_id == f"{user.actor_url}#main-key"
    assert pem == user.private_key_pem


@pytest.mark.asyncio
async def test_sync_user_actor_fans_out_updated_links(db_session, config, monkeypatch):
    """A links-only profile update is propagated to follower inboxes."""
    from songhive.users.manager import update_profile

    user = User(
        username="alice",
        email="alice@example.com",
        password_hash="x",
        links=[UserLink(name="Old", url="https://old.example")],
    )
    db_session.add(user)
    await db_session.commit()

    # Same sequence as PATCH /users/me: update, commit, then sync the actor.
    await update_profile(
        db_session,
        user,
        {"links": [UserLink(name="New", url="https://new.example")]},
    )
    await db_session.commit()

    fed_config = SonghiveConfig(
        database={"url": config.database.url},
        federation={"enabled": True, "instance_domain": "music.example.com"},
        auth={"secret_key": config.auth.secret_key},
    )
    monkeypatch.setattr(
        "songhive.services.federation.get_follower_inboxes",
        lambda *a, **k: ["https://a.example/inbox"],
    )
    deliver_mock = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver_mock)

    assert await sync_user_actor(user, fed_config) is True

    deliver_mock.delay.assert_called_once()
    activity = deliver_mock.delay.call_args.args[0]
    assert activity["type"] == "Update"
    assert activity["object"]["attachment"] == [
        {
            "type": "PropertyValue",
            "name": "New",
            "value": '<a href="https://new.example" rel="me">new.example</a>',
        }
    ]


def _fed_config(**kwargs) -> SonghiveConfig:
    """Return a federation-enabled test config."""
    return SonghiveConfig(
        auth={"secret_key": "a" * 64},
        database={"url": "sqlite+aiosqlite:///:memory:"},
        storage={"backend": "local", "local_path": "/tmp/media"},
        federation={
            "enabled": kwargs.get("enabled", True),
            "instance_domain": "music.example.com",
            "blocked_instances": kwargs.get("blocked_instances", []),
            "allowed_instances": kwargs.get("allowed_instances", []),
        },
    )


@asynccontextmanager
async def _fake_session_cm(session=None):
    """Yield a fixed session for federation task tests."""
    yield session or MagicMock()


def test_load_user_actor_not_found(monkeypatch):
    """_load_user_actor returns None when the user does not exist."""
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *a, **k: _fed_config())
    monkeypatch.setattr("songhive.tasks.federation.get_session", _fake_session_cm)
    monkeypatch.setattr("songhive.services.auth.get_user_by_username", AsyncMock(return_value=None))

    assert _load_user_actor("missing") is None


def test_load_user_actor_found(monkeypatch):
    """_load_user_actor provisions and returns the matching user."""
    user = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *a, **k: _fed_config())
    monkeypatch.setattr("songhive.tasks.federation.get_session", _fake_session_cm)
    monkeypatch.setattr("songhive.services.auth.get_user_by_username", AsyncMock(return_value=user))
    monkeypatch.setattr("songhive.tasks.federation.ensure_user_actor", MagicMock())

    assert _load_user_actor("alice") is user


def _patch_process_incoming(monkeypatch, config=None):
    """Apply common monkeypatches for process_incoming tests."""
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *a, **k: config or _fed_config())
    monkeypatch.setattr(
        "songhive.tasks.federation.get_or_create_private_key",
        lambda *a, **k: MagicMock(read_text=lambda *a, **k: "pem"),
    )
    monkeypatch.setattr("songhive.tasks.federation.get_federation_storage", lambda *a, **k: MagicMock())
    monkeypatch.setattr("songhive.tasks.federation.load_private_key", lambda *a, **k: "private_key")
    monkeypatch.setattr("songhive.tasks.federation.init_db", lambda *a, **k: None)


def test_process_incoming_disabled(monkeypatch):
    """process_incoming is a no-op when federation is disabled."""
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *a, **k: _fed_config(enabled=False))
    assert process_incoming({"actor": "https://example.com/user"}) is None


def test_process_incoming_no_actor(monkeypatch):
    """process_incoming drops activities without a usable actor."""
    _patch_process_incoming(monkeypatch)
    assert process_incoming({}) is None


def test_process_incoming_blocked_domain(monkeypatch):
    """process_incoming drops activities from blocked domains."""
    _patch_process_incoming(monkeypatch, _fed_config(blocked_instances=["example.com"]))
    assert process_incoming({"actor": "https://example.com/user"}) is None


def test_process_incoming_user_actor_missing(monkeypatch):
    """process_incoming drops activities for unknown local users."""
    _patch_process_incoming(monkeypatch)
    monkeypatch.setattr("songhive.tasks.federation._load_user_actor", lambda username: None)
    assert process_incoming({"actor": "https://example.com/user"}, username="missing") is None


def test_process_incoming_user_actor_no_context(monkeypatch):
    """process_incoming drops activities when the local user has no actor context."""
    _patch_process_incoming(monkeypatch)
    user = MagicMock(actor_url="https://music.example.com/users/alice", private_key_pem=None)
    monkeypatch.setattr("songhive.tasks.federation._load_user_actor", lambda username: user)
    assert process_incoming({"actor": "https://example.com/user"}, username="alice") is None


def test_process_incoming_base64_decode_error(monkeypatch):
    """process_incoming continues when body_b64 cannot be decoded."""
    _patch_process_incoming(monkeypatch)
    processor = MagicMock(process=MagicMock(return_value={"ok": True}))
    monkeypatch.setattr("songhive.tasks.federation.InboxProcessor", MagicMock(return_value=processor))

    result = process_incoming(
        {"actor": "https://example.com/user", "type": "Create"},
        body_b64="not-valid-base64!!!",
    )
    assert result == {"ok": True}
    assert processor.process.call_args.kwargs["body"] is None


def test_process_incoming_signature_verification_error(monkeypatch):
    """process_incoming drops activities with bad signatures."""
    _patch_process_incoming(monkeypatch)
    from songhive.tasks.federation import SignatureVerificationError

    processor = MagicMock(process=MagicMock(side_effect=SignatureVerificationError("bad signature")))
    monkeypatch.setattr("songhive.tasks.federation.InboxProcessor", MagicMock(return_value=processor))

    assert process_incoming({"actor": "https://example.com/user", "type": "Create"}) is None


def test_process_incoming_activitypub_error(monkeypatch):
    """process_incoming drops activities that cannot be processed."""
    _patch_process_incoming(monkeypatch)
    from songhive.tasks.federation import ActivityPubError

    processor = MagicMock(process=MagicMock(side_effect=ActivityPubError("bad activity")))
    monkeypatch.setattr("songhive.tasks.federation.InboxProcessor", MagicMock(return_value=processor))

    assert process_incoming({"actor": "https://example.com/user", "type": "Create"}) is None


def _deliver_self(retries: int = 0, retry_side_effect=None):
    self = MagicMock()
    self.request.retries = retries
    self.retry.side_effect = retry_side_effect or Retry()
    return self


def test_deliver_activity_disabled(monkeypatch):
    """deliver_activity is a no-op when federation is disabled."""
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *a, **k: _fed_config(enabled=False))
    self = _deliver_self()
    assert deliver_activity.run.__func__(self, {"type": "Create"}, "https://example.com/inbox", "key", "pem") is None
    self.retry.assert_not_called()


def test_deliver_activity_blocked_domain(monkeypatch):
    """deliver_activity drops deliveries to blocked domains."""
    monkeypatch.setattr(
        "songhive.tasks.federation.load_config",
        lambda *a, **k: _fed_config(blocked_instances=["example.com"]),
    )
    self = _deliver_self()
    assert deliver_activity.run.__func__(self, {"type": "Create"}, "https://example.com/inbox", "key", "pem") is None
    self.retry.assert_not_called()


def test_deliver_activity_success(monkeypatch):
    """deliver_activity delegates the signed POST to pubby and returns the status."""
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *a, **k: _fed_config())
    monkeypatch.setattr("songhive.tasks.federation.load_private_key", lambda pem: "private_key")
    pubby_deliver = MagicMock(return_value=200)
    monkeypatch.setattr("songhive.tasks.federation.pubby_deliver_activity", pubby_deliver)

    self = _deliver_self()
    result = deliver_activity.run.__func__(
        self,
        {"type": "Create"},
        "https://example.com/inbox",
        "https://music.example.com/actor#main-key",
        "pem",
    )
    assert result == {"status_code": 200}
    pubby_deliver.assert_called_once_with(
        {"type": "Create"},
        "https://example.com/inbox",
        key_id="https://music.example.com/actor#main-key",
        private_key="private_key",
        timeout=15.0,
    )


def test_deliver_activity_request_exception_retries(monkeypatch):
    """deliver_activity retries on request exceptions."""
    from requests import RequestException

    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *a, **k: _fed_config())
    monkeypatch.setattr("songhive.tasks.federation.load_private_key", lambda pem: "private_key")
    monkeypatch.setattr(
        "songhive.tasks.federation.pubby_deliver_activity",
        MagicMock(side_effect=RequestException("network down")),
    )

    self = _deliver_self(retries=1)
    with pytest.raises(Retry):
        deliver_activity.run.__func__(self, {"type": "Create"}, "https://example.com/inbox", "key", "pem")

    self.retry.assert_called_once()


def test_deliver_activity_5xx_retries(monkeypatch):
    """deliver_activity retries on 5xx responses."""
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *a, **k: _fed_config())
    monkeypatch.setattr("songhive.tasks.federation.load_private_key", lambda pem: "private_key")
    monkeypatch.setattr("songhive.tasks.federation.pubby_deliver_activity", MagicMock(return_value=503))

    self = _deliver_self(retries=0)
    with pytest.raises(Retry):
        deliver_activity.run.__func__(self, {"type": "Create"}, "https://example.com/inbox", "key", "pem")

    self.retry.assert_called_once()


def test_deliver_activity_4xx_gives_up(monkeypatch):
    """deliver_activity gives up on non-retryable 4xx responses."""
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *a, **k: _fed_config())
    monkeypatch.setattr("songhive.tasks.federation.load_private_key", lambda pem: "private_key")
    monkeypatch.setattr("songhive.tasks.federation.pubby_deliver_activity", MagicMock(return_value=400))

    self = _deliver_self()
    assert deliver_activity.run.__func__(self, {"type": "Create"}, "https://example.com/inbox", "key", "pem") is None
    self.retry.assert_not_called()
