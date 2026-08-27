"""
Admin CLI command tests.
"""

import asyncio
import os
import shutil
import subprocess
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from songhive.cli import admin as cli_admin
from songhive.config.schema import SonghiveConfig, StorageConfig
from songhive.models.artist import Artist
from songhive.models.base import Base
from songhive.models.stored_file import StoredFile
from songhive.models.track import Track
from songhive.models.user import User  # noqa: F401
from songhive.services.auth import create_user
from songhive.users.invites import create_invite


@asynccontextmanager
async def _fake_session(session):
    """Yield a pre-existing session as if it came from get_session()."""
    yield session


def _make_args(admin=False, role=None, **kwargs):
    """Build a simple argparse-like namespace."""
    return SimpleNamespace(admin=admin, role=role, **kwargs)


@pytest.mark.asyncio
async def test_create_user_handler(db_session, monkeypatch, capsys):
    """Test the create-user handler."""
    monkeypatch.setattr(cli_admin, "get_session", lambda: _fake_session(db_session))
    args = _make_args(username="alice", email="alice@example.com", password="secret", admin=False)
    await cli_admin._handle_create_user(args)
    captured = capsys.readouterr()
    assert "User 'alice' created successfully" in captured.out


@pytest.mark.asyncio
async def test_create_user_handler_admin_flag(db_session, monkeypatch, capsys):
    """Test the create-user handler with the admin flag."""
    monkeypatch.setattr(cli_admin, "get_session", lambda: _fake_session(db_session))
    args = _make_args(username="admin", email="admin@example.com", password="secret", admin=True)
    await cli_admin._handle_create_user(args)
    from songhive.services.auth import get_user_by_username

    user = await get_user_by_username(db_session, "admin")
    assert user is not None
    assert user.is_admin is True


@pytest.mark.asyncio
async def test_promote_user_handler(db_session, monkeypatch, capsys):
    """Test the promote-user handler."""
    from songhive.services.auth import create_user

    user = await create_user(db_session, "alice", "alice@example.com", "secret", role="user")
    await db_session.flush()

    monkeypatch.setattr(cli_admin, "get_session", lambda: _fake_session(db_session))
    args = _make_args(username="alice")
    await cli_admin._handle_promote_user(args)
    captured = capsys.readouterr()
    assert "User 'alice' promoted to admin" in captured.out
    assert user.is_admin is True


@pytest.mark.asyncio
async def test_promote_user_handler_missing_user(db_session, monkeypatch, capsys):
    """Test that promoting a missing user exits with an error."""
    monkeypatch.setattr(cli_admin, "get_session", lambda: _fake_session(db_session))
    args = _make_args(username="nobody")
    with pytest.raises(SystemExit) as exc_info:
        await cli_admin._handle_promote_user(args)
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "not found" in captured.err


def test_admin_main_no_command(monkeypatch, capsys):
    """Test that admin_main prints help and exits when no command is given."""
    monkeypatch.setattr(cli_admin, "load_config", lambda argv: SonghiveConfig())
    monkeypatch.setattr(cli_admin, "init_db", lambda url: None)
    with pytest.raises(SystemExit) as exc_info:
        cli_admin.admin_main([])
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "usage:" in captured.out


class _FakeParser:
    """A parser that always reports an unknown command."""

    def parse_args(self, argv):
        return SimpleNamespace(command="unknown")

    def print_help(self):
        print("usage: fake")


def test_admin_main_unknown_command(monkeypatch, capsys):
    """Test that admin_main prints help and exits for an unknown command."""
    monkeypatch.setattr(cli_admin, "load_config", lambda argv: SonghiveConfig())
    monkeypatch.setattr(cli_admin, "init_db", lambda url: None)
    monkeypatch.setattr(cli_admin, "_create_admin_parser", _FakeParser)
    with pytest.raises(SystemExit) as exc_info:
        cli_admin.admin_main([])
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "usage:" in captured.out


class _LocalSessionFactory:
    """Create a fresh session and engine each time it is used."""

    def __init__(self, database_url: str):
        self.database_url = database_url

    async def __aenter__(self):
        self.engine = create_async_engine(self.database_url)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.session = self.factory()
        await self.session.__aenter__()
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is None:
            await self.session.commit()
        else:
            await self.session.rollback()
        await self.session.__aexit__(exc_type, exc, tb)
        await self.engine.dispose()


async def _get_user_by_username(database_url: str, username: str):
    """Load a user from the database at the given URL."""
    from songhive.services.auth import get_user_by_username

    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        user = await get_user_by_username(session, username)
    await engine.dispose()
    return user


