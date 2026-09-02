"""
Unit tests for the local external-library filesystem watchdog.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import pytest
from watchfiles import Change

from songhive.external import watchdog as watchdog_module


def _make_fake_task() -> tuple[Any, list]:
    """Return a fake Celery task and a list recording ``delay`` calls."""
    calls = []

    class FakeTask:
        def delay(self, *args, **kwargs):
            calls.append((args, kwargs))

    return FakeTask(), calls


@pytest.fixture
def watchdog_env(tmp_path, monkeypatch):
    """Set up a fast, patched watchdog for unit tests."""
    monkeypatch.setattr(watchdog_module, "_WATCHDOG_QUIET_SECONDS", 0.05)

    fake_task, calls = _make_fake_task()
    monkeypatch.setattr("songhive.tasks.external_libraries.sync_external_library_task", fake_task)

    root = tmp_path / "watch_root"
    root.mkdir()
    watchdog = watchdog_module._Watchdog([("lib-1", root)])

    return root, watchdog, calls


@pytest.mark.asyncio
async def test_watchdog_enqueues_since_on_create(watchdog_env):
    """A file-create event flushes an incremental sync with ``since`` set."""
    root, watchdog, calls = watchdog_env
    file_path = root / "new.mp3"
    file_path.write_text("audio")

    await watchdog._handle_batch({(Change.added, str(file_path))})
    await asyncio.sleep(0.15)

    assert len(calls) == 1
    assert calls[0][0] == ("lib-1",)
    assert calls[0][1]["triggered_by"] == "watchdog"
    assert calls[0][1]["since"] is not None
    assert "scope" not in calls[0][1]


@pytest.mark.asyncio
async def test_watchdog_enqueues_scope_on_delete(watchdog_env):
    """A file-delete event flushes a scoped sync with ``scope`` set."""
    root, watchdog, calls = watchdog_env
    file_path = root / "sub" / "gone.mp3"
    file_path.parent.mkdir()

    # The path no longer exists, simulating a deletion event.
    await watchdog._handle_batch({(Change.deleted, str(file_path))})
    await asyncio.sleep(0.15)

    assert len(calls) == 1
    assert calls[0][0] == ("lib-1",)
    assert calls[0][1]["triggered_by"] == "watchdog"
    assert calls[0][1]["scope"] == "sub"
    assert "since" not in calls[0][1]


@pytest.mark.asyncio
async def test_watchdog_enqueues_full_on_delete_at_root(watchdog_env):
    """A file-delete at the root has an empty scope, falling back to a full sync."""
    root, watchdog, calls = watchdog_env
    file_path = root / "gone.mp3"

    await watchdog._handle_batch({(Change.deleted, str(file_path))})
    await asyncio.sleep(0.15)

    assert len(calls) == 1
    assert calls[0][0] == ("lib-1",)
    assert calls[0][1]["triggered_by"] == "watchdog"
    assert "scope" not in calls[0][1]
    assert "since" not in calls[0][1]


@pytest.mark.asyncio
async def test_watchdog_coalesces_multiple_adds(watchdog_env):
    """Multiple add/modify events in the quiet window produce a single incremental sync."""
    root, watchdog, calls = watchdog_env
    for name in ("a.mp3", "b.mp3", "c.mp3"):
        (root / name).write_text("audio")

    await watchdog._handle_batch({(Change.added, str(root / "a.mp3"))})
    await asyncio.sleep(0.03)
    await watchdog._handle_batch({(Change.modified, str(root / "b.mp3"))})
    await asyncio.sleep(0.03)
    await watchdog._handle_batch({(Change.added, str(root / "c.mp3"))})
    await asyncio.sleep(0.15)

    # The timer should have been rescheduled by each event, but only one flush.
    assert len(calls) == 1
    assert calls[0][1]["since"] is not None


@pytest.mark.asyncio
async def test_watchdog_deepest_common_scope_for_multiple_deletes(watchdog_env):
    """Multiple deletions in different subdirs under the same ancestor scope that ancestor."""
    root, watchdog, calls = watchdog_env

    await watchdog._handle_batch(
        {
            (Change.deleted, str(root / "a" / "b" / "track1.mp3")),
            (Change.deleted, str(root / "a" / "c" / "track2.mp3")),
        }
    )
    await asyncio.sleep(0.15)

    assert len(calls) == 1
    assert calls[0][1]["scope"] == "a"


@pytest.mark.asyncio
async def test_watchdog_mixed_add_and_delete_enqueues_both(watchdog_env):
    """Mixed add and delete events produce both a scoped and an incremental sync."""
    root, watchdog, calls = watchdog_env
    (root / "new.mp3").write_text("audio")

    await watchdog._handle_batch(
        {
            (Change.deleted, str(root / "sub" / "gone.mp3")),
            (Change.added, str(root / "new.mp3")),
        }
    )
    await asyncio.sleep(0.15)

    assert len(calls) == 2
    kwargs = [call[1] for call in calls]
    scopes = [kw.get("scope") for kw in kwargs]
    sinces = [kw.get("since") for kw in kwargs]
    assert "sub" in scopes
    assert any(since is not None for since in sinces)


def test_event_scope_parts_for_file():
    """A file delete yields the parent directory scope parts."""
    root = Path("/music")
    path = Path("/music/a/b/track.mp3")
    assert watchdog_module._event_scope_parts(root, path) == ["a", "b"]


def test_event_scope_parts_for_directory():
    """A directory delete yields the directory itself as scope parts."""
    # Simulate a directory that still exists at the time of classification.
    from unittest.mock import patch

    root = Path("/music")
    path = Path("/music/a/b")
    with (
        patch("songhive.external.watchdog.os.path.exists", return_value=True),
        patch("songhive.external.watchdog.os.path.isdir", return_value=True),
    ):
        assert watchdog_module._event_scope_parts(root, path) == ["a", "b"]


def test_deepest_common_ancestor():
    """``_deepest_common_ancestor`` returns the common path prefix."""
    assert (
        watchdog_module._deepest_common_ancestor(
            [
                ["a", "b"],
                ["a", "c"],
            ]
        )
        == "a"
    )

    assert (
        watchdog_module._deepest_common_ancestor(
            [
                ["a", "b", "c"],
                ["a", "b", "d"],
            ]
        )
        == "a/b"
    )

    assert watchdog_module._deepest_common_ancestor([["a"]]) == "a"
    assert watchdog_module._deepest_common_ancestor([]) is None
    assert watchdog_module._deepest_common_ancestor([[], ["a"]]) is None


def test_event_scope_parts_for_deleted_directory(tmp_path):
    """A deleted directory without an audio extension scopes to itself."""
    root = tmp_path
    path = tmp_path / "a" / "b"
    assert watchdog_module._event_scope_parts(root, path) == ["a", "b"]


def test_event_scope_parts_for_deleted_top_level_directory(tmp_path):
    """A deleted top-level directory scopes to itself, not the whole root."""
    root = tmp_path
    path = tmp_path / "album"
    assert watchdog_module._event_scope_parts(root, path) == ["album"]


class _FakeAWatch:
    """Async iterator that terminates once ``stop_event`` is set."""

    def __init__(self, stop_event):
        self.stop_event = stop_event

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.stop_event is not None and self.stop_event.is_set():
            raise StopAsyncIteration
        while self.stop_event is None or not self.stop_event.is_set():
            await asyncio.sleep(0.01)
        raise StopAsyncIteration


def _fake_awatch(*roots, recursive=True, stop_event=None):
    return _FakeAWatch(stop_event)


@pytest.mark.asyncio
async def test_watchdog_run_returns_when_stop_event_set(tmp_path, monkeypatch):
    """run() returns when stop_event is set instead of restarting awatch."""
    root = tmp_path / "watch_root"
    root.mkdir()
    watchdog = watchdog_module._Watchdog([("lib-1", root)])

    monkeypatch.setattr(watchdog_module, "awatch", _fake_awatch)

    stop_event = asyncio.Event()
    stop_event.set()

    await asyncio.wait_for(watchdog.run(stop_event=stop_event), timeout=1.0)


@pytest.mark.asyncio
async def test_watch_external_libraries_polls_until_libraries_exist(tmp_path, monkeypatch):
    """The entry point polls the database and starts watching once libraries appear."""
    monkeypatch.setattr(watchdog_module, "_WATCHDOG_POLL_INTERVAL", 0.01)

    root = tmp_path / "watch_root"
    root.mkdir()

    stop_event = asyncio.Event()
    resolve_calls: list[int] = []

    async def _fake_resolve() -> list[tuple[str, Path]]:
        resolve_calls.append(len(resolve_calls))
        if len(resolve_calls) < 3:
            return []
        if len(resolve_calls) == 5:
            stop_event.set()
        return [("lib-1", root)]

    monkeypatch.setattr(watchdog_module, "_resolve_libraries", _fake_resolve)

    created_watchdogs: list[list[tuple[str, Path]]] = []

    class _FakeWatchdog:
        def __init__(self, libraries: list[tuple[str, Path]]) -> None:
            created_watchdogs.append(libraries)

        async def run(self, stop_event: Optional[asyncio.Event] = None) -> None:
            await stop_event.wait()

    monkeypatch.setattr(watchdog_module, "_Watchdog", _FakeWatchdog)

    await asyncio.wait_for(watchdog_module.watch_external_libraries(stop_event=stop_event), timeout=1.0)

    assert len(resolve_calls) >= 3
    assert len(created_watchdogs) == 1
    assert created_watchdogs[0] == [("lib-1", root)]


@pytest.mark.asyncio
async def test_watch_external_libraries_waits_without_libraries(monkeypatch):
    """If no libraries are configured, the process polls and does not exit."""
    monkeypatch.setattr(watchdog_module, "_WATCHDOG_POLL_INTERVAL", 0.01)

    stop_event = asyncio.Event()
    resolve_calls: list[int] = []

    async def _fake_resolve() -> list[tuple[str, Path]]:
        resolve_calls.append(len(resolve_calls))
        if len(resolve_calls) == 3:
            stop_event.set()
        return []

    monkeypatch.setattr(watchdog_module, "_resolve_libraries", _fake_resolve)

    created_watchdogs: list[list[tuple[str, Path]]] = []

    class _FakeWatchdog:
        def __init__(self, libraries: list[tuple[str, Path]]) -> None:
            created_watchdogs.append(libraries)

        async def run(self, stop_event: Optional[asyncio.Event] = None) -> None:
            stop_event.set()

    monkeypatch.setattr(watchdog_module, "_Watchdog", _FakeWatchdog)

    await asyncio.wait_for(watchdog_module.watch_external_libraries(stop_event=stop_event), timeout=1.0)

    assert len(resolve_calls) >= 3
    assert len(created_watchdogs) == 0


@pytest.mark.asyncio
async def test_watch_external_libraries_restarts_when_libraries_change(tmp_path, monkeypatch):
    """The supervisor restarts the watcher when the set of local libraries changes."""
    monkeypatch.setattr(watchdog_module, "_WATCHDOG_POLL_INTERVAL", 0.01)

    root1 = tmp_path / "root1"
    root1.mkdir()
    root2 = tmp_path / "root2"
    root2.mkdir()

    stop_event = asyncio.Event()
    resolve_calls: list[int] = []

    async def _fake_resolve() -> list[tuple[str, Path]]:
        resolve_calls.append(len(resolve_calls))
        if len(resolve_calls) == 1:
            return [("lib-1", root1)]
        if len(resolve_calls) == 3:
            return [("lib-2", root2)]
        if len(resolve_calls) == 5:
            stop_event.set()
        return [("lib-2", root2)]

    monkeypatch.setattr(watchdog_module, "_resolve_libraries", _fake_resolve)

    created_watchdogs: list[list[tuple[str, Path]]] = []

    class _FakeWatchdog:
        def __init__(self, libraries: list[tuple[str, Path]]) -> None:
            created_watchdogs.append(libraries)

        async def run(self, stop_event: Optional[asyncio.Event] = None) -> None:
            await stop_event.wait()

    monkeypatch.setattr(watchdog_module, "_Watchdog", _FakeWatchdog)

    await asyncio.wait_for(watchdog_module.watch_external_libraries(stop_event=stop_event), timeout=1.0)

    assert len(created_watchdogs) == 2
    assert created_watchdogs[0] == [("lib-1", root1)]
    assert created_watchdogs[1] == [("lib-2", root2)]


@pytest.mark.asyncio
async def test_resolve_libraries_skips_undecryptable_config(monkeypatch):
    """A library whose config cannot be decrypted is skipped with a warning, not crashed."""

    class _FakeLibrary:
        id = "lib-1"
        config = "invalid-encrypted-token"

    class _FakeResult:
        def scalars(self):
            return self

        def all(self):
            return [_FakeLibrary()]

    class _FakeSession:
        async def execute(self, *args, **kwargs) -> _FakeResult:
            return _FakeResult()

    class _FakeSessionManager:
        async def __aenter__(self) -> _FakeSession:
            return _FakeSession()

        async def __aexit__(self, *args) -> None:
            pass

    async def _fake_dispose() -> None:
        pass

    fake_config = SimpleNamespace(database=SimpleNamespace(url="sqlite://"))

    monkeypatch.setattr(watchdog_module, "load_config", lambda *_: fake_config)
    monkeypatch.setattr(watchdog_module, "init_db", lambda *args, **kwargs: None)
    monkeypatch.setattr(watchdog_module, "get_session", _FakeSessionManager)
    monkeypatch.setattr(watchdog_module, "dispose_and_reset", _fake_dispose)

    def _bad_decrypt(token: str) -> dict:
        raise ValueError("decrypt failed")

    monkeypatch.setattr(watchdog_module, "decrypt_json", _bad_decrypt)

    libraries = await watchdog_module._resolve_libraries()
    assert libraries == []


@pytest.mark.asyncio
async def test_watch_external_libraries_retries_resolve_errors(monkeypatch):
    """If resolving libraries fails transiently, the supervisor keeps polling."""
    monkeypatch.setattr(watchdog_module, "_WATCHDOG_POLL_INTERVAL", 0.01)

    stop_event = asyncio.Event()
    resolve_calls: list[int] = []

    async def _fake_resolve() -> list[tuple[str, Path]]:
        resolve_calls.append(len(resolve_calls))
        if len(resolve_calls) < 2:
            raise RuntimeError("database down")
        if len(resolve_calls) == 4:
            stop_event.set()
        return []

    monkeypatch.setattr(watchdog_module, "_resolve_libraries", _fake_resolve)

    class _FakeWatchdog:
        def __init__(self, libraries: list[tuple[str, Path]]) -> None:
            pass

        async def run(self, stop_event: Optional[asyncio.Event] = None) -> None:
            pass

    monkeypatch.setattr(watchdog_module, "_Watchdog", _FakeWatchdog)

    await asyncio.wait_for(watchdog_module.watch_external_libraries(stop_event=stop_event), timeout=1.0)

    assert len(resolve_calls) >= 2
