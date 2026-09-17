"""
Tests for explicit remote content lookup, dereference, and caching.

Covers the SSRF-guarded fetcher, the ``remote_search_access`` policy, the
input parser, remote actor lookup through pubby's actor cache, remote
object dereference/materialization, cached-only search, and the
``/api/v1/remote/*`` endpoints.
"""

import json as jsonlib

import pytest

from songhive.federation import fetch as fetch_mod
from songhive.federation.fetch import FetchError, FetchNotFound, FetchResult, guarded_fetch
from songhive.models._enums import Visibility
from songhive.models.activity import Activity
from songhive.models.artist import Artist
from songhive.models.remote_object import RemoteObject
from songhive.models.track import Track
from songhive.services import remote_content as rc
from songhive.services.remote_content import RemoteTargetKind

REMOTE_DOMAIN = "remote.invalid"  # .invalid never resolves — fetch is always stubbed
ACTOR_URL = f"https://{REMOTE_DOMAIN}/users/alice"
OBJECT_URL = f"https://{REMOTE_DOMAIN}/users/alice/objects/1"
ACTIVITY_URL = f"https://{REMOTE_DOMAIN}/activities/1"

ACTOR_DOC = {
    "@context": "https://www.w3.org/ns/activitystreams",
    "id": ACTOR_URL,
    "type": "Person",
    "preferredUsername": "alice",
    "name": "Alice Remote",
    "summary": "A remote musician",
    "inbox": f"https://{REMOTE_DOMAIN}/users/alice/inbox",
    "icon": {"type": "Image", "url": f"https://{REMOTE_DOMAIN}/alice.png"},
}

NOTE_DOC = {
    "@context": "https://www.w3.org/ns/activitystreams",
    "id": OBJECT_URL,
    "type": "Note",
    "attributedTo": ACTOR_URL,
    "content": "<p>hello fediverse</p>",
    "to": ["https://www.w3.org/ns/activitystreams#Public"],
    "published": "2026-01-02T03:04:05Z",
}

CREATE_DOC = {
    "@context": "https://www.w3.org/ns/activitystreams",
    "id": ACTIVITY_URL,
    "type": "Create",
    "actor": ACTOR_URL,
    "to": ["https://www.w3.org/ns/activitystreams#Public"],
    "object": NOTE_DOC,
}

WEBFINGER_DOC = {
    "subject": f"acct:alice@{REMOTE_DOMAIN}",
    "links": [
        {
            "rel": "self",
            "type": "application/activity+json",
            "href": ACTOR_URL,
        }
    ],
}


@pytest.fixture
def remote_config(config, tmp_path):
    """Config with a local instance domain and a throwaway actor key path."""
    config.federation.instance_domain = "local.invalid"
    config.federation.private_key_path = tmp_path / "actor.pem"
    return config


def _doc_result(url: str, doc: dict) -> FetchResult:
    return FetchResult(
        url=url,
        status_code=200,
        content_type="application/activity+json",
        body=jsonlib.dumps(doc).encode(),
        headers={},
    )


