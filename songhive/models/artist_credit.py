"""
ArtistCredit model — how artists are credited on tracks and albums.

Mirrors the MusicBrainz/FEP-be68 ``ArtistCredit``: an ordered join of an
``Artist`` entity to the display name (``credit``) it was credited under
and the ``joinphrase`` text emitted after it in the rendered credit
string (e.g. ``" feat. "``, ``" & "``).

``credit`` is ``None`` when the artist is credited under its canonical
``Artist.name``. ``index`` is the credit's position; index 0 is the
primary credit and must mirror the entity's ``artist_id`` — callers that
reassign ``Track.artist_id``/``Album.artist_id`` must call
``services.music.reconcile_primary_credit`` afterwards.
"""

from typing import List, Optional, Sequence

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

# Default text emitted between two credits when none is specified
# (e.g. a plain ``"A, B"`` join). The final credit's joinphrase is
# always ``""``.
DEFAULT_CREDIT_JOINPHRASE = ", "


def artist_credit_names(credits: Sequence["ArtistCredit"]) -> List[str]:
    """Return the display names of a credit list, in order."""
    return [credit.credit or (credit.artist.name if credit.artist is not None else "") for credit in credits]


def joined_artist_credit(credits: Sequence["ArtistCredit"]) -> str:
    """
    Return the rendered credit string — each credited name followed by its
    ``joinphrase`` (e.g. ``"A feat. B"``).
    """
    parts = []
    for credit in credits:
        name = credit.credit or (credit.artist.name if credit.artist is not None else "")
        if not name:
            continue
        parts.append(name)
        if credit.joinphrase:
            parts.append(credit.joinphrase)
    return "".join(parts)


class ArtistCredit(Base):
    __tablename__ = "artist_credits"
    __table_args__ = (
        UniqueConstraint("track_id", "artist_id", name="uq_artist_credits_track_artist"),
        UniqueConstraint("album_id", "artist_id", name="uq_artist_credits_album_artist"),
        CheckConstraint(
            "(track_id IS NULL) != (album_id IS NULL)",
            name="ck_artist_credits_one_entity",
        ),
        Index("ix_artist_credits_album_id", "album_id"),
    )

    artist_id: Mapped[str] = mapped_column(ForeignKey("artists.id", ondelete="CASCADE"), index=True)
    track_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("tracks.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    album_id: Mapped[Optional[str]] = mapped_column(ForeignKey("albums.id", ondelete="CASCADE"), nullable=True)
    # Display name the artist is credited under on this entity; ``None``
    # credits the artist under its canonical name.
    credit: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    # Text emitted after this credit when joining the credit string
    # (e.g. ``" feat. "``, ``" & "``, ``", "``); empty on the last credit.
    joinphrase: Mapped[str] = mapped_column(String(64), default="", server_default="")
    # Position in the credit list — 0 is the primary credit.
    index: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    artist = relationship("Artist", lazy="selectin")
    track = relationship("Track", back_populates="artist_credits", lazy="raise")
    album = relationship("Album", back_populates="artist_credits", lazy="raise")
