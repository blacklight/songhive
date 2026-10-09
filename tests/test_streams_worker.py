import asyncio
import inspect
import json
from contextlib import asynccontextmanager

import pytest
from fakeredis.aioredis import FakeRedis
from sqlalchemy import func, select

import songhive.streams.live as live_mod
import songhive.streams.worker as worker_mod
from songhive.models.base import get_session, init_db
from songhive.models.history import ListeningHistory
from songhive.models.output_stream import OutputStream
from songhive.models.playback_session import PlaybackSession
from songhive.services.outputs import create_output, update_output
from songhive.services.playback import (
    _control_key,
    get_or_create_session,
    handle_command,
    select_outputs,
)
from songhive.services.secrets import encrypt_json
from songhive.streams.driver import OutputDriver
from songhive.streams.fake import FakeOutput
from songhive.streams.live import encode_live_state, live_key, worker_heartbeat_key
from songhive.streams.registry import get_output as registry_get_output
from songhive.streams.types import AudioSource, OutputCapabilities, TrackMeta
from songhive.streams.worker import SessionDriver, StreamWorker


def _sample_queue() -> list[dict]:
    return [
        {"id": "t1", "title": "Track One", "artist": "Artist", "duration": 100},
        {"id": "t2", "title": "Track Two", "artist": "Artist", "duration": 100},
    ]


def _find_command(driver: OutputDriver, name: str):
    """Return the first command tuple with the given name."""
    for cmd in driver.commands:
        if isinstance(cmd, tuple) and cmd[0] == name:
            return cmd
        if cmd == name:
            return cmd
    return None


def _set_source_commands(driver: OutputDriver) -> list[tuple]:
    """Return every ``set_source`` command the driver received."""
    return [cmd for cmd in driver.commands if isinstance(cmd, tuple) and cmd[0] == "set_source"]


async def _wait_until(predicate, *, timeout: float = 10.0, interval: float = 0.02) -> bool:
    """Poll ``predicate`` until it returns truthy or ``timeout`` seconds elapse.

    The predicate may be synchronous or return an awaitable. Polling on a
    condition instead of sleeping a fixed delay keeps the tests deterministic
    under loaded parallel CI workers.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        result = predicate()
        if inspect.isawaitable(result):
            result = await result
        if result:
            return True
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(interval)


async def _send_command(
    worker: StreamWorker,
    session_id: str,
    command: str,
    args: dict | None = None,
    issued_by: str = "conn-1",
) -> None:
    """Enqueue a control envelope on the session's control list."""
    await worker.redis.rpush(
        _control_key(session_id),
        json.dumps({"command": command, "args": args or {}, "issued_by": issued_by}),
    )


async def _stop_driver_task(driver: SessionDriver, task: asyncio.Task, timeout: float = 10.0) -> None:
    """Flag the driver's loop to exit and wait for ``run()`` to finish."""
    driver._shutting_down = True
    await asyncio.wait_for(task, timeout=timeout)


async def _wait_for_session_field(session_id: str, field: str, expected, *, timeout: float = 10.0) -> bool:
    """Wait until ``PlaybackSession.<field>`` equals ``expected``."""

    async def _check() -> bool:
        async with get_session() as db:
            session = await db.get(PlaybackSession, session_id)
            return session is not None and getattr(session, field) == expected

    return await _wait_until(_check, timeout=timeout)


@pytest.fixture
def worker_config(config):
    """Return a config with a short idle timeout for worker tests."""
    config.streams.allow_user_created_outputs = True
    config.streams.background_idle_timeout_seconds = 900
    return config


@pytest.fixture
def make_worker(worker_config, fake_redis_server, monkeypatch):
    """Factory for a StreamWorker with a fake Redis client."""

    def _make():
        client = FakeRedis(server=fake_redis_server, decode_responses=True)
        monkeypatch.setattr("songhive.streams.worker.create_redis_client", lambda _config: client)
        return StreamWorker(worker_config)

    return _make


@pytest.fixture
def make_session_output(db_session, worker_config, regular_user, make_worker):
    """Factory for a PlaybackSession with a fake stream output attached."""

    async def _make(queue: list[dict], state: str = "idle", output_cfg: dict | None = None):
        output = await create_output(
            db_session,
            regular_user,
            worker_config,
            provider_type="fake",
            name="fake output",
            cfg=output_cfg or {"items": {}},
        )
        await db_session.flush()

        session = await get_or_create_session(db_session, regular_user)
        await select_outputs(db_session, session, [str(output.id)], regular_user)

        session.queue = queue
        session.current_index = 0
        session.state = state
        session.position_seconds = 0.0
        session.position_anchor_at = None
        session.controller_connection_id = None
        await db_session.flush()
        await db_session.commit()

        return session, output

    return _make


async def _fake_resolve_source(self, db, session: PlaybackSession):
    """Return a deterministic fake source for any current track."""
    track = (
        session.queue[session.current_index]
        if session.queue and 0 <= session.current_index < len(session.queue)
        else None
    )
    if track is None:
        return None, None
    return (
        AudioSource(kind="url", url=f"http://test/{track['id']}.mp3"),
        TrackMeta(
            track_id=track["id"],
            title=track["title"],
            artist=track["artist"],
            duration=track.get("duration"),
        ),
    )


