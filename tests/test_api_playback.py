"""API tests for playback session routes."""

import pytest
from fastapi import status


@pytest.mark.asyncio
async def test_get_session(client, regular_user, auth_headers):
    """GET /playback/session returns a fresh session."""
    response = client.get(
        "/api/v1/playback/session",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["state"] == "idle"
    assert data["queue"] == []
    assert data["outputs"] == []


@pytest.mark.asyncio
async def test_select_stream_output(client, regular_user, auth_headers, config):
    """POST /playback/session/outputs attaches a stream output."""
    client.app.state.config.streams.allow_user_created_outputs = True
    out = client.post(
        "/api/v1/outputs",
        headers=auth_headers(regular_user),
        json={"provider_type": "fake", "name": "api stream", "config": {"items": {}}},
    )
    output_id = out.json()["id"]

    response = client.post(
        "/api/v1/playback/session/outputs",
        headers=auth_headers(regular_user),
        json={"output_ids": [output_id]},
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data["outputs"]) == 1
    assert data["outputs"][0]["output_kind"] == "stream"
    assert data["outputs"][0]["output_stream_id"] == output_id
    assert data["outputs"][0]["status"] == "connecting"


@pytest.mark.asyncio
async def test_playback_command_sequence(client, regular_user, auth_headers, config):
    """Command dispatch updates session state and returns it."""
    client.app.state.config.streams.allow_user_created_outputs = True
    queue = [
        {"id": "t1", "title": "Track 1", "artist": "Artist", "duration": 100},
        {"id": "t2", "title": "Track 2", "artist": "Artist", "duration": 100},
    ]

    # set_queue
    response = client.post(
        "/api/v1/playback/session/command",
        headers=auth_headers(regular_user),
        json={"command": "set_queue", "args": {"queue": queue}, "connection_id": "tab-1"},
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["state"] == "idle"

    # play_at
    response = client.post(
        "/api/v1/playback/session/command",
        headers=auth_headers(regular_user),
        json={"command": "play_at", "args": {"index": 0}, "connection_id": "tab-1"},
    )
    data = response.json()
    assert data["state"] == "playing"
    assert data["current_index"] == 0
    assert data["position_anchor_at"] is not None

    # seek
    response = client.post(
        "/api/v1/playback/session/command",
        headers=auth_headers(regular_user),
        json={"command": "seek", "args": {"seconds": 12.5}, "connection_id": "tab-1"},
    )
    data = response.json()
    assert data["position_seconds"] == 12.5

    # next
    response = client.post(
        "/api/v1/playback/session/command",
        headers=auth_headers(regular_user),
        json={"command": "next", "connection_id": "tab-1"},
    )
    data = response.json()
    assert data["current_index"] == 1
    assert data["position_seconds"] == 0

    # pause
    response = client.post(
        "/api/v1/playback/session/command",
        headers=auth_headers(regular_user),
        json={"command": "pause", "connection_id": "tab-1"},
    )
    data = response.json()
    assert data["state"] == "paused"
    assert data["position_anchor_at"] is None


@pytest.mark.asyncio
async def test_another_controller_blocked(client, regular_user, auth_headers, config):
    """A second connection must take control before sending commands."""
    queue = [{"id": "t1", "title": "Track", "artist": "Artist"}]
    client.post(
        "/api/v1/playback/session/command",
        headers=auth_headers(regular_user),
        json={"command": "set_queue", "args": {"queue": queue}, "connection_id": "tab-1"},
    )
    client.post(
        "/api/v1/playback/session/command",
        headers=auth_headers(regular_user),
        json={"command": "play", "connection_id": "tab-1"},
    )

    response = client.post(
        "/api/v1/playback/session/command",
        headers=auth_headers(regular_user),
        json={"command": "pause", "connection_id": "tab-2"},
    )
    assert response.status_code == status.HTTP_409_CONFLICT

    client.post(
        "/api/v1/playback/session/command",
        headers=auth_headers(regular_user),
        json={"command": "take_control", "connection_id": "tab-2"},
    )
    response = client.post(
        "/api/v1/playback/session/command",
        headers=auth_headers(regular_user),
        json={"command": "pause", "connection_id": "tab-2"},
    )
    assert response.status_code == status.HTTP_200_OK
