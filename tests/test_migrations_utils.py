"""
Tests for the shared Alembic migration utilities in ``songhive.migrations.utils``.
"""

import multiprocessing
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine

from songhive.migrations import utils


class _FakeOp:
    """Stand-in for ``alembic.op`` that returns a configurable bind."""

    def __init__(self, bind=None, raise_on_bind=False):
        self._bind = bind
        self._raise = raise_on_bind

    def get_bind(self):
        if self._raise:
            raise RuntimeError("no alembic context")
        return self._bind


@pytest.fixture
def sync_engine(tmp_path):
    """Create a synchronous SQLite engine backed by a fresh file database."""
    url = f"sqlite:///{tmp_path / 'migrations.db'}"
    engine = create_engine(url)
    metadata = sa.MetaData()
    sa.Table(
        "sample",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String),
        sa.Column("email", sa.String),
    )
    metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def bound_op(monkeypatch, sync_engine):
    """Patch ``utils.op`` so it returns the test engine from ``get_bind``."""
    monkeypatch.setattr(utils, "op", _FakeOp(bind=sync_engine))
    return sync_engine


def test_table_exists_returns_true_when_table_present(bound_op):
    assert utils.table_exists("sample") is True


def test_table_exists_returns_false_when_table_missing(bound_op):
    assert utils.table_exists("missing") is False


def test_table_exists_returns_false_when_get_bind_fails(monkeypatch):
    monkeypatch.setattr(utils, "op", _FakeOp(raise_on_bind=True))
    assert utils.table_exists("sample") is False


def test_column_exists_returns_true_when_column_present(bound_op):
    assert utils.column_exists("sample", "name") is True


def test_column_exists_returns_false_when_column_missing(bound_op):
    assert utils.column_exists("sample", "missing") is False


def test_column_exists_returns_false_when_table_missing(bound_op):
    assert utils.column_exists("missing", "name") is False


def test_column_exists_returns_false_when_get_bind_fails(monkeypatch):
    monkeypatch.setattr(utils, "op", _FakeOp(raise_on_bind=True))
    assert utils.column_exists("sample", "name") is False


def test_index_exists_returns_true_when_index_present(bound_op):
    with bound_op.connect() as conn:
        conn.execute(sa.text("CREATE INDEX ix_sample_email ON sample(email)"))
        conn.commit()
    assert utils.index_exists("ix_sample_email", "sample") is True


def test_index_exists_returns_false_when_index_missing(bound_op):
    assert utils.index_exists("missing", "sample") is False


def test_index_exists_returns_false_when_table_missing(bound_op):
    assert utils.index_exists("missing", "missing") is False


def test_index_exists_returns_false_when_get_bind_fails(monkeypatch):
    monkeypatch.setattr(utils, "op", _FakeOp(raise_on_bind=True))
    assert utils.index_exists("missing", "sample") is False


def _run_ensure_migrated(database_url: str, queue: multiprocessing.Queue) -> None:
    """Run ``ensure_migrated`` in a child process and report the result."""
    from songhive.migrations import utils as _utils

    try:
        _utils.ensure_migrated(database_url)
        queue.put(("ok", None))
    except Exception as exc:
        queue.put(("error", str(exc)))


def test_ensure_migrated_creates_and_stamps_fresh_sqlite_db(tmp_path):
    """``ensure_migrated`` creates the schema and stamps head on an empty SQLite DB."""
    db_path = tmp_path / "fresh.db"
    database_url = f"sqlite+aiosqlite:///{db_path}"

    utils.ensure_migrated(database_url)

    sync_url = utils.to_sync_url(database_url)
    engine = create_engine(sync_url)
    try:
        inspector = sa.inspect(engine)
        table_names = inspector.get_table_names()
        assert "users" in table_names
        assert "alembic_version" in table_names
    finally:
        engine.dispose()


def test_ensure_migrated_concurrent_sqlite(tmp_path, monkeypatch):
    """Concurrent ``ensure_migrated`` calls on a fresh SQLite DB do not race."""
    db_path = tmp_path / "concurrent.db"
    database_url = f"sqlite+aiosqlite:///{db_path}"

    # Ensure the project root is on sys.path so spawned interpreters can import
    # ``songhive`` without relying on the current working directory.
    project_root = str(Path(__file__).resolve().parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    monkeypatch.syspath_prepend(project_root)

    # Use ``spawn`` so children do not inherit any open file descriptors.
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    processes = [ctx.Process(target=_run_ensure_migrated, args=(database_url, queue)) for _ in range(2)]

    for proc in processes:
        proc.start()
    for proc in processes:
        proc.join(timeout=60)

    results = []
    for _ in processes:
        try:
            results.append(queue.get(timeout=5))
        except Exception:
            results.append(("error", "queue empty"))

    for proc in processes:
        assert proc.exitcode == 0, f"Migration child exited with code {proc.exitcode}"

    for status, exc in results:
        assert status == "ok", exc

    # The database is migrated and all lock files are released.
    sync_url = utils.to_sync_url(database_url)
    engine = create_engine(sync_url)
    try:
        inspector = sa.inspect(engine)
        table_names = inspector.get_table_names()
        assert "users" in table_names
        assert "alembic_version" in table_names
    finally:
        engine.dispose()