class _DriverCapture:
    """Records each driver ``SessionDriver`` starts, in start order."""

    def __init__(self) -> None:
        self.drivers: list[OutputDriver] = []
        self.error: BaseException | None = None

    @property
    def latest(self) -> OutputDriver:
        """The most recently started driver."""
        if self.error is not None:
            raise AssertionError("SessionDriver._start_driver failed") from self.error
        assert self.drivers, "SessionDriver never started a driver"
        return self.drivers[-1]

    async def wait_for(self, count: int = 1, timeout: float = 10.0) -> OutputDriver:
        """Wait until the ``count``-th driver has started and return it."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while len(self.drivers) < count:
            if self.error is not None:
                raise AssertionError("SessionDriver._start_driver failed") from self.error
            if loop.time() >= deadline:
                raise AssertionError(f"Timed out waiting for driver #{count} ({len(self.drivers)} started)")
            await asyncio.sleep(0.02)
        return self.drivers[count - 1]


@pytest.fixture
def capture_driver(monkeypatch):
    """Capture every OutputDriver the session driver starts so tests can inspect it."""
    capture = _DriverCapture()
    orig = SessionDriver._start_driver

    async def _wrapped(self: SessionDriver) -> None:
        try:
            await orig(self)
        except Exception as exc:
            capture.error = exc
            raise
        if self.driver is not None:
            capture.drivers.append(self.driver)

    monkeypatch.setattr(SessionDriver, "_start_driver", _wrapped)
    return capture


@pytest.mark.asyncio
async def test_play_command_sets_source(
    engine, db_session, worker_config, make_session_output, make_worker, monkeypatch, capture_driver
):
    """A play command causes the driver to set a source and start playing."""
    init_db(engine=engine, force=True)
    monkeypatch.setattr(SessionDriver, "_resolve_source", _fake_resolve_source)

    session, output = await make_session_output(_sample_queue(), state="idle")
    worker = make_worker()
    driver = SessionDriver(worker, session.id, output.id, session.user_id)

    task = asyncio.create_task(driver.run())
    fake = await capture_driver.wait_for()

    await _send_command(worker, session.id, "play")
    assert await _wait_until(lambda: _find_command(fake, "set_source") is not None)

    await _stop_driver_task(driver, task)

    assert _find_command(fake, "start") is not None
    set_source = _find_command(fake, "set_source")
    assert set_source is not None
    assert set_source[3].track_id == "t1"


@pytest.mark.asyncio
async def test_pause_command_swaps_to_silence(
    engine, db_session, worker_config, make_session_output, make_worker, monkeypatch, capture_driver
):
    """A pause command is forwarded to the driver as a pause."""
    init_db(engine=engine, force=True)
    monkeypatch.setattr(SessionDriver, "_resolve_source", _fake_resolve_source)

    session, output = await make_session_output(_sample_queue(), state="playing")
    worker = make_worker()
    driver = SessionDriver(worker, session.id, output.id, session.user_id)

    task = asyncio.create_task(driver.run())
    fake = await capture_driver.wait_for()

    await _send_command(worker, session.id, "pause")
    assert await _wait_until(lambda: _find_command(fake, "pause") is not None)

    await _stop_driver_task(driver, task)

    assert _find_command(fake, "pause") is not None


@pytest.mark.asyncio
async def test_source_ended_autonomous_advance(
    engine, db_session, worker_config, make_session_output, make_worker, monkeypatch, capture_driver
):
    """When no controller is attached, a source_ended event advances the queue."""
    init_db(engine=engine, force=True)
    monkeypatch.setattr(SessionDriver, "_resolve_source", _fake_resolve_source)

    session, output = await make_session_output(_sample_queue(), state="playing")
    worker = make_worker()
    driver = SessionDriver(worker, session.id, output.id, session.user_id)

    task = asyncio.create_task(driver.run())
    fake = await capture_driver.wait_for()

    # Startup sync puts the driver on the first track (state is "playing").
    assert await _wait_until(lambda: _find_command(fake, "set_source") is not None)

    await _send_command(worker, session.id, "play")

    # Simulate the decoder finishing the first track.
    fake.trigger_source_ended()
    assert await _wait_until(lambda: len(_set_source_commands(fake)) >= 2)

    await _stop_driver_task(driver, task)

    set_source_commands = _set_source_commands(fake)
    assert set_source_commands[-1][3].track_id == "t2"


@pytest.mark.asyncio
async def test_source_ended_repeat_one(
    engine, db_session, worker_config, make_session_output, make_worker, monkeypatch, capture_driver
):
    """With repeat one, source_ended restarts the same track."""
    init_db(engine=engine, force=True)
    monkeypatch.setattr(SessionDriver, "_resolve_source", _fake_resolve_source)

    queue = [{"id": "t1", "title": "Track One", "artist": "Artist", "duration": 100}]
    session, output = await make_session_output(queue, state="playing")
    session.repeat = "one"
    await db_session.flush()
    await db_session.commit()

    worker = make_worker()
    driver = SessionDriver(worker, session.id, output.id, session.user_id)

    task = asyncio.create_task(driver.run())
    fake = await capture_driver.wait_for()

    assert await _wait_until(lambda: _find_command(fake, "set_source") is not None)

    await _send_command(worker, session.id, "play")

    fake.trigger_source_ended()
    assert await _wait_until(lambda: len(_set_source_commands(fake)) >= 2)

    await _stop_driver_task(driver, task)

    set_source_commands = _set_source_commands(fake)
    assert set_source_commands[-1][3].track_id == "t1"


@pytest.mark.asyncio
async def test_idle_timeout_stops_driver(
    engine, db_session, worker_config, make_session_output, make_worker, monkeypatch, capture_driver
):
    """A driver with no controller and no listeners stops after the idle timeout."""
    init_db(engine=engine, force=True)
    worker_config.streams.background_idle_timeout_seconds = 0.1
    monkeypatch.setattr(SessionDriver, "_resolve_source", _fake_resolve_source)

    session, output = await make_session_output(_sample_queue(), state="idle")
    worker = make_worker()
    driver = SessionDriver(worker, session.id, output.id, session.user_id)

    task = asyncio.create_task(driver.run())
    # The driver stops itself once the idle timeout elapses.
    await asyncio.wait_for(task, timeout=10.0)

    fake = capture_driver.latest
    assert _find_command(fake, "stop") is not None


@pytest.mark.asyncio
async def test_redis_lock_exclusivity(engine, db_session, worker_config, make_session_output, make_worker, monkeypatch):
    """Only one worker claims an output lock at a time."""
    init_db(engine=engine, force=True)
    monkeypatch.setattr(SessionDriver, "_resolve_source", _fake_resolve_source)

    session, output = await make_session_output(_sample_queue(), state="idle")
    worker1 = make_worker()
    worker2 = make_worker()

    lock_key = f"songhive:stream:lock:{output.id}"

    # Worker 1 claims the output lock.
    acquired1 = await worker1.redis.set(lock_key, "1", nx=True, ex=10)
    assert acquired1 is True

    # Worker 2 must not be able to claim the same lock.
    acquired2 = await worker2.redis.set(lock_key, "1", nx=True, ex=10)
    assert not acquired2

    await worker1.redis.delete(lock_key)


@pytest.mark.asyncio
async def test_controller_loss_autonomous_advance(
    engine, db_session, worker_config, make_session_output, make_worker, monkeypatch, capture_driver
):
    """When the controller releases, source_ended triggers autonomous advance."""
    init_db(engine=engine, force=True)
    monkeypatch.setattr(SessionDriver, "_resolve_source", _fake_resolve_source)

    session, output = await make_session_output(_sample_queue(), state="playing")
    session.controller_connection_id = "conn-1"
    await db_session.flush()
    await db_session.commit()

    worker = make_worker()
    driver = SessionDriver(worker, session.id, output.id, session.user_id)

    task = asyncio.create_task(driver.run())
    fake = await capture_driver.wait_for()

    assert await _wait_until(lambda: _find_command(fake, "set_source") is not None)

    await _send_command(worker, session.id, "play")

    # Controller disconnects; wait until the release has been applied so the
    # track ends while no controller is attached.
    await _send_command(worker, session.id, "release_control")
    assert await _wait_for_session_field(session.id, "controller_connection_id", None)

    fake.trigger_source_ended()
    assert await _wait_until(lambda: len(_set_source_commands(fake)) >= 2)

    await _stop_driver_task(driver, task)

    set_source_commands = _set_source_commands(fake)
    assert set_source_commands[-1][3].track_id == "t2"


@pytest.mark.asyncio
async def test_source_ended_with_controller_advances(
    engine, db_session, worker_config, make_session_output, make_worker, monkeypatch, capture_driver
):
    """A track ending while a controller is attached still advances the queue."""
    init_db(engine=engine, force=True)
    monkeypatch.setattr(SessionDriver, "_resolve_source", _fake_resolve_source)

    session, output = await make_session_output(_sample_queue(), state="playing")
    session.controller_connection_id = "conn-1"
    await db_session.flush()
    await db_session.commit()

    worker = make_worker()
    driver = SessionDriver(worker, session.id, output.id, session.user_id)

    task = asyncio.create_task(driver.run())
    fake = await capture_driver.wait_for()

    assert await _wait_until(lambda: _find_command(fake, "set_source") is not None)

    await _send_command(worker, session.id, "play")

    fake.trigger_source_ended()
    assert await _wait_until(lambda: len(_set_source_commands(fake)) >= 2)

    await _stop_driver_task(driver, task)

    set_source_commands = _set_source_commands(fake)
    assert set_source_commands[-1][3].track_id == "t2"
    assert _find_command(fake, "pause") is None

    async with get_session() as db:
        refreshed = await db.get(PlaybackSession, session.id)
        assert refreshed is not None
        assert refreshed.state == "playing"
        assert refreshed.current_index == 1


@pytest.mark.asyncio
async def test_take_control_does_not_reset_source(
    engine, db_session, worker_config, make_session_output, make_worker, monkeypatch, capture_driver
):
    """take_control must not restart the decoder when the track is unchanged."""
    init_db(engine=engine, force=True)
    monkeypatch.setattr(SessionDriver, "_resolve_source", _fake_resolve_source)

    session, output = await make_session_output(_sample_queue(), state="playing")
    worker = make_worker()
    driver = SessionDriver(worker, session.id, output.id, session.user_id)

    task = asyncio.create_task(driver.run())
    fake = await capture_driver.wait_for()

    assert await _wait_until(lambda: _find_command(fake, "set_source") is not None)

    await _send_command(worker, session.id, "play")
    await _send_command(worker, session.id, "take_control", issued_by="conn-2")

    # Commands are drained FIFO; once the controller switch is persisted, the
    # take_control command has been fully processed.
    assert await _wait_for_session_field(session.id, "controller_connection_id", "conn-2")

    await _stop_driver_task(driver, task)

    assert len(_set_source_commands(fake)) == 1
    assert _find_command(fake, "pause") is None


@pytest.mark.asyncio
async def test_stale_source_ended_ignored(
    engine, db_session, worker_config, make_session_output, make_worker, monkeypatch, capture_driver
):
    """A source_ended event from a superseded decoder generation is dropped."""
    init_db(engine=engine, force=True)
    monkeypatch.setattr(SessionDriver, "_resolve_source", _fake_resolve_source)

    session, output = await make_session_output(_sample_queue(), state="playing")
    worker = make_worker()
    driver = SessionDriver(worker, session.id, output.id, session.user_id)

    task = asyncio.create_task(driver.run())
    fake = await capture_driver.wait_for()

    assert await _wait_until(lambda: _find_command(fake, "set_source") is not None)

    await _send_command(worker, session.id, "play")

    # Generation 0 predates the set_source that play triggered. The stale
    # event is dropped synchronously while draining, so an empty queue means
    # it has been processed.
    fake.trigger_source_ended(generation=0)
    assert await _wait_until(lambda: fake.events.empty())

    await _stop_driver_task(driver, task)

    assert len(_set_source_commands(fake)) == 1

    async with get_session() as db:
        refreshed = await db.get(PlaybackSession, session.id)
        assert refreshed is not None
        assert refreshed.current_index == 0


@pytest.mark.asyncio
async def test_play_after_pause_restarts_source(
    engine, db_session, worker_config, make_session_output, make_worker, monkeypatch, capture_driver
):
    """Resuming a paused session must restart the decoder.

    The API commits state=playing before publishing the control envelope, so
    the worker cannot detect the resume from the loaded session state alone;
    it must rely on the driver still being paused.
    """
    init_db(engine=engine, force=True)
    monkeypatch.setattr(SessionDriver, "_resolve_source", _fake_resolve_source)

    session, output = await make_session_output(_sample_queue(), state="playing")
    worker = make_worker()
    driver = SessionDriver(worker, session.id, output.id, session.user_id)

    task = asyncio.create_task(driver.run())
    fake = await capture_driver.wait_for()

    assert await _wait_until(lambda: _find_command(fake, "set_source") is not None)

    async def _persist_and_notify(command: str) -> None:
        async with get_session() as db:
            persisted = await db.get(PlaybackSession, session.id)
            assert persisted is not None
            await handle_command(db, persisted, command, {}, "conn-1")
        await _send_command(worker, session.id, command)

    await _persist_and_notify("pause")
    assert await _wait_until(lambda: fake.is_paused)
    assert _find_command(fake, "pause") is not None

    await _persist_and_notify("play")
    assert await _wait_until(lambda: not fake.is_paused and len(_set_source_commands(fake)) >= 2)

    await _stop_driver_task(driver, task)

    # One set_source from the startup sync, one from the resume.
    assert len(_set_source_commands(fake)) == 2
    assert not fake.is_paused


@pytest.mark.asyncio
async def test_set_queue_while_playing_keeps_source(
    engine, db_session, worker_config, make_session_output, make_worker, monkeypatch, capture_driver
):
    """An enqueue-style set_queue while playing must not pause or restart the source."""
    init_db(engine=engine, force=True)
    monkeypatch.setattr(SessionDriver, "_resolve_source", _fake_resolve_source)

    session, output = await make_session_output(_sample_queue(), state="playing")
    worker = make_worker()
    driver = SessionDriver(worker, session.id, output.id, session.user_id)

    task = asyncio.create_task(driver.run())
    fake = await capture_driver.wait_for()

    assert await _wait_until(lambda: _find_command(fake, "set_source") is not None)

    await _send_command(worker, session.id, "play")

    # The API persists the new queue before notifying the worker; the current
    # track survives, so state/index are preserved by cmd_set_queue.
    async with get_session() as db:
        persisted = await db.get(PlaybackSession, session.id)
        assert persisted is not None
        await handle_command(
            db,
            persisted,
            "set_queue",
            {"queue": _sample_queue() + [{"id": "t3", "title": "Three", "artist": "a", "duration": 100}]},
            "conn-1",
        )
    await _send_command(worker, session.id, "set_queue", {"queue_length": 3})

    # set_queue produces no driver command of its own, so wait on a trailing
    # marker: commands are drained FIFO, one per loop iteration, and once the
    # set_shuffle write lands every command enqueued before it ran.
    await _send_command(worker, session.id, "set_shuffle", {"shuffle": True})
    assert await _wait_for_session_field(session.id, "shuffle", True)

    await _stop_driver_task(driver, task)

    assert len(_set_source_commands(fake)) == 1
    assert _find_command(fake, "pause") is None


@pytest.mark.asyncio
async def test_set_volume_command_reaches_driver(
    engine, db_session, worker_config, make_session_output, make_worker, monkeypatch, capture_driver
):
    """A set_volume command updates the session and reaches the driver."""
    init_db(engine=engine, force=True)
    monkeypatch.setattr(SessionDriver, "_resolve_source", _fake_resolve_source)

    session, output = await make_session_output(_sample_queue(), state="playing")
    worker = make_worker()
    driver = SessionDriver(worker, session.id, output.id, session.user_id)

    task = asyncio.create_task(driver.run())
    fake = await capture_driver.wait_for()

    await _send_command(worker, session.id, "set_volume", {"volume": 0.3})

    # The startup sync also pushes a set_volume (default 1.0); the command's
    # value must reach the driver too.
    def _volume_applied() -> bool:
        vols = [c[1] for c in fake.commands if isinstance(c, tuple) and c[0] == "set_volume"]
        return any(v == pytest.approx(0.3) for v in vols)

    assert await _wait_until(_volume_applied)

    await _stop_driver_task(driver, task)

    async with get_session() as db:
        persisted = await db.get(PlaybackSession, session.id)
        assert persisted is not None
        assert persisted.volume == pytest.approx(0.3)


@pytest.mark.asyncio
async def test_startup_sync_applies_persisted_volume(
    engine, db_session, worker_config, make_session_output, make_worker, monkeypatch, capture_driver
):
    """The worker pushes the persisted session volume to the driver on claim."""
    init_db(engine=engine, force=True)
    monkeypatch.setattr(SessionDriver, "_resolve_source", _fake_resolve_source)

    session, output = await make_session_output(_sample_queue(), state="playing")
    session.volume = 0.5
    await db_session.commit()

    worker = make_worker()
    driver = SessionDriver(worker, session.id, output.id, session.user_id)

    task = asyncio.create_task(driver.run())
    fake = await capture_driver.wait_for()

    # Startup completion is marked by the first source reaching the driver.
    assert await _wait_until(lambda: _find_command(fake, "set_source") is not None)

    await _stop_driver_task(driver, task)

    vol = _find_command(fake, "set_volume")
    assert vol is not None
    assert vol[1] == pytest.approx(0.5)
    # Volume lands before the first source so the decoder starts at the right
    # gain instead of being restarted a moment later.
    commands = [c[0] if isinstance(c, tuple) else c for c in fake.commands]
    assert commands.index("set_volume") < commands.index("set_source")


@pytest.mark.asyncio
async def test_update_output_config_reloads_driver(
    engine,
    db_session,
    worker_config,
    regular_user,
    make_session_output,
    make_worker,
    monkeypatch,
    capture_driver,
):
    """Editing an output's config pushes reload_output and restarts the driver."""
    init_db(engine=engine, force=True)
    monkeypatch.setattr(SessionDriver, "_resolve_source", _fake_resolve_source)

    # Capture the published envelopes instead of pushing them: the reload
    # command must only reach the worker after the new config is committed,
    # or the restarted driver could read the pre-update row.
    published: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        "songhive.services.playback.publish_control_command",
        lambda session_id, command, args, issued_by: published.append((session_id, command, args)),
    )

    session, output = await make_session_output(_sample_queue(), state="playing")
    worker = make_worker()
    driver = SessionDriver(worker, session.id, output.id, session.user_id)

    task = asyncio.create_task(driver.run())
    first = await capture_driver.wait_for()

    await update_output(
        db_session,
        output,
        regular_user,
        worker_config,
        cfg={"items": {"reloaded": True}},
    )
    await db_session.commit()

    for session_id, command, args in published:
        await _send_command(worker, session_id, command, args)

    second = await capture_driver.wait_for(2)

    await _stop_driver_task(driver, task)

    assert second is not first
    assert _find_command(first, "stop") is not None
    assert _find_command(second, "start") is not None
    assert second.config.get("items") == {"reloaded": True}


