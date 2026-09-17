"""
Webmention tests - target resolution, materialization, notifications,
endpoint behavior, and outgoing delivery.
"""

import asyncio
import ipaddress
from typing import Optional

import pytest
import requests
from sqlalchemy import select
from webmentions import Webmention, WebmentionDirection, WebmentionType

from songhive.config.schema import SonghiveConfig
from songhive.models._enums import Visibility
from songhive.models.activity import Activity
from songhive.models.notification import Notification, NotificationType
from songhive.models.radio import Radio
from songhive.models.track import Track
from songhive.models.user import User
from songhive.webmentions.service import (
    WEBMENTION_PAYLOAD_KEY,
    materialize_webmention,
    resolve_webmention_target,
    retract_webmention,
    webmention_activity_source_id,
    webmention_display_excerpt,
    webmention_interaction_target,
)

DOMAIN = "music.example.com"


@pytest.fixture
def config(tmp_path):
    """Test config with an instance domain so Webmention routes mount."""
    return SonghiveConfig(
        server={
            "host": "127.0.0.1",
            "port": 8000,
            "debug": True,
            "cors_origins": ["http://localhost:8080"],
        },
        database={"url": f"sqlite+aiosqlite:///{tmp_path / 'songhive.db'}"},
        federation={"enabled": False, "instance_domain": DOMAIN},
        auth={"secret_key": "a" * 32},
        storage={
            "local_path": str(tmp_path / "media"),
            "backend": "local",
        },
    )


def _mention(
    source: str = "https://blog.example/posts/1",
    target: str = f"https://{DOMAIN}/tracks/t1",
    **kwargs,
) -> Webmention:
    """Build a parsed incoming Webmention as the library's parser would."""
    return Webmention(
        source=source,
        target=target,
        direction=WebmentionDirection.IN,
        **kwargs,
    )


async def _make_track(db_session, owner: User, visibility: str = Visibility.PUBLIC.value) -> Track:
    from songhive.models.artist import Artist

    artist = Artist(name="Test Artist")
    db_session.add(artist)
    await db_session.flush()
    track = Track(title="T", artist_id=artist.id, owner_id=owner.id, visibility=visibility)
    db_session.add(track)
    await db_session.flush()
    return track


async def _make_radio(db_session, owner: User) -> Radio:
    radio = Radio(name="Test Radio", owner_id=owner.id, visibility=Visibility.PUBLIC.value)
    db_session.add(radio)
    await db_session.flush()
    return radio


# ---------------------------------------------------------------------------
# Target resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_user_profile_targets(db_session, regular_user):
    for path in (f"/@{regular_user.username}", f"/users/{regular_user.username}"):
        target = await resolve_webmention_target(db_session, f"https://{DOMAIN}{path}", DOMAIN)
        assert target is not None
        assert target.entity_type == "user"
        assert target.entity_id == str(regular_user.id)


@pytest.mark.asyncio
async def test_resolve_entity_targets(db_session, regular_user):
    track = await _make_track(db_session, regular_user)
    radio = await _make_radio(db_session, regular_user)

    for path, expected in (
        (f"/tracks/{track.id}", "track"),
        (f"/radios/{radio.id}", "radio"),
    ):
        target = await resolve_webmention_target(db_session, f"https://{DOMAIN}{path}", DOMAIN)
        assert target is not None
        assert target.entity_type == expected
        assert target.entity_id == path.split("/")[-1]


