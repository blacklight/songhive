"""Tests for the live-broadcast ingest WebSocket (/ws/live/{output_id})."""

import asyncio
import base64
import json

import pytest
import tornado.httpserver
import tornado.web
import tornado.websocket

from songhive.api.middleware.auth import create_access_token
from songhive.models.base import init_db
from songhive.models.output_stream import OutputStream
from songhive.services.secrets import encrypt_json
from songhive.streaming.live import LiveIngestHandler
from songhive.streams.http import stream_meta_key
from songhive.streams.live import (
    ingest_key,
    live_key,
    read_live_state,
    worker_heartbeat_key,
)


async def _make_output(db, user, *, provider_type="http", mount="radio", enabled=True) -> OutputStream:
    cfg = {
        "mount": mount,
        "format": "mp3",
        "bitrate": "128k",
        "sample_rate": 44100,
        "name": "Test Radio",
    }
    output = OutputStream(
        user_id=str(user.id),
        provider_type=provider_type,
        name="test output",
        config=encrypt_json(cfg),
        enabled=enabled,
    )
    db.add(output)
    await db.flush()
    await db.commit()
    return output


@pytest.fixture
async def live_server(config, engine, fake_redis, db_session):
    """Run a Tornado app serving only the live ingest handler."""
    init_db(engine=engine, force=True)
    app = tornado.web.Application(
        [(r"/ws/live/(?P<output_id>[^/]+)", LiveIngestHandler)],
        config=config,
        redis=fake_redis,
    )
    server = tornado.httpserver.HTTPServer(app)
    server.listen(0, "127.0.0.1")
    port = next(iter(server._sockets.values())).getsockname()[1]
    yield f"ws://127.0.0.1:{port}"
    server.stop()
    await server.close_all_connections()


def _token_for(config, user) -> str:
    return create_access_token(str(user.id), config.auth.secret_key)


async def _connect(live_server, config, user, output_id) -> tornado.websocket.WebSocketClientConnection:
    url = f"{live_server}/ws/live/{output_id}?token={_token_for(config, user)}"
    return await tornado.websocket.websocket_connect(url)


async def _read_json(ws, timeout: float = 5.0):
    msg = await asyncio.wait_for(ws.read_message(), timeout=timeout)
    if msg is None:
        return None
    return json.loads(msg)


async def _read_until_close(ws, timeout: float = 5.0):
    """Drain messages until the socket closes; returns the last JSON message."""
    last = None

    async def _drain():
        nonlocal last
        while True:
            msg = await ws.read_message()
            if msg is None:
                return
            try:
                last = json.loads(msg)
            except (TypeError, ValueError):
                last = msg

    await asyncio.wait_for(_drain(), timeout=timeout)
    return last


async def _start_broadcast(ws, ingest_id: str = "ingest-0001", mime: str = "audio/webm;codecs=opus"):
    await ws.write_message(json.dumps({"action": "start", "ingest_id": ingest_id, "mime": mime, "title": "Test"}))
    return await _read_json(ws)


@pytest.mark.asyncio
async def test_live_ingest_requires_auth(live_server, fake_redis, db_session, regular_user):
    """An unauthenticated connection is closed with 4001."""
    output = await _make_output(db_session, regular_user)
    ws = await tornado.websocket.websocket_connect(f"{live_server}/ws/live/{output.id}")
    await asyncio.wait_for(ws.read_message(), timeout=5)
    assert ws.close_code == 4001


@pytest.mark.asyncio
async def test_live_ingest_unknown_output(live_server, config, fake_redis, db_session, regular_user):
    """Broadcasting on a missing output closes with 4004."""
    await fake_redis.set(worker_heartbeat_key("w"), "1", ex=10)
    ws = await _connect(live_server, config, regular_user, "no-such-output")
    msg = await _read_until_close(ws)
    assert msg is not None and msg["code"] == "not_found"
    assert ws.close_code == 4004


