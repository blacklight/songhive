"""
Podcast routes — RSS/Atom feed subscriptions under ``/api/v1/podcasts``.

Podcasts are followed by RSS link: ``POST /`` fetches and parses the feed,
stores show and episode metadata, and records a per-user subscription.
Episode audio is streamed from the source enclosure URL, never copied into
local storage. ``GET /opml`` exports the caller's subscriptions and
``POST /opml/import`` follows every feed in an uploaded OPML document.
"""

import json
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.schema import SonghiveConfig
from ...models.podcast import Podcast, PodcastEpisode
from ...models.user import User
from ...services import podcasts as podcasts_service
from ...services.podcasts import FeedFetchError
from .._common import Pagination, get_pagination
from ..deps import get_config, get_current_user, get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/podcasts")

OPML_MEDIA_TYPE = "text/x-opml"


class PodcastResponse(BaseModel):
    """Public podcast response."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    feed_url: str
    title: str
    description: Optional[str] = None
    author: Optional[str] = None
    link: Optional[str] = None
    image_url: Optional[str] = None
    language: Optional[str] = None
    categories: List[str] = []
    explicit: bool = False
    episode_count: int = 0
    last_fetched_at: Optional[datetime] = None
    last_error: Optional[str] = None
    following: bool = False


class PodcastEpisodeResponse(BaseModel):
    """A single podcast episode; ``audio_url`` is the remote enclosure."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    podcast_id: str
    guid: str
    title: str
    description: Optional[str] = None
    link: Optional[str] = None
    image_url: Optional[str] = None
    audio_url: str
    audio_type: Optional[str] = None
    audio_length: Optional[int] = None
    duration_seconds: Optional[int] = None
    published_at: Optional[datetime] = None
    season_number: Optional[int] = None
    episode_number: Optional[int] = None
    episode_type: Optional[str] = None


class PodcastFollowRequest(BaseModel):
    """Follow a podcast by RSS/Atom feed URL."""

    feed_url: str


class OpmlImportResponse(BaseModel):
    """Summary of an OPML subscription import."""

    subscribed: int = 0
    skipped: int = 0
    failed: int = 0
    errors: List[str] = []


def _check_podcasts(config: SonghiveConfig) -> None:
    """Raise 404 when the podcasts feature is disabled."""
    if not config.podcasts.enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def _categories(podcast: Podcast) -> List[str]:
    """Decode the JSON category list stored on the row."""
    if not podcast.categories:
        return []
    try:
        value = json.loads(podcast.categories)
    except (TypeError, ValueError):
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def _podcast_response(podcast: Podcast, following: bool, episode_count: int) -> PodcastResponse:
    return PodcastResponse(
        id=str(podcast.id),
        feed_url=podcast.feed_url,
        title=podcast.title,
        description=podcast.description,
        author=podcast.author,
        link=podcast.link,
        image_url=podcast.image_url,
        language=podcast.language,
        categories=_categories(podcast),
        explicit=podcast.explicit,
        episode_count=episode_count,
        last_fetched_at=podcast.last_fetched_at,
        last_error=podcast.last_error,
        following=following,
    )


async def _podcast_responses(db: AsyncSession, podcasts: List[Podcast], following: bool) -> List[PodcastResponse]:
    """Serialize podcasts, resolving episode counts with one grouped query."""
    counts = await podcasts_service.episode_counts(db, [str(p.id) for p in podcasts])
    return [_podcast_response(p, following, counts.get(str(p.id), 0)) for p in podcasts]


def _episode_response(episode: PodcastEpisode) -> PodcastEpisodeResponse:
    return PodcastEpisodeResponse(
        id=str(episode.id),
        podcast_id=str(episode.podcast_id),
        guid=episode.guid,
        title=episode.title,
        description=episode.description,
        link=episode.link,
        image_url=episode.image_url,
        audio_url=episode.audio_url,
        audio_type=episode.audio_type,
        audio_length=episode.audio_length,
        duration_seconds=episode.duration_seconds,
        published_at=episode.published_at,
        season_number=episode.season_number,
        episode_number=episode.episode_number,
        episode_type=episode.episode_type,
    )


async def _get_podcast_or_404(db: AsyncSession, podcast_id: str) -> Podcast:
    podcast = await podcasts_service.get_podcast(db, podcast_id)
    if podcast is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Podcast not found")
    return podcast


