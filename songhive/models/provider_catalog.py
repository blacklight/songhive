"""
ProviderCatalogEntry model - an instance-wide cache of provider-owned entity
metadata keyed by the provider's stable global id.

Entity-backed providers (TIDAL, Jellyfin, …) share catalog objects across
users: a TIDAL track id carries the same title/duration/ISRC no matter which
subscriber's library references it. Caching the raw provider payload here
means materializing a local ``Track`` for an already-known id is a pure DB
operation — no provider API call — even for a second user's library.

``payload`` is the provider object verbatim. ``expires_at`` NULL marks an
entry as immutable (TIDAL tracks are treated as immutable by id); other kinds
expire so refreshed listings pick up catalog-side changes. ``contents`` holds
the lazily-fetched, ordered child ids of a container (playlist/album) and is
bookkept by ``contents_etag``/``contents_fetched_at``. ``unavailable_at``
pins ids the provider reported as gone (404 / not streamable) so dead ids are
not hammered on every sync.

This table is never exposed through the API; it only feeds materialization of
local rows the Songhive ACL already governs.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TZDateTime


class ProviderCatalogEntry(Base):
    """A cached provider catalog object (track/album/artist/playlist)."""

    __tablename__ = "provider_catalog_entries"
    __table_args__ = (
        UniqueConstraint(
            "provider_type",
            "kind",
            "provider_key",
            name="uq_provider_catalog_entries",
        ),
        Index(
            "ix_provider_catalog_entries_expiry",
            "provider_type",
            "kind",
            "expires_at",
        ),
    )

    provider_type: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(16))
    provider_key: Mapped[str] = mapped_column(String(512))
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # Ordered child refs for lazy containers:
    # [{"id": "<provider_key>", "position": 0}, ...]
    contents: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    contents_etag: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    contents_fetched_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
    fetched_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
    # NULL = immutable (provider ids whose metadata never changes).
    expires_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
    # Set when the provider reported the object gone/unplayable.
    unavailable_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
