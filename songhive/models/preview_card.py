"""
Preview card model.

A ``PreviewCard`` caches the link-preview metadata (OpenGraph, ``<title>``
fallbacks) fetched for a URL that appeared in an activity's content. Cards
are keyed by URL and shared across activities — the row is refreshed only
when it grows stale, so posts linking the same URL never trigger a fetch
per view.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TZDateTime


class PreviewCard(Base):
    """Cached link-preview metadata for a URL mentioned in an activity."""

    __tablename__ = "preview_cards"

    url: Mapped[str] = mapped_column(String(2048), unique=True, index=True)
    title: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    image_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    site_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    type: Mapped[str] = mapped_column(
        String(32),
        default="link",
        insert_default="link",
        server_default="link",
    )
    fetched_at: Mapped[datetime] = mapped_column(TZDateTime())