@pytest.mark.asyncio
async def test_live_ingest_owner_only(
    live_server, config, fake_redis, db_session, regular_user, other_user, admin_user
):
    """Neither another user nor an admin may broadcast on the owner's mount."""
    await fake_redis.set(worker_heartbeat_key("w"), "1", ex=10)
    output = await _make_output(db_session, regular_user)

    for intruder in (other_user, admin_user):
        ws = await _connect(live_server, config, intruder, str(output.id))
        msg = await _read_until_close(ws)
        assert msg is not None and msg["code"] == "forbidden"
        assert ws.close_code == 4003


@pytest.mark.asyncio
async def test_live_ingest_rejects_non_http_output(live_server, config, fake_redis, db_session, regular_user):
    """Only native HTTP mounts may go live."""
    await fake_redis.set(worker_heartbeat_key("w"), "1", ex=10)
    output = await _make_output(db_session, regular_user, provider_type="fake")
    ws = await _connect(live_server, config, regular_user, str(output.id))
    msg = await _read_until_close(ws)
    assert msg is not None and msg["code"] == "forbidden"
    assert ws.close_code == 4003


@pytest.mark.asyncio
async def test_live_ingest_rejects_disabled_output(live_server, config, fake_redis, db_session, regular_user):
    """A disabled output cannot be broadcast on."""
    await fake_redis.set(worker_heartbeat_key("w"), "1", ex=10)
    output = await _make_output(db_session, regular_user, enabled=False)
    ws = await _connect(live_server, config, regular_user, str(output.id))
    msg = await _read_until_close(ws)
    assert msg is not None and msg["code"] == "forbidden"
    assert ws.close_code == 4003


@pytest.mark.asyncio
async def test_live_ingest_feature_disabled(live_server, config, fake_redis, db_session, regular_user):
    """streams.live_enabled off rejects the connection outright."""
    config.streams.live_enabled = False
    output = await _make_output(db_session, regular_user)
    ws = await _connect(live_server, config, regular_user, str(output.id))
    msg = await _read_until_close(ws)
    assert msg is not None and msg["code"] == "disabled"
    assert ws.close_code == 4003


@pytest.mark.asyncio
async def test_live_ingest_requires_worker(live_server, config, fake_redis, db_session, regular_user):
    """Without a stream-worker heartbeat the broadcast fails fast (4503)."""
    output = await _make_output(db_session, regular_user)
    ws = await _connect(live_server, config, regular_user, str(output.id))
    msg = await _read_until_close(ws)
    assert msg is not None and msg["code"] == "worker_unavailable"
    assert ws.close_code == 4503


@pytest.mark.asyncio
async def test_live_ingest_relay_and_stop(live_server, config, fake_redis, db_session, regular_user):
    """Chunks land in the ingest stream and stop appends an end entry."""
    await fake_redis.set(worker_heartbeat_key("w"), "1", ex=10)
    output = await _make_output(db_session, regular_user)
    ws = await _connect(live_server, config, regular_user, str(output.id))

    msg = await _start_broadcast(ws, ingest_id="ingest-0001")
    assert msg == {"type": "accepted", "ingest_id": "ingest-0001"}

    # The live claim is registered for the worker to pick up.
    state = await read_live_state(fake_redis, str(output.id))
    assert state is not None
    assert state.ingest_id == "ingest-0001"
    assert state.user_id == str(regular_user.id)
    assert state.mime == "audio/webm;codecs=opus"

    await ws.write_message(b"chunk-one", binary=True)
    await ws.write_message(b"chunk-two", binary=True)

    # Let the writer task drain, then check the ingest stream.
    await asyncio.sleep(0.2)
    entries = await fake_redis.xrange(ingest_key(str(output.id)))
    kinds = [set(fields) for _, fields in entries]
    assert kinds[0] == {"start", "mime"}
    payload_chunks = [base64.b64decode(fields["d"]) for _, fields in entries if fields.get("d")]
    assert payload_chunks == [b"chunk-one", b"chunk-two"]

    await ws.write_message(json.dumps({"action": "stop"}))
    msg = await _read_json(ws)
    assert msg["type"] == "stopped"
    await asyncio.wait_for(ws.read_message(), timeout=5)
    assert ws.close_code == 1000

    await asyncio.sleep(0.2)
    entries = await fake_redis.xrange(ingest_key(str(output.id)))
    assert any(fields.get("end") == "ingest-0001" for _, fields in entries)
    # The claim is released so a new broadcast can take the slot.
    assert await read_live_state(fake_redis, str(output.id)) is None