@pytest.mark.asyncio
async def test_resolve_media_targets_to_track(db_session, regular_user):
    """Stream/download/file URLs resolve to the track they serve."""
    from songhive.models.stored_file import StoredFile

    stored = StoredFile(
        storage_path="a" * 64,
        storage_backend="local",
        content_type="audio/mpeg",
        size=1,
        sha256="b" * 64,
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(stored)
    await db_session.flush()
    track = await _make_track(db_session, regular_user)
    track.audio_file_id = str(stored.id)
    await db_session.flush()

    for path in (
        f"/api/v1/stream/{track.id}",
        f"/api/v1/tracks/{track.id}/download",
        f"/api/v1/files/{stored.id}",
        f"/api/v1/files/{stored.id}/download",
    ):
        target = await resolve_webmention_target(db_session, f"https://{DOMAIN}{path}", DOMAIN)
        assert target is not None, path
        assert target.entity_type == "track"
        assert target.entity_id == str(track.id)


@pytest.mark.asyncio
async def test_resolve_media_target_unknown_file(db_session, regular_user):
    assert (
        await resolve_webmention_target(db_session, f"https://{DOMAIN}/api/v1/files/missing/download", DOMAIN) is None
    )
    assert await resolve_webmention_target(db_session, f"https://{DOMAIN}/api/v1/stream/missing", DOMAIN) is None


@pytest.mark.asyncio
async def test_resolve_rejects_foreign_and_unknown_urls(db_session, regular_user):
    assert await resolve_webmention_target(db_session, "https://other.example/tracks/1", DOMAIN) is None
    assert await resolve_webmention_target(db_session, f"https://{DOMAIN}/tracks/missing", DOMAIN) is None
    assert await resolve_webmention_target(db_session, f"https://{DOMAIN}/@nobody", DOMAIN) is None


# ---------------------------------------------------------------------------
# Materialization / retraction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_materialize_track_mention_creates_activity_and_notification(db_session, config, regular_user):
    track = await _make_track(db_session, regular_user)
    mention = _mention(
        target=f"https://{DOMAIN}/tracks/{track.id}",
        title="A post",
        excerpt="Nice track!",
        author_name="Alice",
        author_url="https://blog.example/alice",
        mention_type=WebmentionType.MENTION,
        metadata={"mf2": {"category": ["rock"]}},
    )

    activity = await materialize_webmention(db_session, mention, config)
    assert activity is not None
    assert activity.entity_type == "track"
    assert activity.entity_id == str(track.id)
    assert activity.activity_type == "webmention"
    assert activity.source_type == "webmention"
    assert activity.content == "Nice track!"
    assert activity.payload[WEBMENTION_PAYLOAD_KEY]["source"] == mention.source

    notification = (
        await db_session.execute(select(Notification).where(Notification.user_id == str(regular_user.id)))
    ).scalar_one_or_none()
    assert notification is not None
    assert notification.type == NotificationType.WEBMENTION
    assert notification.source_url == mention.source


@pytest.mark.asyncio
async def test_materialize_user_mention_notifies_user(db_session, config, regular_user):
    mention = _mention(target=f"https://{DOMAIN}/@{regular_user.username}")

    activity = await materialize_webmention(db_session, mention, config)
    assert activity is not None
    assert activity.entity_type == "user"
    assert activity.entity_id == str(regular_user.id)

    notification = (
        await db_session.execute(select(Notification).where(Notification.user_id == str(regular_user.id)))
    ).scalar_one_or_none()
    assert notification is not None
    assert notification.type == NotificationType.WEBMENTION


@pytest.mark.asyncio
async def test_materialize_is_idempotent_and_updates(db_session, config, regular_user):
    track = await _make_track(db_session, regular_user)
    mention = _mention(target=f"https://{DOMAIN}/tracks/{track.id}", excerpt="v1")

    first = await materialize_webmention(db_session, mention, config)
    mention.excerpt = "v2"
    second = await materialize_webmention(db_session, mention, config)

    assert second is not None
    assert str(second.id) == str(first.id)
    assert second.content == "v2"
    # Updates do not re-notify.
    count = (
        (await db_session.execute(select(Notification).where(Notification.user_id == str(regular_user.id))))
        .scalars()
        .all()
    )
    assert len(count) == 1


@pytest.mark.asyncio
async def test_retract_webmention_soft_deletes(db_session, config, regular_user):
    track = await _make_track(db_session, regular_user)
    mention = _mention(target=f"https://{DOMAIN}/tracks/{track.id}")

    activity = await materialize_webmention(db_session, mention, config)
    retracted = await retract_webmention(db_session, mention)
    await db_session.refresh(activity)
    assert retracted is not None
    assert activity.deleted_at is not None


@pytest.mark.asyncio
async def test_materialize_skips_unknown_targets(db_session, config):
    mention = _mention(target=f"https://{DOMAIN}/tracks/missing")
    assert await materialize_webmention(db_session, mention, config) is None


def test_source_id_is_deterministic():
    a = webmention_activity_source_id("https://a.example/x", "https://b.example/y")
    b = webmention_activity_source_id("https://a.example/x", "https://b.example/y")
    c = webmention_activity_source_id("https://a.example/x", "https://b.example/z")
    assert a == b != c
    assert a.startswith("urn:songhive:webmention:")


# ---------------------------------------------------------------------------
# Display excerpt semantics
# ---------------------------------------------------------------------------


def test_display_excerpt_keeps_explicit_summary():
    # A ``p-summary`` is editorial text, not a prefix of the content.
    excerpt = webmention_display_excerpt(
        "A short summary",
        "<p>The full body of the post.</p>",
    )
    assert excerpt == "A short summary"


def test_display_excerpt_drops_truncated_derived_excerpt():
    # The parser derives the excerpt by truncating the entry markup — a
    # prefix of the content's text, possibly cut mid-tag by older
    # versions. A cut-off dump is not a real summary.
    content = "<p>Hello <a href='https://x.y'>world</a> and friends</p>"
    assert webmention_display_excerpt("<p>Hello <a href=", content) is None
    assert webmention_display_excerpt("Hello world…", content) is None


def test_display_excerpt_keeps_complete_short_content():
    # A derived excerpt that captures the whole (short) entry — e.g. a
    # one-line reply — is shown as the card's text.
    content = "<p>Hello <a href='https://x.y'>world</a></p>"
    assert webmention_display_excerpt("Hello world", content) == "Hello world"
    assert webmention_display_excerpt(content, content) == "Hello world"


def test_display_excerpt_keeps_text_only_content():
    # ``og:description``-style fallbacks are plain text — they double as
    # the page's own summary.
    assert webmention_display_excerpt(None, "Just a description") == "Just a description"


def test_display_excerpt_truncates_at_word_boundary():
    long_text = "lorem ipsum dolor sit amet " * 20
    excerpt = webmention_display_excerpt(None, long_text)
    assert excerpt is not None
    assert excerpt.endswith("…")
    assert len(excerpt) <= 241


@pytest.mark.asyncio
async def test_materialize_without_summary_stores_no_content(db_session, config, regular_user):
    track = await _make_track(db_session, regular_user)
    mention = _mention(
        target=f"https://{DOMAIN}/tracks/{track.id}",
        title="A post",
        excerpt="<p>Derived excerpt <a href=",
        content="<p>Derived excerpt <a href='https://x.y'>link</a></p>",
    )

    activity = await materialize_webmention(db_session, mention, config)
    assert activity is not None
    assert activity.content is None
    assert activity.content_type == "text/plain"


# ---------------------------------------------------------------------------
# Incoming HTTP endpoint
# ---------------------------------------------------------------------------


def test_receive_webmention_queues_task(client, monkeypatch):
    from songhive.tasks import webmentions as wm_tasks

    queued = []
    monkeypatch.setattr(wm_tasks.process_incoming_webmention, "delay", lambda *a: queued.append(a))

    response = client.post(
        "/webmentions",
        data={"source": "https://blog.example/post", "target": f"https://{DOMAIN}/@alice"},
    )
    assert response.status_code == 202
    assert queued == [("https://blog.example/post", f"https://{DOMAIN}/@alice")]


def test_receive_webmention_validates(client):
    assert client.post("/webmentions", data={}).status_code == 400
    assert (
        client.post(
            "/webmentions",
            data={"source": "https://blog.example/post", "target": "https://other.example/x"},
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/webmentions",
            data={"source": "notaurl", "target": f"https://{DOMAIN}/@alice"},
        ).status_code
        == 400
    )


def test_media_responses_advertise_webmention_endpoint(client):
    """Non-HTML media responses carry the Link header (discovery on audio)."""
    response = client.get("/api/v1/files/missing/download")
    assert 'rel="webmention"' in response.headers.get("link", "")


# ---------------------------------------------------------------------------
# Outgoing source page
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outgoing_source_page_renders_h_entry(client, db_session, config, regular_user, monkeypatch):
    from songhive.services.activities import create_status

    monkeypatch.setattr("songhive.webmentions.service.enqueue_outgoing_webmentions", lambda *a, **k: False)
    activity = await create_status(
        db_session,
        author=regular_user,
        config=config,
        status_text="Check https://blog.example/post",
    )
    await db_session.commit()

    response = client.get(f"/webmentions/source/{activity.id}")
    assert response.status_code == 200
    assert 'rel="webmention"' in response.headers.get("link", "")
    body = response.text
    assert "h-entry" in body
    assert f"https://{DOMAIN}/activities/{activity.id}" in body
    assert "blog.example/post" in body


@pytest.mark.asyncio
async def test_outgoing_source_page_marks_interactions(client, db_session, config, regular_user):
    """A like marker on a local activity renders a ``u-like-of`` link."""
    from songhive.webmentions.service import set_webmention_marker

    activity = Activity(
        entity_type="user",
        entity_id=str(regular_user.id),
        activity_type="like",
        source_type="local",
        source_actor=f"urn:songhive:user:{regular_user.username}",
        source_id="https://example.com/like-1",
        owner_user_id=str(regular_user.id),
        visibility="public",
    )
    set_webmention_marker(activity, "like-of", "https://blog.example/liked-post")
    db_session.add(activity)
    await db_session.commit()

    response = client.get(f"/webmentions/source/{activity.id}")
    assert response.status_code == 200
    assert 'class="u-like-of"' in response.text
    assert "https://blog.example/liked-post" in response.text


@pytest.mark.asyncio
async def test_outgoing_source_page_gone_for_deleted(client, db_session, config, regular_user, monkeypatch):
    from songhive.services.activities import create_status, retract_activity

    monkeypatch.setattr("songhive.webmentions.service.enqueue_outgoing_webmentions", lambda *a, **k: False)
    activity = await create_status(
        db_session,
        author=regular_user,
        config=config,
        status_text="hi",
    )
    await db_session.commit()
    await retract_activity(db_session, activity)
    await db_session.commit()

    response = client.get(f"/webmentions/source/{activity.id}")
    assert response.status_code == 410


# ---------------------------------------------------------------------------
# Incoming processing task
# ---------------------------------------------------------------------------


def _source_html(target: str) -> str:
    return f"""<!DOCTYPE html><html><body>
<article class="h-entry">
  <a class="u-url" href="https://blog.example/post/1">post</a>
  <a href="{target}">mentioning this</a>
  <span class="p-author h-card">
    <a class="u-url" href="https://blog.example/alice"><span class="p-name">Alice</span></a>
  </span>
  <time class="dt-published" datetime="2026-01-01T12:00:00+00:00"></time>
  <p class="p-summary">Nice track!</p>
  <div class="e-content">Full body of the post.</div>
  <a class="p-category" href="https://blog.example/tags/rock">rock</a>
</article>
</body></html>"""


class _FakeResponse:
    def __init__(self, text: str = "", status_code: int = 200, headers: Optional[dict] = None):
        self.text = text
        self.status_code = status_code
        self.headers = headers or {"Content-Type": "text/html"}
        self.url = "https://blog.example/post/1"
        self.encoding = "utf-8"
        has_location = any(k.lower() == "location" for k in self.headers)
        self.is_redirect = has_location and status_code in (301, 302, 303, 307, 308)
        self.is_permanent_redirect = has_location and status_code in (301, 308)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)

    def iter_content(self, chunk_size=65536, decode_unicode=False):
        yield self.text.encode("utf-8")

    def close(self):
        pass