def test_admin_main_create_user(tmp_path, monkeypatch):
    """Test the full create-user CLI command."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'admin.db'}"
    monkeypatch.setattr(
        cli_admin,
        "load_config",
        lambda argv: SonghiveConfig(database={"url": db_url}, federation={"enabled": False}),
    )
    monkeypatch.setattr(cli_admin, "init_db", lambda url: None)
    monkeypatch.setattr(cli_admin, "get_session", lambda: _LocalSessionFactory(db_url))

    cli_admin.admin_main(["create-user", "--username", "alice", "--email", "alice@example.com", "--password", "secret"])

    user = asyncio.run(_get_user_by_username(db_url, "alice"))
    assert user is not None
    assert user.username == "alice"
    assert user.email == "alice@example.com"
    assert user.is_admin is False


def test_admin_main_promote_user(tmp_path, monkeypatch):
    """Test the full promote-user CLI command."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'admin.db'}"
    monkeypatch.setattr(
        cli_admin,
        "load_config",
        lambda argv: SonghiveConfig(database={"url": db_url}, federation={"enabled": False}),
    )
    monkeypatch.setattr(cli_admin, "init_db", lambda url: None)
    monkeypatch.setattr(cli_admin, "get_session", lambda: _LocalSessionFactory(db_url))

    cli_admin.admin_main(["create-user", "--username", "alice", "--email", "alice@example.com", "--password", "secret"])
    cli_admin.admin_main(["promote-user", "--username", "alice"])

    user = asyncio.run(_get_user_by_username(db_url, "alice"))
    assert user is not None
    assert user.is_admin is True


@pytest.mark.asyncio
async def test_create_invite_handler(db_session, monkeypatch, capsys):
    """Test the create-invite handler."""
    await create_user(db_session, "admin", "admin@example.com", "secret", role="admin")
    await db_session.flush()

    monkeypatch.setattr(cli_admin, "get_session", lambda: _fake_session(db_session))
    args = _make_args(created_by="admin", max_uses=5, expires_at=None)
    await cli_admin._handle_create_invite(args)
    captured = capsys.readouterr()
    assert "Invite code:" in captured.out


@pytest.mark.asyncio
async def test_create_invite_handler_with_expires_at(db_session, monkeypatch, capsys):
    """Test the create-invite handler with an expiration."""
    await create_user(db_session, "admin", "admin@example.com", "secret", role="admin")
    await db_session.flush()

    monkeypatch.setattr(cli_admin, "get_session", lambda: _fake_session(db_session))
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    args = _make_args(created_by="admin", max_uses=5, expires_at=expires_at)
    await cli_admin._handle_create_invite(args)
    captured = capsys.readouterr()
    assert "Invite code:" in captured.out
    assert "expires_at" in captured.out


@pytest.mark.asyncio
async def test_create_invite_handler_missing_user(db_session, monkeypatch, capsys):
    """Test that create-invite fails when the creator does not exist."""
    monkeypatch.setattr(cli_admin, "get_session", lambda: _fake_session(db_session))
    args = _make_args(created_by="nobody", max_uses=None, expires_at=None)
    with pytest.raises(SystemExit) as exc_info:
        await cli_admin._handle_create_invite(args)
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "not found" in captured.err


@pytest.mark.asyncio
async def test_create_invite_handler_invalid_expires_at(db_session, monkeypatch, capsys):
    """Test that create-invite fails for an invalid expiration."""
    await create_user(db_session, "admin", "admin@example.com", "secret", role="admin")
    await db_session.flush()

    monkeypatch.setattr(cli_admin, "get_session", lambda: _fake_session(db_session))
    args = _make_args(created_by="admin", max_uses=None, expires_at="not-a-date")
    with pytest.raises(SystemExit) as exc_info:
        await cli_admin._handle_create_invite(args)
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "invalid expires_at" in captured.err


@pytest.mark.asyncio
async def test_list_invites_handler(db_session, monkeypatch, capsys):
    """Test the list-invites handler."""
    admin = await create_user(db_session, "admin", "admin@example.com", "secret", role="admin")
    await db_session.flush()
    await create_invite(db_session, created_by=admin.id, max_uses=3)
    await db_session.flush()

    monkeypatch.setattr(cli_admin, "get_session", lambda: _fake_session(db_session))
    args = _make_args()
    await cli_admin._handle_list_invites(args)
    captured = capsys.readouterr()
    assert "max_uses=3" in captured.out
    assert "uses=0" in captured.out


@pytest.mark.asyncio
async def test_list_invites_handler_empty(db_session, monkeypatch, capsys):
    """Test the list-invites handler when no invites exist."""
    monkeypatch.setattr(cli_admin, "get_session", lambda: _fake_session(db_session))
    args = _make_args()
    await cli_admin._handle_list_invites(args)
    captured = capsys.readouterr()
    assert "No invite codes found" in captured.out


def test_admin_main_create_invite(tmp_path, monkeypatch):
    """Test the full create-invite CLI command."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'admin.db'}"
    monkeypatch.setattr(
        cli_admin,
        "load_config",
        lambda argv: SonghiveConfig(database={"url": db_url}, federation={"enabled": False}),
    )
    monkeypatch.setattr(cli_admin, "init_db", lambda url: None)
    monkeypatch.setattr(cli_admin, "get_session", lambda: _LocalSessionFactory(db_url))

    cli_admin.admin_main(
        ["create-user", "--username", "admin", "--email", "admin@example.com", "--password", "secret", "--admin"]
    )
    cli_admin.admin_main(["create-invite", "--created-by", "admin", "--max-uses", "5"])

    def _count_invites(database_url: str) -> int:
        from songhive.users.invites import count_invites

        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        async def _run():
            async with factory() as session:
                return await count_invites(session)

        result = asyncio.run(_run())
        return result

    assert _count_invites(db_url) == 1


def test_admin_main_list_invites(tmp_path, monkeypatch, capsys):
    """Test the full list-invites CLI command."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'admin.db'}"
    monkeypatch.setattr(
        cli_admin,
        "load_config",
        lambda argv: SonghiveConfig(database={"url": db_url}, federation={"enabled": False}),
    )
    monkeypatch.setattr(cli_admin, "init_db", lambda url: None)
    monkeypatch.setattr(cli_admin, "get_session", lambda: _LocalSessionFactory(db_url))

    cli_admin.admin_main(
        ["create-user", "--username", "admin", "--email", "admin@example.com", "--password", "secret", "--admin"]
    )
    cli_admin.admin_main(["create-invite", "--created-by", "admin", "--max-uses", "2"])
    cli_admin.admin_main(["list-invites"])

    captured = capsys.readouterr()
    assert "max_uses=2" in captured.out
    assert "uses=0" in captured.out