@pytest.mark.asyncio
async def test_live_ingest_claim_released_on_close(live_server, config, fake_redis, db_session, regular_user):
    """Dropping the socket releases the live claim for the next broadcast."""
    await fake_redis.set(worker_heartbeat_key("w"), "1", ex=10)
    own = await _make_output(db_session, regular_user)

    ws1 = await _connect(live_server, config, regular_user, str(own.id))
    msg = await _start_broadcast(ws1, ingest_id="ingest-0001")
    assert msg["type"] == "accepted"
    assert await read_live_state(fake_redis, str(own.id)) is not None

    ws1.close()
    for _ in range(50):
        if await read_live_state(fake_redis, str(own.id)) is None:
            break
        await asyncio.sleep(0.05)
    assert await read_live_state(fake_redis, str(own.id)) is None


@pytest.mark.asyncio
async def test_live_ingest_duplicate_start_conflicts(live_server, config, fake_redis, db_session, regular_user):
    """A second start on an active socket is refused; a stale claim by
    another broadcaster blocks the slot entirely."""
    await fake_redis.set(worker_heartbeat_key("w"), "1", ex=10)
    output = await _make_output(db_session, regular_user)
    ws = await _connect(live_server, config, regular_user, str(output.id))

    msg = await _start_broadcast(ws, ingest_id="ingest-0001")
    assert msg["type"] == "accepted"

    await ws.write_message(json.dumps({"action": "start", "ingest_id": "ingest-0002", "mime": "audio/webm"}))
    msg = await _read_json(ws)
    assert msg["code"] == "already_started"
    ws.close()


@pytest.mark.asyncio
async def test_live_ingest_foreign_claim_conflicts(
    live_server, config, fake_redis, db_session, regular_user, other_user
):
    """A live claim owned by another user cannot be taken over."""
    await fake_redis.set(worker_heartbeat_key("w"), "1", ex=10)
    output = await _make_output(db_session, regular_user)
    # Simulate another broadcaster's live claim on the owner's output.
    await fake_redis.set(
        live_key(str(output.id)),
        json.dumps(
            {
                "ingest_id": "foreign-1",
                "user_id": str(other_user.id),
                "mime": "audio/webm",
                "title": "Taken",
                "started_at": 0,
            }
        ),
        ex=30,
    )
    ws = await _connect(live_server, config, regular_user, str(output.id))
    msg = await _start_broadcast(ws, ingest_id="ingest-0001")
    assert msg["code"] == "conflict"
    await _read_until_close(ws)
    assert ws.close_code == 4409


@pytest.mark.asyncio
async def test_live_ingest_same_user_takeover(live_server, config, fake_redis, db_session, regular_user):
    """The owner may supersede their own stale claim (reconnect case)."""
    await fake_redis.set(worker_heartbeat_key("w"), "1", ex=10)
    output = await _make_output(db_session, regular_user)
    await fake_redis.set(
        live_key(str(output.id)),
        json.dumps(
            {
                "ingest_id": "stale-1",
                "user_id": str(regular_user.id),
                "mime": "audio/webm",
                "title": "Old",
                "started_at": 0,
            }
        ),
        ex=30,
    )
    ws = await _connect(live_server, config, regular_user, str(output.id))
    msg = await _start_broadcast(ws, ingest_id="ingest-0001")
    assert msg["type"] == "accepted"
    state = await read_live_state(fake_redis, str(output.id))
    assert state is not None and state.ingest_id == "ingest-0001"
    ws.close()


