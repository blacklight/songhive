"""
Admin CLI command tests.
"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from songhive.cli import admin as cli_admin
from songhive.config.schema import SonghiveConfig
from songhive.models.base import Base
from songhive.models.user import User  # noqa: F401


@asynccontextmanager
async def _fake_session(session):
    """Yield a pre-existing session as if it came from get_session()."""
    yield session


def _make_args(**kwargs):
    """Build a simple argparse-like namespace."""
    return SimpleNamespace(**kwargs)


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

    user = await create_user(db_session, "alice", "alice@example.com", "secret", is_admin=False)
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