@pytest.mark.asyncio
async def test_create_user_handler_role_option(db_session, monkeypatch, capsys):
    """Test the create-user handler with the --role option."""
    monkeypatch.setattr(cli_admin, "get_session", lambda: _fake_session(db_session))
    args = _make_args(username="mod", email="mod@example.com", password="secret", admin=False, role="moderator")
    await cli_admin._handle_create_user(args)
    from songhive.services.auth import get_user_by_username

    user = await get_user_by_username(db_session, "mod")
    assert user is not None
    assert user.role == "moderator"


@pytest.mark.asyncio
async def test_demote_user_handler(db_session, monkeypatch, capsys):
    """Test the demote-user handler."""
    await create_user(db_session, "keeper", "keeper@example.com", "secret", role="admin")
    user = await create_user(db_session, "alice", "alice@example.com", "secret", role="admin")
    await db_session.flush()

    monkeypatch.setattr(cli_admin, "get_session", lambda: _fake_session(db_session))
    args = _make_args(username="alice")
    await cli_admin._handle_demote_user(args)
    captured = capsys.readouterr()
    assert "demoted to user" in captured.out
    assert user.role == "user"


@pytest.mark.asyncio
async def test_demote_user_handler_last_admin(db_session, monkeypatch, capsys):
    """Test that demote-user guards the last active admin."""
    await create_user(db_session, "admin", "admin@example.com", "secret", role="admin")
    await db_session.flush()

    monkeypatch.setattr(cli_admin, "get_session", lambda: _fake_session(db_session))
    args = _make_args(username="admin")
    with pytest.raises(SystemExit) as exc_info:
        await cli_admin._handle_demote_user(args)
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "Cannot demote the last active admin" in captured.err


@pytest.mark.asyncio
async def test_demote_user_handler_missing_user(db_session, monkeypatch, capsys):
    """Test that demoting a missing user exits with an error."""
    monkeypatch.setattr(cli_admin, "get_session", lambda: _fake_session(db_session))
    args = _make_args(username="nobody")
    with pytest.raises(SystemExit) as exc_info:
        await cli_admin._handle_demote_user(args)
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "not found" in captured.err


@pytest.mark.asyncio
async def test_approve_user_handler(db_session, monkeypatch, capsys):
    """Test the approve-user handler."""
    user = await create_user(
        db_session,
        "pending",
        "pending@example.com",
        "secret",
        role="user",
        is_active=False,
    )
    await db_session.flush()

    monkeypatch.setattr(cli_admin, "get_session", lambda: _fake_session(db_session))
    args = _make_args(username="pending")
    await cli_admin._handle_approve_user(args)
    captured = capsys.readouterr()
    assert "approved and activated" in captured.out
    assert user.is_active is True


@pytest.mark.asyncio
async def test_approve_user_handler_missing_user(db_session, monkeypatch, capsys):
    """Test that approving a missing user exits with an error."""
    monkeypatch.setattr(cli_admin, "get_session", lambda: _fake_session(db_session))
    args = _make_args(username="nobody")
    with pytest.raises(SystemExit) as exc_info:
        await cli_admin._handle_approve_user(args)
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "not found" in captured.err


def test_admin_main_create_user_with_role(tmp_path, monkeypatch):
    """Test the full create-user CLI command with --role."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'admin.db'}"
    monkeypatch.setattr(
        cli_admin,
        "load_config",
        lambda argv: SonghiveConfig(database={"url": db_url}, federation={"enabled": False}),
    )
    monkeypatch.setattr(cli_admin, "init_db", lambda url: None)
    monkeypatch.setattr(cli_admin, "get_session", lambda: _LocalSessionFactory(db_url))

    cli_admin.admin_main(
        [
            "create-user",
            "--username",
            "bob",
            "--email",
            "bob@example.com",
            "--password",
            "secret",
            "--role",
            "moderator",
        ]
    )

    user = asyncio.run(_get_user_by_username(db_url, "bob"))
    assert user is not None
    assert user.role == "moderator"


def test_admin_main_demote_user(tmp_path, monkeypatch):
    """Test the full demote-user CLI command."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'admin.db'}"
    monkeypatch.setattr(
        cli_admin,
        "load_config",
        lambda argv: SonghiveConfig(database={"url": db_url}, federation={"enabled": False}),
    )
    monkeypatch.setattr(cli_admin, "init_db", lambda url: None)
    monkeypatch.setattr(cli_admin, "get_session", lambda: _LocalSessionFactory(db_url))

    cli_admin.admin_main(
        ["create-user", "--username", "admin", "--email", "admin@example.com", "--password", "secret", "--admin"]
    )
    cli_admin.admin_main(
        ["create-user", "--username", "bob", "--email", "bob@example.com", "--password", "secret", "--admin"]
    )
    cli_admin.admin_main(["demote-user", "--username", "bob"])

    user = asyncio.run(_get_user_by_username(db_url, "bob"))
    assert user is not None
    assert user.role == "user"


