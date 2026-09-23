"""Tests for the native HTTP mountpoint handler (GET /streams/{mount})."""

import asyncio
import base64
import json

import pytest
import tornado.httpclient
import tornado.httpserver
import tornado.web

from songhive.models.base import init_db
from songhive.models.output_stream import OutputStream
from songhive.services.secrets import encrypt_json
from songhive.streaming.mount import StreamMountHandler
from songhive.streams.http import stream_data_key, stream_listener_pattern, stream_meta_key


@pytest.fixture
async def mount_server(config, engine, fake_redis, regular_user, db_session):
    """Run a Tornado app serving only the mountpoint handler on an ephemeral port."""
    init_db(engine=engine, force=True)
    app = tornado.web.Application(
        [(r"/streams/(?P<mount>[^/]+)", StreamMountHandler)],
        config=config,
        redis=fake_redis,
    )
    server = tornado.httpserver.HTTPServer(app)
    server.listen(0, "127.0.0.1")
    sock = next(iter(server._sockets.values()))
    port = sock.getsockname()[1]
    yield f"http://127.0.0.1:{port}", regular_user
    server.stop()
    await server.close_all_connections()


async def _make_http_output(db, user, mount: str = "radio", **overrides) -> OutputStream:
    cfg = {
        "mount": mount,
        "format": "mp3",
        "bitrate": "128k",
        "sample_rate": 44100,
        "name": "Test Radio",
        **overrides,
    }
    output = OutputStream(
        user_id=str(user.id),
        provider_type="http",
        name="test output",
        config=encrypt_json(cfg),
        enabled=True,
    )
    db.add(output)
    await db.flush()
    # The handler reads through its own session — the row must be committed.
    await db.commit()
    return output


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


@pytest.mark.asyncio
async def test_mount_unknown_returns_404(mount_server, fake_redis):
    base, _user = mount_server
    client = tornado.httpclient.AsyncHTTPClient()
    resp = await client.fetch(f"{base}/streams/nope", raise_error=False)
    assert resp.code == 404


@pytest.mark.asyncio
async def test_mount_offline_returns_404(mount_server, fake_redis, db_session):
    base, user = mount_server
    await _make_http_output(db_session, user)
    client = tornado.httpclient.AsyncHTTPClient()
    resp = await client.fetch(f"{base}/streams/radio", raise_error=False)
    assert resp.code == 404


@pytest.mark.asyncio
async def test_mount_streams_tail_and_live_chunks(mount_server, fake_redis, db_session):
    base, user = mount_server
    await _make_http_output(db_session, user)
    await fake_redis.set(
        stream_meta_key("radio"),
        json.dumps({"content_type": "audio/mpeg", "name": "Test Radio", "bitrate": "128k"}),
        ex=30,
    )
    await fake_redis.xadd(stream_data_key("radio"), {"d": _b64(b"old-chunk ")})

    client = tornado.httpclient.AsyncHTTPClient()
    chunks: list[bytes] = []
    request = tornado.httpclient.HTTPRequest(
        f"{base}/streams/radio",
        streaming_callback=chunks.append,
        request_timeout=30,
    )
    fetch = asyncio.ensure_future(client.fetch(request))
    try:
        # Give the handler a moment to attach, then append live data + end.
        await asyncio.sleep(0.2)
        await fake_redis.xadd(stream_data_key("radio"), {"d": _b64(b"live-chunk")})
        await fake_redis.xadd(stream_data_key("radio"), {"end": "1"})
        resp = await asyncio.wait_for(fetch, timeout=15)
    finally:
        client.close()

    assert resp.code == 200
    assert resp.headers.get("Content-Type") == "audio/mpeg"
    assert resp.headers.get("icy-name") == "Test Radio"
    assert resp.headers.get("X-Accel-Buffering") == "no"
    body = b"".join(chunks)
    assert b"old-chunk " in body
    assert b"live-chunk" in body

    # The listener presence key is cleaned up once the stream ends.
    await asyncio.sleep(0.05)
    remaining = [key async for key in fake_redis.scan_iter(match=stream_listener_pattern("radio"))]
    assert remaining == []