def _update_metadata_commands(driver: OutputDriver) -> list[tuple]:
    """Return every ``update_metadata`` command the driver received."""
    return [cmd for cmd in driver.commands if isinstance(cmd, tuple) and cmd[0] == "update_metadata"]


@pytest.mark.asyncio
async def test_play_pushes_now_playing_metadata(
    engine, db_session, worker_config, make_session_output, make_worker, monkeypatch, capture_driver
):
    """Playback start pushes the current track's metadata to the driver."""
    init_db(engine=engine, force=True)
    monkeypatch.setattr(SessionDriver, "_resolve_source", _fake_resolve_source)

    session, output = await make_session_output(_sample_queue(), state="idle")
    worker = make_worker()
    driver = SessionDriver(worker, session.id, output.id, session.user_id)

    task = asyncio.create_task(driver.run())
    fake = await capture_driver.wait_for()

    await _send_command(worker, session.id, "play")
    assert await _wait_until(lambda: _update_metadata_commands(fake))

    await _stop_driver_task(driver, task)

    update = _update_metadata_commands(fake)[0]
    assert update[1].track_id == "t1"
    assert update[1].song == "Artist - Track One"
    # The title is pushed right after the decoder swap.
    commands = [c[0] if isinstance(c, tuple) else c for c in fake.commands]
    assert commands.index("update_metadata") > commands.index("set_source")