def test_admin_main_approve_user(tmp_path, monkeypatch):
    """Test the full approve-user CLI command."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'admin.db'}"
    monkeypatch.setattr(
        cli_admin,
        "load_config",
        lambda argv: SonghiveConfig(database={"url": db_url}, federation={"enabled": False}),
    )
    monkeypatch.setattr(cli_admin, "init_db", lambda url: None)
    monkeypatch.setattr(cli_admin, "get_session", lambda: _LocalSessionFactory(db_url))

    async def _create_inactive_user():
        async with _LocalSessionFactory(db_url) as session:
            await create_user(session, "pending", "pending@example.com", "secret", role="user", is_active=False)

    asyncio.run(_create_inactive_user())
    cli_admin.admin_main(["approve-user", "--username", "pending"])

    user = asyncio.run(_get_user_by_username(db_url, "pending"))
    assert user is not None
    assert user.is_active is True


def test_admin_main_provision_federation_keys(tmp_path, monkeypatch, capsys):
    """Test the provision-federation-keys CLI command."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'provision.db'}"

    def _load_config(argv=None):
        return SonghiveConfig(
            database={"url": db_url},
            federation={"enabled": False},
        )

    monkeypatch.setattr(cli_admin, "load_config", _load_config)
    monkeypatch.setattr(cli_admin, "init_db", lambda url: None)
    monkeypatch.setattr(cli_admin, "get_session", lambda: _LocalSessionFactory(db_url))

    cli_admin.admin_main(["create-user", "--username", "alice", "--email", "alice@example.com", "--password", "secret"])

    user = asyncio.run(_get_user_by_username(db_url, "alice"))
    assert user is not None
    assert user.actor_url is None

    def _load_config_enabled(argv=None):
        return SonghiveConfig(
            database={"url": db_url},
            federation={
                "enabled": True,
                "instance_domain": "music.example.com",
            },
        )

    monkeypatch.setattr(cli_admin, "load_config", _load_config_enabled)
    cli_admin.admin_main(["provision-federation-keys"])

    user = asyncio.run(_get_user_by_username(db_url, "alice"))
    assert user is not None
    assert user.actor_url == "https://music.example.com/users/alice"
    assert user.public_key_pem
    assert user.private_key_pem

    captured = capsys.readouterr()
    assert "Provisioned federation keys for 1 user(s)" in captured.out


def test_admin_main_init_db(tmp_path, monkeypatch, capsys):
    """Test the full init-db CLI command."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'init.db'}"
    monkeypatch.setattr(
        cli_admin,
        "load_config",
        lambda argv: SonghiveConfig(database={"url": db_url}, federation={"enabled": False}),
    )

    cli_admin.admin_main(["init-db"])

    captured = capsys.readouterr()
    assert "Database tables initialized successfully" in captured.out


def test_admin_main_migrate(tmp_path, monkeypatch, capsys):
    """Test the full migrate CLI command."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'migrate.db'}"
    monkeypatch.setattr(
        cli_admin,
        "load_config",
        lambda argv: SonghiveConfig(database={"url": db_url}, federation={"enabled": False}),
    )

    cli_admin.admin_main(["migrate"])

    captured = capsys.readouterr()
    assert "Migrations applied successfully" in captured.out


class _FakeCeleryResult:
    """A minimal Celery AsyncResult stand-in."""

    def __init__(self, task_id: str):
        self.id = task_id


class _FakeScanDir:
    """Stand-in for the Celery ``scan_directory`` task."""

    def __init__(self, delay_result=None, call_result=0, raise_delay=None, raise_call=None):
        self.delay_result = delay_result
        self.call_result = call_result
        self.raise_delay = raise_delay
        self.raise_call = raise_call

    def delay(self, path: str, library_id: str, owner_id=None):
        if self.raise_delay:
            raise self.raise_delay
        return self.delay_result

    def __call__(self, path: str, library_id: str, owner_id=None):
        if self.raise_call:
            raise self.raise_call
        return self.call_result


@pytest.mark.asyncio
async def test_reset_password_handler(db_session, monkeypatch, capsys):
    """Test the reset-password handler."""
    user = await create_user(db_session, "alice", "alice@example.com", "secret", role="user")
    await db_session.flush()

    monkeypatch.setattr(cli_admin, "get_session", lambda: _fake_session(db_session))
    args = _make_args(username="alice", password="newsecret")
    await cli_admin._handle_reset_password(args)
    captured = capsys.readouterr()
    assert "Password reset for user 'alice'" in captured.out
    assert user.password_hash is not None


@pytest.mark.asyncio
async def test_reset_password_handler_missing_user(db_session, monkeypatch, capsys):
    """Test that resetting a missing user's password exits with an error."""
    monkeypatch.setattr(cli_admin, "get_session", lambda: _fake_session(db_session))
    args = _make_args(username="nobody", password="newsecret")
    with pytest.raises(SystemExit) as exc_info:
        await cli_admin._handle_reset_password(args)
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "not found" in captured.err


