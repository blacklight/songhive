"""
Cached remote (federated) objects.

A ``RemoteObject`` is the shared cache substrate for remote activities and
remote resources (tracks, albums, artists, playlists, libraries, profiles)
that were explicitly looked up or delivered through the inbox. Materialized
standalone activities attach to it via ``Activity.entity_type="remote"``
with ``entity_id`` pointing at the row's UUID.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TZDateTime

REMOTE_OBJECT_RESOURCE_TYPES = ("track", "album", "artist", "playlist", "library", "profile")


class RemoteObject(Base):
    __tablename__ = "remote_objects"
    __table_args__ = (
        Index("ix_remote_objects_resource_type_domain", "resource_type", "domain"),
        Index("ix_remote_objects_actor_url", "actor_url"),
    )

    # The canonical ActivityPub object URL this row caches.
    canonical_url: Mapped[str] = mapped_column(String(512), unique=True)
    # The wrapping activity's id when it differs from the object id.
    activity_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    domain: Mapped[str] = mapped_column(String(255), index=True)
    # Raw ActivityStreams type of the fetched document (Note, Audio, Tombstone…).
    object_type: Mapped[str] = mapped_column(String(64))
    # Normalized Songhive resource kind when the document maps to one.
    resource_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    actor_url: Mapped[str] = mapped_column(String(512))
    visibility: Mapped[str] = mapped_column(String(16), default="public", server_default="public")
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # Denormalized display fields for cards/search.
    name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    audio_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    etag: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_modified: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        TZDateTime(),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
    )
    unavailable_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