@pytest.mark.asyncio
async def test_track_change_pushes_now_playing_metadata(
    engine, db_session, worker_config, make_session_output, make_worker, monkeypatch, capture_driver
):
    """Advancing to the next track pushes its metadata to the driver."""
    init_db(engine=engine, force=True)
    monkeypatch.setattr(SessionDriver, "_resolve_source", _fake_resolve_source)

    session, output = await make_session_output(_sample_queue(), state="playing")
    worker = make_worker()
    driver = SessionDriver(worker, session.id, output.id, session.user_id)

    task = asyncio.create_task(driver.run())
    fake = await capture_driver.wait_for()

    # Startup sync puts the driver on the first track and publishes its title.
    assert await _wait_until(lambda: _update_metadata_commands(fake))

    fake.trigger_source_ended()
    assert await _wait_until(lambda: len(_update_metadata_commands(fake)) >= 2)

    await _stop_driver_task(driver, task)

    updates = _update_metadata_commands(fake)
    assert updates[0][1].track_id == "t1"
    assert updates[-1][1].track_id == "t2"
    assert updates[-1][1].song == "Artist - Track Two"


@pytest.mark.asyncio
async def test_command_burst_syncs_driver_once(
    engine, db_session, worker_config, make_session_output, make_worker, monkeypatch, capture_driver
):
    """A burst of commands must restart the decoder once, not once per envelope.

    Actions like setQueueAndPlay publish set_queue + play_at + play as
    separate envelopes a few hundred milliseconds apart; syncing the driver
    per envelope restarted the source repeatedly, so listeners heard the
    track start over.
    """
    init_db(engine=engine, force=True)
    monkeypatch.setattr(SessionDriver, "_resolve_source", _fake_resolve_source)

    session, output = await make_session_output(_sample_queue(), state="playing")
    worker = make_worker()
    driver = SessionDriver(worker, session.id, output.id, session.user_id)

    task = asyncio.create_task(driver.run())
    fake = await capture_driver.wait_for()

    # Startup sync puts the driver on the first track.
    assert await _wait_until(lambda: _find_command(fake, "set_source") is not None)

    async def _persist_and_notify(command: str, args: dict | None = None) -> None:
        async with get_session() as db:
            persisted = await db.get(PlaybackSession, session.id)
            assert persisted is not None
            await handle_command(db, persisted, command, args or {}, "conn-1")
        await _send_command(worker, session.id, command, args or {})

    # Emulate a setQueueAndPlay-style burst serialized over several loop
    # ticks: every envelope repositions playback, so syncing per envelope
    # would restart the decoder once per command.
    await _persist_and_notify("seek", {"seconds": 5})
    await asyncio.sleep(0.15)
    await _persist_and_notify("play_at", {"index": 1})
    await asyncio.sleep(0.15)
    await _persist_and_notify("seek", {"seconds": 10})

    assert await _wait_until(lambda: len(_set_source_commands(fake)) >= 2)
    # Let the settle window elapse so any stray per-envelope sync lands before
    # the count is asserted.
    await asyncio.sleep(SessionDriver._CONTROL_SETTLE_S + 0.3)

    await _stop_driver_task(driver, task)

    set_source_commands = _set_source_commands(fake)
    assert len(set_source_commands) == 2
    # The batch resolves to the final position of the last command.
    assert set_source_commands[1][3].track_id == "t2"
    assert set_source_commands[1][2] == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_separate_seeks_still_reposition(
    engine, db_session, worker_config, make_session_output, make_worker, monkeypatch, capture_driver
):
    """Commands arriving after the burst settles each get their own sync."""
    init_db(engine=engine, force=True)
    monkeypatch.setattr(SessionDriver, "_resolve_source", _fake_resolve_source)

    session, output = await make_session_output(_sample_queue(), state="playing")
    worker = make_worker()
    driver = SessionDriver(worker, session.id, output.id, session.user_id)

    task = asyncio.create_task(driver.run())
    fake = await capture_driver.wait_for()

    # Startup sync puts the driver on the first track.
    assert await _wait_until(lambda: _find_command(fake, "set_source") is not None)

    await _send_command(worker, session.id, "seek", {"seconds": 5})
    assert await _wait_until(lambda: len(_set_source_commands(fake)) >= 2)

    # Wait out the settle window so the next command lands in its own batch.
    await asyncio.sleep(SessionDriver._CONTROL_SETTLE_S + 0.3)
    await _send_command(worker, session.id, "seek", {"seconds": 10})
    assert await _wait_until(lambda: len(_set_source_commands(fake)) >= 3)

    await _stop_driver_task(driver, task)

    set_source_commands = _set_source_commands(fake)
    assert len(set_source_commands) == 3
    assert set_source_commands[1][2] == pytest.approx(5.0)
    assert set_source_commands[2][2] == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_source_ended_retries_transient_failure(
    engine, db_session, worker_config, make_session_output, make_worker, monkeypatch, capture_driver
):
    """A transient failure while advancing to the next track is retried."""
    init_db(engine=engine, force=True)
    fail_next = False

    async def _flaky_resolve(self, db, session: PlaybackSession):
        nonlocal fail_next
        if fail_next:
            fail_next = False
            raise RuntimeError("transient storage error")
        return await _fake_resolve_source(self, db, session)

    monkeypatch.setattr(SessionDriver, "_resolve_source", _flaky_resolve)
    monkeypatch.setattr(SessionDriver, "_ADVANCE_RETRY_DELAY_S", 0.05)

    session, output = await make_session_output(_sample_queue(), state="playing")
    worker = make_worker()
    driver = SessionDriver(worker, session.id, output.id, session.user_id)

    task = asyncio.create_task(driver.run())
    fake = await capture_driver.wait_for()
    assert await _wait_until(lambda: _find_command(fake, "set_source") is not None)

    # The first advance attempt dies inside _resolve_source (rolling back the
    # index update); the scheduled retry succeeds and lands on track two.
    fail_next = True
    fake.trigger_source_ended()
    assert await _wait_until(lambda: len(_set_source_commands(fake)) >= 2)

    await _stop_driver_task(driver, task)

    assert _set_source_commands(fake)[-1][3].track_id == "t2"