@pytest.mark.asyncio
async def test_reset_password_handler_change_error(db_session, monkeypatch, capsys):
    """Test that reset-password exits when the password change fails."""
    await create_user(db_session, "alice", "alice@example.com", "secret", role="user")
    await db_session.flush()

    monkeypatch.setattr(cli_admin, "get_session", lambda: _fake_session(db_session))
    monkeypatch.setattr(
        cli_admin.user_manager,
        "change_password",
        AsyncMock(side_effect=cli_admin.user_manager.UserManagementError("too weak")),
    )

    args = _make_args(username="alice", password="newsecret")
    with pytest.raises(SystemExit) as exc_info:
        await cli_admin._handle_reset_password(args)
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "too weak" in captured.err


@pytest.mark.asyncio
async def test_disable_user_handler(db_session, monkeypatch, capsys):
    """Test the disable-user handler."""
    user = await create_user(db_session, "alice", "alice@example.com", "secret", role="user")
    await db_session.flush()

    monkeypatch.setattr(cli_admin, "get_session", lambda: _fake_session(db_session))
    args = _make_args(username="alice")
    await cli_admin._handle_disable_user(args)
    captured = capsys.readouterr()
    assert "deactivated" in captured.out
    assert user.is_active is False


@pytest.mark.asyncio
async def test_disable_user_handler_missing_user(db_session, monkeypatch, capsys):
    """Test that disabling a missing user exits with an error."""
    monkeypatch.setattr(cli_admin, "get_session", lambda: _fake_session(db_session))
    args = _make_args(username="nobody")
    with pytest.raises(SystemExit) as exc_info:
        await cli_admin._handle_disable_user(args)
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "not found" in captured.err


@pytest.mark.asyncio
async def test_disable_user_handler_last_admin(db_session, monkeypatch, capsys):
    """Test that disable-user guards the last active admin."""
    await create_user(db_session, "admin", "admin@example.com", "secret", role="admin")
    await db_session.flush()

    monkeypatch.setattr(cli_admin, "get_session", lambda: _fake_session(db_session))
    args = _make_args(username="admin")
    with pytest.raises(SystemExit) as exc_info:
        await cli_admin._handle_disable_user(args)
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "Cannot deactivate the last active admin" in captured.err


@pytest.mark.asyncio
async def test_promote_user_handler_error(db_session, monkeypatch, capsys):
    """Test that promote-user exits on a user management error."""
    await create_user(db_session, "alice", "alice@example.com", "secret", role="user")
    await db_session.flush()

    monkeypatch.setattr(cli_admin, "get_session", lambda: _fake_session(db_session))
    monkeypatch.setattr(
        cli_admin.user_manager,
        "promote_user",
        AsyncMock(side_effect=cli_admin.user_manager.UserManagementError("bad")),
    )

    args = _make_args(username="alice")
    with pytest.raises(SystemExit) as exc_info:
        await cli_admin._handle_promote_user(args)
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "bad" in captured.err


@pytest.mark.asyncio
async def test_approve_user_handler_error(db_session, monkeypatch, capsys):
    """Test that approve-user exits on a user management error."""
    await create_user(db_session, "pending", "pending@example.com", "secret", role="user", is_active=False)
    await db_session.flush()

    monkeypatch.setattr(cli_admin, "get_session", lambda: _fake_session(db_session))
    monkeypatch.setattr(
        cli_admin.user_manager,
        "approve_user",
        AsyncMock(side_effect=cli_admin.user_manager.UserManagementError("bad")),
    )

    args = _make_args(username="pending")
    with pytest.raises(SystemExit) as exc_info:
        await cli_admin._handle_approve_user(args)
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "bad" in captured.err


def test_parse_iso_datetime_with_z():
    """Test parsing an ISO 8601 timestamp with a 'Z' suffix."""
    result = cli_admin._parse_iso_datetime("2026-01-01T00:00:00Z")
    assert result.tzinfo is not None
    assert result.isoformat() == "2026-01-01T00:00:00+00:00"


def test_parse_iso_datetime_naive():
    """Test parsing a naive ISO 8601 timestamp."""
    result = cli_admin._parse_iso_datetime("2026-01-01T00:00:00")
    assert result.tzinfo == timezone.utc


@pytest.mark.asyncio
async def test_create_invite_handler_with_z_expires_at(db_session, monkeypatch, capsys):
    """Test the create-invite handler with a 'Z' suffix expires_at."""
    await create_user(db_session, "admin", "admin@example.com", "secret", role="admin")
    await db_session.flush()

    monkeypatch.setattr(cli_admin, "get_session", lambda: _fake_session(db_session))
    args = _make_args(created_by="admin", max_uses=5, expires_at="2099-01-01T00:00:00Z")
    await cli_admin._handle_create_invite(args)
    captured = capsys.readouterr()
    assert "Invite code:" in captured.out


@pytest.mark.asyncio
async def test_create_invite_handler_invalid_max_uses(db_session, monkeypatch, capsys):
    """Test that create-invite exits on an InviteError."""
    await create_user(db_session, "admin", "admin@example.com", "secret", role="admin")
    await db_session.flush()

    monkeypatch.setattr(cli_admin, "get_session", lambda: _fake_session(db_session))
    args = _make_args(created_by="admin", max_uses=0, expires_at=None)
    with pytest.raises(SystemExit) as exc_info:
        await cli_admin._handle_create_invite(args)
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "max_uses must be a positive integer" in captured.err


