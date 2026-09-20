"""
Tests for GPodder-compatible podcast subscription sync.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from songhive.models.podcast import (
    PodcastSyncConfig,
    PodcastSyncEvent,
)
from songhive.services import gpodder as gpodder_service
from songhive.services import podcasts as podcasts_service
from songhive.services.gpodder import (
    GPodderClient,
    GPodderError,
    NextcloudGPodderClient,
    SubscriptionChanges,
)
from songhive.services.podcasts import FeedFetchError, FetchResult

RSS_FEED = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>Test Show</title>
    <item>
      <title>Episode One</title>
      <guid>ep-1</guid>
      <enclosure url="https://cdn.pod.example/ep1.mp3" type="audio/mpeg" length="12345"/>
    </item>
  </channel>
</rss>
"""

FEED_URL = "https://pod.example/feed.xml"
OTHER_FEED_URL = "https://other.example/feed.xml"
SERVER_URL = "https://gpodder.example.com"


def _stub_fetch(monkeypatch, result=RSS_FEED):
    """Patch ``podcasts.fetch_feed`` to return a canned feed."""

    def _fetch(*_args, **_kwargs):
        return FetchResult(body=result, final_url=FEED_URL)

    monkeypatch.setattr(podcasts_service, "fetch_feed", _fetch)


class FakeGPodderClient:
    """In-memory stand-in for ``GPodderClient`` used by the sync engine."""

    def __init__(
        self,
        changes: SubscriptionChanges | None = None,
        upload_result: SubscriptionChanges | None = None,
        error: Exception | None = None,
    ):
        self.changes = changes or SubscriptionChanges(timestamp=100)
        self.upload_result = upload_result or SubscriptionChanges(timestamp=200)
        self.error = error
        self.uploaded: list[tuple[list[str], list[str]]] = []
        self.since_calls: list[float] = []
        self.closed = False

    def login(self):
        pass

    def update_device(self, device_id, *, caption, device_type):
        pass

    def get_subscription_changes(self, device_id, since):
        self.since_calls.append(since)
        if self.error:
            raise self.error
        return self.changes

    def upload_subscription_changes(self, device_id, add, remove):
        self.uploaded.append((list(add), list(remove)))
        return self.upload_result

    def close(self):
        self.closed = True


def _stub_client(monkeypatch, client: FakeGPodderClient) -> FakeGPodderClient:
    monkeypatch.setattr(gpodder_service, "make_client", lambda _row, _config: client)
    return client