def _stub_dns(monkeypatch, mapping: Optional[dict] = None):
    """Stub guarded-fetch DNS so test hosts resolve as public addresses.

    IP-literal hosts resolve to themselves (as real DNS would), so a URL
    like ``http://169.254.169.254/x`` exercises the private-address guard.
    Other hosts map through ``mapping`` or default to a public IP.
    """
    mapping = mapping or {}

    def fake_resolve(host):
        try:
            return {ipaddress.ip_address(host)}
        except ValueError:
            return {ipaddress.ip_address(mapping.get(host, "93.184.216.34"))}

    monkeypatch.setattr("webmentions.handlers._fetch._resolve_ips", fake_resolve)


@pytest.mark.asyncio
async def test_process_incoming_materializes_and_notifies(db_session, config, regular_user, monkeypatch):
    """End-to-end: source fetch -> parse -> stored mention -> activity + notification."""
    from songhive.tasks.webmentions import process_incoming_webmention
    from songhive.webmentions.storage import create_webmentions_storage

    track = await _make_track(db_session, regular_user)
    track_id = str(track.id)
    user_id = str(regular_user.id)
    await db_session.commit()
    target = f"https://{DOMAIN}/tracks/{track_id}"
    source = "https://blog.example/post/1"

    monkeypatch.setattr("songhive.tasks.webmentions.load_config", lambda *a: config)
    _stub_dns(monkeypatch)
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(_source_html(target)))

    await asyncio.to_thread(process_incoming_webmention.apply, (), {"source": source, "target": target})

    db_session.expire_all()
    activity = (
        await db_session.execute(
            select(Activity).where(Activity.source_id == webmention_activity_source_id(source, target))
        )
    ).scalar_one_or_none()
    assert activity is not None
    assert activity.activity_type == "webmention"
    assert activity.entity_id == track_id
    assert activity.payload[WEBMENTION_PAYLOAD_KEY]["excerpt"] == "Nice track!"

    notification = (
        await db_session.execute(
            select(Notification).where(
                Notification.user_id == user_id,
                Notification.type == NotificationType.WEBMENTION,
            )
        )
    ).scalar_one_or_none()
    assert notification is not None

    storage = create_webmentions_storage(config.database.url)
    stored = storage.retrieve_webmentions(target, direction=WebmentionDirection.IN)
    assert [m.source for m in stored] == [source]