@pytest.mark.asyncio
async def test_list_invites_handler_with_expiration(db_session, monkeypatch, capsys):
    """Test the list-invites handler with an expiring invite."""
    admin = await create_user(db_session, "admin", "admin@example.com", "secret", role="admin")
    await db_session.flush()
    expires = datetime(2099, 1, 1, tzinfo=timezone.utc)
    invite = await create_invite(db_session, created_by=admin.id, max_uses=1, expires_at=expires)
    await db_session.flush()

    monkeypatch.setattr(cli_admin, "get_session", lambda: _fake_session(db_session))
    await cli_admin._handle_list_invites(_make_args())
    captured = capsys.readouterr()
    assert invite.code in captured.out
    assert "2099-01-01T00:00:00+00:00" in captured.out


def test_normalize_scan_roots(tmp_path):
    """Test that scan roots are resolved and filtered to existing directories."""
    existing = tmp_path / "exists"
    existing.mkdir()
    missing = tmp_path / "missing"
    file_path = tmp_path / "file.txt"
    file_path.write_text("x")

    roots = [str(existing), str(missing), str(file_path)]
    normalized = cli_admin._normalize_scan_roots(roots)
    assert normalized == [existing.resolve()]


def test_count_audio_files(tmp_path):
    """Test counting audio files under a directory."""
    (tmp_path / "track.mp3").write_text("audio")
    (tmp_path / "notes.txt").write_text("text")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "deep.flac").write_text("audio")

    assert cli_admin._count_audio_files(tmp_path) == 2


def test_validate_import_path_success(tmp_path):
    """Test resolving a valid import path within a scan root."""
    root = tmp_path / "media"
    root.mkdir()
    target = root / "imports"
    target.mkdir()
    config = SonghiveConfig(
        imports={"scan_roots": [str(root)]},
        federation={"enabled": False},
    )

    resolved = cli_admin._validate_import_path(config, target)
    assert resolved == target.resolve()


def test_validate_import_path_not_exists(tmp_path):
    """Test that a non-existent import path is rejected."""
    config = SonghiveConfig(
        imports={"scan_roots": [str(tmp_path)]},
        federation={"enabled": False},
    )
    with pytest.raises(ValueError, match="does not exist"):
        cli_admin._validate_import_path(config, tmp_path / "missing")


def test_validate_import_path_not_directory(tmp_path):
    """Test that a file path is rejected as an import directory."""
    file_path = tmp_path / "file.txt"
    file_path.write_text("x")
    config = SonghiveConfig(
        imports={"scan_roots": [str(tmp_path)]},
        federation={"enabled": False},
    )
    with pytest.raises(ValueError, match="not a directory"):
        cli_admin._validate_import_path(config, file_path)


def test_validate_import_path_no_scan_roots(tmp_path):
    """Test that importing without configured scan roots is rejected."""
    target = tmp_path / "imports"
    target.mkdir()
    config = SonghiveConfig(
        imports={"scan_roots": []},
        federation={"enabled": False},
    )
    with pytest.raises(ValueError, match="No configured scan roots"):
        cli_admin._validate_import_path(config, target)


def test_validate_import_path_outside_root(tmp_path):
    """Test that an import path outside the scan roots is rejected."""
    root = tmp_path / "media"
    root.mkdir()
    outside = tmp_path / "other"
    outside.mkdir()
    config = SonghiveConfig(
        imports={"scan_roots": [str(root)]},
        federation={"enabled": False},
    )
    with pytest.raises(ValueError, match="not within any configured scan root"):
        cli_admin._validate_import_path(config, outside)


@pytest.mark.asyncio
async def test_import_dir_handler_success(tmp_path, monkeypatch, capsys):
    """Test the import-dir handler queuing a Celery task."""
    root = tmp_path / "media"
    root.mkdir()
    import_dir = root / "imports"
    import_dir.mkdir()

    config = SonghiveConfig(
        imports={"scan_roots": [str(root)]},
        federation={"enabled": False},
    )
    monkeypatch.setattr(cli_admin, "load_config", lambda argv: config)
    monkeypatch.setattr(
        cli_admin,
        "scan_directory",
        _FakeScanDir(delay_result=_FakeCeleryResult("task-123")),
    )

    args = _make_args(path=str(import_dir), library_id="lib-1", owner=None)
    await cli_admin._handle_import_dir(args)
    captured = capsys.readouterr()
    assert "Import queued with task id: task-123" in captured.out


@pytest.mark.asyncio
async def test_import_dir_handler_with_owner(db_session, tmp_path, monkeypatch, capsys):
    """Test the import-dir handler with a valid owner."""
    owner = await create_user(db_session, "owner", "owner@example.com", "secret", role="user")
    await db_session.flush()

    assert owner is not None

    root = tmp_path / "media"
    root.mkdir()
    import_dir = root / "imports"
    import_dir.mkdir()

    config = SonghiveConfig(
        imports={"scan_roots": [str(root)]},
        federation={"enabled": False},
    )
    monkeypatch.setattr(cli_admin, "load_config", lambda argv: config)
    monkeypatch.setattr(cli_admin, "get_session", lambda: _fake_session(db_session))
    monkeypatch.setattr(
        cli_admin,
        "scan_directory",
        _FakeScanDir(delay_result=_FakeCeleryResult("task-456")),
    )

    args = _make_args(path=str(import_dir), library_id="lib-1", owner="owner")
    await cli_admin._handle_import_dir(args)
    captured = capsys.readouterr()
    assert "Import queued with task id: task-456" in captured.out


