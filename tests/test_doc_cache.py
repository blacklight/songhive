"""
Tests for the ActivityPub dereference document cache (federation.doc_cache).

The cache mechanics (single-flight, TTL, stale-if-error, bounded store)
live in ``pubby.cache`` and are covered by pubby's own suite; these tests
cover Songhive's shared instance, the tuple key schema and the
``session=`` after-commit invalidation deferral.
"""

import asyncio

import pytest

from songhive.federation import doc_cache


async def test_cache_hit_skips_render():
    calls = 0

    async def render():
        nonlocal calls
        calls += 1
        return {"doc": 1}

    assert await doc_cache.get_or_render(("k",), 60, render) == {"doc": 1}
    assert await doc_cache.get_or_render(("k",), 60, render) == {"doc": 1}
    assert calls == 1


async def test_concurrent_calls_share_one_render():
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def render():
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return {"doc": 2}

    first = asyncio.ensure_future(doc_cache.get_or_render(("k",), 60, render))
    await started.wait()
    second = asyncio.ensure_future(doc_cache.get_or_render(("k",), 60, render))
    third = asyncio.ensure_future(doc_cache.get_or_render(("k",), 60, render))
    release.set()

    assert await first == {"doc": 2}
    assert await second == {"doc": 2}
    assert await third == {"doc": 2}
    assert calls == 1


async def test_entry_expires_after_ttl():
    calls = 0

    async def render():
        nonlocal calls
        calls += 1
        return calls

    assert await doc_cache.get_or_render(("k",), 0.01, render) == 1
    await asyncio.sleep(0.02)
    assert await doc_cache.get_or_render(("k",), 0.01, render) == 2


async def test_miss_is_cached_briefly():
    calls = 0

    async def render():
        nonlocal calls
        calls += 1
        return None

    assert await doc_cache.get_or_render(("k",), 60, render) is None
    assert await doc_cache.get_or_render(("k",), 60, render) is None
    assert calls == 1


async def test_miss_ttl_capped():
    calls = 0

    async def render():
        nonlocal calls
        calls += 1
        return None

    # A huge positive TTL must not extend the miss TTL past the cap.
    assert await doc_cache.get_or_render(("k",), 3600, render, miss_ttl=0.01) is None
    await asyncio.sleep(0.02)
    assert await doc_cache.get_or_render(("k",), 3600, render, miss_ttl=0.01) is None
    assert calls == 2


async def test_exceptions_propagate_and_are_not_cached():
    calls = 0

    async def render():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("boom")
        return {"doc": 3}

    with pytest.raises(RuntimeError):
        await doc_cache.get_or_render(("k",), 60, render)
    assert await doc_cache.get_or_render(("k",), 60, render) == {"doc": 3}
    assert calls == 2


async def test_concurrent_exception_propagates_to_all_waiters():
    started = asyncio.Event()
    release = asyncio.Event()

    async def render():
        started.set()
        await release.wait()
        raise RuntimeError("boom")

    first = asyncio.ensure_future(doc_cache.get_or_render(("k",), 60, render))
    await started.wait()
    second = asyncio.ensure_future(doc_cache.get_or_render(("k",), 60, render))
    release.set()

    with pytest.raises(RuntimeError):
        await first
    with pytest.raises(RuntimeError):
        await second


async def test_cancelled_waiter_does_not_cancel_render():
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def render():
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return {"doc": 4}

    first = asyncio.ensure_future(doc_cache.get_or_render(("k",), 60, render))
    await started.wait()
    second = asyncio.ensure_future(doc_cache.get_or_render(("k",), 60, render))
    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second

    release.set()
    assert await first == {"doc": 4}
    assert calls == 1
    # The completed render is cached for later callers.
    assert await doc_cache.get_or_render(("k",), 60, render) == {"doc": 4}


async def test_zero_ttl_disables_caching_but_still_coalesces():
    calls = 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def render():
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return calls

    first = asyncio.ensure_future(doc_cache.get_or_render(("k",), 0, render))
    await started.wait()
    second = asyncio.ensure_future(doc_cache.get_or_render(("k",), 0, render))
    release.set()
    assert await first == 1
    assert await second == 1

    assert await doc_cache.get_or_render(("k",), 0, render) == 2
    assert calls == 2


async def test_invalidate_keys_drops_exact_entries():
    async def render():
        return {"doc": 5}

    await doc_cache.get_or_render(("obj", "u", "oid"), 60, render)
    await doc_cache.get_or_render(("obj", "u", "oid2"), 60, render)
    doc_cache.invalidate_keys(("obj", "u", "oid"))

    calls = 0

    async def rerender():
        nonlocal calls
        calls += 1
        return {"doc": 6}

    assert await doc_cache.get_or_render(("obj", "u", "oid"), 60, rerender) == {"doc": 6}
    assert await doc_cache.get_or_render(("obj", "u", "oid2"), 60, rerender) == {"doc": 5}
    assert calls == 1


