"""
Scrobbling model.

Per-user configuration for scrobbling plays to an Audioscrobbler-compatible
service (Last.fm, Libre.fm). The instance supplies the API key pair (see
``config.scrobbling``); users authorize through ``auth.getMobileSession``
and Songhive stores the returned session key Fernet-encrypted — it is a
bearer credential and is never returned by the API.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TZDateTime


class ScrobbleConfig(Base):
    """Per-user scrobbling settings and session state.

    ``min_seconds``/``min_percent`` define when a play counts as a full
    scrobble: whichever threshold is crossed first wins. They also drive the
    server-side listen threshold used while streaming.
    """

    __tablename__ = "scrobble_configs"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    # ``lastfm`` or ``librefm`` — Audioscrobbler 2.0 API services.
    service: Mapped[str] = mapped_column(String(16))
    # Username on the scrobble service, as returned by auth.getMobileSession.
    username: Mapped[str] = mapped_column(String(255))
    # Fernet-encrypted Audioscrobbler session key (``sk``).
    session_key: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, insert_default=True)
    min_seconds: Mapped[int] = mapped_column(Integer, default=30, insert_default=30)
    min_percent: Mapped[int] = mapped_column(Integer, default=25, insert_default=25)

    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_scrobbled_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