@pytest.mark.asyncio
async def test_import_dir_handler_owner_missing(db_session, tmp_path, monkeypatch, capsys):
    """Test that import-dir exits when the owner does not exist."""
    root = tmp_path / "media"
    root.mkdir()
    import_dir = root / "imports"
    import_dir.mkdir()

    config = SonghiveConfig(
        imports={"scan_roots": [str(root)]},
        federation={"enabled": False},
    )
    monkeypatch.setattr(cli_admin, "load_config", lambda argv: config)
    monkeypatch.setattr(cli_admin, "get_session", lambda: _fake_session(db_session))

    args = _make_args(path=str(import_dir), library_id="lib-1", owner="nobody")
    with pytest.raises(SystemExit) as exc_info:
        await cli_admin._handle_import_dir(args)
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "owner 'nobody' not found" in captured.err


@pytest.mark.asyncio
async def test_import_dir_handler_outside_root(tmp_path, monkeypatch, capsys):
    """Test that import-dir exits for a path outside the scan roots."""
    root = tmp_path / "media"
    root.mkdir()
    outside = tmp_path / "other"
    outside.mkdir()

    config = SonghiveConfig(
        imports={"scan_roots": [str(root)]},
        federation={"enabled": False},
    )
    monkeypatch.setattr(cli_admin, "load_config", lambda argv: config)

    args = _make_args(path=str(outside), library_id="lib-1", owner=None)
    with pytest.raises(SystemExit) as exc_info:
        await cli_admin._handle_import_dir(args)
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "not within any configured scan root" in captured.err


@pytest.mark.asyncio
async def test_import_dir_handler_broker_fallback(tmp_path, monkeypatch, capsys):
    """Test the import-dir synchronous fallback when the Celery broker is down."""
    root = tmp_path / "media"
    root.mkdir()
    import_dir = root / "imports"
    import_dir.mkdir()

    config = SonghiveConfig(
        imports={"scan_roots": [str(root)]},
        federation={"enabled": False},
    )
    monkeypatch.setattr(cli_admin, "load_config", lambda argv: config)
    monkeypatch.setattr(
        cli_admin,
        "scan_directory",
        _FakeScanDir(
            raise_delay=cli_admin.KombuOperationalError("broker down"),
            call_result=3,
        ),
    )

    args = _make_args(path=str(import_dir), library_id="lib-1", owner=None)
    await cli_admin._handle_import_dir(args)
    captured = capsys.readouterr()
    assert "Enqueued 3 file(s) for import" in captured.out


@pytest.mark.asyncio
async def test_import_dir_handler_sync_value_error(tmp_path, monkeypatch, capsys):
    """Test the import-dir exit when the synchronous scan raises a ValueError."""
    root = tmp_path / "media"
    root.mkdir()
    import_dir = root / "imports"
    import_dir.mkdir()

    config = SonghiveConfig(
        imports={"scan_roots": [str(root)]},
        federation={"enabled": False},
    )
    monkeypatch.setattr(cli_admin, "load_config", lambda argv: config)
    monkeypatch.setattr(
        cli_admin,
        "scan_directory",
        _FakeScanDir(
            raise_delay=cli_admin.KombuOperationalError("broker down"),
            raise_call=ValueError("invalid scan"),
        ),
    )

    args = _make_args(path=str(import_dir), library_id="lib-1", owner=None)
    with pytest.raises(SystemExit) as exc_info:
        await cli_admin._handle_import_dir(args)
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "invalid scan" in captured.err


@pytest.mark.asyncio
async def test_import_dir_handler_full_celery_fallback(tmp_path, monkeypatch, capsys):
    """Test the final fallback that counts files when Celery is completely unavailable."""
    root = tmp_path / "media"
    root.mkdir()
    import_dir = root / "imports"
    import_dir.mkdir()
    (import_dir / "track.mp3").write_text("audio")
    (import_dir / "notes.txt").write_text("text")

    config = SonghiveConfig(
        imports={"scan_roots": [str(root)]},
        federation={"enabled": False},
    )
    monkeypatch.setattr(cli_admin, "load_config", lambda argv: config)
    monkeypatch.setattr(
        cli_admin,
        "scan_directory",
        _FakeScanDir(
            raise_delay=cli_admin.KombuOperationalError("broker down"),
            raise_call=cli_admin.KombuOperationalError("still down"),
        ),
    )

    args = _make_args(path=str(import_dir), library_id="lib-1", owner=None)
    await cli_admin._handle_import_dir(args)
    captured = capsys.readouterr()
    assert "Found 1 file(s) to import" in captured.out
    assert "Celery broker is not available" in captured.err


@pytest.mark.asyncio
async def test_provision_federation_keys_disabled(monkeypatch, capsys):
    """Test that provision-federation-keys no-ops when federation is disabled."""
    monkeypatch.setattr(
        cli_admin,
        "load_config",
        lambda argv: SonghiveConfig(federation={"enabled": False}),
    )
    await cli_admin._handle_provision_federation_keys(_make_args())
    captured = capsys.readouterr()
    assert "Federation is not enabled" in captured.out


