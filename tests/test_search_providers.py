"""Tests for external-provider search and the import/materialization path.

Covers ``GET /api/v1/search/providers`` (fan-out to the caller's own external
libraries with per-provider timeout/failure isolation) and
``POST /api/v1/external-libraries/{id}/import`` (catalog-first
materialization with ``membership="saved"``).
"""

from __future__ import annotations

import asyncio
from typing import Optional

import pytest
from sqlalchemy import func, select

from songhive.external.base import ExternalLibraryAdapter
from songhive.external.errors import ExternalLibraryError
from songhive.external.registry import register_external_adapter, unregister_external_adapter
from songhive.external.types import ExternalItemRef, ExternalLibraryCapabilities, ExternalTrackMetadata
from songhive.models.external_item import ExternalItem
from songhive.models.external_library import ExternalLibrary
from songhive.models.library import Library
from songhive.models.library_track import LibraryTrack
from songhive.models.provider_catalog import ProviderCatalogEntry
from songhive.models.track import Track
from songhive.services import secrets


class FakeSearchAdapter(ExternalLibraryAdapter):
    """Search-capable fake: ``config`` drives behavior, results and payloads."""

    provider_type = "fake-search"
    user_configurable = True

    # Class-level probes the tests reset per case.
    fetch_calls: list = []

    async def validate_config(self, config: dict) -> ExternalLibraryCapabilities:
        return ExternalLibraryCapabilities(
            list_items=True,
            read_bytes=False,
            stream_url=True,
            range_read=False,
            download=False,
            compute_hash=False,
            read_tags=False,
            write_tags=False,
            rename_source=False,
            delete_source=False,
            detect_changes=True,
            validate_config=True,
            list_albums=True,
            list_artists=True,
            list_playlists=True,
            limits={"entity_import": True, "search": True},
        )

    async def iter_items(self, config: dict, since=None, scope=None):
        return
        yield

    async def search(self, config: dict, query: str, *, limit: int = 20) -> list[dict]:
        behavior = config.get("search_behavior", "ok")
        if behavior == "fail":
            raise ExternalLibraryError("provider exploded")
        if behavior == "slow":
            await asyncio.sleep(float(config.get("delay", 5.0)))
        return list(config.get("results") or [])

    def entity_from_payload(self, config: dict, kind: str, payload: dict):
        if kind != "track" or not isinstance(payload, dict):
            return None
        return _ref_from_payload(payload)

    async def fetch_entity_metadata(self, config: dict, kind: str, provider_key: str):
        FakeSearchAdapter.fetch_calls.append(provider_key)
        if kind != "track":
            return None
        return _ref_from_payload({"provider_key": provider_key, "title": f"Fetched {provider_key}"})


class FakeNoSearchAdapter(ExternalLibraryAdapter):
    """Adapter without a ``search`` override — must be skipped, not failed."""

    provider_type = "fake-nosearch"
    user_configurable = True

    async def validate_config(self, config: dict) -> ExternalLibraryCapabilities:
        return ExternalLibraryCapabilities(
            list_items=True,
            read_bytes=False,
            stream_url=False,
            range_read=False,
            download=False,
            compute_hash=False,
            read_tags=False,
            write_tags=False,
            rename_source=False,
            delete_source=False,
            detect_changes=False,
            validate_config=True,
            list_albums=False,
            list_artists=False,
            list_playlists=False,
            limits={"entity_import": True},
        )

    async def iter_items(self, config: dict, since=None, scope=None):
        return
        yield


def _ref_from_payload(payload: dict) -> ExternalItemRef:
    return ExternalItemRef(
        provider_key=str(payload["provider_key"]),
        display_path=str(payload["provider_key"]),
        etag=None,
        mime_type="audio/flac",
        metadata=ExternalTrackMetadata(
            title=payload.get("title") or "Imported",
            artist=payload.get("artist") or "Artist A",
            album=payload.get("album"),
            album_artist=payload.get("album_artist") or "Artist A",
            duration=180.0,
            raw_metadata=dict(payload),
        ),
    )


@pytest.fixture(autouse=True)
def _register_search_adapters():
    register_external_adapter("fake-search", FakeSearchAdapter)
    register_external_adapter("fake-nosearch", FakeNoSearchAdapter)
    FakeSearchAdapter.fetch_calls = []
    yield
    unregister_external_adapter("fake-search")
    unregister_external_adapter("fake-nosearch")


