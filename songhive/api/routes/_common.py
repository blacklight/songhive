"""Shared helpers for FastAPI route modules."""

from typing import Optional, Protocol

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.user import User
from ...services import acl


class HasOwnerId(Protocol):
    """Protocol for objects that expose an ``owner_id`` attribute."""

    @property
    def owner_id(self) -> Optional[str]: ...


def redact_owner(row: HasOwnerId, user: Optional[User]) -> Optional[str]:
    """Return ``row.owner_id`` only for the owner or an admin."""
    if user is not None and (user.is_admin or row.owner_id == user.id):
        return row.owner_id
    return None


def validate_item_type(value: str) -> str:
    """Reject unknown item types with a clear message."""
    if value not in acl.ITEM_TYPES:
        raise ValueError(f"Invalid item type: {value!r}")
    return value


async def load_and_authorize(
    db: AsyncSession,
    current_user: User,
    item_type: str,
    item_id: str,
) -> None:
    """Validate the item type, confirm the item exists, and check management rights.

    ``item_type`` is checked here both for Pydantic-validated request bodies and
    for query parameters, so this helper is the single backstop for item-type
    validation across the share and share-url routes.
    """
    if item_type not in acl.ITEM_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid item type",
        )

    item = await acl.get_item(db, item_type, item_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )

    if not await acl.can_manage(db, current_user, item_type, item_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )
