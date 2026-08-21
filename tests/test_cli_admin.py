"""
Admin CLI command tests.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from songhive.cli import admin as cli_admin
from songhive.config.schema import SonghiveConfig
from songhive.models.base import Base
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