async def test_invalidate_prefix_drops_matching_entries():
    async def render():
        return {"doc": 7}

    await doc_cache.get_or_render(("obj", "u", "oid"), 60, render)
    await doc_cache.get_or_render(("obj", "u", "oid", "followers"), 60, render)
    await doc_cache.get_or_render(("obj", "v", "oid"), 60, render)
    doc_cache.document_cache().invalidate_prefix(("obj", "u"))

    calls = 0

    async def rerender():
        nonlocal calls
        calls += 1
        return {"doc": 8}

    assert await doc_cache.get_or_render(("obj", "u", "oid"), 60, rerender) == {"doc": 8}
    assert await doc_cache.get_or_render(("obj", "u", "oid", "followers"), 60, rerender) == {"doc": 8}
    assert await doc_cache.get_or_render(("obj", "v", "oid"), 60, rerender) == {"doc": 7}
    assert calls == 2


async def test_prefix_invalidations_are_element_wise():
    """``("obj", "u")`` must not match ``("obj", "u2", ...)`` keys."""

    async def render():
        return {"doc": 1}

    await doc_cache.get_or_render(("obj", "u", "oid"), 60, render)
    await doc_cache.get_or_render(("obj", "u2", "oid"), 60, render)
    doc_cache.invalidate_object("u", "oid")

    calls = 0

    async def rerender():
        nonlocal calls
        calls += 1
        return {"doc": 2}

    assert await doc_cache.get_or_render(("obj", "u", "oid"), 60, rerender) == {"doc": 2}
    # Same string prefix but a different tuple element — must survive.
    assert await doc_cache.get_or_render(("obj", "u2", "oid"), 60, rerender) == {"doc": 1}
    assert calls == 1


async def test_object_id_with_colon_does_not_poison_followers_key():
    """``/objects/x:followers`` must not collide with ``/objects/x/followers``."""

    async def render():
        return {"doc": 3}

    # A request for object id "oid:followers" caches a miss under
    # ("obj", "u", "oid:followers"); the followers collection of "oid"
    # lives at ("obj", "u", "oid", "followers") — distinct keys.
    assert await doc_cache.get_or_render(("obj", "u", "oid:followers"), 60, lambda: None) is None
    await doc_cache.get_or_render(("obj", "u", "oid", "followers"), 60, render)

    doc_cache.invalidate_object("u", "oid")

    calls = 0

    async def rerender():
        nonlocal calls
        calls += 1
        return {"doc": 4}

    assert await doc_cache.get_or_render(("obj", "u", "oid", "followers"), 60, rerender) == {"doc": 4}
    assert calls == 1


async def test_invalidate_segment_drops_segment_matches():
    async def render():
        return {"doc": 9}

    await doc_cache.get_or_render(("obj", "u", "oid"), 60, render)
    await doc_cache.get_or_render(("obj", "u", "oid", "followers"), 60, render)
    await doc_cache.get_or_render(("obj", "u", "oid2"), 60, render)
    doc_cache.invalidate_segment("oid")

    calls = 0

    async def rerender():
        nonlocal calls
        calls += 1
        return {"doc": 10}

    assert await doc_cache.get_or_render(("obj", "u", "oid"), 60, rerender) == {"doc": 10}
    assert await doc_cache.get_or_render(("obj", "u", "oid", "followers"), 60, rerender) == {"doc": 10}
    # ``oid2`` is a different element — substring similarity must not match.
    assert await doc_cache.get_or_render(("obj", "u", "oid2"), 60, rerender) == {"doc": 9}
    assert calls == 2


async def test_invalidation_during_render_prevents_caching():
    """A render in flight when an invalidation lands must not re-cache."""
    started = asyncio.Event()
    release = asyncio.Event()

    async def render():
        started.set()
        await release.wait()
        return {"doc": "stale"}

    first = asyncio.ensure_future(doc_cache.get_or_render(("k",), 60, render))
    await started.wait()
    doc_cache.invalidate_keys(("k",))
    release.set()
    assert await first == {"doc": "stale"}  # the waiter still gets the result

    calls = 0

    async def rerender():
        nonlocal calls
        calls += 1
        return {"doc": "fresh"}

    # …but it was not stored.
    assert await doc_cache.get_or_render(("k",), 60, rerender) == {"doc": "fresh"}
    assert calls == 1


