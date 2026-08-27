"""Shared sort parameter parsing for list endpoints."""

from dataclasses import dataclass
from typing import Any, Callable, Optional, Set

from fastapi import Query


@dataclass
class SortParams:
    """Validated sort parameters for a list request."""

    field: str
    direction: str


def get_sort(
    allowed: Set[str],
    default_field: str,
    default_dir: str = "asc",
) -> Callable[..., Any]:
    """
    Build a FastAPI dependency that parses ``sort_by`` / ``sort_dir`` query
    parameters and falls back to safe defaults for unknown values.
    """

    async def _dep(
        sort_by: Optional[str] = Query(None, description="Field to sort by"),
        sort_dir: Optional[str] = Query(None, description="Sort direction (asc or desc)"),
    ) -> SortParams:
        field = sort_by if sort_by in allowed else default_field
        direction = sort_dir if sort_dir in ("asc", "desc") else default_dir
        return SortParams(field=field, direction=direction)

    return _dep
