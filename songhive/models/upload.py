"""
Upload model - represents a stored audio file.
"""

from typing import Optional

from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Upload(Base):
    __tablename__ = "uploads"

    track_id: Mapped[str] = mapped_column(ForeignKey("tracks.id"), index=True)
    library_id: Mapped[str] = mapped_column(ForeignKey("libraries.id"), index=True)
    storage_path: Mapped[str] = mapped_column(String(1024))
    storage_backend: Mapped[str] = mapped_column(String(16))  # "local" or "s3"
    mimetype: Mapped[str] = mapped_column(String(64))
    size: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    bitrate: Mapped[Optional[int]] = mapped_column(nullable=True)
    checksum: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    track = relationship("Track", backref="uploads", lazy="selectin")
    library = relationship("Library", backref="uploads", lazy="selectin")
