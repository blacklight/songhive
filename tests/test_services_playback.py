"""
Tests for the playback-session control plane.
"""

from datetime import datetime, timezone

import pytest

from songhive.services.outputs import create_output
from songhive.services.playback import (
    compute_next_index,
    get_or_create_session,
    handle_command,
    select_outputs,
    session_state_dict,
)


@pytest.fixture
async def session_user(db_session):
    """Create a test user with a playback session."""
    from songhive.services.auth import create_user

    user = await create_user(
        db_session,
        username="session_user",
        email="session_user@example.com",
        password="secret",
    )
    await db_session.flush()
    await db_session.commit()
    return user


@pytest.fixture
async def playback_session(db_session, session_user):
    """Return an empty playback session for the test user."""
    return await get_or_create_session(db_session, session_user)


@pytest.fixture
def capture_events(monkeypatch):
    """Patch playback fan-out and control publishing to capture calls."""
    states = []
    commands = []

    class _MockWebSocket:
        @staticmethod
        def send_to_user(user_id, event_type, data):
            states.append(data)

    monkeypatch.setattr("songhive.ws.events.EventWebSocket", _MockWebSocket)

    def _publish_control(session_id, command, args, issued_by):
        commands.append(
            {
                "session_id": session_id,
                "command": command,
                "args": args,
                "issued_by": issued_by,
            }
        )

    monkeypatch.setattr("songhive.services.playback.publish_control_command", _publish_control)
    return states, commands


@pytest.mark.asyncio
async def test_get_or_create_session_creates_once(db_session, session_user):
    """The same session is returned on repeated calls."""
    session = await get_or_create_session(db_session, session_user)
    assert session.user_id == str(session_user.id)
    same = await get_or_create_session(db_session, session_user)
    assert same.id == session.id


@pytest.mark.asyncio
async def test_compute_next_index_repeat_off(db_session, session_user, playback_session):
    """Next/prev respect repeat off."""
    playback_session.queue = [
        {"id": "t1", "title": "a", "artist": "a", "duration": 100},
        {"id": "t2", "title": "b", "artist": "b", "duration": 100},
    ]
    playback_session.current_index = 0
    playback_session.repeat = "off"

    assert compute_next_index(playback_session, direction="next") == 1
    assert compute_next_index(playback_session, direction="prev") is None

    playback_session.current_index = 1
    assert compute_next_index(playback_session, direction="next") is None


@pytest.mark.asyncio
async def test_compute_next_index_repeat_all(db_session, session_user, playback_session):
    """Next/prev wrap when repeat all is on."""
    playback_session.queue = [
        {"id": "t1", "title": "a", "artist": "a"},
        {"id": "t2", "title": "b", "artist": "b"},
    ]
    playback_session.current_index = 1
    playback_session.repeat = "all"

    assert compute_next_index(playback_session, direction="next") == 0
    playback_session.current_index = 0
    assert compute_next_index(playback_session, direction="prev") == 1


@pytest.mark.asyncio
async def test_compute_next_index_repeat_one_and_restart(db_session, session_user, playback_session):
    """Repeat one restarts the current track; prev restarts after 3s."""
    playback_session.queue = [
        {"id": "t1", "title": "a", "artist": "a"},
    ]
    playback_session.current_index = 0
    playback_session.repeat = "one"

    assert compute_next_index(playback_session, direction="next") == 0
    assert compute_next_index(playback_session, direction="prev", position_seconds=5) == 0