@pytest.mark.asyncio
async def test_process_incoming_retracts_on_gone_source(db_session, config, regular_user, monkeypatch):
    """A re-sent mention whose source vanished deletes storage + activity."""
    from songhive.tasks.webmentions import process_incoming_webmention

    track = await _make_track(db_session, regular_user)
    track_id = str(track.id)
    await db_session.commit()
    target = f"https://{DOMAIN}/tracks/{track_id}"
    source = "https://blog.example/post/1"

    monkeypatch.setattr("songhive.tasks.webmentions.load_config", lambda *a: config)
    _stub_dns(monkeypatch)
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(_source_html(target)))
    await asyncio.to_thread(process_incoming_webmention.apply, (), {"source": source, "target": target})

    # The source now 410s: the stored mention and materialized activity go away.
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse("", status_code=410))
    await asyncio.to_thread(process_incoming_webmention.apply, (), {"source": source, "target": target})

    db_session.expire_all()
    activity = (
        await db_session.execute(
            select(Activity).where(Activity.source_id == webmention_activity_source_id(source, target))
        )
    ).scalar_one_or_none()
    assert activity is not None
    assert activity.deleted_at is not None


# ---------------------------------------------------------------------------
# Outgoing processing task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_outgoing_discovers_and_delivers(db_session, config, regular_user, monkeypatch):
    """A local status with a URL delivers a Webmention to its endpoint."""
    from songhive.services.activities import create_status
    from songhive.tasks.webmentions import process_outgoing_webmentions
    from songhive.webmentions.storage import create_webmentions_storage

    target_url = "https://blog.example/post/1"
    endpoint = "https://blog.example/webmention"

    activity = await create_status(
        db_session,
        author=regular_user,
        config=config,
        status_text=f"Nice post {target_url}",
    )
    await db_session.commit()

    discovered_page = _FakeResponse(
        f'<html><head><link rel="webmention" href="{endpoint}" /></head><body></body></html>'
    )
    posts = []

    def fake_get(url, **kwargs):
        return discovered_page

    def fake_post(url, data=None, **kwargs):
        posts.append((url, data))
        return _FakeResponse(status_code=202)

    monkeypatch.setattr("songhive.tasks.webmentions.load_config", lambda *a: config)
    _stub_dns(monkeypatch)
    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)

    await asyncio.to_thread(process_outgoing_webmentions.apply, (str(activity.id),))

    assert posts == [(endpoint, {"source": f"https://{DOMAIN}/webmentions/source/{activity.id}", "target": target_url})]

    storage = create_webmentions_storage(config.database.url)
    stored = storage.retrieve_webmentions(
        f"https://{DOMAIN}/webmentions/source/{activity.id}", direction=WebmentionDirection.OUT
    )
    assert [m.target for m in stored] == [target_url]

    await db_session.refresh(activity)
    assert activity.payload.get("webmentions_sent") is True


