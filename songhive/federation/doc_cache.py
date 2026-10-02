"""
Document cache for ActivityPub dereference reads.

When a local post is boosted across the Fediverse, hundreds of remote
instances dereference the same URLs at once — actor documents, WebFinger
records, objects, collections. Rendering each fetch from the database turns a
burst of identical GETs into a burst of identical queries, and under enough
concurrency the database saturates (``sorry, too many clients already``).

The primitive lives in :mod:`pubby.cache` — :class:`pubby.cache.DocumentCache`
provides TTL caching with single-flight request coalescing (concurrent fetches
of one key share a single render), short-TTL miss caching, stale-if-error
serving and generation-guarded invalidation. This module keeps the shared
process-wide instance and the Songhive-specific key schema:

- keys are tuples, e.g. ``("obj", "alice", "abc123")`` or
  ``("obj", "alice", "abc123", "followers")``, so invalidation matches
  element-wise — ``("obj", "alice")`` cannot collide with ``("obj", "al")``
  and an ``x:followers`` object id cannot poison ``x``'s followers key the
  way flat ``a:b:c`` strings could;
- the ``invalidate_*`` helpers express the domain rules (which namespaces a
  user/activity/track mutation affects) on top of
  :meth:`DocumentCache.invalidate`/:meth:`invalidate_prefix`/
  :meth:`invalidate_segment`.

Mutation timing: invalidating before a transaction commits lets a
concurrent render re-cache the pre-commit row. The ``session=`` keyword on
every helper defers the invalidation to SQLAlchemy's ``after_commit``
event (via ``session.info``), so the cache drop happens only once the
mutation is durable. Callers that own no session (tasks after commit, the
pubby handler path) invalidate immediately.

Cross-process invalidation is not attempted with the default in-memory
store — the TTL bounds staleness. A shared ``RedisDocumentStore`` passed to
:func:`document_cache` shares entries and invalidations across processes.
"""

import logging
import threading
from typing import Awaitable, Callable, Iterable, Optional, TypeVar

from pubby.cache import DocumentCache
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Cached misses (404s) always expire quickly: object ids are assigned at
# publish time, so a cached "not found" must not outlive the document by long.
_MISS_TTL_CAP = 15.0

_cache: Optional[DocumentCache] = None
_cache_lock = threading.Lock()


def document_cache(**kwargs) -> DocumentCache:
    """
    Return the process-wide :class:`pubby.cache.DocumentCache`.

    Keyword arguments (``default_ttl``, ``store``, ``max_entries``,
    ``stale_factor``, …) only apply when the instance is created on first
    use — later calls return the shared instance unchanged.
    """
    global _cache
    if _cache is None:
        with _cache_lock:
            if _cache is None:
                kwargs.setdefault("miss_ttl", _MISS_TTL_CAP)
                _cache = DocumentCache(**kwargs)
    return _cache


async def get_or_render(
    key: tuple,
    ttl: float,
    render: Callable[[], Awaitable[T]],
    *,
    miss_ttl: Optional[float] = None,
) -> T:
    """
    Return the cached document for ``key`` or render it once.

    ``render`` is only invoked when no fresh entry exists; concurrent callers
    share a single in-flight render. A rendered ``None`` is treated as a miss
    and cached for ``miss_ttl`` seconds (default ``min(ttl, 15)``) so repeated
    fetches of deleted/unknown objects also collapse. Exceptions propagate to
    every waiter and are never cached; when a slightly expired entry exists
    it is served instead of the error (stale-if-error). ``ttl <= 0`` disables
    caching entirely (renders still coalesce while in flight).
    """
    return await document_cache().get_or_render_async(key, render, ttl=ttl, miss_ttl=miss_ttl)


def ttl_remaining(key: tuple) -> float:
    """
    Seconds of freshness left on the cached entry for ``key`` (``0`` when
    absent or expired).

    Routes pass this to :func:`songhive.api._common.document_response` so a
    hit advertises its *remaining* lifetime rather than restarting the full
    TTL clock at every layer.
    """
    return document_cache().ttl_remaining(key)


def invalidate_prefix(*prefixes: tuple, session: Optional[AsyncSession] = None) -> None:
    """Drop keys whose leading elements equal any of ``prefixes``."""
    normalized = [tuple(p) for p in prefixes]
    _run_or_defer(session, lambda: document_cache().invalidate_prefix(*normalized))


# ---------------------------------------------------------------------------
# After-commit deferral
# ---------------------------------------------------------------------------
#
# Invalidating before a transaction commits lets a concurrent render
# re-cache the pre-mutation row. ``session=`` on the helpers below queues
# the invalidation in ``session.info`` and drains it from SQLAlchemy's
# ``after_commit`` event, so entries drop only once the mutation is
# durable. The listener ignores nested-transaction (SAVEPOINT) commits —
# they release before the outer transaction commits — and drains only
# after the outermost commit. Callbacks queued on a session whose
# transaction rolls back stay queued: a later commit still drains them
# (over-invalidation is safe), and a session that never commits drops
# them with the session.

