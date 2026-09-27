"""Shared helpers for FastAPI route modules."""

from typing import Iterable, List, Optional, Protocol

from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.user import User
from ...services import acl


class AudioImportOptions(BaseModel):
    """How a post's audio file attachments are imported into the library.

    ``upload_to_library`` imports each attached audio file as a track;
    ``fetch_metadata`` additionally enqueues MusicBrainz enrichment for the
    new tracks, and ``library_id`` selects the target library (``null``
    resolves to the author's default "Uploads" library).
    """

    upload_to_library: bool = True
    fetch_metadata: bool = False
    library_id: Optional[str] = None


class TagListRequest(BaseModel):
    """Add/remove tags on a resource."""

    tags: List[str]


class GenreListRequest(BaseModel):
    """Set/replace genres on a resource."""

    genres: List[str]


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
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
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


async def enforce_editable_fields(
    db: AsyncSession,
    kind: str,
    entity_id: str,
    fields: Iterable[str],
) -> None:
    """Reject edits to fields the entity's provider does not allow.

    Provider-backed entities may declare ``editable_fields`` in their
    library capabilities (e.g. TIDAL allows ``genres``/``tags`` only since
    provider metadata is immutable). ``visibility`` is local ACL state and
    always stays editable. ``None`` from the resolver means the entity is
    not provider-managed and everything is editable.
    """
    from ...services.provider_catalog import editable_fields_for_entity

    editable = await editable_fields_for_entity(db, kind, entity_id)
    if editable is None:
        return
    allowed = set(editable) | {"visibility"}
    blocked = sorted({field for field in fields if field not in allowed})
    if blocked:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Field(s) {', '.join(blocked)} are managed by the external provider and cannot be edited",
        )