@pytest.mark.asyncio
async def test_process_outgoing_skips_local_targets(db_session, config, regular_user, monkeypatch):
    """Targets on the instance domain are dropped before discovery/delivery."""
    from songhive.services.activities import create_status
    from songhive.tasks.webmentions import process_outgoing_webmentions

    remote_target = "https://blog.example/post/1"
    endpoint = "https://blog.example/webmention"

    activity = await create_status(
        db_session,
        author=regular_user,
        config=config,
        status_text=f"See {remote_target} and https://{DOMAIN}/tracks/t1",
    )
    await db_session.commit()

    monkeypatch.setattr("songhive.tasks.webmentions.load_config", lambda *a: config)
    _stub_dns(monkeypatch)
    posts = []
    fetches = []
    monkeypatch.setattr(
        requests,
        "get",
        lambda url, **kw: fetches.append(url)
        or _FakeResponse(f'<html><head><link rel="webmention" href="{endpoint}" /></head></html>'),
    )
    monkeypatch.setattr(
        requests,
        "post",
        lambda url, data=None, **kw: posts.append((url, data)) or _FakeResponse(status_code=202),
    )

    await asyncio.to_thread(process_outgoing_webmentions.apply, (str(activity.id),))

    assert posts == [
        (
            endpoint,
            {
                "source": f"https://{DOMAIN}/webmentions/source/{activity.id}",
                "target": remote_target,
            },
        )
    ]
    assert fetches == [remote_target]