@router.get("/", response_model=List[PodcastResponse])
async def list_podcasts(
    response: Response,
    current_user: User = Depends(get_current_user),
    pagination: Pagination = Depends(get_pagination),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """List podcasts the current user follows."""
    _check_podcasts(config)
    podcasts, total = await podcasts_service.list_subscribed_podcasts(
        db, current_user, limit=pagination.limit, offset=pagination.offset
    )
    pagination.set_total(response, total)
    return await _podcast_responses(db, podcasts, following=True)


@router.post("/", response_model=PodcastResponse, status_code=201)
async def follow_podcast(
    body: PodcastFollowRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Follow a podcast by RSS/Atom feed URL."""
    _check_podcasts(config)
    try:
        podcast, _created = await podcasts_service.subscribe(db, current_user, body.feed_url, config)
    except FeedFetchError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Could not fetch or parse feed: {exc}",
        ) from exc
    counts = await podcasts_service.episode_counts(db, [str(podcast.id)])
    return _podcast_response(podcast, True, counts.get(str(podcast.id), 0))


@router.get("/opml")
async def export_opml(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Export the current user's podcast subscriptions as an OPML document."""
    _check_podcasts(config)
    podcasts, _total = await podcasts_service.list_subscribed_podcasts(db, current_user, limit=1000)
    username = current_user.display_name or current_user.username
    document = podcasts_service.render_opml(f"{username}'s podcasts", podcasts)
    return Response(
        content=document,
        media_type=OPML_MEDIA_TYPE,
        headers={"Content-Disposition": 'attachment; filename="podcasts.opml"'},
    )


@router.post("/opml/import", response_model=OpmlImportResponse)
async def import_opml(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """
    Follow every feed listed in an uploaded OPML document.

    Each feed is fetched and parsed synchronously; feeds that fail are
    reported in ``errors`` without aborting the rest of the import.
    """
    _check_podcasts(config)
    data = await file.read()
    try:
        entries = podcasts_service.parse_opml(data)
    except FeedFetchError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Could not parse OPML document: {exc}",
        ) from exc

    result = OpmlImportResponse()
    for feed_url, _title in entries:
        try:
            _podcast, created = await podcasts_service.subscribe(db, current_user, feed_url, config)
        except FeedFetchError as exc:
            result.failed += 1
            result.errors.append(f"{feed_url}: {exc}")
            continue
        if created:
            result.subscribed += 1
        else:
            result.skipped += 1
    return result


@router.get("/{podcast_id}", response_model=PodcastResponse)
async def get_podcast(
    podcast_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Get a podcast by id."""
    _check_podcasts(config)
    podcast = await _get_podcast_or_404(db, podcast_id)
    following = await podcasts_service.is_subscribed(db, current_user, podcast_id)
    counts = await podcasts_service.episode_counts(db, [podcast_id])
    return _podcast_response(podcast, following, counts.get(podcast_id, 0))


@router.delete("/{podcast_id}", status_code=204)
async def unfollow_podcast(
    podcast_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Unfollow a podcast (drops the subscription, not the shared feed row)."""
    _check_podcasts(config)
    removed = await podcasts_service.unsubscribe(db, current_user, podcast_id)
    if not removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found")


@router.post("/{podcast_id}/refresh", response_model=PodcastResponse)
async def refresh_podcast(
    podcast_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Re-fetch and reparse the podcast's feed right now."""
    _check_podcasts(config)
    podcast = await _get_podcast_or_404(db, podcast_id)
    try:
        await podcasts_service.refresh_podcast(db, podcast, config, force=True)
    except FeedFetchError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Could not refresh feed: {exc}",
        ) from exc
    following = await podcasts_service.is_subscribed(db, current_user, podcast_id)
    counts = await podcasts_service.episode_counts(db, [podcast_id])
    return _podcast_response(podcast, following, counts.get(podcast_id, 0))


@router.get(
    "/{podcast_id}/episodes",
    dependencies=[Depends(get_current_user)],
    response_model=List[PodcastEpisodeResponse],
)
async def list_episodes(
    podcast_id: str,
    response: Response,
    sort: str = Query("newest", pattern="^(newest|oldest)$"),
    pagination: Pagination = Depends(get_pagination),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """List a podcast's episodes, newest first."""
    _check_podcasts(config)
    await _get_podcast_or_404(db, podcast_id)
    episodes, total = await podcasts_service.list_episodes(
        db, podcast_id, limit=pagination.limit, offset=pagination.offset, oldest_first=sort == "oldest"
    )
    pagination.set_total(response, total)
    return [_episode_response(episode) for episode in episodes]