@pytest.mark.asyncio
async def test_source_ended_retry_exhaustion_pauses(
    engine, db_session, worker_config, make_session_output, make_worker, monkeypatch, capture_driver
):
    """After repeated advance failures the driver feeds silence, not dead air."""
    init_db(engine=engine, force=True)

    async def _always_fail(self, db, session: PlaybackSession):
        raise RuntimeError("persistent storage error")

    monkeypatch.setattr(SessionDriver, "_resolve_source", _always_fail)
    monkeypatch.setattr(SessionDriver, "_ADVANCE_RETRY_DELAY_S", 0.05)
    monkeypatch.setattr(SessionDriver, "_ADVANCE_MAX_RETRIES", 2)

    session, output = await make_session_output(_sample_queue(), state="playing")
    worker = make_worker()
    driver = SessionDriver(worker, session.id, output.id, session.user_id)

    task = asyncio.create_task(driver.run())
    fake = await capture_driver.wait_for()

    fake.trigger_source_ended()
    assert await _wait_until(lambda: _find_command(fake, "pause") is not None)

    await _stop_driver_task(driver, task)


@pytest.mark.asyncio
async def test_source_ended_committed_advance_resyncs(
    engine, db_session, worker_config, make_session_output, make_worker, monkeypatch, capture_driver
):
    """A retry after a committed advance resyncs instead of skipping a track.

    When the queue advance commits but ``set_source`` dies, the retry must
    feed the track the session already moved to — advancing again would skip
    the listener's next song.
    """
    init_db(engine=engine, force=True)
    monkeypatch.setattr(SessionDriver, "_resolve_source", _fake_resolve_source)
    monkeypatch.setattr(SessionDriver, "_ADVANCE_RETRY_DELAY_S", 0.05)

    session, output = await make_session_output(_sample_queue(), state="playing")
    worker = make_worker()
    driver = SessionDriver(worker, session.id, output.id, session.user_id)

    task = asyncio.create_task(driver.run())
    fake = await capture_driver.wait_for()
    assert await _wait_until(lambda: _find_command(fake, "set_source") is not None)

    # Sabotage the next set_source: the advance commits, the swap dies.
    original_set_source = fake.set_source
    broken = True

    async def _flaky_set_source(source, *, position, metadata):
        nonlocal broken
        if broken:
            broken = False
            raise RuntimeError("encoder rejected source")
        return await original_set_source(source, position=position, metadata=metadata)

    monkeypatch.setattr(fake, "set_source", _flaky_set_source)

    fake.trigger_source_ended()
    assert await _wait_until(lambda: len(_set_source_commands(fake)) >= 2)

    await _stop_driver_task(driver, task)

    assert _set_source_commands(fake)[-1][3].track_id == "t2"
    async with get_session() as db:
        refreshed = await db.get(PlaybackSession, session.id)
        assert refreshed is not None
        assert refreshed.current_index == 1