# ---------------------------------------------------------------------------
# Interaction markers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_like_webmention_activity_marks_like_of(db_session, config, regular_user, other_user, monkeypatch):
    from songhive.services.activities import like_activity

    enqueued = []
    monkeypatch.setattr(
        "songhive.webmentions.service.enqueue_outgoing_webmentions",
        lambda *a, **k: enqueued.append(a),
    )

    track = await _make_track(db_session, regular_user)
    mention = _mention(
        target=f"https://{DOMAIN}/tracks/{track.id}",
        author_url="https://blog.example/alice",
    )
    activity = await materialize_webmention(db_session, mention, config)
    assert webmention_interaction_target(activity) == mention.source

    like = await like_activity(db_session, activity=activity, author=other_user)
    assert like.payload[WEBMENTION_PAYLOAD_KEY] == {"property": "like-of", "target": mention.source}
    assert any(str(args[0].id) == str(like.id) for args in enqueued)


@pytest.mark.asyncio
async def test_quote_webmention_activity_marks_quotation_of(db_session, config, regular_user, other_user, monkeypatch):
    from songhive.services.activities import quote_activity

    monkeypatch.setattr(
        "songhive.webmentions.service.enqueue_outgoing_webmentions",
        lambda *a, **k: True,
    )

    track = await _make_track(db_session, regular_user)
    mention = _mention(
        target=f"https://{DOMAIN}/tracks/{track.id}",
        author_url="https://blog.example/alice",
    )
    activity = await materialize_webmention(db_session, mention, config)

    quote = await quote_activity(db_session, activity=activity, author=other_user, config=config, status_text="my take")
    assert quote.payload[WEBMENTION_PAYLOAD_KEY] == {"property": "quotation-of", "target": mention.source}