@pytest.mark.asyncio
async def test_provision_federation_keys_batch(db_session, monkeypatch, capsys):
    """Test batch flushing while provisioning federation keys for 50 users."""
    for i in range(50):
        db_session.add(
            User(
                username=f"user{i:02d}",
                email=f"user{i:02d}@example.com",
                password_hash="x",
            )
        )
    await db_session.flush()

    config = SonghiveConfig(
        federation={"enabled": True, "instance_domain": "fed.example.com"},
    )
    monkeypatch.setattr(cli_admin, "load_config", lambda argv: config)
    monkeypatch.setattr(cli_admin, "get_session", lambda: _fake_session(db_session))

    await cli_admin._handle_provision_federation_keys(_make_args())
    captured = capsys.readouterr()
    assert "Provisioned federation keys for 50 user(s) on fed.example.com" in captured.out


def test_admin_main_secret_file(tmp_path, monkeypatch):
    """Test that admin_main loads a secret key from SONGHIVE_SECRET_FILE."""
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("my-secret-value")
    monkeypatch.delenv("SONGHIVE_AUTH__SECRET_KEY", raising=False)
    monkeypatch.setenv("SONGHIVE_SECRET_FILE", str(secret_file))
    monkeypatch.setattr(
        cli_admin,
        "load_config",
        lambda argv: SonghiveConfig(
            database={"url": "sqlite+aiosqlite:///:memory:"},
            federation={"enabled": False},
            auth={"secret_key": "a" * 64},
        ),
    )
    monkeypatch.setattr(cli_admin, "init_db", lambda url: None)
    monkeypatch.setattr(cli_admin, "_handle_init_db", AsyncMock())

    cli_admin.admin_main(["init-db"])
    assert os.environ.get("SONGHIVE_AUTH__SECRET_KEY") == "my-secret-value"


def test_admin_main_import_dir(tmp_path, monkeypatch, capsys):
    """Test the admin_main dispatch for the import-dir command."""
    root = tmp_path / "media"
    root.mkdir()
    import_dir = root / "imports"
    import_dir.mkdir()

    config = SonghiveConfig(
        imports={"scan_roots": [str(root)]},
        federation={"enabled": False},
        auth={"secret_key": "a" * 64},
    )
    monkeypatch.setattr(cli_admin, "load_config", lambda argv: config)
    monkeypatch.setattr(cli_admin, "init_db", lambda url: None)
    monkeypatch.setattr(
        cli_admin,
        "scan_directory",
        _FakeScanDir(delay_result=_FakeCeleryResult("task-id")),
    )

    cli_admin.admin_main(["import-dir", "--path", str(import_dir), "--library-id", "lib-1"])
    captured = capsys.readouterr()
    assert "Import queued with task id: task-id" in captured.out


def test_admin_main_sync_tags_all_dry_run(tmp_path, monkeypatch, capsys):
    """``sync-tags --all --dry-run`` counts matching tracks without enqueuing."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'sync.db'}"
    monkeypatch.setattr(
        cli_admin,
        "load_config",
        lambda argv: SonghiveConfig(database={"url": db_url}, federation={"enabled": False}),
    )
    monkeypatch.setattr(cli_admin, "init_db", lambda url: None)
    monkeypatch.setattr(cli_admin, "get_session", lambda: _LocalSessionFactory(db_url))

    async def _create_track():
        engine = create_async_engine(db_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            artist = Artist(name="Test Artist")
            session.add(artist)
            await session.flush()
            track = Track(title="Test Track", artist_id=artist.id, owner_id=None)
            session.add(track)
            await session.commit()

    asyncio.run(_create_track())

    cli_admin.admin_main(["sync-tags", "--all", "--dry-run"])
    captured = capsys.readouterr()
    assert "Would enqueue tag sync for 1 track(s)." in captured.out


def test_admin_main_rehash_audio_dry_run(tmp_path, monkeypatch, capsys):
    """``rehash-audio --dry-run`` reports files that would be migrated."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'rehash.db'}"
    media_dir = tmp_path / "media"
    media_dir.mkdir()

    audio_path = tmp_path / "sample.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:duration=0.5",
            "-b:a",
            "128k",
            "-ac",
            "1",
            "-ar",
            "44100",
            "-c:a",
            "libmp3lame",
            str(audio_path),
        ],
        check=True,
        capture_output=True,
    )
    shutil.copy(audio_path, media_dir / "sample.mp3")

    async def _setup():
        engine = create_async_engine(db_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            stored_file = StoredFile(
                sha256="0" * 64,
                size=0,
                storage_path="sample.mp3",
                storage_backend="local",
                content_type="audio/mpeg",
                owner_id=None,
                visibility="private",
            )
            session.add(stored_file)
            await session.commit()

    asyncio.run(_setup())

    from songhive.storage import get_storage

    def _load_config(argv=None):
        return SonghiveConfig(
            database={"url": db_url},
            storage={"backend": "local", "local_path": str(media_dir)},
            federation={"enabled": False},
        )

    monkeypatch.setattr(cli_admin, "load_config", _load_config)
    monkeypatch.setattr(cli_admin, "init_db", lambda url: None)
    monkeypatch.setattr(cli_admin, "get_session", lambda: _LocalSessionFactory(db_url))
    monkeypatch.setattr(
        cli_admin,
        "get_storage",
        lambda cfg: get_storage(StorageConfig(backend="local", local_path=str(media_dir))),
    )

    cli_admin.admin_main(["rehash-audio", "--dry-run"])
    captured = capsys.readouterr()
    assert "Would rehash 1 audio file(s)" in captured.out