async def _listen_count(session_user_id: str) -> int:
    """Count listening-history rows recorded for the session owner."""
    async with get_session() as db:
        return (
            await db.scalar(select(func.count(ListeningHistory.id)).where(ListeningHistory.user_id == session_user_id))
            or 0
        )


@pytest.mark.asyncio
async def test_source_ended_records_listen_by_default(
    engine, db_session, worker_config, make_session_output, make_worker, monkeypatch, capture_driver
):
    """A track played past the threshold on a stream output records a listen."""
    init_db(engine=engine, force=True)
    monkeypatch.setattr(SessionDriver, "_resolve_source", _fake_resolve_source)

    session, output = await make_session_output(_sample_queue(), state="playing")
    # Past the 30s default threshold so the finished track counts as a listen.
    session.position_seconds = 45.0
    await db_session.commit()

    worker = make_worker()
    driver = SessionDriver(worker, session.id, output.id, session.user_id)

    task = asyncio.create_task(driver.run())
    fake = await capture_driver.wait_for()
    assert await _wait_until(lambda: _find_command(fake, "set_source") is not None)

    fake.trigger_source_ended()
    assert await _wait_until(lambda: len(_set_source_commands(fake)) >= 2)

    await _stop_driver_task(driver, task)

    assert await _listen_count(str(session.user_id)) == 1


@pytest.mark.asyncio
async def test_record_listens_disabled_skips_listen(
    engine, db_session, worker_config, make_session_output, make_worker, monkeypatch, capture_driver
):
    """An output with record_listens off advances the queue without recording."""
    init_db(engine=engine, force=True)
    monkeypatch.setattr(SessionDriver, "_resolve_source", _fake_resolve_source)

    session, output = await make_session_output(
        _sample_queue(),
        state="playing",
        output_cfg={"items": {}, "record_listens": False},
    )
    session.position_seconds = 45.0
    await db_session.commit()

    worker = make_worker()
    driver = SessionDriver(worker, session.id, output.id, session.user_id)

    task = asyncio.create_task(driver.run())
    fake = await capture_driver.wait_for()
    assert await _wait_until(lambda: _find_command(fake, "set_source") is not None)

    assert driver._record_listens is False

    fake.trigger_source_ended()
    # The second set_source lands after the listen decision, so reaching it
    # proves the skip rather than a still-pending write.
    assert await _wait_until(lambda: len(_set_source_commands(fake)) >= 2)

    await _stop_driver_task(driver, task)

    assert _set_source_commands(fake)[-1][3].track_id == "t2"
    assert await _listen_count(str(session.user_id)) == 0