class FakeFetcher:
    """A ``guarded_fetch`` stub routing URLs to canned documents."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[str] = []
        self.call_kwargs: list[dict] = []

    def __call__(self, url: str, *, check_url=None, **kwargs) -> FetchResult:
        self.calls.append(url)
        self.call_kwargs.append(kwargs)
        if check_url is not None:
            check_url(url)
        response = self.routes[url]
        if isinstance(response, Exception):
            raise response
        return _doc_result(url, response)


@pytest.fixture
def fetcher(monkeypatch):
    """Stub ``remote_content.guarded_fetch`` and return the FakeFetcher to fill."""

    def _install(routes: dict) -> FakeFetcher:
        fake = FakeFetcher(routes)
        monkeypatch.setattr(rc, "guarded_fetch", fake)
        return fake

    return _install


# ---------------------------------------------------------------------------
# Guarded fetcher (real implementation, stubbed transport)
# ---------------------------------------------------------------------------


class _StubResponse:
    def __init__(self, status_code=200, body=b"{}", headers=None):
        self.status_code = status_code
        self.headers = headers or {"Content-Type": "application/activity+json"}
        self._body = body

    @property
    def is_redirect(self):
        return "location" in {k.lower() for k in self.headers} and self.status_code in (
            301,
            302,
            303,
            307,
            308,
        )

    @property
    def is_permanent_redirect(self):
        return self.is_redirect and self.status_code in (301, 308)

    def iter_content(self, _size):
        yield self._body

    def close(self):
        pass


def _stub_session(monkeypatch, handler):
    calls = []

    class _Session:
        def get(self, url, **kwargs):
            calls.append(url)
            return handler(url)

        def close(self):
            pass

    monkeypatch.setattr(fetch_mod.requests, "Session", lambda: _Session())
    return calls


class TestGuardedFetch:
    def test_rejects_non_http_scheme(self):
        with pytest.raises(FetchError):
            guarded_fetch("file:///etc/passwd")

    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost/x",
            "http://127.0.0.1/x",
            "http://169.254.169.254/latest/meta-data",
            "http://[::1]/x",
            "http://10.0.0.1/internal",
            "http://192.168.1.1/router",
        ],
    )
    def test_rejects_private_and_metadata_hosts(self, url):
        with pytest.raises(FetchError):
            guarded_fetch(url)

    def test_rejects_private_dns_answer(self, monkeypatch):
        def _gaierror(host, *args, **kwargs):
            return [(2, 1, 6, "", ("10.1.2.3", 0))]

        monkeypatch.setattr(fetch_mod.socket, "getaddrinfo", _gaierror)
        with pytest.raises(FetchError):
            guarded_fetch(f"https://{REMOTE_DOMAIN}/x")

    def test_follows_redirect_and_revalidates(self, monkeypatch):
        body = b'{"ok": true}'
        responses = {
            f"https://{REMOTE_DOMAIN}/a": _StubResponse(302, headers={"Location": f"https://{REMOTE_DOMAIN}/b"}),
            f"https://{REMOTE_DOMAIN}/b": _StubResponse(200, body=body),
        }
        calls = _stub_session(monkeypatch, lambda url: responses[url])
        result = guarded_fetch(f"https://{REMOTE_DOMAIN}/a")
        assert result.url == f"https://{REMOTE_DOMAIN}/b"
        assert result.json() == {"ok": True}
        assert calls == [f"https://{REMOTE_DOMAIN}/a", f"https://{REMOTE_DOMAIN}/b"]

    def test_redirect_to_private_host_is_refused(self, monkeypatch):
        responses = {
            f"https://{REMOTE_DOMAIN}/a": _StubResponse(302, headers={"Location": "http://169.254.169.254/meta"}),
        }
        calls = _stub_session(monkeypatch, lambda url: responses[url])
        with pytest.raises(FetchError):
            guarded_fetch(f"https://{REMOTE_DOMAIN}/a")
        assert calls == [f"https://{REMOTE_DOMAIN}/a"]

    def test_too_many_redirects(self, monkeypatch):
        def handler(url):
            return _StubResponse(302, headers={"Location": f"{url}x"})

        _stub_session(monkeypatch, handler)
        with pytest.raises(FetchError, match="Too many redirects"):
            guarded_fetch(f"https://{REMOTE_DOMAIN}/a", max_redirects=2)

    def test_404_raises_not_found(self, monkeypatch):
        _stub_session(monkeypatch, lambda url: _StubResponse(404))
        with pytest.raises(FetchNotFound):
            guarded_fetch(f"https://{REMOTE_DOMAIN}/gone")

    def test_non_200_raises_fetch_error(self, monkeypatch):
        _stub_session(monkeypatch, lambda url: _StubResponse(500))
        with pytest.raises(FetchError):
            guarded_fetch(f"https://{REMOTE_DOMAIN}/oops")

    def test_disallowed_content_type(self, monkeypatch):
        _stub_session(
            monkeypatch,
            lambda url: _StubResponse(200, headers={"Content-Type": "text/html"}),
        )
        with pytest.raises(FetchError, match="content type"):
            guarded_fetch(f"https://{REMOTE_DOMAIN}/page")

    def test_body_size_cap(self, monkeypatch):
        big = b"x" * (fetch_mod.MAX_BODY_BYTES + 10)
        _stub_session(monkeypatch, lambda url: _StubResponse(200, body=big))
        result = guarded_fetch(f"https://{REMOTE_DOMAIN}/big")
        assert len(result.body) == fetch_mod.MAX_BODY_BYTES


# ---------------------------------------------------------------------------
# Input parsing and policy gates
# ---------------------------------------------------------------------------


class TestParseRemoteTarget:
    def test_handle(self, remote_config):
        for raw in ("@alice@remote.example", "alice@remote.example"):
            target = rc.parse_remote_target(raw, remote_config)
            assert target.kind == RemoteTargetKind.HANDLE
            assert target.username == "alice"
            assert target.domain == "remote.example"

    def test_local_handle(self, remote_config):
        target = rc.parse_remote_target("@alice@local.invalid", remote_config)
        assert target.kind == RemoteTargetKind.LOCAL

    def test_actor_url(self, remote_config):
        target = rc.parse_remote_target(f"https://{REMOTE_DOMAIN}/users/alice", remote_config)
        assert target.kind == RemoteTargetKind.ACTOR_URL
        assert target.username == "alice"

    def test_at_profile_url(self, remote_config):
        target = rc.parse_remote_target(f"https://{REMOTE_DOMAIN}/@alice", remote_config)
        assert target.kind == RemoteTargetKind.ACTOR_URL
        assert target.username == "alice"

    def test_songhive_activity_urls(self, remote_config):
        for path in (
            f"https://{REMOTE_DOMAIN}/users/alice/objects/1",
            f"https://{REMOTE_DOMAIN}/activities/xyz",
            f"https://{REMOTE_DOMAIN}/users/alice/statuses/xyz",
        ):
            assert rc.parse_remote_target(path, remote_config).kind == RemoteTargetKind.SONGHIVE_ACTIVITY_URL

    def test_songhive_resource_url(self, remote_config):
        target = rc.parse_remote_target(f"https://{REMOTE_DOMAIN}/tracks/abc", remote_config)
        assert target.kind == RemoteTargetKind.SONGHIVE_RESOURCE_URL
        assert target.resource_kind == "track"
        assert target.resource_id == "abc"

    def test_generic_object_url(self, remote_config):
        target = rc.parse_remote_target(f"https://{REMOTE_DOMAIN}/notes/1", remote_config)
        assert target.kind == RemoteTargetKind.OBJECT_URL

    def test_local_url(self, remote_config):
        target = rc.parse_remote_target("https://local.invalid/tracks/1", remote_config)
        assert target.kind == RemoteTargetKind.LOCAL

    def test_unsupported(self, remote_config):
        for raw in ("not a url", "", "ftp://remote.example/x"):
            assert rc.parse_remote_target(raw, remote_config).kind == RemoteTargetKind.UNSUPPORTED


class TestRemoteAccessPolicy:
    def test_disabled_denies_everyone(self, remote_config, regular_user):
        remote_config.federation.remote_search_access = "disabled"
        assert not rc.remote_lookup_allowed(regular_user, remote_config)
        assert not rc.remote_lookup_allowed(None, remote_config)
        with pytest.raises(rc.RemoteAccessDenied) as exc:
            rc.check_remote_access(regular_user, "disabled")
        assert exc.value.status_code == 403

    def test_authenticated_requires_login(self, remote_config, regular_user):
        remote_config.federation.remote_search_access = "authenticated"
        assert rc.remote_lookup_allowed(regular_user, remote_config)
        assert not rc.remote_lookup_allowed(None, remote_config)
        with pytest.raises(rc.RemoteAccessDenied) as exc:
            rc.check_remote_access(None, "authenticated")
        assert exc.value.status_code == 401

    def test_public_allows_anonymous(self, remote_config):
        remote_config.federation.remote_search_access = "public"
        assert rc.remote_lookup_allowed(None, remote_config)
        rc.check_remote_access(None, "public")


class TestRemoteDomainRules:
    def test_blocked_domain(self, remote_config):
        remote_config.federation.blocked_instances = ["bad.example"]
        assert not rc.remote_domain_allowed("bad.example", remote_config)
        with pytest.raises(FetchError) as exc:
            rc.require_remote_domain("https://bad.example/x", remote_config)
        assert exc.value.status_code == 403

    def test_allow_list(self, remote_config):
        remote_config.federation.allowed_instances = ["good.example"]
        assert rc.remote_domain_allowed("good.example", remote_config)
        assert not rc.remote_domain_allowed("other.example", remote_config)

    def test_local_domain_rejected(self, remote_config):
        assert not rc.remote_domain_allowed("local.invalid", remote_config)


class TestResolveLocalTarget:
    """Local-domain inputs map straight to SPA routes — no fetch."""

    async def test_local_handle(self, db_session, remote_config):
        target = rc.parse_remote_target("@alice@local.invalid", remote_config)
        assert target.kind == RemoteTargetKind.LOCAL
        assert await rc.resolve_local_target(db_session, target) == "/@alice"

    async def test_local_urls(self, db_session, remote_config):
        cases = {
            "https://local.invalid/tracks/abc": "/tracks/abc",
            "https://local.invalid/api/v1/tracks/abc": "/tracks/abc",
            "https://local.invalid/libraries/lib-9": "/libraries/lib-9",
            "https://local.invalid/users/alice": "/@alice",
            "https://local.invalid/@alice": "/@alice",
            "https://local.invalid/activities/act-1": "/activities/act-1",
            "https://local.invalid/whatever": "/whatever",
            "https://local.invalid/search?q=x": "/search?q=x",
        }
        for url, expected in cases.items():
            target = rc.parse_remote_target(url, remote_config)
            assert target.kind == RemoteTargetKind.LOCAL, url
            assert await rc.resolve_local_target(db_session, target) == expected, url

    async def test_object_permalink_resolves_track(self, db_session, remote_config, regular_user):
        artist = Artist(name="A")
        db_session.add(artist)
        await db_session.flush()
        track = Track(
            title="T",
            artist_id=artist.id,
            owner_id=regular_user.id,
            visibility=Visibility.PUBLIC.value,
            federation_object_id="obj-1",
        )
        db_session.add(track)
        await db_session.flush()

        target = rc.parse_remote_target("https://local.invalid/users/alice/objects/obj-1", remote_config)
        assert await rc.resolve_local_target(db_session, target) == f"/tracks/{track.id}"

    async def test_object_permalink_resolves_activity(self, db_session, remote_config, regular_user):
        activity = Activity(
            entity_type="track",
            entity_id="t-1",
            activity_type="create",
            source_type="local",
            source_actor="https://local.invalid/users/alice",
            source_id="src-1",
            visibility=Visibility.PUBLIC.value,
            local_object_id="obj-2",
        )
        db_session.add(activity)
        await db_session.flush()

        target = rc.parse_remote_target("https://local.invalid/users/alice/statuses/obj-2", remote_config)
        assert await rc.resolve_local_target(db_session, target) == f"/activities/{activity.id}"

    async def test_object_permalink_miss_falls_back_to_path(self, db_session, remote_config):
        target = rc.parse_remote_target("https://local.invalid/users/alice/objects/missing", remote_config)
        assert await rc.resolve_local_target(db_session, target) == "/users/alice/objects/missing"


class TestFetchTimeout:
    """``federation.fetch_timeout_seconds`` reaches ``guarded_fetch``."""

    async def test_document_fetch(self, remote_config, fetcher):
        remote_config.federation.fetch_timeout_seconds = 7.5
        fake = fetcher({ACTOR_URL: ACTOR_DOC})
        await rc.fetch_remote_document(ACTOR_URL, remote_config)
        assert fake.call_kwargs[0]["timeout"] == 7.5

    async def test_document_fetch_unsigned(self, remote_config, fetcher):
        remote_config.federation.fetch_timeout_seconds = 7.5
        fake = fetcher({ACTOR_URL: ACTOR_DOC})
        await rc.fetch_ap_document(ACTOR_URL, remote_config)
        assert fake.call_kwargs[0]["timeout"] == 7.5

    async def test_webfinger(self, remote_config, fetcher):
        remote_config.federation.fetch_timeout_seconds = 42
        fake = fetcher(
            {f"https://{REMOTE_DOMAIN}/.well-known/webfinger?resource=acct:alice@{REMOTE_DOMAIN}": WEBFINGER_DOC}
        )
        assert await rc._webfinger_actor_url("alice", REMOTE_DOMAIN, remote_config) == ACTOR_URL
        assert fake.call_kwargs[0]["timeout"] == 42

    async def test_default_timeout(self, remote_config, fetcher):
        fake = fetcher({ACTOR_URL: ACTOR_DOC})
        await rc.fetch_remote_document(ACTOR_URL, remote_config)
        assert fake.call_kwargs[0]["timeout"] == 20.0


# ---------------------------------------------------------------------------
# Remote actor lookup
# ---------------------------------------------------------------------------


class TestLookupRemoteActor:
    async def test_handle_lookup_via_webfinger(self, db_session, remote_config, fetcher):
        fake = fetcher(
            {
                f"https://{REMOTE_DOMAIN}/.well-known/webfinger?resource=acct:alice@{REMOTE_DOMAIN}": WEBFINGER_DOC,
                ACTOR_URL: ACTOR_DOC,
            }
        )
        actor = await rc.lookup_remote_actor(db_session, remote_config, "@alice@remote.invalid")
        assert actor.username == "alice"
        assert actor.domain == REMOTE_DOMAIN
        assert actor.display_name == "Alice Remote"
        assert actor.handle == f"alice@{REMOTE_DOMAIN}"
        assert not actor.cached
        assert len(fake.calls) == 2

    async def test_cached_actor_served_without_fetch(self, db_session, remote_config, fetcher):
        fake = fetcher(
            {
                f"https://{REMOTE_DOMAIN}/.well-known/webfinger?resource=acct:alice@{REMOTE_DOMAIN}": WEBFINGER_DOC,
                ACTOR_URL: ACTOR_DOC,
            }
        )
        await rc.lookup_remote_actor(db_session, remote_config, "@alice@remote.invalid")
        actor = await rc.lookup_remote_actor(db_session, remote_config, "@alice@remote.invalid")
        assert actor.cached
        assert len(fake.calls) == 2  # no additional fetches

    async def test_refresh_refetches(self, db_session, remote_config, fetcher):
        fake = fetcher({ACTOR_URL: ACTOR_DOC})
        await rc.lookup_remote_actor(db_session, remote_config, ACTOR_URL)
        await rc.lookup_remote_actor(db_session, remote_config, ACTOR_URL, refresh=True)
        assert fake.calls == [ACTOR_URL, ACTOR_URL]

    async def test_blocked_domain_never_fetches(self, db_session, remote_config, fetcher):
        remote_config.federation.blocked_instances = [REMOTE_DOMAIN]
        fake = fetcher({})
        with pytest.raises(FetchError):
            await rc.lookup_remote_actor(db_session, remote_config, "@alice@remote.invalid")
        assert fake.calls == []

    async def test_gone_actor_marks_cached_unavailable(self, db_session, remote_config, fetcher):
        fetcher({ACTOR_URL: ACTOR_DOC})
        await rc.lookup_remote_actor(db_session, remote_config, ACTOR_URL)

        fetcher({ACTOR_URL: FetchNotFound("gone", url=ACTOR_URL)})
        actor = await rc.lookup_remote_actor(db_session, remote_config, ACTOR_URL, refresh=True)
        assert actor.unavailable

    async def test_uncached_gone_actor_raises_not_found(self, db_session, remote_config, fetcher):
        fetcher({ACTOR_URL: FetchNotFound("gone", url=ACTOR_URL)})
        with pytest.raises(FetchNotFound):
            await rc.lookup_remote_actor(db_session, remote_config, ACTOR_URL)

    async def test_non_actor_document_rejected(self, db_session, remote_config, fetcher):
        fetcher({ACTOR_URL: NOTE_DOC})
        with pytest.raises(FetchError) as exc:
            await rc.lookup_remote_actor(db_session, remote_config, ACTOR_URL)
        assert exc.value.status_code == 422

    async def test_get_cached_remote_actor(self, db_session, remote_config, fetcher):
        fetcher({ACTOR_URL: ACTOR_DOC})
        assert await rc.get_cached_remote_actor(db_session, remote_config, "alice@remote.invalid") is None
        await rc.lookup_remote_actor(db_session, remote_config, ACTOR_URL)
        cached = await rc.get_cached_remote_actor(db_session, remote_config, "alice@remote.invalid")
        assert cached is not None and cached.username == "alice"


# ---------------------------------------------------------------------------
# Remote object dereference and materialization
# ---------------------------------------------------------------------------


class TestDereferenceRemoteObject:
    async def test_create_note_materializes(self, db_session, remote_config, fetcher):
        fetcher({ACTIVITY_URL: CREATE_DOC, ACTOR_URL: ACTOR_DOC})
        result = await rc.dereference_remote_object(db_session, remote_config, ACTIVITY_URL)
        await db_session.commit()

        row = result.remote_object
        assert result.status == "ok"
        assert row.canonical_url == OBJECT_URL
        assert row.activity_url == ACTIVITY_URL
        assert row.domain == REMOTE_DOMAIN
        assert row.object_type == "Note"
        assert row.visibility == "public"
        assert row.content == "<p>hello fediverse</p>"

        activity = result.activity
        assert activity is not None
        assert activity.entity_type == "remote"
        assert activity.entity_id == str(row.id)
        assert activity.activity_type == "create"
        assert activity.source_type == "remote"
        assert activity.source_id == OBJECT_URL
        assert activity.source_actor == ACTOR_URL
        assert activity.owner_user_id is None
        assert activity.visibility == "public"

    async def test_cached_object_served_without_fetch(self, db_session, remote_config, fetcher):
        fake = fetcher({ACTIVITY_URL: CREATE_DOC, ACTOR_URL: ACTOR_DOC})
        await rc.dereference_remote_object(db_session, remote_config, ACTIVITY_URL)
        await db_session.commit()
        result = await rc.dereference_remote_object(db_session, remote_config, ACTIVITY_URL)
        assert not result.fetched
        assert fake.calls == [ACTIVITY_URL, ACTOR_URL]

    async def test_announce_materializes_as_announce(self, db_session, remote_config, fetcher):
        announce = {
            "id": f"https://{REMOTE_DOMAIN}/activities/2",
            "type": "Announce",
            "actor": ACTOR_URL,
            "to": ["https://www.w3.org/ns/activitystreams#Public"],
            "object": {**NOTE_DOC, "id": f"https://{REMOTE_DOMAIN}/users/alice/objects/2"},
        }
        fetcher({f"https://{REMOTE_DOMAIN}/activities/2": announce, ACTOR_URL: ACTOR_DOC})
        result = await rc.dereference_remote_object(db_session, remote_config, f"https://{REMOTE_DOMAIN}/activities/2")
        assert result.activity.activity_type == "announce"

    async def test_reply_links_cached_parent(self, db_session, remote_config, fetcher):
        fetcher({ACTIVITY_URL: CREATE_DOC, ACTOR_URL: ACTOR_DOC})
        parent = (await rc.dereference_remote_object(db_session, remote_config, ACTIVITY_URL)).activity
        await db_session.commit()

        reply_doc = {
            "id": f"https://{REMOTE_DOMAIN}/activities/3",
            "type": "Create",
            "actor": ACTOR_URL,
            "to": ["https://www.w3.org/ns/activitystreams#Public"],
            "object": {
                "id": f"https://{REMOTE_DOMAIN}/users/alice/objects/3",
                "type": "Note",
                "attributedTo": ACTOR_URL,
                "content": "<p>reply</p>",
                "inReplyTo": OBJECT_URL,
                "to": ["https://www.w3.org/ns/activitystreams#Public"],
            },
        }
        fetcher({f"https://{REMOTE_DOMAIN}/activities/3": reply_doc, ACTOR_URL: ACTOR_DOC})
        result = await rc.dereference_remote_object(db_session, remote_config, f"https://{REMOTE_DOMAIN}/activities/3")
        assert result.activity.activity_type == "reply"
        assert result.activity.in_reply_to_activity_id == str(parent.id)

    async def test_reply_to_uncached_parent_not_fetched(self, db_session, remote_config, fetcher):
        reply_doc = {
            "id": f"https://{REMOTE_DOMAIN}/activities/3",
            "type": "Create",
            "actor": ACTOR_URL,
            "to": ["https://www.w3.org/ns/activitystreams#Public"],
            "object": {
                "id": f"https://{REMOTE_DOMAIN}/users/alice/objects/3",
                "type": "Note",
                "attributedTo": ACTOR_URL,
                "inReplyTo": f"https://{REMOTE_DOMAIN}/users/bob/objects/9",
                "to": ["https://www.w3.org/ns/activitystreams#Public"],
            },
        }
        fake = fetcher({f"https://{REMOTE_DOMAIN}/activities/3": reply_doc, ACTOR_URL: ACTOR_DOC})
        result = await rc.dereference_remote_object(db_session, remote_config, f"https://{REMOTE_DOMAIN}/activities/3")
        assert result.activity.activity_type == "reply"
        assert result.activity.in_reply_to_activity_id is None
        # The uncached parent URL is never dereferenced.
        assert f"https://{REMOTE_DOMAIN}/users/bob/objects/9" not in fake.calls

    async def test_attribution_mismatch_rejected(self, db_session, remote_config, fetcher):
        doc = {
            **CREATE_DOC,
            "object": {**NOTE_DOC, "attributedTo": f"https://{REMOTE_DOMAIN}/users/mallory"},
        }
        fetcher({ACTIVITY_URL: doc, ACTOR_URL: ACTOR_DOC})
        with pytest.raises(Exception) as exc:
            await rc.dereference_remote_object(db_session, remote_config, ACTIVITY_URL)
        assert "attributedTo" in str(exc.value)

    async def test_delete_tombstones_cached_object(self, db_session, remote_config, fetcher):
        fetcher({ACTIVITY_URL: CREATE_DOC, ACTOR_URL: ACTOR_DOC})
        await rc.dereference_remote_object(db_session, remote_config, ACTIVITY_URL)
        await db_session.commit()

        delete_doc = {
            "id": f"https://{REMOTE_DOMAIN}/activities/del",
            "type": "Delete",
            "actor": ACTOR_URL,
            "object": {"id": OBJECT_URL, "type": "Tombstone"},
        }
        fetcher({ACTIVITY_URL: delete_doc})
        result = await rc.dereference_remote_object(db_session, remote_config, ACTIVITY_URL, refresh=True)
        assert result.status == "gone"
        assert result.remote_object.unavailable_at is not None
        assert result.activity.deleted_at is not None

    async def test_404_tombstones_cached_object(self, db_session, remote_config, fetcher):
        fetcher({ACTIVITY_URL: CREATE_DOC, ACTOR_URL: ACTOR_DOC})
        await rc.dereference_remote_object(db_session, remote_config, ACTIVITY_URL)
        await db_session.commit()

        fetcher({ACTIVITY_URL: FetchNotFound("gone", url=ACTIVITY_URL)})
        result = await rc.dereference_remote_object(db_session, remote_config, ACTIVITY_URL, refresh=True)
        assert result.status == "gone"

    async def test_404_uncached_raises(self, db_session, remote_config, fetcher):
        fetcher({ACTIVITY_URL: FetchNotFound("gone", url=ACTIVITY_URL)})
        with pytest.raises(FetchNotFound):
            await rc.dereference_remote_object(db_session, remote_config, ACTIVITY_URL)

    async def test_blocked_domain_rejected(self, db_session, remote_config, fetcher):
        remote_config.federation.blocked_instances = [REMOTE_DOMAIN]
        fake = fetcher({})
        with pytest.raises(FetchError):
            await rc.dereference_remote_object(db_session, remote_config, ACTIVITY_URL)
        assert fake.calls == []

    async def test_audio_resource_not_materialized(self, db_session, remote_config, fetcher):
        audio_doc = {
            "id": f"https://{REMOTE_DOMAIN}/users/alice/objects/track1",
            "type": "Audio",
            "attributedTo": ACTOR_URL,
            "name": "Remote Song",
            "url": {
                "type": "Link",
                "href": f"https://{REMOTE_DOMAIN}/media/song.ogg",
                "mediaType": "audio/ogg",
            },
            "to": ["https://www.w3.org/ns/activitystreams#Public"],
        }
        url = f"https://{REMOTE_DOMAIN}/tracks/track1"
        fetcher({url: audio_doc, ACTOR_URL: ACTOR_DOC})
        result = await rc.dereference_remote_object(db_session, remote_config, url)
        row = result.remote_object
        assert row.resource_type == "track"
        assert row.audio_url == f"https://{REMOTE_DOMAIN}/media/song.ogg"
        assert result.activity is None  # bare resources are not feed entries


# ---------------------------------------------------------------------------
# Cached-only search
# ---------------------------------------------------------------------------


async def _remote_row(db_session, **kwargs) -> RemoteObject:
    row = RemoteObject(
        canonical_url=kwargs.get("canonical_url", f"https://{REMOTE_DOMAIN}/o/1"),
        domain=kwargs.get("domain", REMOTE_DOMAIN),
        object_type=kwargs.get("object_type", "Note"),
        resource_type=kwargs.get("resource_type"),
        actor_url=kwargs.get("actor_url", ACTOR_URL),
        visibility=kwargs.get("visibility", "public"),
        name=kwargs.get("name"),
        summary=kwargs.get("summary"),
        content=kwargs.get("content"),
    )
    db_session.add(row)
    await db_session.flush()
    return row


class TestCachedRemoteSearch:
    async def test_matches_name_and_content(self, db_session, remote_config):
        await _remote_row(db_session, name="Federated anthem")
        await _remote_row(db_session, canonical_url=f"https://{REMOTE_DOMAIN}/o/2", name="other")
        hits = await rc.search_cached_remote_objects(db_session, remote_config, "anthem", user=None)
        assert [h.name for h in hits] == ["Federated anthem"]

    async def test_anonymous_sees_only_public(self, db_session, remote_config, regular_user):
        await _remote_row(db_session, name="public hit")
        await _remote_row(
            db_session,
            canonical_url=f"https://{REMOTE_DOMAIN}/o/2",
            name="private hit",
            visibility="private",
        )
        anon = await rc.search_cached_remote_objects(db_session, remote_config, "hit", user=None)
        assert {r.name for r in anon} == {"public hit"}
        authed = await rc.search_cached_remote_objects(db_session, remote_config, "hit", user=regular_user)
        assert {r.name for r in authed} == {"public hit", "private hit"}

    async def test_blocked_domains_never_returned(self, db_session, remote_config):
        await _remote_row(
            db_session,
            name="bad domain hit",
            domain="bad.example",
            canonical_url="https://bad.example/o/1",
            actor_url="https://bad.example/users/mallory",
        )
        remote_config.federation.blocked_instances = ["bad.example"]
        hits = await rc.search_cached_remote_objects(db_session, remote_config, "hit", user=None)
        assert hits == []

    async def test_unavailable_rows_excluded(self, db_session, remote_config):
        from datetime import datetime, timezone

        row = await _remote_row(db_session, name="gone hit")
        row.unavailable_at = datetime.now(timezone.utc)
        await db_session.flush()
        hits = await rc.search_cached_remote_objects(db_session, remote_config, "hit", user=None)
        assert hits == []


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------


class TestRemoteRoutes:
    def test_lookup_requires_auth_by_default(self, client):
        response = client.get("/api/v1/remote/lookup", params={"input": "@alice@remote.invalid"})
        assert response.status_code == 401

    async def test_lookup_forbidden_when_disabled(self, client, regular_user, auth_headers, db_session):
        await db_session.commit()
        client.app.state.config.federation.remote_search_access = "disabled"
        response = client.get(
            "/api/v1/remote/lookup",
            params={"input": "@alice@remote.invalid"},
            headers=auth_headers(regular_user),
        )
        assert response.status_code == 403

    def test_lookup_public_allows_anonymous(self, client, fetcher):
        client.app.state.config.federation.remote_search_access = "public"
        fetcher(
            {
                f"https://{REMOTE_DOMAIN}/.well-known/webfinger?resource=acct:alice@{REMOTE_DOMAIN}": WEBFINGER_DOC,
                ACTOR_URL: ACTOR_DOC,
            }
        )
        response = client.get("/api/v1/remote/lookup", params={"input": "@alice@remote.invalid"})
        assert response.status_code == 200
        body = response.json()
        assert body["kind"] == "actor"
        assert body["actor"]["handle"] == f"alice@{REMOTE_DOMAIN}"
        assert body["url"] == f"/@alice@{REMOTE_DOMAIN}"

    async def test_lookup_local_url_maps_to_spa_route(self, client, regular_user, auth_headers, db_session):
        await db_session.commit()
        client.app.state.config.federation.instance_domain = "local.invalid"
        response = client.get(
            "/api/v1/remote/lookup",
            params={"input": "https://local.invalid/tracks/track-1"},
            headers=auth_headers(regular_user),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["kind"] == "local"
        assert body["url"] == "/tracks/track-1"

        handle = client.get(
            "/api/v1/remote/lookup",
            params={"input": "@alice@local.invalid"},
            headers=auth_headers(regular_user),
        )
        assert handle.status_code == 200
        assert handle.json()["url"] == "/@alice"

    async def test_lookup_object_url(self, client, fetcher, regular_user, auth_headers, db_session):
        # Commit so the shared session holds no write transaction; pubby's
        # actor-cache storage writes through its own sync engine on the same
        # SQLite file during the request.
        await db_session.commit()
        fetcher({ACTIVITY_URL: CREATE_DOC, ACTOR_URL: ACTOR_DOC})
        response = client.get(
            "/api/v1/remote/lookup",
            params={"input": ACTIVITY_URL},
            headers=auth_headers(regular_user),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["kind"] == "object"
        assert body["object"]["canonical_url"] == OBJECT_URL
        assert body["activity"]["activity_type"] == "create"
        assert body["url"].startswith("/activities/@")

    async def test_actor_endpoint(self, client, fetcher, regular_user, auth_headers, db_session):
        await db_session.commit()
        fetcher(
            {
                f"https://{REMOTE_DOMAIN}/.well-known/webfinger?resource=acct:alice@{REMOTE_DOMAIN}": WEBFINGER_DOC,
                ACTOR_URL: ACTOR_DOC,
            }
        )
        response = client.get(
            f"/api/v1/remote/actors/alice@{REMOTE_DOMAIN}",
            headers=auth_headers(regular_user),
        )
        assert response.status_code == 200
        assert response.json()["username"] == "alice"

    async def test_actor_activities_cached_only(self, client, fetcher, regular_user, auth_headers, db_session):
        await db_session.commit()
        fetcher({ACTIVITY_URL: CREATE_DOC, ACTOR_URL: ACTOR_DOC})
        client.get(
            "/api/v1/remote/lookup",
            params={"input": ACTIVITY_URL},
            headers=auth_headers(regular_user),
        )
        response = client.get(
            f"/api/v1/remote/actors/alice@{REMOTE_DOMAIN}/activities",
            headers=auth_headers(regular_user),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["activities"][0]["source_id"] == OBJECT_URL

    async def test_actor_activities_uncached_404(self, client, regular_user, auth_headers, db_session):
        await db_session.commit()
        response = client.get(
            f"/api/v1/remote/actors/nobody@{REMOTE_DOMAIN}/activities",
            headers=auth_headers(regular_user),
        )
        assert response.status_code == 404

    async def test_object_detail_and_resource_routes(self, client, fetcher, regular_user, auth_headers, db_session):
        await db_session.commit()
        fetcher({ACTIVITY_URL: CREATE_DOC, ACTOR_URL: ACTOR_DOC})
        found = client.get(
            "/api/v1/remote/lookup",
            params={"input": ACTIVITY_URL},
            headers=auth_headers(regular_user),
        ).json()

        detail = client.get(
            f"/api/v1/remote/objects/{found['object']['id']}",
            headers=auth_headers(regular_user),
        )
        assert detail.status_code == 200
        assert detail.json()["activity"]["source_id"] == OBJECT_URL

        missing = client.get(
            f"/api/v1/remote/track/{found['object']['id']}",
            headers=auth_headers(regular_user),
        )
        assert missing.status_code == 404  # kind mismatch

    async def test_object_refresh_marks_gone(self, client, fetcher, regular_user, auth_headers, db_session):
        await db_session.commit()
        fetcher({ACTIVITY_URL: CREATE_DOC, ACTOR_URL: ACTOR_DOC})
        found = client.get(
            "/api/v1/remote/lookup",
            params={"input": ACTIVITY_URL},
            headers=auth_headers(regular_user),
        ).json()
        fetcher({OBJECT_URL: FetchNotFound("gone", url=OBJECT_URL)})
        detail = client.get(
            f"/api/v1/remote/objects/{found['object']['id']}",
            params={"refresh": "true"},
            headers=auth_headers(regular_user),
        )
        assert detail.status_code == 200
        assert detail.json()["object"]["unavailable"] is True

    async def test_search_remote_section_cached_only(self, client, regular_user, auth_headers, db_session):
        # Generic search must not fetch remotely — seed the cache directly.
        await _remote_row(db_session, name="federated anthem")
        await db_session.commit()

        anon = client.get("/api/v1/search/", params={"q": "anthem", "entities": "remote"})
        assert anon.status_code == 200
        remote = [s for s in anon.json()["sections"] if s["entity"] == "remote"]
        assert remote[0]["items"] == []
        assert anon.json()["remote_available"] is False

        authed = client.get(
            "/api/v1/search/",
            params={"q": "anthem", "entities": "remote"},
            headers=auth_headers(regular_user),
        )
        remote = [s for s in authed.json()["sections"] if s["entity"] == "remote"]
        assert len(remote[0]["items"]) == 1
        assert remote[0]["items"][0]["title"] == "federated anthem"
        assert remote[0]["items"][0]["url"].startswith("/activities/@")
        assert authed.json()["remote_available"] is True

    def test_admin_setting_validates_choices(self, client, admin_user, auth_headers):
        ok = client.put(
            "/api/v1/admin/settings/remote_search_access",
            json={"value": "public"},
            headers=auth_headers(admin_user),
        )
        assert ok.status_code == 200

        bad = client.put(
            "/api/v1/admin/settings/remote_search_access",
            json={"value": "everyone"},
            headers=auth_headers(admin_user),
        )
        assert bad.status_code == 400