_PENDING_KEY = "songhive.federation.doc_cache.pending"
_LISTENER_KEY = "songhive.federation.doc_cache.listener"


def _drain_deferred(session: Session) -> None:
    # ``after_commit`` also fires when a nested transaction (SAVEPOINT) is
    # released — draining there would drop pending invalidations while the
    # outer transaction is still open, letting a concurrent render re-cache
    # the pre-commit row and survive the real commit. Only the outermost
    # commit drains.
    if session.in_nested_transaction():
        return
    for fn in session.info.pop(_PENDING_KEY, []):
        try:
            fn()
        except Exception:
            logger.exception("Deferred document-cache invalidation failed")


def _defer(session: AsyncSession, fn: Callable[[], None]) -> None:
    """Run ``fn`` after ``session``'s outermost transaction commits."""
    info = session.info
    info.setdefault(_PENDING_KEY, []).append(fn)
    if not info.get(_LISTENER_KEY):
        info[_LISTENER_KEY] = True
        event.listen(session.sync_session, "after_commit", _drain_deferred)


def _run_or_defer(session: Optional[AsyncSession], fn: Callable[[], None]) -> None:
    if session is None:
        fn()
    else:
        _defer(session, fn)


# ---------------------------------------------------------------------------
# Songhive key schema + invalidation rules
# ---------------------------------------------------------------------------


def invalidate_actor(username: str, *, session: Optional[AsyncSession] = None) -> None:
    """Drop the cached actor document, WebFinger records and all user-keyed
    documents (objects, quote authorizations, collections, library pages)."""

    def _do() -> None:
        cache = document_cache()
        cache.invalidate(("actor", username), ("ulibfol", username))
        cache.invalidate_prefix(
            ("webfinger",),
            ("coll", username),
            ("obj", username),
            ("qa", username),
            ("ulib", username),
        )

    _run_or_defer(session, _do)


def invalidate_object(
    username: str,
    object_id: Optional[str],
    *,
    session: Optional[AsyncSession] = None,
) -> None:
    """Drop the cached object document and its followers collection."""
    if not object_id:
        return
    _run_or_defer(
        session,
        lambda: document_cache().invalidate_prefix(("obj", username, object_id)),
    )


def invalidate_keys(*keys: tuple, session: Optional[AsyncSession] = None) -> None:
    """Drop the given cache keys exactly."""
    normalized = [tuple(k) if isinstance(k, (tuple, list)) else (k,) for k in keys]
    _run_or_defer(session, lambda: document_cache().invalidate(*normalized))


def invalidate_segment(*segments: Optional[str], session: Optional[AsyncSession] = None) -> None:
    """Drop keys containing any of ``segments`` as an element.

    For call sites that know a document's id but not which key namespaces
    embed it — e.g. an object id lives under ``("obj", user, id)`` and
    ``("obj", user, id, "followers")``. Element-wise matching avoids the
    false prefix hits flat string keys produce (``abc`` in ``abc2``).
    """
    wanted = [s for s in segments if s]
    if not wanted:
        return
    _run_or_defer(session, lambda: document_cache().invalidate_segment(*wanted))


def invalidate_activity(
    activity_id: Optional[str],
    object_ids: Iterable[Optional[str]] = (),
    *,
    session: Optional[AsyncSession] = None,
) -> None:
    """Drop the cached ``/activities/{id}`` document and its object docs."""

    def _do() -> None:
        cache = document_cache()
        if activity_id:
            cache.invalidate(("act", activity_id))
        cache.invalidate_segment(*[o for o in object_ids if o])

    _run_or_defer(session, _do)


def invalidate_track(
    track_id: Optional[str],
    object_id: Optional[str] = None,
    *,
    session: Optional[AsyncSession] = None,
) -> None:
    """Drop cached documents that may embed a track.

    Besides the track's own object/page documents this clears library pages
    (``("lib", …)``/``("ulib", …)``): a track's visibility, metadata or
    deletion changes their contents and membership is not indexed by the
    cache keys. The dereferenced object document lives under
    ``("obj", owner, object_id)`` — segment-matching the object id drops it
    (and its followers collection) without needing the owner's username at
    the mutation site.
    """

    def _do() -> None:
        cache = document_cache()
        if track_id:
            cache.invalidate(("trackpage", track_id))
        if object_id:
            cache.invalidate_segment(object_id)
        cache.invalidate_prefix(("lib",), ("ulib",))

    _run_or_defer(session, _do)


def clear(*, session: Optional[AsyncSession] = None) -> None:
    """Drop every cached entry (tests, config reload, user teardown).

    Suspending, deactivating or deleting a user affects documents that are
    not keyed by username — ``("trackpage", id)``, ``("act", id)``,
    ``("lib", …)``, ``("artist", …)``, ``("album", …)`` — so those rare,
    privacy-relevant events drop the whole cache rather than enumerate
    affected keys. ``session=`` defers the clear to ``after_commit``.
    """
    if session is None:
        if _cache is not None:
            _cache.clear()
    else:
        _run_or_defer(session, lambda: document_cache().clear())