async def _make_sync_config(db_session, user, mode="pull", enabled=True, **kwargs) -> PodcastSyncConfig:
    row = PodcastSyncConfig(
        user_id=user.id,
        server_url=SERVER_URL,
        username="gpodder-user",
        password="secret",
        device_id="songhive",
        mode=mode,
        enabled=enabled,
        **kwargs,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _subscribed_urls(db_session, user) -> set[str]:
    return set(await gpodder_service.subscribed_feed_urls(db_session, user))


async def _events(db_session, user) -> list[PodcastSyncEvent]:
    rows = await db_session.scalars(
        select(PodcastSyncEvent).where(PodcastSyncEvent.user_id == user.id).order_by(PodcastSyncEvent.created_at)
    )
    return list(rows)


def test_normalize_server_url():
    assert gpodder_service.normalize_server_url(" https://gpodder.net/ ") == "https://gpodder.net"
    for bad in ("ftp://x", "not-a-url", "https://gpodder.net/sub/path"):
        with pytest.raises(GPodderError):
            gpodder_service.normalize_server_url(bad)
    # Nextcloud servers legitimately live under a path.
    assert (
        gpodder_service.normalize_server_url("https://cloud.example.com/nextcloud", "nextcloud")
        == "https://cloud.example.com/nextcloud"
    )


def test_api_base_url_nextcloud():
    base = gpodder_service.api_base_url
    # Root URL → the app's index.php routes.
    assert base("https://cloud.example.com", "nextcloud") == ("https://cloud.example.com/index.php/apps/gpoddersync")
    # Subdirectory install keeps its prefix.
    assert base("https://cloud.example.com/nextcloud", "nextcloud") == (
        "https://cloud.example.com/nextcloud/index.php/apps/gpoddersync"
    )
    # App URL (or pasted endpoint URL) is trimmed to the app root.
    assert base("https://cloud.example.com/apps/gpoddersync", "nextcloud") == (
        "https://cloud.example.com/apps/gpoddersync"
    )
    assert base("https://cloud.example.com/index.php/apps/gpoddersync/subscriptions", "nextcloud") == (
        "https://cloud.example.com/index.php/apps/gpoddersync"
    )


async def test_subscribe_unsubscribe_record_sync_events(db_session, regular_user, config, monkeypatch):
    _stub_fetch(monkeypatch)
    podcast, created = await podcasts_service.subscribe(db_session, regular_user, FEED_URL, config)
    assert created
    await podcasts_service.unsubscribe(db_session, regular_user, str(podcast.id))
    events = await _events(db_session, regular_user)
    assert [(e.action, e.origin) for e in events] == [("add", "local"), ("remove", "local")]


async def test_pull_sync_applies_remote_adds(db_session, regular_user, config, monkeypatch):
    _stub_fetch(monkeypatch)
    row = await _make_sync_config(
        db_session,
        regular_user,
        mode="pull",
        last_sync_timestamp=50,
        last_synced_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    client = _stub_client(
        monkeypatch,
        FakeGPodderClient(changes=SubscriptionChanges(add=[FEED_URL], timestamp=60)),
    )
    result = await gpodder_service.run_sync(db_session, regular_user, row, config)
    assert result.subscribed == 1
    assert FEED_URL in await _subscribed_urls(db_session, regular_user)
    assert client.since_calls == [50]
    # Pull mode never uploads.
    assert client.uploaded == []
    assert row.last_sync_timestamp == 60
    assert row.last_error is None
    # The change was applied by sync — recorded as remote, not local.
    events = await _events(db_session, regular_user)
    assert [(e.action, e.origin) for e in events] == [("add", "remote")]


async def test_pull_sync_applies_remote_removes(db_session, regular_user, config, monkeypatch):
    _stub_fetch(monkeypatch)
    podcast, _ = await podcasts_service.subscribe(db_session, regular_user, FEED_URL, config)
    row = await _make_sync_config(
        db_session,
        regular_user,
        mode="pull",
        last_sync_timestamp=50,
        last_synced_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    _stub_client(
        monkeypatch,
        FakeGPodderClient(changes=SubscriptionChanges(remove=[FEED_URL], timestamp=60)),
    )
    result = await gpodder_service.run_sync(db_session, regular_user, row, config)
    assert result.unsubscribed == 1
    assert FEED_URL not in await _subscribed_urls(db_session, regular_user)
    events = await _events(db_session, regular_user)
    assert events[-1].action == "remove" and events[-1].origin == "remote"


async def test_bidirectional_uploads_local_changes(db_session, regular_user, config, monkeypatch):
    _stub_fetch(monkeypatch)
    row = await _make_sync_config(
        db_session,
        regular_user,
        mode="bidirectional",
        last_sync_timestamp=50,
        last_synced_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    # Subscribe *after* the watermark so the event is pending.
    await podcasts_service.subscribe(db_session, regular_user, FEED_URL, config)
    client = _stub_client(
        monkeypatch,
        FakeGPodderClient(changes=SubscriptionChanges(timestamp=60)),
    )
    result = await gpodder_service.run_sync(db_session, regular_user, row, config)
    assert client.uploaded == [([FEED_URL], [])]
    assert result.pushed_adds == 1
    assert row.last_sync_timestamp == 200  # upload timestamp wins


async def test_bidirectional_conflict_pending_local_change_wins(db_session, regular_user, config, monkeypatch):
    """A pending local change outranks a remote change in the same window."""
    _stub_fetch(monkeypatch)
    row = await _make_sync_config(
        db_session,
        regular_user,
        mode="bidirectional",
        last_sync_timestamp=50,
        last_synced_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    podcast, _ = await podcasts_service.subscribe(db_session, regular_user, FEED_URL, config)
    await podcasts_service.unsubscribe(db_session, regular_user, str(podcast.id))
    # Remote wants to re-add the feed, but the local remove is pending.
    client = _stub_client(
        monkeypatch,
        FakeGPodderClient(changes=SubscriptionChanges(add=[FEED_URL], timestamp=60)),
    )
    result = await gpodder_service.run_sync(db_session, regular_user, row, config)
    assert result.subscribed == 0
    assert FEED_URL not in await _subscribed_urls(db_session, regular_user)
    assert client.uploaded == [([], [FEED_URL])]


async def test_bidirectional_remote_add_beats_nothing_pending(db_session, regular_user, config, monkeypatch):
    """Without a pending local change, remote changes apply normally."""
    _stub_fetch(monkeypatch)
    row = await _make_sync_config(
        db_session,
        regular_user,
        mode="bidirectional",
        last_sync_timestamp=50,
        last_synced_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    _stub_client(
        monkeypatch,
        FakeGPodderClient(changes=SubscriptionChanges(add=[FEED_URL], timestamp=60)),
    )
    result = await gpodder_service.run_sync(db_session, regular_user, row, config)
    assert result.subscribed == 1
    assert FEED_URL in await _subscribed_urls(db_session, regular_user)


async def test_first_sync_unions_local_and_remote(db_session, regular_user, config, monkeypatch):
    _stub_fetch(monkeypatch)
    # Local subscription predates the link; remote has another feed.
    await podcasts_service.subscribe(db_session, regular_user, FEED_URL, config)
    row = await _make_sync_config(db_session, regular_user, mode="bidirectional")
    client = _stub_client(
        monkeypatch,
        FakeGPodderClient(changes=SubscriptionChanges(add=[OTHER_FEED_URL], timestamp=60)),
    )

    def _fetch(url, **_kwargs):
        return FetchResult(body=RSS_FEED, final_url=url)

    monkeypatch.setattr(podcasts_service, "fetch_feed", _fetch)
    result = await gpodder_service.run_sync(db_session, regular_user, row, config)
    urls = await _subscribed_urls(db_session, regular_user)
    assert {FEED_URL, OTHER_FEED_URL} <= urls
    # First sync pushes local-only feeds as adds, never removes.
    assert client.uploaded == [([FEED_URL], [])]
    assert result.pushed_adds == 1
    assert row.last_sync_timestamp == 200


async def test_first_sync_pull_does_not_remove_local(db_session, regular_user, config, monkeypatch):
    _stub_fetch(monkeypatch)
    await podcasts_service.subscribe(db_session, regular_user, FEED_URL, config)
    row = await _make_sync_config(db_session, regular_user, mode="pull")
    _stub_client(
        monkeypatch,
        FakeGPodderClient(changes=SubscriptionChanges(remove=[FEED_URL], timestamp=60)),
    )
    result = await gpodder_service.run_sync(db_session, regular_user, row, config)
    # Union baseline: remote removes are ignored on the very first sync.
    assert result.unsubscribed == 0
    assert FEED_URL in await _subscribed_urls(db_session, regular_user)


async def test_sync_remote_add_feed_failure_is_collected(db_session, regular_user, config, monkeypatch):
    row = await _make_sync_config(
        db_session,
        regular_user,
        mode="pull",
        last_sync_timestamp=50,
        last_synced_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )

    def _fail(*_a, **_kw):
        raise FeedFetchError("boom")

    monkeypatch.setattr(podcasts_service, "fetch_feed", _fail)
    _stub_client(
        monkeypatch,
        FakeGPodderClient(changes=SubscriptionChanges(add=[FEED_URL], timestamp=60)),
    )
    result = await gpodder_service.run_sync(db_session, regular_user, row, config)
    assert result.subscribed == 0
    assert len(result.errors) == 1
    # The sync itself still succeeds and advances the watermark.
    assert row.last_sync_timestamp == 60


async def test_sync_failure_records_error(db_session, regular_user, config, monkeypatch):
    last_synced = datetime.now(timezone.utc) - timedelta(hours=2)
    row = await _make_sync_config(
        db_session,
        regular_user,
        mode="pull",
        last_sync_timestamp=50,
        last_synced_at=last_synced,
    )
    _stub_client(monkeypatch, FakeGPodderClient(error=GPodderError("server down")))
    with pytest.raises(GPodderError):
        await gpodder_service.run_sync(db_session, regular_user, row, config)
    assert row.last_error == "server down"
    # Failure must not advance the success watermark — pending events stay pending.
    assert row.last_synced_at == last_synced
    assert row.last_attempt_at is not None


async def test_sync_update_urls_renames_feed(db_session, regular_user, config, monkeypatch):
    _stub_fetch(monkeypatch)
    podcast, _ = await podcasts_service.subscribe(db_session, regular_user, FEED_URL, config)
    row = await _make_sync_config(
        db_session,
        regular_user,
        mode="pull",
        last_sync_timestamp=50,
        last_synced_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    new_url = "https://pod.example/new-feed.xml"
    _stub_client(
        monkeypatch,
        FakeGPodderClient(changes=SubscriptionChanges(timestamp=60, update_urls=[(FEED_URL, new_url)])),
    )
    await gpodder_service.run_sync(db_session, regular_user, row, config)
    await db_session.refresh(podcast)
    assert podcast.feed_url == new_url
    # Pending events follow the rename so future pushes use the new URL.
    events = await _events(db_session, regular_user)
    assert all(e.feed_url == new_url for e in events)


async def test_due_sync_user_ids(db_session, regular_user, other_user, make_user):
    interval = timedelta(minutes=30)
    # Never attempted → due.
    await _make_sync_config(db_session, regular_user)
    # Disabled → not due.
    await _make_sync_config(db_session, other_user, enabled=False)
    third = await make_user("third")
    # Recently attempted → not due.
    await _make_sync_config(db_session, third, last_attempt_at=datetime.now(timezone.utc))
    due = await gpodder_service.due_sync_user_ids(db_session, interval)
    assert str(regular_user.id) in due
    assert str(other_user.id) not in due
    assert str(third.id) not in due


async def test_upsert_resets_watermark_on_account_change(db_session, regular_user):
    row, created = await gpodder_service.upsert_sync_config(
        db_session,
        regular_user,
        server_type="gpodder",
        server_url=SERVER_URL,
        username="u",
        password="p",
        device_id="songhive",
        mode="pull",
        enabled=True,
    )
    assert created
    row.last_sync_timestamp = 42
    row.last_synced_at = datetime.now(timezone.utc)
    await db_session.flush()

    # Same account, mode change only → watermark kept.
    row, _ = await gpodder_service.upsert_sync_config(
        db_session,
        regular_user,
        server_type="gpodder",
        server_url=SERVER_URL,
        username="u",
        password=None,
        device_id="songhive",
        mode="bidirectional",
        enabled=True,
    )
    assert row.last_sync_timestamp == 42

    # Different server type → watermark reset.
    row, _ = await gpodder_service.upsert_sync_config(
        db_session,
        regular_user,
        server_type="nextcloud",
        server_url=SERVER_URL,
        username="u",
        password=None,
        device_id="songhive",
        mode="bidirectional",
        enabled=True,
    )
    assert row.last_sync_timestamp is None
    row.last_sync_timestamp = 42
    await db_session.flush()

    # Different username → watermark reset.
    row, _ = await gpodder_service.upsert_sync_config(
        db_session,
        regular_user,
        server_type="nextcloud",
        server_url=SERVER_URL,
        username="other",
        password=None,
        device_id="songhive",
        mode="bidirectional",
        enabled=True,
    )
    assert row.last_sync_timestamp is None
    assert row.last_synced_at is None
    # Omitted password was kept.
    assert row.password == "p"


# --- HTTP client tests -------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.content = json.dumps(payload).encode() if payload is not None else b""
        self.is_redirect = status_code in (301, 302, 303, 307, 308)
        self.is_permanent_redirect = status_code in (301, 308)

    def json(self):
        return self._payload

    def close(self):
        pass


class _FakeSession:
    """Queue-based stand-in for ``requests.Session``."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []
        self.auth = None

    def request(self, method, url, **_kwargs):
        self.calls.append((method, url))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        pass


def _client_with(monkeypatch, responses):
    session = _FakeSession(responses)
    monkeypatch.setattr(gpodder_service.requests, "Session", lambda: session)
    client = GPodderClient(SERVER_URL, "user", "pw", timeout=5)
    return client, session


def test_client_get_subscription_changes(monkeypatch):
    client, session = _client_with(
        monkeypatch,
        [
            _FakeResponse(
                200,
                {
                    "add": ["https://a.example/feed"],
                    "remove": ["https://b.example/feed"],
                    "timestamp": 1234.5,
                    "update_urls": [["https://old.example/f", "https://new.example/f"]],
                },
            )
        ],
    )
    changes = client.get_subscription_changes("songhive", 42)
    assert changes.add == ["https://a.example/feed"]
    assert changes.remove == ["https://b.example/feed"]
    assert changes.timestamp == 1234.5
    assert changes.update_urls == [("https://old.example/f", "https://new.example/f")]
    assert session.calls == [("GET", f"{SERVER_URL}/api/2/subscriptions/user/songhive.json")]
    assert session.auth == ("user", "pw")


def test_client_upload_subscription_changes(monkeypatch):
    client, session = _client_with(
        monkeypatch,
        [_FakeResponse(200, {"timestamp": 99, "update_urls": []})],
    )
    result = client.upload_subscription_changes("songhive", ["https://a/f"], ["https://b/f"])
    assert result.timestamp == 99
    method, url = session.calls[0]
    assert method == "POST"
    assert url == f"{SERVER_URL}/api/2/subscriptions/user/songhive.json"


def test_client_401_logs_in_and_retries(monkeypatch):
    client, session = _client_with(
        monkeypatch,
        [
            _FakeResponse(401),
            _FakeResponse(200),  # login
            _FakeResponse(200, {"add": [], "remove": [], "timestamp": 7}),
        ],
    )
    changes = client.get_subscription_changes("songhive", 0)
    assert changes.timestamp == 7
    assert session.calls[1] == ("POST", f"{SERVER_URL}/api/2/auth/user/login.json")
    assert session.calls[2][0] == "GET"


def test_client_rejects_private_server_url():
    # url_allowed rejects non-public hosts before any request is made.
    client = GPodderClient("http://localhost:8080", "u", "p", timeout=5)
    with pytest.raises(GPodderError):
        client.get_subscription_changes("d", 0)


def test_client_http_error_raises(monkeypatch):
    client, _ = _client_with(monkeypatch, [_FakeResponse(500)])
    with pytest.raises(GPodderError, match="HTTP 500"):
        client.get_subscription_changes("d", 0)


# --- Nextcloud gpoddersync client --------------------------------------------

NC_BASE = "https://cloud.example.com/index.php/apps/gpoddersync"


def _nextcloud_client_with(monkeypatch, responses, server_url="https://cloud.example.com"):
    session = _FakeSession(responses)
    monkeypatch.setattr(gpodder_service.requests, "Session", lambda: session)
    client = NextcloudGPodderClient(server_url, "user", "pw", timeout=5)
    return client, session


def test_nextcloud_client_uses_app_paths(monkeypatch):
    client, session = _nextcloud_client_with(
        monkeypatch,
        [
            _FakeResponse(200, {"add": ["https://a.example/feed"], "remove": [], "timestamp": 5}),
            _FakeResponse(200, {"timestamp": 6}),
        ],
    )
    changes = client.get_subscription_changes("songhive", 3)
    assert changes.add == ["https://a.example/feed"]
    client.upload_subscription_changes("songhive", ["https://a/f"], [])
    assert session.calls == [
        ("GET", f"{NC_BASE}/subscriptions"),
        ("POST", f"{NC_BASE}/subscription_change/create"),
    ]
    assert session.auth == ("user", "pw")


def test_nextcloud_login_probes_subscriptions(monkeypatch):
    client, session = _nextcloud_client_with(monkeypatch, [_FakeResponse(200, {"add": [], "remove": []})])
    client.login()
    assert session.calls == [("GET", f"{NC_BASE}/subscriptions")]


def test_make_client_picks_nextcloud(config):
    row = PodcastSyncConfig(
        user_id="u1",
        server_type="nextcloud",
        server_url="https://cloud.example.com/index.php/apps/gpoddersync/subscriptions",
        username="u",
        password="p",
        device_id="songhive",
        mode="pull",
    )
    client = gpodder_service.make_client(row, config)
    assert isinstance(client, NextcloudGPodderClient)
    # The pasted endpoint URL resolves to the app base.
    assert client._base == NC_BASE


# --- API tests ---------------------------------------------------------------


def _stub_verify_ok(monkeypatch):
    async def _verify(_row, _config):
        return None

    monkeypatch.setattr(gpodder_service, "verify_credentials", _verify)


def test_sync_config_crud_api(client, regular_user, auth_headers, monkeypatch):
    _stub_verify_ok(monkeypatch)
    headers = auth_headers(regular_user)

    resp = client.get("/api/v1/podcasts/sync", headers=headers)
    assert resp.status_code == 404

    resp = client.put(
        "/api/v1/podcasts/sync",
        headers=headers,
        json={
            "server_type": "nextcloud",
            "server_url": "https://cloud.example.com",
            "username": "gp",
            "password": "pw",
            "mode": "bidirectional",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["server_type"] == "nextcloud"
    assert body["server_url"] == "https://cloud.example.com"
    assert body["mode"] == "bidirectional"
    assert body["has_password"] is True
    assert "password" not in body

    resp = client.get("/api/v1/podcasts/sync", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["username"] == "gp"

    resp = client.delete("/api/v1/podcasts/sync", headers=headers)
    assert resp.status_code == 204
    assert client.get("/api/v1/podcasts/sync", headers=headers).status_code == 404


def test_sync_config_put_rejects_bad_credentials(client, regular_user, auth_headers, monkeypatch):
    async def _verify(_row, _config):
        raise GPodderError("auth failed")

    monkeypatch.setattr(gpodder_service, "verify_credentials", _verify)
    headers = auth_headers(regular_user)
    resp = client.put(
        "/api/v1/podcasts/sync",
        headers=headers,
        json={"server_url": SERVER_URL, "username": "gp", "password": "pw"},
    )
    assert resp.status_code == 422
    # The failed config must not persist.
    assert client.get("/api/v1/podcasts/sync", headers=headers).status_code == 404


def test_sync_config_requires_password_on_create(client, regular_user, auth_headers, monkeypatch):
    _stub_verify_ok(monkeypatch)
    resp = client.put(
        "/api/v1/podcasts/sync",
        headers=auth_headers(regular_user),
        json={"server_url": SERVER_URL, "username": "gp"},
    )
    assert resp.status_code == 422


def test_sync_now_api(client, regular_user, auth_headers, db_session, config, monkeypatch):
    _stub_verify_ok(monkeypatch)
    _stub_fetch(monkeypatch)
    headers = auth_headers(regular_user)
    client.put(
        "/api/v1/podcasts/sync",
        headers=headers,
        json={"server_url": SERVER_URL, "username": "gp", "password": "pw", "mode": "pull"},
    )
    _stub_client(
        monkeypatch,
        FakeGPodderClient(changes=SubscriptionChanges(add=[FEED_URL], timestamp=5)),
    )
    resp = client.post("/api/v1/podcasts/sync/now", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["subscribed"] == 1
    assert body["pushed_adds"] == 0


def test_sync_now_unconfigured_404(client, regular_user, auth_headers):
    resp = client.post("/api/v1/podcasts/sync/now", headers=auth_headers(regular_user))
    assert resp.status_code == 404


def test_sync_endpoints_require_auth(client):
    assert client.get("/api/v1/podcasts/sync").status_code == 401
    assert client.put("/api/v1/podcasts/sync", json={}).status_code == 401
    assert client.delete("/api/v1/podcasts/sync").status_code == 401
    assert client.post("/api/v1/podcasts/sync/now").status_code == 401
