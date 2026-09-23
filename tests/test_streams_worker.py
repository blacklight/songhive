import asyncio
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


@pytest.fixture
def capture_driver(monkeypatch):
    """Capture the started OutputDriver so tests can inspect commands after run()."""
    holder: dict[str, OutputDriver | None] = {}
    orig = SessionDriver._start_driver

    async def _wrapped(self) -> None:
        await orig(self)
        holder["driver"] = self.driver

    monkeypatch.setattr(SessionDriver, "_start_driver", _wrapped)
    return holder


def _find_command(driver: OutputDriver, name: str):
    """Return the first command tuple with the given name."""
    for cmd in driver.commands:
        if isinstance(cmd, tuple) and cmd[0] == name:
            return cmd
        if cmd == name:
            return cmd
    return None


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
    # Give the driver a moment to start.
    await asyncio.sleep(0.1)

    await worker.redis.rpush(
        _control_key(session.id),
        json.dumps({"command": "play", "args": {}, "issued_by": "conn-1"}),
    )

    await asyncio.sleep(0.3)

    driver._shutting_down = True
    await asyncio.wait_for(task, timeout=2.0)

    fake = capture_driver["driver"]
    assert fake is not None
    start = _find_command(fake, "start")
    assert start is not None
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
    await asyncio.sleep(0.1)

    await worker.redis.rpush(
        _control_key(session.id),
        json.dumps({"command": "pause", "args": {}, "issued_by": "conn-1"}),
    )

    await asyncio.sleep(0.3)

    driver._shutting_down = True
    await asyncio.wait_for(task, timeout=2.0)

    fake = capture_driver["driver"]
    assert fake is not None
    pause = _find_command(fake, "pause")
    assert pause is not None


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
    await asyncio.sleep(0.1)

    # Put the driver into a playing state with the first track.
    await worker.redis.rpush(
        _control_key(session.id),
        json.dumps({"command": "play", "args": {}, "issued_by": "conn-1"}),
    )
    await asyncio.sleep(0.2)

    # Simulate the decoder finishing the first track.
    fake = capture_driver["driver"]
    assert fake is not None
    fake.trigger_source_ended()

    await asyncio.sleep(0.4)

    driver._shutting_down = True
    await asyncio.wait_for(task, timeout=2.0)

    set_source_commands = [cmd for cmd in fake.commands if isinstance(cmd, tuple) and cmd[0] == "set_source"]
    assert len(set_source_commands) >= 2
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
    await asyncio.sleep(0.1)

    await worker.redis.rpush(
        _control_key(session.id),
        json.dumps({"command": "play", "args": {}, "issued_by": "conn-1"}),
    )
    await asyncio.sleep(0.2)

    fake = capture_driver["driver"]
    assert fake is not None
    fake.trigger_source_ended()

    await asyncio.sleep(0.4)

    driver._shutting_down = True
    await asyncio.wait_for(task, timeout=2.0)

    set_source_commands = [cmd for cmd in fake.commands if isinstance(cmd, tuple) and cmd[0] == "set_source"]
    assert len(set_source_commands) >= 2
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
    await asyncio.sleep(0.5)

    assert task.done()
    fake = capture_driver["driver"]
    assert fake is not None
    stop = _find_command(fake, "stop")
    assert stop is not None


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
    await asyncio.sleep(0.1)

    await worker.redis.rpush(
        _control_key(session.id),
        json.dumps({"command": "play", "args": {}, "issued_by": "conn-1"}),
    )
    await asyncio.sleep(0.2)

    # Controller disconnects.
    await worker.redis.rpush(
        _control_key(session.id),
        json.dumps({"command": "release_control", "args": {}, "issued_by": "conn-1"}),
    )
    await asyncio.sleep(0.2)

    # Track ends while no controller is attached.
    fake = capture_driver["driver"]
    assert fake is not None
    fake.trigger_source_ended()

    await asyncio.sleep(0.4)

    driver._shutting_down = True
    await asyncio.wait_for(task, timeout=2.0)

    set_source_commands = [cmd for cmd in fake.commands if isinstance(cmd, tuple) and cmd[0] == "set_source"]
    assert len(set_source_commands) >= 2
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
    await asyncio.sleep(0.1)

    await worker.redis.rpush(
        _control_key(session.id),
        json.dumps({"command": "play", "args": {}, "issued_by": "conn-1"}),
    )
    await asyncio.sleep(0.2)

    fake = capture_driver["driver"]
    assert fake is not None
    fake.trigger_source_ended()

    await asyncio.sleep(0.4)

    driver._shutting_down = True
    await asyncio.wait_for(task, timeout=2.0)

    set_source_commands = [cmd for cmd in fake.commands if isinstance(cmd, tuple) and cmd[0] == "set_source"]
    assert len(set_source_commands) >= 2
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
    await asyncio.sleep(0.1)

    await worker.redis.rpush(
        _control_key(session.id),
        json.dumps({"command": "play", "args": {}, "issued_by": "conn-1"}),
    )
    await asyncio.sleep(0.2)

    await worker.redis.rpush(
        _control_key(session.id),
        json.dumps({"command": "take_control", "args": {}, "issued_by": "conn-2"}),
    )
    await asyncio.sleep(0.3)

    driver._shutting_down = True
    await asyncio.wait_for(task, timeout=2.0)

    fake = capture_driver["driver"]
    set_source_commands = [cmd for cmd in fake.commands if isinstance(cmd, tuple) and cmd[0] == "set_source"]
    assert len(set_source_commands) == 1
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
    await asyncio.sleep(0.1)

    await worker.redis.rpush(
        _control_key(session.id),
        json.dumps({"command": "play", "args": {}, "issued_by": "conn-1"}),
    )
    await asyncio.sleep(0.2)

    fake = capture_driver["driver"]
    assert fake is not None
    # Generation 0 predates the set_source that play triggered.
    fake.trigger_source_ended(generation=0)

    await asyncio.sleep(0.4)

    driver._shutting_down = True
    await asyncio.wait_for(task, timeout=2.0)

    set_source_commands = [cmd for cmd in fake.commands if isinstance(cmd, tuple) and cmd[0] == "set_source"]
    assert len(set_source_commands) == 1

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
    await asyncio.sleep(0.1)

    async def _persist_and_notify(command: str) -> None:
        async with get_session() as db:
            persisted = await db.get(PlaybackSession, session.id)
            assert persisted is not None
            await handle_command(db, persisted, command, {}, "conn-1")
        await worker.redis.rpush(
            _control_key(session.id),
            json.dumps({"command": command, "args": {}, "issued_by": "conn-1"}),
        )

    await _persist_and_notify("pause")
    await asyncio.sleep(0.3)

    fake = capture_driver["driver"]
    assert fake is not None
    assert _find_command(fake, "pause") is not None
    assert fake.is_paused

    await _persist_and_notify("play")
    await asyncio.sleep(0.3)

    driver._shutting_down = True
    await asyncio.wait_for(task, timeout=2.0)

    set_source_commands = [cmd for cmd in fake.commands if isinstance(cmd, tuple) and cmd[0] == "set_source"]
    # One set_source from the startup sync, one from the resume.
    assert len(set_source_commands) == 2
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
    await asyncio.sleep(0.1)

    await worker.redis.rpush(
        _control_key(session.id),
        json.dumps({"command": "play", "args": {}, "issued_by": "conn-1"}),
    )
    await asyncio.sleep(0.2)

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
    await worker.redis.rpush(
        _control_key(session.id),
        json.dumps({"command": "set_queue", "args": {"queue_length": 3}, "issued_by": "conn-1"}),
    )

    await asyncio.sleep(0.3)

    driver._shutting_down = True
    await asyncio.wait_for(task, timeout=2.0)

    fake = capture_driver["driver"]
    set_source_commands = [cmd for cmd in fake.commands if isinstance(cmd, tuple) and cmd[0] == "set_source"]
    assert len(set_source_commands) == 1
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
    await asyncio.sleep(0.1)

    await worker.redis.rpush(
        _control_key(session.id),
        json.dumps({"command": "set_volume", "args": {"volume": 0.3}, "issued_by": "conn-1"}),
    )

    await asyncio.sleep(0.3)

    driver._shutting_down = True
    await asyncio.wait_for(task, timeout=2.0)

    fake = capture_driver["driver"]
    assert fake is not None
    # The startup sync also pushes a set_volume (default 1.0); the command's
    # value must reach the driver too.
    vols = [c[1] for c in fake.commands if isinstance(c, tuple) and c[0] == "set_volume"]
    assert any(v == pytest.approx(0.3) for v in vols)

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
    await asyncio.sleep(0.3)

    driver._shutting_down = True
    await asyncio.wait_for(task, timeout=2.0)

    fake = capture_driver["driver"]
    assert fake is not None
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
    fake_redis_server,
):
    """Editing an output's config pushes reload_output and restarts the driver."""
    init_db(engine=engine, force=True)
    monkeypatch.setattr(SessionDriver, "_resolve_source", _fake_resolve_source)

    from fakeredis import FakeRedis as SyncFakeRedis

    monkeypatch.setattr(
        "songhive.services.playback.get_sync_redis_client",
        lambda config=None: SyncFakeRedis(server=fake_redis_server),
    )

    session, output = await make_session_output(_sample_queue(), state="playing")
    worker = make_worker()
    driver = SessionDriver(worker, session.id, output.id, session.user_id)

    task = asyncio.create_task(driver.run())
    await asyncio.sleep(0.1)
    first = capture_driver["driver"]
    assert first is not None

    await update_output(
        db_session,
        output,
        regular_user,
        worker_config,
        cfg={"items": {"reloaded": True}},
    )
    await db_session.commit()
    await asyncio.sleep(0.4)

    driver._shutting_down = True
    await asyncio.wait_for(task, timeout=2.0)

    second = capture_driver["driver"]
    assert second is not None
    assert second is not first
    assert _find_command(first, "stop") is not None
    assert _find_command(second, "start") is not None
    assert second.config.get("items") == {"reloaded": True}