# ---------------------------------------------------------------------------
# SSRF guard: private addresses, redirect hops, body cap, transient retries
# ---------------------------------------------------------------------------


async def _no_webmention_activities(db_session) -> list:
    rows = (await db_session.execute(select(Activity))).scalars().all()
    return [a for a in rows if a.activity_type == "webmention"]


@pytest.mark.asyncio
async def test_process_incoming_blocks_private_ip_source(db_session, config, regular_user, monkeypatch):
    """A source on a private/link-local/loopback address is rejected before any fetch."""
    from songhive.tasks.webmentions import process_incoming_webmention

    track = await _make_track(db_session, regular_user)
    await db_session.commit()
    target = f"https://{DOMAIN}/tracks/{track.id}"

    monkeypatch.setattr("songhive.tasks.webmentions.load_config", lambda *a: config)
    _stub_dns(monkeypatch)
    fetches = []
    monkeypatch.setattr(requests, "get", lambda *a, **k: fetches.append(a))

    for source in (
        "http://169.254.169.254/latest/meta-data",
        "http://127.0.0.1:6379/",
        "http://10.0.0.5/internal",
    ):
        await asyncio.to_thread(process_incoming_webmention.apply, (), {"source": source, "target": target})

    assert fetches == []
    db_session.expire_all()
    assert await _no_webmention_activities(db_session) == []


@pytest.mark.asyncio
async def test_process_incoming_blocks_redirect_to_private(db_session, config, regular_user, monkeypatch):
    """A public-looking source that 302s into private space is rejected at the hop."""
    from songhive.tasks.webmentions import process_incoming_webmention

    track = await _make_track(db_session, regular_user)
    await db_session.commit()
    target = f"https://{DOMAIN}/tracks/{track.id}"
    source = "https://blog.example/post/1"

    monkeypatch.setattr("songhive.tasks.webmentions.load_config", lambda *a: config)
    _stub_dns(monkeypatch)
    fetches = []

    def fake_get(url, **kwargs):
        fetches.append(url)
        return _FakeResponse("", 302, headers={"Location": "http://169.254.169.254/x"})

    monkeypatch.setattr(requests, "get", fake_get)

    await asyncio.to_thread(process_incoming_webmention.apply, (), {"source": source, "target": target})

    # The first hop is fetched; the redirect target is blocked before connect.
    assert fetches == [source]
    db_session.expire_all()
    assert await _no_webmention_activities(db_session) == []


