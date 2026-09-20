"""
Podcast models — followed RSS feeds and their episode catalog.

Podcast audio is never copied into local storage: ``PodcastEpisode`` rows
keep the remote enclosure URL and the player streams straight from the
source. ``podcasts`` holds one row per feed URL shared instance-wide;
``podcast_subscriptions`` tracks which local users follow it.
"""

from datetime import datetime
from typing import List, Optional

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TZDateTime


class Podcast(Base):
    __tablename__ = "podcasts"
    __table_args__ = (Index("ix_podcasts_last_fetched_at", "last_fetched_at"),)

    # Canonical RSS/Atom feed URL — unique so all subscribers share one row.
    feed_url: Mapped[str] = mapped_column(String(1024), unique=True)
    title: Mapped[str] = mapped_column(String(512))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    author: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    link: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    image_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    language: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # JSON-encoded list of category strings.
    categories: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    explicit: Mapped[bool] = mapped_column(Boolean, default=False, insert_default=False)

    # Conditional-fetch validators from the last successful fetch.
    etag: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    last_modified: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    last_fetched_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
    # Last fetch/parse failure, cleared on success — surfaced in the API so
    # users can see a broken feed instead of a silently stale one.
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    episodes: Mapped[List["PodcastEpisode"]] = relationship(
        "PodcastEpisode",
        back_populates="podcast",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    subscriptions: Mapped[List["PodcastSubscription"]] = relationship(
        "PodcastSubscription",
        back_populates="podcast",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class PodcastEpisode(Base):
    __tablename__ = "podcast_episodes"
    __table_args__ = (
        UniqueConstraint("podcast_id", "guid", name="uq_podcast_episodes_podcast_id_guid"),
        Index("ix_podcast_episodes_podcast_published", "podcast_id", "published_at"),
    )

    podcast_id: Mapped[str] = mapped_column(ForeignKey("podcasts.id", ondelete="CASCADE"), index=True)
    # Feed-supplied stable id (<guid> or atom <id>); falls back to the
    # enclosure URL so re-parses dedupe instead of duplicating.
    guid: Mapped[str] = mapped_column(String(512))
    title: Mapped[str] = mapped_column(String(512))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    link: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    image_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)

    # Remote enclosure — streamed directly, never downloaded locally.
    audio_url: Mapped[str] = mapped_column(String(2048))
    audio_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    audio_length: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
    season_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    episode_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # full / trailer / bonus
    episode_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    podcast = relationship("Podcast", back_populates="episodes")


class PodcastSubscription(Base):
    __tablename__ = "podcast_subscriptions"
    __table_args__ = (UniqueConstraint("user_id", "podcast_id", name="uq_podcast_subscriptions_user_id_podcast_id"),)

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    podcast_id: Mapped[str] = mapped_column(ForeignKey("podcasts.id", ondelete="CASCADE"), index=True)

    podcast = relationship("Podcast", back_populates="subscriptions")


class PodcastEpisodePlay(Base):
    __tablename__ = "podcast_episode_plays"
    __table_args__ = (UniqueConstraint("user_id", "episode_id", name="uq_podcast_episode_plays_user_episode"),)

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    episode_id: Mapped[str] = mapped_column(ForeignKey("podcast_episodes.id", ondelete="CASCADE"), index=True)

    episode = relationship("PodcastEpisode")
