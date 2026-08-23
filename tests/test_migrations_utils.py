"""
Tests for the shared Alembic migration utilities in ``songhive.migrations.utils``.
"""

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