@pytest.mark.asyncio
async def test_process_incoming_follows_validated_redirect(db_session, config, regular_user, monkeypatch):
    """A redirect to another public host is followed and the mention materializes."""
    from songhive.tasks.webmentions import process_incoming_webmention

    track = await _make_track(db_session, regular_user)
    await db_session.commit()
    target = f"https://{DOMAIN}/tracks/{track.id}"
    source = "https://blog.example/post/1"

    monkeypatch.setattr("songhive.tasks.webmentions.load_config", lambda *a: config)
    _stub_dns(monkeypatch)
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        if "blog.example" in url:
            return _FakeResponse("", 302, headers={"Location": "https://cdn.example/post"})
        return _FakeResponse(_source_html(target))

    monkeypatch.setattr(requests, "get", fake_get)

    await asyncio.to_thread(process_incoming_webmention.apply, (), {"source": source, "target": target})

    assert calls == [source, "https://cdn.example/post"]
    db_session.expire_all()
    activity = (
        await db_session.execute(
            select(Activity).where(Activity.source_id == webmention_activity_source_id(source, target))
        )
    ).scalar_one_or_none()
    assert activity is not None


@pytest.mark.asyncio
async def test_process_incoming_caps_source_body(db_session, config, regular_user, monkeypatch):
    """A source body over ``discovery_max_bytes`` is rejected, not buffered."""
    from songhive.tasks.webmentions import process_incoming_webmention

    track = await _make_track(db_session, regular_user)
    await db_session.commit()
    target = f"https://{DOMAIN}/tracks/{track.id}"
    source = "https://blog.example/post/1"

    config.webmentions.discovery_max_bytes = 64
    monkeypatch.setattr("songhive.tasks.webmentions.load_config", lambda *a: config)
    _stub_dns(monkeypatch)
    monkeypatch.setattr(
        requests,
        "get",
        lambda *a, **k: _FakeResponse("x" * 4096 + f'<a href="{target}">m</a>'),
    )

    await asyncio.to_thread(process_incoming_webmention.apply, (), {"source": source, "target": target})

    db_session.expire_all()
    assert await _no_webmention_activities(db_session) == []


@pytest.mark.asyncio
async def test_process_incoming_retries_transient_fetch_errors(db_session, config, regular_user, monkeypatch):
    """A fetch failure propagates out of the task so ``autoretry_for`` can retry.

    Before the fix the catch-all swallowed ``requests.RequestException`` —
    the sender already held a 202, so the mention was permanently lost.
    """
    from celery.exceptions import Retry

    from songhive.tasks.webmentions import process_incoming_webmention

    track = await _make_track(db_session, regular_user)
    await db_session.commit()
    target = f"https://{DOMAIN}/tracks/{track.id}"
    source = "https://blog.example/post/1"

    monkeypatch.setattr("songhive.tasks.webmentions.load_config", lambda *a: config)
    _stub_dns(monkeypatch)

    def boom(*a, **k):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(requests, "get", boom)

    result = await asyncio.to_thread(process_incoming_webmention.apply, (), {"source": source, "target": target})

    assert result.failed()
    assert isinstance(result.result, (Retry, requests.ConnectionError))


@pytest.mark.asyncio
async def test_process_outgoing_skips_private_discovery_target(db_session, config, regular_user, monkeypatch):
    """A status linking to a private address never reaches discovery or delivery."""
    from songhive.services.activities import create_status
    from songhive.tasks.webmentions import process_outgoing_webmentions

    activity = await create_status(
        db_session,
        author=regular_user,
        config=config,
        status_text="check http://169.254.169.254/latest/meta-data",
    )
    await db_session.commit()

    monkeypatch.setattr("songhive.tasks.webmentions.load_config", lambda *a: config)
    _stub_dns(monkeypatch)
    posts = []
    fetches = []
    monkeypatch.setattr(requests, "get", lambda *a, **k: fetches.append(a))
    monkeypatch.setattr(requests, "post", lambda *a, **k: posts.append(a))

    await asyncio.to_thread(process_outgoing_webmentions.apply, (str(activity.id),))

    assert posts == []
    assert fetches == []