async def test_invalidate_actor_covers_user_key_namespaces():
    """invalidate_actor drops every user-scoped key family."""
    doc = {"doc": 1}

    async def render():
        return doc

    for key in (
        ("actor", "fab"),
        ("webfinger", "fab@example.com"),
        ("coll", "fab", "outbox"),
        ("obj", "fab", "oid"),
        ("obj", "fab", "oid", "followers"),
        ("qa", "fab", "auth"),
        ("ulib", "fab", 1),
        ("ulibfol", "fab"),
        ("actor", "fabio"),  # same-prefix user must survive
        ("coll", "gio", "outbox"),
        ("nodeinfo",),
    ):
        await doc_cache.get_or_render(key, 60, render)

    doc_cache.invalidate_actor("fab")

    calls = 0

    async def rerender():
        nonlocal calls
        calls += 1
        return {"doc": 2}

    for key in (
        ("actor", "fab"),
        ("webfinger", "fab@example.com"),
        ("coll", "fab", "outbox"),
        ("obj", "fab", "oid"),
        ("obj", "fab", "oid", "followers"),
        ("qa", "fab", "auth"),
        ("ulib", "fab", 1),
        ("ulibfol", "fab"),
    ):
        assert await doc_cache.get_or_render(key, 60, rerender) == {"doc": 2}
    assert calls == 8

    # Unrelated entries survive.
    for key in (("actor", "fabio"), ("coll", "gio", "outbox"), ("nodeinfo",)):
        assert await doc_cache.get_or_render(key, 60, rerender) == {"doc": 1}
    assert calls == 8


async def test_deferred_invalidation_waits_for_commit(db_session):
    """session= defers the drop until after_commit — a render started
    between the mutation and the commit must still hit the old entry, and
    the entry must be gone once the transaction commits."""

    async def render():
        return {"doc": "old"}

    await doc_cache.get_or_render(("obj", "u", "x"), 60, render)
    doc_cache.invalidate_object("u", "x", session=db_session)

    calls = 0

    async def rerender():
        nonlocal calls
        calls += 1
        return {"doc": "new"}

    # Not yet committed: the old entry still serves.
    assert await doc_cache.get_or_render(("obj", "u", "x"), 60, rerender) == {"doc": "old"}
    assert calls == 0

    await db_session.commit()
    assert await doc_cache.get_or_render(("obj", "u", "x"), 60, rerender) == {"doc": "new"}
    assert calls == 1


async def test_deferred_invalidation_survives_rollback(db_session):
    """A rolled-back session keeps its pending callbacks queued; the entry
    stays cached until a later commit drains them."""

    async def render():
        return {"doc": "old"}

    await doc_cache.get_or_render(("obj", "u", "y"), 60, render)
    doc_cache.invalidate_object("u", "y", session=db_session)
    await db_session.rollback()

    calls = 0

    async def rerender():
        nonlocal calls
        calls += 1
        return {"doc": "new"}

    assert await doc_cache.get_or_render(("obj", "u", "y"), 60, rerender) == {"doc": "old"}
    assert calls == 0

    # A later commit on the same session drains the queue.
    await db_session.commit()
    assert await doc_cache.get_or_render(("obj", "u", "y"), 60, rerender) == {"doc": "new"}
    assert calls == 1


async def test_deferred_invalidation_ignores_savepoint_release(db_session):
    """A successful SAVEPOINT release must not drain pending invalidations.

    SQLAlchemy fires ``after_commit`` for a nested-transaction commit too;
    draining there would drop the queued invalidation while the outer
    transaction is still open, letting a concurrent render re-cache the
    pre-commit row and survive the real commit."""

    async def render():
        return {"doc": "old"}

    await doc_cache.get_or_render(("obj", "u", "sp"), 60, render)
    doc_cache.invalidate_object("u", "sp", session=db_session)

    calls = 0

    async def rerender():
        nonlocal calls
        calls += 1
        return {"doc": "new"}

    nested = await db_session.begin_nested()
    await nested.commit()  # SAVEPOINT release — not an outer commit

    # The pending invalidation must still be queued: the old entry serves.
    assert await doc_cache.get_or_render(("obj", "u", "sp"), 60, rerender) == {"doc": "old"}
    assert calls == 0

    await db_session.commit()
    assert await doc_cache.get_or_render(("obj", "u", "sp"), 60, rerender) == {"doc": "new"}
    assert calls == 1


async def test_deferred_invalidation_survives_savepoint_rollback(db_session):
    """A rolled-back SAVEPOINT keeps the pending invalidation queued; the
    outer commit drains it (conservative over-invalidation)."""

    async def render():
        return {"doc": "old"}

    await doc_cache.get_or_render(("obj", "u", "spr"), 60, render)
    doc_cache.invalidate_object("u", "spr", session=db_session)

    calls = 0

    async def rerender():
        nonlocal calls
        calls += 1
        return {"doc": "new"}

    nested = await db_session.begin_nested()
    await nested.rollback()

    assert await doc_cache.get_or_render(("obj", "u", "spr"), 60, rerender) == {"doc": "old"}
    assert calls == 0

    await db_session.commit()
    assert await doc_cache.get_or_render(("obj", "u", "spr"), 60, rerender) == {"doc": "new"}
    assert calls == 1
