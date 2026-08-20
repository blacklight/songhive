"""Shared helpers for FastAPI route modules."""

from typing import Optional, Protocol

from ...models.user import User


class _HasOwnerId(Protocol):
    """Protocol for objects that expose an ``owner_id`` attribute."""

    @property
    def owner_id(self) -> Optional[str]: ...


def redact_owner(row: _HasOwnerId, user: Optional[User]) -> Optional[str]:
    """Return ``row.owner_id`` only for the owner or an admin."""
    if user is not None and (user.is_admin or row.owner_id == user.id):
        return row.owner_id
    return None