@pytest.mark.asyncio
async def test_session_has_stream_output_survives_db_error(worker_config, make_worker, monkeypatch):
    """A transient DB error must not read as 'output removed' and stop the driver."""
    worker = make_worker()
    driver = SessionDriver(worker, "sess", "out", "user")

    @asynccontextmanager
    async def _failing_session():
        raise RuntimeError("database unreachable")
        yield

    monkeypatch.setattr("songhive.streams.worker.get_session", _failing_session)
    assert await driver._session_has_stream_output() is True


class _FakeHttpProvider(FakeOutput):
    """Registry stand-in so http-typed outputs get an in-memory driver."""

    provider_type = "http"

    async def validate_config(self, config: dict) -> OutputCapabilities:
        return OutputCapabilities(
            metadata_updates=True,
            pause_supported=True,
            seek_supported=True,
            multi_listener=True,
            user_configurable=True,
        )


@pytest.fixture
def fake_http_provider(monkeypatch):
    """Serve http-typed outputs through the fake driver inside the worker."""
    monkeypatch.setattr(
        worker_mod,
        "get_output",
        lambda provider_type: _FakeHttpProvider if provider_type == "http" else registry_get_output(provider_type),
    )
    return _FakeHttpProvider


async def _make_http_output(db, user, *, mount: str = "radio", enabled: bool = True) -> OutputStream:
    """Persist an http-typed output row without touching the registry."""
    cfg = {
        "mount": mount,
        "format": "mp3",
        "bitrate": "128k",
        "sample_rate": 44100,
        "name": "Test Radio",
    }
    output = OutputStream(
        user_id=str(user.id),
        provider_type="http",
        name="test http output",
        config=encrypt_json(cfg),
        enabled=enabled,
    )
    db.add(output)
    await db.flush()
    await db.commit()
    return output


async def _claim_live(redis, output_id: str, *, ingest_id: str = "ingest-1", user_id: str = "u", title: str = "Live"):
    """Write a live claim as the ingest handler would."""
    await redis.set(
        live_key(str(output_id)),
        encode_live_state(ingest_id=ingest_id, user_id=user_id, mime="audio/webm", title=title),
        ex=live_mod.LIVE_KEY_TTL_SECONDS,
    )


def _live_state_commands(driver: OutputDriver) -> list[object]:
    """Return the ingest ids passed to set_live_state, in order."""
    return [cmd[1] for cmd in driver.commands if isinstance(cmd, tuple) and cmd[0] == "set_live_state"]


@pytest.mark.asyncio
async def test_worker_heartbeat_key(worker_config, make_worker):
    """The scan loop's heartbeat proves a stream worker is running."""
    worker = make_worker()
    await worker._heartbeat()
    assert await worker.redis.get(worker_heartbeat_key(worker.worker_id)) == "1"


@pytest.mark.asyncio
async def test_live_broadcast_takes_over_and_resumes_queue(
    engine, db_session, worker_config, make_session_output, make_worker, monkeypatch, capture_driver
):
    """Going live pauses the playing queue; ending the broadcast resumes it."""
    init_db(engine=engine, force=True)
    monkeypatch.setattr(SessionDriver, "_resolve_source", _fake_resolve_source)

    session, output = await make_session_output(_sample_queue(), state="playing")
    worker = make_worker()
    driver = SessionDriver(worker, session.id, output.id, session.user_id)

    task = asyncio.create_task(driver.run())
    fake = await capture_driver.wait_for()
    assert await _wait_until(lambda: _find_command(fake, "set_source") is not None)

    await _claim_live(worker.redis, str(output.id), ingest_id="ingest-1", user_id=str(session.user_id))
    assert await _wait_until(lambda: "ingest-1" in _live_state_commands(fake))
    assert await _wait_for_session_field(session.id, "state", "paused")

    live_sources = [c for c in _set_source_commands(fake) if c[1].kind == "live"]
    assert live_sources, "live ingest never reached the driver"
    assert live_sources[0][1].input_format == "matroska"
    assert live_sources[0][3].title == "Live"

    # Ending the broadcast resumes the frozen queue at the same track.
    await worker.redis.delete(live_key(str(output.id)))
    assert await _wait_until(lambda: _live_state_commands(fake)[-1] is None)
    assert await _wait_for_session_field(session.id, "state", "playing")

    await _stop_driver_task(driver, task)
    assert _set_source_commands(fake)[-1][3].track_id == "t1"


@pytest.mark.asyncio
async def test_live_end_does_not_resume_after_explicit_stop(
    engine, db_session, worker_config, make_session_output, make_worker, monkeypatch, capture_driver
):
    """A transport command during the broadcast cancels the pending resume."""
    init_db(engine=engine, force=True)
    monkeypatch.setattr(SessionDriver, "_resolve_source", _fake_resolve_source)

    session, output = await make_session_output(_sample_queue(), state="playing")
    worker = make_worker()
    driver = SessionDriver(worker, session.id, output.id, session.user_id)

    task = asyncio.create_task(driver.run())
    fake = await capture_driver.wait_for()
    assert await _wait_until(lambda: _find_command(fake, "set_source") is not None)

    await _claim_live(worker.redis, str(output.id), ingest_id="ingest-1", user_id=str(session.user_id))
    assert await _wait_until(lambda: "ingest-1" in _live_state_commands(fake))
    assert await _wait_for_session_field(session.id, "state", "paused")

    # The user deliberately stops the queue while live; that choice wins.
    async with get_session() as db:
        persisted = await db.get(PlaybackSession, session.id)
        assert persisted is not None
        await handle_command(db, persisted, "stop", {}, "conn-1")
    assert await _wait_for_session_field(session.id, "state", "idle")

    await worker.redis.delete(live_key(str(output.id)))
    assert await _wait_until(lambda: _live_state_commands(fake)[-1] is None)
    # Let the resync settle, then confirm nothing was restarted.
    await asyncio.sleep(0.5)

    await _stop_driver_task(driver, task)

    async with get_session() as db:
        refreshed = await db.get(PlaybackSession, session.id)
        assert refreshed is not None
        assert refreshed.state == "idle"
    assert _find_command(fake, "pause") is not None


