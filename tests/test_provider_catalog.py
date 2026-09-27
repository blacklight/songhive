"""Tests for the instance-wide provider catalog cache."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from songhive.models.provider_catalog import ProviderCatalogEntry
from songhive.services import provider_catalog


@pytest.mark.asyncio
async def test_upsert_inserts_new_entries(db_session):
    """New payloads create catalog rows with payload + fetched_at."""
    inserted = await provider_catalog.upsert_catalog_entries(
        db_session,
        "tidal",
        "track",
        [
            {"id": 1, "title": "One"},
            {"id": 2, "title": "Two"},
        ],
        write_once=True,
    )
    assert inserted == 2

    entries = (await db_session.execute(select(ProviderCatalogEntry))).scalars().all()
    assert len(entries) == 2
    assert {e.provider_key for e in entries} == {"1", "2"}
    assert all(e.fetched_at is not None for e in entries)
    assert all(e.expires_at is None for e in entries)


@pytest.mark.asyncio
async def test_upsert_write_once_never_rewrites_payload(db_session):
    """write_once=True leaves existing payloads untouched."""
    await provider_catalog.upsert_catalog_entries(
        db_session, "tidal", "track", [{"id": 1, "title": "One"}], write_once=True
    )
    inserted = await provider_catalog.upsert_catalog_entries(
        db_session, "tidal", "track", [{"id": 1, "title": "REWRITTEN"}], write_once=True
    )
    assert inserted == 0
    entry = (await db_session.execute(select(ProviderCatalogEntry))).scalar_one()
    assert entry.payload["title"] == "One"


@pytest.mark.asyncio
async def test_upsert_without_write_once_rewrites_payload(db_session):
    """Default (expiring) upserts refresh the payload and clear unavailable."""
    await provider_catalog.upsert_catalog_entries(
        db_session, "tidal", "artist", [{"id": 9, "name": "Old"}], ttl_seconds=60
    )
    await provider_catalog.mark_unavailable(db_session, "tidal", "artist", "9")

    inserted = await provider_catalog.upsert_catalog_entries(
        db_session, "tidal", "artist", [{"id": 9, "name": "New"}], ttl_seconds=60
    )
    assert inserted == 0
    entry = (await db_session.execute(select(ProviderCatalogEntry))).scalar_one()
    assert entry.payload["name"] == "New"
    assert entry.unavailable_at is None
    assert entry.expires_at is not None


@pytest.mark.asyncio
async def test_get_entry_returns_none_for_expired(db_session):
    """Expired entries are hidden unless allow_expired is set."""
    await provider_catalog.upsert_catalog_entries(
        db_session, "tidal", "album", [{"id": 5, "title": "A"}], ttl_seconds=-1
    )
    assert await provider_catalog.get_catalog_entry(db_session, "tidal", "album", "5") is None
    expired = await provider_catalog.get_catalog_entry(db_session, "tidal", "album", "5", allow_expired=True)
    assert expired is not None


@pytest.mark.asyncio
async def test_unavailable_entries_hidden_and_clearable(db_session):
    """unavailable_at hides entries; clear_unavailable revives them."""
    await provider_catalog.upsert_catalog_entries(
        db_session, "tidal", "track", [{"id": 7, "title": "Gone"}], write_once=True
    )
    await provider_catalog.mark_unavailable(db_session, "tidal", "track", "7")
    assert await provider_catalog.get_catalog_entry(db_session, "tidal", "track", "7") is None

    await provider_catalog.clear_unavailable(db_session, "tidal", "track", "7")
    assert await provider_catalog.get_catalog_entry(db_session, "tidal", "track", "7") is not None


@pytest.mark.asyncio
async def test_upsert_contents(db_session):
    """Container contents are stored with etag + timestamp."""
    entry = await provider_catalog.upsert_catalog_contents(
        db_session,
        "tidal",
        "playlist",
        "uuid-1",
        [{"id": "1", "position": 0}, {"id": "2", "position": 1}],
        etag="e1",
    )
    assert entry.contents == [{"id": "1", "position": 0}, {"id": "2", "position": 1}]
    assert entry.contents_etag == "e1"
    assert entry.contents_fetched_at is not None


@pytest.mark.asyncio
async def test_refresh_entry_rewrites_write_once(db_session):
    """The admin escape hatch rewrites even immutable payloads."""
    await provider_catalog.upsert_catalog_entries(
        db_session, "tidal", "track", [{"id": 3, "title": "Before"}], write_once=True
    )
    entry = await provider_catalog.refresh_catalog_entry(db_session, "tidal", "track", "3", {"id": 3, "title": "After"})
    assert entry.payload["title"] == "After"


@pytest.mark.asyncio
async def test_expired_entries_sweep(db_session):
    """expired_catalog_entries returns only past-TTL entries."""
    await provider_catalog.upsert_catalog_entries(
        db_session, "tidal", "album", [{"id": 1, "title": "old"}], ttl_seconds=-10
    )
    await provider_catalog.upsert_catalog_entries(
        db_session, "tidal", "album", [{"id": 2, "title": "fresh"}], ttl_seconds=3600
    )
    await provider_catalog.upsert_catalog_entries(
        db_session, "jellyfin", "album", [{"id": 3, "title": "other"}], ttl_seconds=-10
    )
    expired = await provider_catalog.expired_catalog_entries(db_session, "tidal")
    assert [e.provider_key for e in expired] == ["1"]


@pytest.mark.asyncio
async def test_get_catalog_entries_batch(db_session):
    """Batch lookup returns a key→entry map honoring expiry."""
    await provider_catalog.upsert_catalog_entries(
        db_session,
        "tidal",
        "track",
        [{"id": 1, "title": "a"}, {"id": 2, "title": "b"}],
        write_once=True,
    )
    found = await provider_catalog.get_catalog_entries(db_session, "tidal", "track", ["1", "2", "3"])
    assert set(found.keys()) == {"1", "2"}
