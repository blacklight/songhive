"""
Tests for the incoming ActivityPub federation task.
"""

import asyncio
from unittest.mock import patch

from songhive.config.schema import SonghiveConfig
from songhive.models.base import get_session, init_db, reset_db
from songhive.services.auth import create_user
from songhive.tasks.federation import process_incoming


def _make_config(tmp_path, *, enabled=True, **overrides):
    """Build a test configuration with an optional private key path."""
    return SonghiveConfig(
        auth={"secret_key": "a" * 64},
        database={"url": f"sqlite+aiosqlite:///{tmp_path / 'songhive.db'}"},
        federation={
            "enabled": enabled,
            "instance_domain": "music.example.com",
            "private_key_path": tmp_path / "actor.pem",
            **overrides,
        },
    )


def test_process_incoming_noops_when_federation_disabled(tmp_path, caplog):
    """The task does nothing when federation is disabled."""
    config = _make_config(tmp_path, enabled=False)
    activity = {"actor": "https://remote.example/users/bob", "type": "Follow"}

    with (
        patch("songhive.tasks.federation.InboxProcessor") as mock_processor,
        patch("songhive.tasks.federation.load_config", return_value=config),
        patch("songhive.tasks.federation.get_federation_storage") as mock_storage,
    ):
        result = process_incoming(activity)

    assert result is None
    assert not mock_processor.called
    assert not mock_storage.called


def test_process_incoming_skips_blocked_domain(tmp_path):
    """The task drops activities from blocked or non-allowed domains."""
    config = _make_config(
        tmp_path,
        allowed_instances=["allowed.example"],
    )
    activity = {"actor": "https://blocked.example/users/bob", "type": "Follow"}

    with (
        patch("songhive.tasks.federation.InboxProcessor") as mock_processor,
        patch("songhive.tasks.federation.load_config", return_value=config),
        patch("songhive.tasks.federation.get_federation_storage") as mock_storage,
    ):
        result = process_incoming(activity)

    assert result is None
    assert not mock_processor.called
    assert not mock_storage.called


def test_process_incoming_instance_actor(tmp_path):
    """Instance-targeted activities use the instance actor and key, with signatures enabled."""
    config = _make_config(tmp_path)
    activity = {"actor": "https://remote.example/users/bob", "type": "Follow"}

    with (
        patch("songhive.tasks.federation.InboxProcessor") as mock_processor,
        patch("songhive.tasks.federation.load_config", return_value=config),
        patch("songhive.tasks.federation.get_federation_storage") as mock_storage,
    ):
        result = process_incoming(activity)

    expected_actor = "https://music.example.com/ap/actor"
    expected_key_id = f"{expected_actor}#main-key"

    assert result is not None
    mock_storage.assert_called_once_with(config.database.url)
    call_args = mock_processor.call_args
    assert call_args.kwargs["actor_id"] == expected_actor
    assert call_args.kwargs["key_id"] == expected_key_id
    assert call_args.kwargs["private_key"] is not None

    processor_instance = mock_processor.return_value
    process_call = processor_instance.process.call_args
    assert process_call.args[0] == activity
    assert process_call.kwargs["method"] == "POST"
    assert process_call.kwargs["path"] == "/ap/inbox"
    assert process_call.kwargs.get("skip_verification") is not True

    # The generated key should be persisted on disk.
    assert (tmp_path / "actor.pem").exists()
    assert (tmp_path / "actor.pem").stat().st_size > 0


def test_process_incoming_user_actor(engine, tmp_path, monkeypatch):
    """User-targeted activities use the target user's actor and key, with signatures enabled."""
    config = _make_config(tmp_path)
    init_db(engine=engine, force=True)

    async def _create():
        async with get_session() as session:
            return await create_user(
                session,
                username="alice",
                email="alice@example.com",
                password="secret",
                config=config,
            )

    user = asyncio.run(_create())
    assert user.actor_url == "https://music.example.com/users/alice"
    assert user.private_key_pem

    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *_, **__: config)

    with (
        patch("songhive.tasks.federation.InboxProcessor") as mock_processor,
        patch("songhive.tasks.federation.get_federation_storage") as mock_storage,
    ):
        activity = {"actor": "https://remote.example/users/bob", "type": "Follow"}
        result = process_incoming(activity, username="alice")

    expected_actor = "https://music.example.com/users/alice"
    expected_key_id = f"{expected_actor}#main-key"

    assert result is not None
    mock_storage.assert_called_once_with(config.database.url)
    call_args = mock_processor.call_args
    assert call_args.kwargs["actor_id"] == expected_actor
    assert call_args.kwargs["key_id"] == expected_key_id
    assert call_args.kwargs["private_key"] is not None

    processor_instance = mock_processor.return_value
    process_call = processor_instance.process.call_args
    assert process_call.args[0] == activity
    assert process_call.kwargs["method"] == "POST"
    assert process_call.kwargs["path"] == "/ap/inbox"
    assert process_call.kwargs.get("skip_verification") is not True

    reset_db()
