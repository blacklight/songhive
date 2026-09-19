"""
CollectionItem model - a piece of content saved to a user's collection.

A ``CollectionItem`` row records that a user explicitly added an item
(library, playlist, album, artist, track, or radio) created by someone else
to their own collection.  Saved items surface in list views whenever the
"my collection" filter is active, alongside content the user owns.  Tracks
additionally count as collected when they are present in the user's
``favorites``.
"""

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class CollectionItem(Base):
    __tablename__ = "collection_items"
    __table_args__ = (
        UniqueConstraint("user_id", "item_type", "item_id", name="uq_collection_item"),
        Index("ix_collection_items_item", "item_type", "item_id"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    item_type: Mapped[str] = mapped_column(String(32))
    item_id: Mapped[str] = mapped_column(String(36))
