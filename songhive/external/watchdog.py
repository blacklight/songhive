"""
Filesystem watchdog for local external libraries.

Watches the union of configured local library roots and enqueues
incremental or scoped syncs based on observed filesystem events.
"""

import asyncio
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select
from watchfiles import Change, awatch

from ..config.constants import AUDIO_EXTENSIONS
from ..config.loader import load_config
from ..models.base import dispose_and_reset, get_session, init_db
from ..models.external_library import ExternalLibrary
from ..services.secrets import decrypt_json
from ._local import LocalExternalAdapter

logger = logging.getLogger(__name__)

# Quiet window: events within this interval are coalesced into one sync.
_WATCHDOG_QUIET_SECONDS = 5.0

# How long the supervisor waits between database polls for local libraries.
_WATCHDOG_POLL_INTERVAL = 5.0


def _utcnow() -> datetime:
    """Return a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def _is_audio_file(name: str) -> bool:
    """Return whether a file name looks like an audio track."""
    return name.lower().endswith(tuple(AUDIO_EXTENSIONS))


def _event_scope_parts(root: Path, abs_path: Path) -> list[str]:
    """
    Return the relative path parts that describe the narrowest sync scope
    for a delete event at ``abs_path``.

    * If the deleted path is (or appears to have been) a directory, the scope
      is the directory itself.
    * If the deleted path is an audio file, the scope is its parent directory.
    """
    try:
        rel = abs_path.relative_to(root)
    except ValueError:
        return []

    parts = rel.parts
    if not parts:
        return []

    # If the path still exists and is a directory, treat the directory itself
    # as the scope.
    if os.path.exists(abs_path) and os.path.isdir(abs_path):
        return list(parts)

    # Audio files are leaf tracks: scope to the parent directory so the sync
    # can mark them missing.
    if _is_audio_file(abs_path.name):
        return list(parts[:-1])

    # Non-audio paths are treated as directory deletions.  This keeps deleted
    # directories scoped to themselves; a deleted non-audio file triggers a
    # harmless narrow sync that finds no audio files.
    return list(parts)


def _deepest_common_ancestor(scope_parts: list[list[str]]) -> Optional[str]:
    """Return the deepest POSIX path common to all scope part lists."""
    if not scope_parts or any(not parts for parts in scope_parts):
        return None

    common = list(scope_parts[0])
    for parts in scope_parts[1:]:
        new_common = []
        for a, b in zip(common, parts):
            if a == b:
                new_common.append(a)
            else:
                break
        common = new_common
        if not common:
            return None

    return "/".join(common)


class _LibraryWatchState:
    """Accumulator for filesystem events bound to a single local library."""

    def __init__(self, library_id: str, root: Path) -> None:
        self.library_id = library_id
        self.root = root
        self.window_start: Optional[datetime] = None
        self.has_add_or_modify = False
        self.delete_scope_parts: list[list[str]] = []
        self.timer_handle: Optional[asyncio.Handle] = None

    def reset(self) -> None:
        self.window_start = None
        self.has_add_or_modify = False
        self.delete_scope_parts = []
        self.timer_handle = None


class _Watchdog:
    """Long-running supervisor for local external-library filesystem events."""

    def __init__(self, libraries: list[tuple[str, Path]]) -> None:
        self.libraries = dict(libraries)
        self.roots = sorted({root for _, root in libraries})
        self._by_root: dict[Path, list[str]] = defaultdict(list)
        for library_id, root in libraries:
            self._by_root[root].append(library_id)
        self._state: dict[str, _LibraryWatchState] = {
            library_id: _LibraryWatchState(library_id, root) for library_id, root in self.libraries.items()
        }

    @staticmethod
    def _enqueue(library_id: str, **kwargs: Any) -> None:
        """Enqueue ``sync_external_library_task`` for the given library."""
        from ..tasks.external_libraries import sync_external_library_task

        try:
            sync_external_library_task.delay(library_id, triggered_by="watchdog", **kwargs)
        except Exception:
            logger.exception("Failed to enqueue sync for %s with %s", library_id, kwargs)

    def _flush(self, library_id: str) -> None:
        """Flush the accumulated state for a library into the correct sync task(s)."""
        state = self._state.get(library_id)
        if state is None:
            return

        window_start = state.window_start
        scope = _deepest_common_ancestor(state.delete_scope_parts)

        if state.has_add_or_modify and state.delete_scope_parts:
            # Mixed events: reconcile deletes in the narrowest common scope and
            # run an incremental pass for add/modify events elsewhere.
            if scope:
                self._enqueue(library_id, scope=scope)
            else:
                self._enqueue(library_id)
            if window_start is not None:
                self._enqueue(library_id, since=window_start.isoformat())
        elif state.delete_scope_parts:
            if scope:
                self._enqueue(library_id, scope=scope)
            else:
                self._enqueue(library_id)
        elif state.has_add_or_modify and window_start is not None:
            self._enqueue(library_id, since=window_start.isoformat())

        state.reset()

    def _schedule_flush(self, library_id: str) -> None:
        """Schedule (or reschedule) the quiet-window flush for a library."""
        state = self._state[library_id]
        if state.timer_handle is not None:
            state.timer_handle.cancel()

        loop = asyncio.get_running_loop()

        def _on_timer() -> None:
            asyncio.create_task(self._flush_async(library_id))

        state.timer_handle = loop.call_later(_WATCHDOG_QUIET_SECONDS, _on_timer)

    async def _flush_async(self, library_id: str) -> None:
        """Async wrapper for ``_flush`` so it can be created as a task."""
        self._flush(library_id)

    def _classify_and_accumulate(self, library_id: str, change: Change, abs_path: Path) -> None:
        """Add a filesystem event to the per-library accumulator."""
        state = self._state[library_id]

        if state.window_start is None:
            # Start the window one second in the past so that filesystems with
            # whole-second mtime granularity are not truncated at the boundary.
            state.window_start = _utcnow() - timedelta(seconds=1)

        if change in (Change.added, Change.modified):
            state.has_add_or_modify = True
        elif change == Change.deleted:
            scope_parts = _event_scope_parts(state.root, abs_path)
            state.delete_scope_parts.append(scope_parts)

        self._schedule_flush(library_id)

    def _libraries_for_path(self, abs_path: Path) -> list[str]:
        """Return the library IDs whose root contains the given absolute path."""
        matched = []
        for root, library_ids in self._by_root.items():
            try:
                abs_path.relative_to(root)
                matched.extend(library_ids)
            except ValueError:
                continue
        return matched

    async def _handle_batch(self, changes: set[tuple[Change, str]]) -> None:
        """Process a single batch of watchfiles events."""
        for change, raw_path in changes:
            abs_path = Path(raw_path).expanduser().resolve()
            for library_id in self._libraries_for_path(abs_path):
                self._classify_and_accumulate(library_id, change, abs_path)

    def _flush_all_full(self) -> None:
        """Enqueue a full sync for every watched library."""
        for library_id in self.libraries:
            self._enqueue(library_id)

    async def run(self, stop_event: Optional[asyncio.Event] = None) -> None:
        """Run the watchdog until ``stop_event`` is set or an unrecoverable error occurs."""
        try:
            while True:
                if stop_event is not None and stop_event.is_set():
                    break
                try:
                    async for changes in awatch(*self.roots, recursive=True, stop_event=stop_event):
                        if stop_event is not None and stop_event.is_set():
                            break
                        await self._handle_batch(changes)
                    break
                except Exception as exc:
                    logger.exception("Watchdog encountered an error: %s", exc)
                    logger.warning("Persistent watch failures will enqueue full syncs for all libraries every second.")
                    self._flush_all_full()
                    await asyncio.sleep(1)
        finally:
            for state in self._state.values():
                if state.timer_handle is not None:
                    state.timer_handle.cancel()


async def _resolve_libraries() -> list[tuple[str, Path]]:
    """Load enabled local libraries from the database and resolve their roots."""
    config = load_config([])
    init_db(config.database.url)

    libraries: list[tuple[str, Path]] = []

    try:
        async with get_session() as session:
            result = await session.execute(
                select(ExternalLibrary).where(
                    ExternalLibrary.provider_type == "local",
                    ExternalLibrary.enabled.is_(True),
                    ExternalLibrary.sync_enabled.is_(True),
                )
            )
            rows = result.scalars().all()

            for library in rows:
                raw_config = library.config
                try:
                    decrypted = decrypt_json(raw_config) if isinstance(raw_config, str) else dict(raw_config or {})
                except Exception as exc:
                    logger.warning(
                        "Skipping local library %s: config decryption failed (%s). "
                        "Check that auth.secret_key matches the key used to encrypt the library config.",
                        library.id,
                        exc,
                    )
                    continue

                adapter = LocalExternalAdapter()
                try:
                    await adapter.validate_config(decrypted)
                except Exception:
                    logger.exception(
                        "Skipping local library %s: config validation failed",
                        library.id,
                    )
                    continue

                if adapter.resolved_root is None:
                    continue

                libraries.append((str(library.id), adapter.resolved_root))
    finally:
        await dispose_and_reset()

    return libraries


async def watch_external_libraries(stop_event: Optional[asyncio.Event] = None) -> None:
    """Entry point for the local external-library filesystem watchdog.

    Polls the database for enabled local libraries and starts watching once any
    are configured. The supervisor keeps polling while a watcher runs so it can
    pick up newly added or removed libraries and restart the watcher with the
    current set of roots.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if stop_event is None:
        stop_event = asyncio.Event()

    current_stop: Optional[asyncio.Event] = None
    current_task: Optional[asyncio.Task] = None
    last_libraries: Optional[list[tuple[str, Path]]] = None

    def _sorted_libraries(libraries: list[tuple[str, Path]]) -> list[tuple[str, Path]]:
        return sorted(libraries, key=lambda item: (item[0], str(item[1])))

    async def _stop_current() -> None:
        nonlocal current_stop, current_task
        if current_stop is not None:
            current_stop.set()
        if current_task is not None:
            try:
                await asyncio.wait_for(current_task, timeout=5.0)
            except asyncio.TimeoutError:
                current_task.cancel()
                try:
                    await current_task
                except asyncio.CancelledError:
                    pass
            except Exception:
                logger.exception("Watchdog task failed")
        current_stop = None
        current_task = None

    try:
        is_first_loop = True

        while not stop_event.is_set():
            try:
                libraries = await _resolve_libraries()
            except Exception:
                logger.exception("Failed to resolve local libraries; retrying...")
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=_WATCHDOG_POLL_INTERVAL)
                except asyncio.TimeoutError:
                    pass
                continue

            libraries = _sorted_libraries(libraries)

            if not libraries:
                if current_task is not None and not current_task.done():
                    if is_first_loop:
                        logger.info("No enabled local libraries to watch; stopping current watch and waiting...")
                    await _stop_current()
                elif last_libraries is None and is_first_loop:
                    logger.info("No enabled local libraries to watch; waiting...")
                last_libraries = None

                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=_WATCHDOG_POLL_INTERVAL)
                except asyncio.TimeoutError:
                    pass

                is_first_loop = False
                continue

            is_first_loop = False
            if libraries != last_libraries or current_task is None or current_task.done():
                if current_task is not None:
                    await _stop_current()

                logger.info(
                    "Watching %d local library root(s): %s",
                    len(libraries),
                    ", ".join(str(root) for _, root in libraries),
                )
                last_libraries = libraries
                current_stop = asyncio.Event()
                watchdog = _Watchdog(libraries)
                current_task = asyncio.create_task(watchdog.run(stop_event=current_stop))

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=_WATCHDOG_POLL_INTERVAL)
            except asyncio.TimeoutError:
                pass

    finally:
        await _stop_current()
