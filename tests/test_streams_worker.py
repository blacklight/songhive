import asyncio
import inspect
import json

import pytest
from fakeredis.aioredis import FakeRedis

from songhive.models.base import get_session, init_db
from songhive.models.playback_session import PlaybackSession
from songhive.services.outputs import create_output, update_output
from songhive.services.playback import (
    _control_key,
    get_or_create_session,
    handle_command,
    select_outputs,
)
from songhive.streams.driver import OutputDriver
from songhive.streams.types import AudioSource, TrackMeta
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

    async def _make(queue: list[dict], state: str = "idle"):
        output = await create_output(
            db_session,
            regular_user,
            worker_config,
            provider_type="fake",
            name="fake output",
            cfg={"items": {}},
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
