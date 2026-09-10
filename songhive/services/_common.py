"""Shared helpers for service-layer SQLAlchemy statements."""

from typing import Any

from sqlalchemy import ColumnElement


def escape_like_pattern(term: str) -> str:
    """Escape LIKE wildcards in ``term`` so it is matched literally."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def ilike_contains(column: Any, term: str) -> ColumnElement[bool]:
    """
    Return a case-insensitive ``LIKE`` predicate matching ``term`` anywhere in
    ``column``, with ``%``, ``_`` and ``\\`` treated literally.
    """
    return column.ilike(f"%{escape_like_pattern(term)}%", escape="\\")