@pytest.fixture
def _make_search_library(db_session):
    async def _inner(user, provider_type: str = "fake-search", config: Optional[dict] = None, **overrides):
        library = Library(name="Search Lib", owner_id=str(user.id), visibility="private")
        db_session.add(library)
        await db_session.flush()
        external_library = ExternalLibrary(
            library_id=str(library.id),
            provider_type=provider_type,
            config=secrets.encrypt_json(config or {}),
            enabled=overrides.pop("enabled", True),
            created_by_id=str(user.id),
            capabilities={"limits": {"entity_import": True, "search": True}},
            **overrides,
        )
        db_session.add(external_library)
        await db_session.flush()
        return external_library, library

    return _inner


def _result(provider_key: str = "t1", kind: str = "track", title: str = "Result") -> dict:
    return {
        "kind": kind,
        "provider_key": provider_key,
        "title": title,
        "subtitle": "Sub",
        "image_url": None,
        "external_url": "https://provider.invalid/x",
    }


@pytest.mark.asyncio
async def test_provider_search_requires_auth(client):
    response = client.get("/api/v1/search/providers", params={"q": "anything"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_provider_search_fans_out_to_own_libraries(
    client, db_session, regular_user, other_user, auth_headers, _make_search_library
):
    await _make_search_library(regular_user, config={"results": [_result("a1")]})
    await _make_search_library(regular_user, config={"results": [_result("b1")]})
    # Another user's library must never be searched.
    await _make_search_library(other_user, config={"results": [_result("other")]})

    response = client.get(
        "/api/v1/search/providers",
        params={"q": "test"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["query"] == "test"
    assert len(data["providers"]) == 2
    keys = {r["provider_key"] for g in data["providers"] for r in g["results"]}
    assert keys == {"a1", "b1"}
    for group in data["providers"]:
        assert group["error"] is None
        assert group["provider_type"] == "fake-search"


@pytest.mark.asyncio
async def test_provider_search_skips_adapters_without_search(
    client, db_session, regular_user, auth_headers, _make_search_library
):
    await _make_search_library(regular_user, provider_type="fake-nosearch")
    response = client.get(
        "/api/v1/search/providers",
        params={"q": "test"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    assert response.json()["providers"] == []


@pytest.mark.asyncio
async def test_provider_search_isolates_failures(client, db_session, regular_user, auth_headers, _make_search_library):
    bad, _ = await _make_search_library(regular_user, config={"search_behavior": "fail"})
    good, _ = await _make_search_library(regular_user, config={"results": [_result("ok1")]})

    response = client.get(
        "/api/v1/search/providers",
        params={"q": "test"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    groups = {g["external_library_id"]: g for g in response.json()["providers"]}
    assert groups[str(bad.id)]["error"] == "provider exploded"
    assert groups[str(bad.id)]["results"] == []
    assert groups[str(good.id)]["error"] is None
    assert groups[str(good.id)]["results"][0]["provider_key"] == "ok1"


@pytest.mark.asyncio
async def test_provider_search_isolates_timeouts(
    client, db_session, regular_user, auth_headers, _make_search_library, monkeypatch
):
    monkeypatch.setattr("songhive.api.routes.search._PROVIDER_SEARCH_TIMEOUT", 0.05)
    slow, _ = await _make_search_library(regular_user, config={"search_behavior": "slow", "delay": 2.0})
    fast, _ = await _make_search_library(regular_user, config={"results": [_result("fast1")]})

    response = client.get(
        "/api/v1/search/providers",
        params={"q": "test"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    groups = {g["external_library_id"]: g for g in response.json()["providers"]}
    assert groups[str(slow.id)]["error"] == "provider search timed out"
    assert groups[str(fast.id)]["results"][0]["provider_key"] == "fast1"


@pytest.mark.asyncio
async def test_provider_search_url_query_gets_wider_timeout(
    client, db_session, regular_user, auth_headers, _make_search_library, monkeypatch
):
    """URL lookups get the wider single-entity budget, not the text timeout."""
    monkeypatch.setattr("songhive.api.routes.search._PROVIDER_SEARCH_TIMEOUT", 0.05)
    monkeypatch.setattr("songhive.api.routes.search._PROVIDER_SEARCH_URL_TIMEOUT", 5.0)
    await _make_search_library(regular_user, config={"search_behavior": "slow", "delay": 0.5})

    response = client.get(
        "/api/v1/search/providers",
        params={"q": "https://provider.invalid/watch?v=x"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    group = response.json()["providers"][0]
    assert group["error"] is None


@pytest.mark.asyncio
async def test_provider_search_persists_nothing(client, db_session, regular_user, auth_headers, _make_search_library):
    await _make_search_library(regular_user, config={"results": [_result("ghost")]})
    response = client.get(
        "/api/v1/search/providers",
        params={"q": "ghost"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    tracks = await db_session.scalar(select(func.count()).select_from(Track))
    items = await db_session.scalar(select(func.count()).select_from(ExternalItem))
    assert tracks == 0
    assert items == 0


@pytest.mark.asyncio
async def test_import_materializes_track_as_saved(client, db_session, regular_user, auth_headers, _make_search_library):
    external_library, library = await _make_search_library(regular_user)

    response = client.post(
        f"/api/v1/external-libraries/{external_library.id}/import",
        json={"kind": "track", "provider_key": "imp1"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "track"
    assert body["provider_key"] == "imp1"
    assert FakeSearchAdapter.fetch_calls == ["imp1"]

    track = await db_session.get(Track, body["entity_id"])
    assert track is not None
    assert track.title == "Fetched imp1"

    external_item = (
        await db_session.execute(
            select(ExternalItem).where(
                ExternalItem.external_library_id == str(external_library.id),
                ExternalItem.kind == "track",
                ExternalItem.provider_key == "imp1",
            )
        )
    ).scalar_one()
    assert external_item.membership == "saved"
    assert external_item.track_id == str(track.id)

    membership = (
        await db_session.execute(
            select(LibraryTrack).where(
                LibraryTrack.library_id == str(library.id),
                LibraryTrack.track_id == str(track.id),
            )
        )
    ).scalar_one_or_none()
    assert membership is not None


@pytest.mark.asyncio
async def test_import_uses_catalog_without_provider_fetch(
    client, db_session, regular_user, auth_headers, _make_search_library
):
    external_library, library = await _make_search_library(regular_user)
    db_session.add(
        ProviderCatalogEntry(
            provider_type="fake-search",
            kind="track",
            provider_key="cached1",
            payload={"provider_key": "cached1", "title": "Cached Title", "artist": "Cat Artist"},
        )
    )
    await db_session.flush()

    response = client.post(
        f"/api/v1/external-libraries/{external_library.id}/import",
        json={"kind": "track", "provider_key": "cached1"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    assert FakeSearchAdapter.fetch_calls == []

    track = await db_session.get(Track, response.json()["entity_id"])
    assert track.title == "Cached Title"


@pytest.mark.asyncio
async def test_import_requires_auth(client, db_session, regular_user, _make_search_library):
    external_library, _ = await _make_search_library(regular_user)
    response = client.post(
        f"/api/v1/external-libraries/{external_library.id}/import",
        json={"kind": "track", "provider_key": "x"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_import_forbidden_for_other_users_library(
    client, db_session, regular_user, other_user, auth_headers, _make_search_library
):
    external_library, _ = await _make_search_library(other_user)
    response = client.post(
        f"/api/v1/external-libraries/{external_library.id}/import",
        json={"kind": "track", "provider_key": "x"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_import_unknown_provider_key_is_404(client, db_session, regular_user, auth_headers, _make_search_library):
    class _MissingAdapter(FakeSearchAdapter):
        provider_type = "fake-search"

        async def fetch_entity_metadata(self, config, kind, provider_key):
            return None

    register_external_adapter("fake-missing", _MissingAdapter)
    try:
        library = Library(name="Missing Lib", owner_id=str(regular_user.id), visibility="private")
        db_session.add(library)
        await db_session.flush()
        external_library = ExternalLibrary(
            library_id=str(library.id),
            provider_type="fake-missing",
            config=secrets.encrypt_json({}),
            enabled=True,
            created_by_id=str(regular_user.id),
            capabilities={"limits": {"entity_import": True, "search": True}},
        )
        db_session.add(external_library)
        await db_session.flush()

        response = client.post(
            f"/api/v1/external-libraries/{external_library.id}/import",
            json={"kind": "track", "provider_key": "nope"},
            headers=auth_headers(regular_user),
        )
        assert response.status_code == 404
    finally:
        unregister_external_adapter("fake-missing")


@pytest.mark.asyncio
async def test_local_search_unaffected_by_providers(
    client, db_session, regular_user, auth_headers, _make_search_library
):
    """Local search stays fast/local even with a failing provider configured."""
    await _make_search_library(regular_user, config={"search_behavior": "slow"})
    response = client.get(
        "/api/v1/search/",
        params={"q": "test"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    assert "providers" not in response.json()
