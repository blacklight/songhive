"""
Tests for output stream and playback session models.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select, text

from songhive.models.output_stream import OutputStream
from songhive.models.playback_session import PlaybackSession, PlaybackSessionOutput


@pytest.fixture
async def output_stream_owner(db_session):
    """Create a user that owns output streams and playback sessions."""
    from songhive.services.auth import create_user

    user = await create_user(
        db_session,
        username="streamer",
        email="streamer@example.com",
        password="secret",
    )
    await db_session.flush()
    await db_session.commit()
    return user


@pytest.mark.asyncio
async def test_create_output_stream(db_session, output_stream_owner):
    """An output stream row can be created and read back."""
    stream = OutputStream(
        user_id=output_stream_owner.id,
        provider_type="fake",
        name="Living room",
        config="encrypted-blob",
    )
    db_session.add(stream)
    await db_session.flush()
    await db_session.refresh(stream)

    assert stream.id
    assert stream.user_id == output_stream_owner.id
    assert stream.provider_type == "fake"
    assert stream.name == "Living room"
    assert stream.config == "encrypted-blob"
    assert stream.enabled is True
    assert stream.output_id == stream.id


@pytest.mark.asyncio
async def test_output_stream_cascade_on_user_delete(db_session, output_stream_owner):
    """Deleting a user removes their output streams."""
    await db_session.execute(text("PRAGMA foreign_keys = ON"))
    stream = OutputStream(
        user_id=output_stream_owner.id,
        provider_type="fake",
        name="Bedroom",
        config="encrypted-blob",
    )
    db_session.add(stream)
    await db_session.flush()
    stream_id = stream.id

    await db_session.delete(output_stream_owner)
    await db_session.commit()

    result = await db_session.execute(select(OutputStream).where(OutputStream.id == stream_id))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_create_playback_session(db_session, output_stream_owner):
    """A playback session row can be created and read back."""
    session = PlaybackSession(
        user_id=output_stream_owner.id,
        state="playing",
        current_index=2,
        position_seconds=12.5,
        position_anchor_at=datetime.now(timezone.utc),
        repeat="all",
        shuffle=True,
        queue=[{"id": "t1", "title": "Song"}],
    )
    db_session.add(session)
    await db_session.flush()
    await db_session.refresh(session)

    assert session.id
    assert session.user_id == output_stream_owner.id
    assert session.state == "playing"
    assert session.current_index == 2
    assert session.position_seconds == 12.5
    assert session.repeat == "all"
    assert session.shuffle is True
    assert len(session.queue) == 1
    assert session.session_id == session.id


@pytest.mark.asyncio
async def test_playback_session_cascade_on_user_delete(db_session, output_stream_owner):
    """Deleting a user removes their playback sessions."""
    await db_session.execute(text("PRAGMA foreign_keys = ON"))
    session = PlaybackSession(
        user_id=output_stream_owner.id,
        state="idle",
    )
    db_session.add(session)
    await db_session.flush()
    session_id = session.id

    await db_session.delete(output_stream_owner)
    await db_session.commit()

    result = await db_session.execute(select(PlaybackSession).where(PlaybackSession.id == session_id))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_playback_session_output_relationship(db_session, output_stream_owner):
    """A session output row links a session to an output stream."""
    stream = OutputStream(
        user_id=output_stream_owner.id,
        provider_type="fake",
        name="Kitchen",
        config="encrypted-blob",
    )
    session = PlaybackSession(
        user_id=output_stream_owner.id,
        state="idle",
    )
    db_session.add(stream)
    db_session.add(session)
    await db_session.flush()

    output = PlaybackSessionOutput(
        session_id=session.id,
        output_kind="stream",
        output_stream_id=stream.id,
        status="connecting",
        latency_offset_ms=250,
    )
    db_session.add(output)
    await db_session.flush()

    assert output.session_id == session.id
    assert output.output_stream_id == stream.id
    assert output.output_kind == "stream"
    assert output.status == "connecting"


@pytest.mark.asyncio
async def test_playback_session_output_cascade_on_session_delete(db_session, output_stream_owner):
    """Deleting a playback session removes its attached outputs."""
    stream = OutputStream(
        user_id=output_stream_owner.id,
        provider_type="fake",
        name="Kitchen",
        config="encrypted-blob",
    )
    session = PlaybackSession(
        user_id=output_stream_owner.id,
        state="idle",
    )
    db_session.add(stream)
    db_session.add(session)
    await db_session.flush()

    output = PlaybackSessionOutput(
        session_id=session.id,
        output_kind="stream",
        output_stream_id=stream.id,
    )
    db_session.add(output)
    await db_session.flush()
    output_id = output.id

    await db_session.delete(session)
    await db_session.commit()

    result = await db_session.execute(select(PlaybackSessionOutput).where(PlaybackSessionOutput.id == output_id))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_user_unique_playback_session(db_session, output_stream_owner):
    """A user can have at most one playback session (v1 single-session)."""
    s1 = PlaybackSession(user_id=output_stream_owner.id, state="idle")
    db_session.add(s1)
    await db_session.flush()

    s2 = PlaybackSession(user_id=output_stream_owner.id, state="paused")
    db_session.add(s2)
    with pytest.raises(Exception):
        await db_session.flush()