@pytest.mark.asyncio
async def test_live_ingest_on_air_and_worker_end(live_server, config, fake_redis, db_session, regular_user):
    """The client is told on_air once the worker flags the mount, and stopped
    when the worker drops the flag."""
    await fake_redis.set(worker_heartbeat_key("w"), "1", ex=10)
    output = await _make_output(db_session, regular_user)
    ws = await _connect(live_server, config, regular_user, str(output.id))
    msg = await _start_broadcast(ws, ingest_id="ingest-0001")
    assert msg["type"] == "accepted"

    # Simulate the worker taking the mount over.
    await fake_redis.set(
        stream_meta_key("radio"),
        json.dumps({"live": True, "live_ingest_id": "ingest-0001"}),
        ex=30,
    )
    msg = await _read_json(ws, timeout=5)
    # A listeners update may arrive first; skip to on_air.
    while msg is not None and msg["type"] == "listeners":
        msg = await _read_json(ws, timeout=5)
    assert msg is not None
    assert msg["type"] == "on_air"

    # The worker ending the broadcast notifies and closes the socket.
    await fake_redis.set(
        stream_meta_key("radio"),
        json.dumps({"live": False, "live_ingest_id": None}),
        ex=30,
    )
    msg = await _read_json(ws, timeout=5)
    while msg is not None and msg["type"] == "listeners":
        msg = await _read_json(ws, timeout=5)
    assert msg is not None
    assert msg["type"] == "stopped"
    ws.close()


@pytest.mark.asyncio
async def test_live_ingest_start_timeout(live_server, config, fake_redis, db_session, regular_user):
    """No worker acknowledgement within the start timeout closes with 4415."""
    config.streams.live_start_timeout_seconds = 0.5
    await fake_redis.set(worker_heartbeat_key("w"), "1", ex=10)
    output = await _make_output(db_session, regular_user)
    ws = await _connect(live_server, config, regular_user, str(output.id))
    msg = await _start_broadcast(ws, ingest_id="ingest-0001")
    assert msg["type"] == "accepted"

    msg = await _read_json(ws, timeout=5)
    assert msg is not None and msg["type"] == "error"
    assert msg["code"] == "start_timeout"
    await _read_until_close(ws)
    assert ws.close_code == 4415


@pytest.mark.asyncio
async def test_live_ingest_bad_start(live_server, config, fake_redis, db_session, regular_user):
    """Malformed start messages are rejected with 4003."""
    await fake_redis.set(worker_heartbeat_key("w"), "1", ex=10)
    output = await _make_output(db_session, regular_user)

    ws = await _connect(live_server, config, regular_user, str(output.id))
    await ws.write_message(json.dumps({"action": "start", "ingest_id": "bad id!", "mime": "audio/webm"}))
    msg = await _read_json(ws)
    assert msg["code"] == "bad_ingest_id"
    await asyncio.wait_for(ws.read_message(), timeout=5)
    assert ws.close_code == 4003
    ws.close()

    ws = await _connect(live_server, config, regular_user, str(output.id))
    await ws.write_message(json.dumps({"action": "start", "ingest_id": "ingest-0001", "mime": "audio/flac"}))
    msg = await _read_json(ws)
    assert msg["code"] == "unsupported_format"
    await asyncio.wait_for(ws.read_message(), timeout=5)
    assert ws.close_code == 4003
    ws.close()


@pytest.mark.asyncio
async def test_live_ingest_binary_before_start(live_server, config, fake_redis, db_session, regular_user):
    """Audio before the start message is a protocol error."""
    await fake_redis.set(worker_heartbeat_key("w"), "1", ex=10)
    output = await _make_output(db_session, regular_user)
    ws = await _connect(live_server, config, regular_user, str(output.id))
    await ws.write_message(b"\x00binary", binary=True)
    msg = await _read_json(ws)
    assert msg is not None and msg["code"] == "expected_start"
    await asyncio.wait_for(ws.read_message(), timeout=5)
    assert ws.close_code == 4003
