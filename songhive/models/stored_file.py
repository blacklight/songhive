"""
StoredFile model - represents a content-addressable stored media file.
"""

from typing import Optional

from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from ._enums import Visibility
from .base import Base


class StoredFile(Base):
    __tablename__ = "stored_files"

    storage_path: Mapped[str] = mapped_column(String(1024), unique=True, index=True)
    storage_backend: Mapped[str] = mapped_column(String(16))
    content_type: Mapped[str] = mapped_column(String(128))
    size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    owner_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    visibility: Mapped[str] = mapped_column(
        String(16),
        default=Visibility.PRIVATE.value,
        index=True,
    )
    original_filename: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