@pytest.mark.asyncio
async def test_play_at_and_pause_anchor(db_session, session_user, playback_session, capture_events):
    """play_at starts at 0; pause freezes the live position."""
    states, _ = capture_events
    queue = [
        {"id": "t1", "title": "a", "artist": "a", "duration": 100},
    ]
    await handle_command(db_session, playback_session, "set_queue", {"queue": queue}, "conn-1")
    await db_session.flush()

    assert playback_session.state == "idle"
    await handle_command(db_session, playback_session, "play_at", {"index": 0}, "conn-1")
    assert playback_session.state == "playing"
    assert playback_session.current_index == 0
    assert playback_session.position_seconds == 0
    assert playback_session.position_anchor_at is not None

    # Simulate elapsed time before pausing.
    playback_session.position_anchor_at = datetime.now(timezone.utc)
    await handle_command(db_session, playback_session, "pause", {}, "conn-1")
    assert playback_session.state == "paused"
    assert playback_session.position_anchor_at is None
    assert playback_session.position_seconds >= 0

    assert len(states) >= 3
    assert states[-1]["state"] == "paused"


@pytest.mark.asyncio
async def test_seek_and_next_commands(db_session, session_user, playback_session, capture_events):
    """seek updates position; next advances the index."""
    queue = [
        {"id": "t1", "title": "a", "artist": "a", "duration": 100},
        {"id": "t2", "title": "b", "artist": "b", "duration": 100},
    ]
    await handle_command(db_session, playback_session, "set_queue", {"queue": queue}, "conn-1")
    await handle_command(db_session, playback_session, "play_at", {"index": 0}, "conn-1")

    await handle_command(db_session, playback_session, "seek", {"seconds": 12.5}, "conn-1")
    assert playback_session.position_seconds == 12.5

    await handle_command(db_session, playback_session, "next", {}, "conn-1")
    assert playback_session.current_index == 1
    assert playback_session.position_seconds == 0


@pytest.mark.asyncio
async def test_take_control_and_another_connection_rejected(db_session, session_user, playback_session):
    """A second connection cannot control until it takes control."""
    queue = [{"id": "t1", "title": "a", "artist": "a"}]
    await handle_command(db_session, playback_session, "set_queue", {"queue": queue}, "conn-1")
    await handle_command(db_session, playback_session, "play", {}, "conn-1")
    assert playback_session.controller_connection_id == "conn-1"

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await handle_command(db_session, playback_session, "pause", {}, "conn-2")
    assert exc.value.status_code == 409

    await handle_command(db_session, playback_session, "take_control", {}, "conn-2")
    assert playback_session.controller_connection_id == "conn-2"
    await handle_command(db_session, playback_session, "pause", {}, "conn-2")


@pytest.mark.asyncio
async def test_select_outputs_replaces_and_enforces_single_stream(
    db_session,
    session_user,
    playback_session,
    config,
):
    """Selecting outputs replaces the set and rejects multiple stream outputs."""
    config.streams.allow_user_created_outputs = True
    output = await create_output(
        db_session,
        session_user,
        config,
        provider_type="fake",
        name="fake output",
        cfg={"items": {}},
    )
    await db_session.flush()

    await select_outputs(db_session, playback_session, [str(output.id)], session_user)
    assert len(playback_session.outputs) == 1
    assert playback_session.outputs[0].output_stream_id == output.id

    output2 = await create_output(
        db_session,
        session_user,
        config,
        provider_type="fake",
        name="second output",
        cfg={"items": {}},
    )
    await db_session.flush()

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await select_outputs(db_session, playback_session, [str(output.id), str(output2.id)], session_user)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_session_state_dict_queue_and_outputs(db_session, session_user, playback_session, config):
    """session_state_dict exposes queue and attached output names."""
    config.streams.allow_user_created_outputs = True
    queue = [{"id": "t1", "title": "a", "artist": "a", "duration": 100}]
    await handle_command(db_session, playback_session, "set_queue", {"queue": queue}, "conn-1")

    output = await create_output(
        db_session,
        session_user,
        config,
        provider_type="fake",
        name="named output",
        cfg={"items": {}},
    )
    await db_session.flush()
    await select_outputs(db_session, playback_session, [str(output.id)], session_user)

    state = await session_state_dict(db_session, playback_session)
    assert state["queue"] == queue
    assert state["outputs"][0]["name"] == "named output"