@pytest.mark.asyncio
async def test_mount_drops_stale_burst_chunks(mount_server, fake_redis, db_session):
    """Buffered chunks older than the lag cap are not bursted to new listeners."""
    base, user = mount_server
    await _make_http_output(db_session, user)
    await fake_redis.set(stream_meta_key("radio"), json.dumps({"content_type": "audio/mpeg"}), ex=30)
    # Entries with old timestamped IDs: stale relative to the lag cap.
    await fake_redis.xadd(stream_data_key("radio"), {"d": _b64(b"stale-one ")}, id="1-1")
    await fake_redis.xadd(stream_data_key("radio"), {"d": _b64(b"stale-two ")}, id="1-2")

    client = tornado.httpclient.AsyncHTTPClient()
    chunks: list[bytes] = []
    request = tornado.httpclient.HTTPRequest(
        f"{base}/streams/radio",
        streaming_callback=chunks.append,
        request_timeout=30,
    )
    fetch = asyncio.ensure_future(client.fetch(request))
    try:
        await asyncio.sleep(0.2)
        await fake_redis.xadd(stream_data_key("radio"), {"d": _b64(b"live-chunk")})
        await fake_redis.xadd(stream_data_key("radio"), {"end": "1"})
        resp = await asyncio.wait_for(fetch, timeout=15)
    finally:
        client.close()

    assert resp.code == 200
    body = b"".join(chunks)
    assert b"live-chunk" in body
    assert b"stale" not in body


@pytest.mark.asyncio
async def test_mount_drops_stale_tail_chunks(mount_server, fake_redis, db_session, config):
    """A lagging listener's cursor skips stale entries in the tail loop."""
    config.streams.http_stream_burst_entries = 0
    base, user = mount_server
    await _make_http_output(db_session, user)
    await fake_redis.set(stream_meta_key("radio"), json.dumps({"content_type": "audio/mpeg"}), ex=30)
    await fake_redis.xadd(stream_data_key("radio"), {"d": _b64(b"stale-tail ")}, id="1-1")

    client = tornado.httpclient.AsyncHTTPClient()
    chunks: list[bytes] = []
    request = tornado.httpclient.HTTPRequest(
        f"{base}/streams/radio",
        streaming_callback=chunks.append,
        request_timeout=30,
    )
    fetch = asyncio.ensure_future(client.fetch(request))
    try:
        await asyncio.sleep(0.2)
        await fake_redis.xadd(stream_data_key("radio"), {"d": _b64(b"live-chunk")})
        await fake_redis.xadd(stream_data_key("radio"), {"end": "1"})
        resp = await asyncio.wait_for(fetch, timeout=15)
    finally:
        client.close()

    assert resp.code == 200
    body = b"".join(chunks)
    assert b"live-chunk" in body
    assert b"stale" not in body


@pytest.mark.asyncio
async def test_mount_requires_listen_token(mount_server, fake_redis, db_session):
    base, user = mount_server
    await _make_http_output(db_session, user, listen_token="tok123")
    await fake_redis.set(stream_meta_key("radio"), json.dumps({"content_type": "audio/mpeg"}), ex=30)

    client = tornado.httpclient.AsyncHTTPClient()
    denied = await client.fetch(f"{base}/streams/radio", raise_error=False)
    assert denied.code == 403

    # HEAD also enforces the token.
    denied_head = await client.fetch(f"{base}/streams/radio", method="HEAD", raise_error=False)
    assert denied_head.code == 403

    chunks: list[bytes] = []
    request = tornado.httpclient.HTTPRequest(
        f"{base}/streams/radio?token=tok123",
        streaming_callback=chunks.append,
        request_timeout=30,
    )
    fetch = asyncio.ensure_future(client.fetch(request))
    await asyncio.sleep(0.2)
    await fake_redis.xadd(stream_data_key("radio"), {"end": "1"})
    resp = await asyncio.wait_for(fetch, timeout=15)
    client.close()
    assert resp.code == 200
