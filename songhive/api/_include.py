"""Shared `?include=` query parameter helper."""

from dataclasses import dataclass
from typing import Optional, Set

from fastapi import Query


@dataclass(frozen=True)
class IncludeQuery:
    """Parsed and validated `?include=` query parameter."""

    values: Set[str]

    def __contains__(self, item: str) -> bool:
        return item in self.values

    def has(self, *items: str) -> bool:
        return any(item in self.values for item in items)


def get_include(allowed: Set[str]):
    """Return a FastAPI dependency that parses a comma-separated `?include=` list."""

    def _parse(
        include: Optional[str] = Query(
            None,
            description=f"Comma-separated relations to include. Allowed: {', '.join(sorted(allowed))}",
        )
    ) -> IncludeQuery:
        values: Set[str] = set()
        if include:
            for part in include.split(","):
                part = part.strip().lower()
                if part in allowed:
                    values.add(part)
        return IncludeQuery(values)

    return _parse