@pytest.mark.asyncio
async def test_live_feed_end_resumes_queue(
    engine, db_session, worker_config, make_session_output, make_worker, monkeypatch, capture_driver
):
    """A source_ended from the live decoder hands the mount back to the queue."""
    init_db(engine=engine, force=True)
    monkeypatch.setattr(SessionDriver, "_resolve_source", _fake_resolve_source)

    session, output = await make_session_output(_sample_queue(), state="playing")
    worker = make_worker()
    driver = SessionDriver(worker, session.id, output.id, session.user_id)

    task = asyncio.create_task(driver.run())
    fake = await capture_driver.wait_for()
    assert await _wait_until(lambda: _find_command(fake, "set_source") is not None)

    await _claim_live(worker.redis, str(output.id), ingest_id="ingest-1", user_id=str(session.user_id))
    assert await _wait_until(lambda: "ingest-1" in _live_state_commands(fake))

    # The ingest feed ending (broadcaster stopped/disconnected) ends live.
    fake.trigger_source_ended()
    assert await _wait_until(lambda: _live_state_commands(fake)[-1] is None)
    assert await _wait_for_session_field(session.id, "state", "playing")

    await _stop_driver_task(driver, task)


@pytest.mark.asyncio
async def test_live_superseded_ingest_swaps_without_resuming(
    engine, db_session, worker_config, make_session_output, make_worker, monkeypatch, capture_driver
):
    """A reconnecting broadcaster's new ingest id replaces the old feed."""
    init_db(engine=engine, force=True)
    monkeypatch.setattr(SessionDriver, "_resolve_source", _fake_resolve_source)

    session, output = await make_session_output(_sample_queue(), state="playing")
    worker = make_worker()
    driver = SessionDriver(worker, session.id, output.id, session.user_id)

    task = asyncio.create_task(driver.run())
    fake = await capture_driver.wait_for()
    assert await _wait_until(lambda: _find_command(fake, "set_source") is not None)

    await _claim_live(worker.redis, str(output.id), ingest_id="ingest-1", user_id=str(session.user_id))
    assert await _wait_until(lambda: "ingest-1" in _live_state_commands(fake))

    await _claim_live(worker.redis, str(output.id), ingest_id="ingest-2", user_id=str(session.user_id))
    assert await _wait_until(lambda: _live_state_commands(fake)[-1] == "ingest-2")
    # The swap happens without a queue resume in between.
    assert await _wait_for_session_field(session.id, "state", "paused")

    await _stop_driver_task(driver, task)

    live_sources = [c for c in _set_source_commands(fake) if c[1].kind == "live"]
    assert len(live_sources) == 2


@pytest.mark.asyncio
async def test_live_only_driver_lifecycle(
    engine, db_session, worker_config, regular_user, make_worker, monkeypatch, capture_driver, fake_http_provider
):
    """A mount with no session is claimed purely to serve the broadcast."""
    init_db(engine=engine, force=True)
    output = await _make_http_output(db_session, regular_user)
    worker = make_worker()

    await _claim_live(worker.redis, str(output.id), ingest_id="ingest-9", user_id=str(regular_user.id))
    await worker._scan_live()
    task = worker._tasks.get(str(output.id))
    assert task is not None

    fake = await capture_driver.wait_for()
    assert await _wait_until(lambda: "ingest-9" in _live_state_commands(fake))

    # When the broadcast ends with no session to hand over, the driver stops.
    await worker.redis.delete(live_key(str(output.id)))
    await asyncio.wait_for(task, timeout=10.0)
    assert _find_command(fake, "stop") is not None
    assert _live_state_commands(fake)[-1] is None


@pytest.mark.asyncio
async def test_live_only_driver_adopts_late_session(
    engine, db_session, worker_config, regular_user, make_worker, monkeypatch, capture_driver, fake_http_provider
):
    """A session attaching mid-broadcast is adopted so the mount survives."""
    init_db(engine=engine, force=True)
    monkeypatch.setattr(SessionDriver, "_resolve_source", _fake_resolve_source)
    output = await _make_http_output(db_session, regular_user)
    worker = make_worker()

    await _claim_live(worker.redis, str(output.id), ingest_id="ingest-1", user_id=str(regular_user.id))
    await worker._scan_live()
    task = worker._tasks[str(output.id)]
    fake = await capture_driver.wait_for()
    assert await _wait_until(lambda: "ingest-1" in _live_state_commands(fake))

    session = await get_or_create_session(db_session, regular_user)
    await select_outputs(db_session, session, [str(output.id)], regular_user)
    session.queue = _sample_queue()
    session.current_index = 0
    session.state = "paused"
    await db_session.flush()
    await db_session.commit()

    # The live-only tick adopts the driving session (1s poll cadence), so
    # ending the broadcast hands the mount to the session instead of
    # stopping the driver.
    await worker.redis.delete(live_key(str(output.id)))
    assert await _wait_until(lambda: _live_state_commands(fake)[-1] is None)
    await asyncio.sleep(0.5)
    assert not task.done()
    # The adopted paused session leaves the driver feeding silence.
    assert _find_command(fake, "pause") is not None

    worker._shutting_down = True
    await asyncio.wait_for(task, timeout=10.0)


@pytest.mark.asyncio
async def test_scan_live_ignores_ineligible_outputs(
    engine, db_session, worker_config, regular_user, make_session_output, make_worker, monkeypatch, fake_http_provider
):
    """Only enabled native-HTTP outputs with a live claim get a driver."""
    init_db(engine=engine, force=True)
    worker = make_worker()

    # A non-http output: provider_type "fake" must never be claimed for live.
    fake_session, fake_output = await make_session_output(_sample_queue(), state="idle")
    await _claim_live(worker.redis, str(fake_output.id), user_id=str(regular_user.id))

    # A disabled http output stays dark even with a live claim.
    disabled = await _make_http_output(db_session, regular_user, mount="off", enabled=False)
    await _claim_live(worker.redis, str(disabled.id), user_id=str(regular_user.id))

    # An enabled http output without a claim is not picked up either.
    await _make_http_output(db_session, regular_user, mount="idle-mount")

    await worker._scan_live()
    assert worker._tasks == {}
