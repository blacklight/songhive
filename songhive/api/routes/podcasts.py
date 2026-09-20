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
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.schema import SonghiveConfig
from ...models.podcast import Podcast, PodcastEpisode, PodcastSyncConfig
from ...models.user import User
from ...services import gpodder as gpodder_service
from ...services import podcasts as podcasts_service
from ...services.gpodder import GPodderError
from ...services.podcasts import FeedFetchError, PodcastStats
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
    unplayed_count: int = 0
    latest_episode_at: Optional[datetime] = None
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
    played: bool = False


class PodcastFollowRequest(BaseModel):
    """Follow a podcast by RSS/Atom feed URL."""

    feed_url: str


class OpmlImportResponse(BaseModel):
    """Summary of an OPML subscription import."""

    subscribed: int = 0
    skipped: int = 0
    failed: int = 0
    errors: List[str] = []


class PodcastSyncConfigRequest(BaseModel):
    """Create or update the caller's GPodder sync configuration."""

    # ``gpodder`` — gpodder.net API servers (gpodder.net, opodsync);
    # ``nextcloud`` — the Nextcloud gpoddersync app.
    server_type: Literal["gpodder", "nextcloud"] = "gpodder"
    server_url: str
    username: str
    # Write-only: omitted keeps the stored password, "" clears it.
    password: Optional[str] = None
    device_id: str = "songhive"
    mode: Literal["pull", "bidirectional"] = "pull"
    enabled: bool = True


class PodcastSyncConfigResponse(BaseModel):
    """The caller's GPodder sync configuration (password never returned)."""

    server_type: str
    server_url: str
    username: str
    device_id: str
    mode: str
    enabled: bool
    has_password: bool
    last_synced_at: Optional[datetime] = None
    last_error: Optional[str] = None


class PodcastSyncResultResponse(BaseModel):
    """Summary of a single sync run."""

    subscribed: int = 0
    unsubscribed: int = 0
    pushed_adds: int = 0
    pushed_removes: int = 0
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


def _podcast_response(podcast: Podcast, following: bool, stats: PodcastStats) -> PodcastResponse:
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
        episode_count=stats.episode_count,
        unplayed_count=stats.unplayed_count,
        latest_episode_at=stats.latest_episode_at,
        last_fetched_at=podcast.last_fetched_at,
        last_error=podcast.last_error,
        following=following,
    )


def _episode_response(episode: PodcastEpisode, played: bool = False) -> PodcastEpisodeResponse:
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
        played=played,
    )


async def _get_podcast_or_404(db: AsyncSession, podcast_id: str) -> Podcast:
    podcast = await podcasts_service.get_podcast(db, podcast_id)
    if podcast is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Podcast not found")
    return podcast


@router.get("/", response_model=List[PodcastResponse])
async def list_podcasts(
    response: Response,
    q: Optional[str] = Query(None, max_length=200),
    sort_by: str = Query("latest", pattern="^(latest|episodes|unplayed|name)$"),
    sort_dir: Optional[str] = Query(None, pattern="^(asc|desc)$"),
    current_user: User = Depends(get_current_user),
    pagination: Pagination = Depends(get_pagination),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """
    List podcasts the current user follows.

    ``sort_by`` accepts ``latest`` (newest episode first, the default),
    ``episodes``, ``unplayed`` or ``name``; ``q`` filters on title, author
    and description.
    """
    _check_podcasts(config)
    entries, total = await podcasts_service.list_subscribed_podcasts(
        db,
        current_user,
        limit=pagination.limit,
        offset=pagination.offset,
        search=q,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
    pagination.set_total(response, total)
    return [_podcast_response(entry.podcast, True, entry.stats) for entry in entries]


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
        podcast, _ = await podcasts_service.subscribe(db, current_user, body.feed_url, config)
    except FeedFetchError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Could not fetch or parse feed: {exc}",
        ) from exc
    stats = await podcasts_service.podcast_stats(db, current_user, [str(podcast.id)])
    return _podcast_response(podcast, True, stats.get(str(podcast.id), PodcastStats()))


@router.get("/opml")
async def export_opml(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Export the current user's podcast subscriptions as an OPML document."""
    _check_podcasts(config)
    entries, _ = await podcasts_service.list_subscribed_podcasts(db, current_user, limit=1000, sort_by="name")
    username = current_user.display_name or current_user.username
    document = podcasts_service.render_opml(f"{username}'s podcasts", [entry.podcast for entry in entries])
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
    for feed_url, _ in entries:
        try:
            __, created = await podcasts_service.subscribe(db, current_user, feed_url, config)
        except FeedFetchError as exc:
            result.failed += 1
            result.errors.append(f"{feed_url}: {exc}")
            continue
        if created:
            result.subscribed += 1
        else:
            result.skipped += 1
    return result


def _sync_config_response(row: PodcastSyncConfig) -> PodcastSyncConfigResponse:
    return PodcastSyncConfigResponse(
        server_type=row.server_type,
        server_url=row.server_url,
        username=row.username,
        device_id=row.device_id,
        mode=row.mode,
        enabled=row.enabled,
        has_password=bool(row.password),
        last_synced_at=row.last_synced_at,
        last_error=row.last_error,
    )


# The ``/sync`` routes must be declared before ``/{podcast_id}`` so the
# literal "sync" is not captured as a podcast id.
@router.get("/sync", response_model=PodcastSyncConfigResponse)
async def get_sync_config(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Return the current user's GPodder sync configuration."""
    _check_podcasts(config)
    row = await gpodder_service.get_sync_config(db, current_user)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sync is not configured")
    return _sync_config_response(row)


@router.put("/sync", response_model=PodcastSyncConfigResponse)
async def put_sync_config(
    body: PodcastSyncConfigRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """
    Configure GPodder subscription sync for the current user.

    ``mode`` is ``pull`` (apply remote changes only) or ``bidirectional``
    (also upload local changes; on conflict the most recent change wins —
    see the sync service for how that is resolved). The credentials are
    verified against the server before the configuration is saved.
    """
    _check_podcasts(config)
    if not body.username.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="username is required",
        )
    try:
        server_url = gpodder_service.normalize_server_url(body.server_url, body.server_type)
    except GPodderError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    existing = await gpodder_service.get_sync_config(db, current_user)
    effective_password = body.password if body.password is not None else (existing.password if existing else None)
    if not effective_password:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="password is required",
        )
    # Verify the credentials before persisting anything — a transient row is
    # enough for the client.
    candidate = PodcastSyncConfig(
        user_id=str(current_user.id),
        server_type=body.server_type,
        server_url=server_url,
        username=body.username.strip(),
        password=effective_password,
        device_id=body.device_id.strip() or gpodder_service.DEFAULT_DEVICE_ID,
        mode=body.mode,
        enabled=body.enabled,
    )
    try:
        await gpodder_service.verify_credentials(candidate, config)
    except GPodderError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Could not authenticate with sync server: {exc}",
        ) from exc
    row, _ = await gpodder_service.upsert_sync_config(
        db,
        current_user,
        server_type=body.server_type,
        server_url=server_url,
        username=body.username.strip(),
        password=body.password,
        device_id=body.device_id,
        mode=body.mode,
        enabled=body.enabled,
    )
    return _sync_config_response(row)


@router.delete("/sync", status_code=204)
async def delete_sync_config(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Remove the current user's GPodder sync configuration and change log."""
    _check_podcasts(config)
    if not await gpodder_service.delete_sync_config(db, current_user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sync is not configured")


@router.post("/sync/now", response_model=PodcastSyncResultResponse)
async def sync_now(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Run a subscription sync immediately for the current user."""
    _check_podcasts(config)
    row = await gpodder_service.get_sync_config(db, current_user)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sync is not configured")
    try:
        result = await gpodder_service.run_sync(db, current_user, row, config)
    except GPodderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Sync failed: {exc}",
        ) from exc
    return PodcastSyncResultResponse(
        subscribed=result.subscribed,
        unsubscribed=result.unsubscribed,
        pushed_adds=result.pushed_adds,
        pushed_removes=result.pushed_removes,
        errors=result.errors,
    )


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
    stats = await podcasts_service.podcast_stats(db, current_user, [podcast_id])
    return _podcast_response(podcast, following, stats.get(podcast_id, PodcastStats()))


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
    stats = await podcasts_service.podcast_stats(db, current_user, [podcast_id])
    return _podcast_response(podcast, following, stats.get(podcast_id, PodcastStats()))


@router.get("/{podcast_id}/episodes", response_model=List[PodcastEpisodeResponse])
async def list_episodes(
    podcast_id: str,
    response: Response,
    sort: str = Query("newest", pattern="^(newest|oldest)$"),
    current_user: User = Depends(get_current_user),
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
    played = await podcasts_service.played_episode_ids(db, current_user, [str(e.id) for e in episodes])
    return [_episode_response(episode, str(episode.id) in played) for episode in episodes]


@router.get("/episodes/{episode_id}", response_model=PodcastEpisodeResponse)
async def get_episode(
    episode_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Get a single podcast episode by id."""
    _check_podcasts(config)
    episode = await podcasts_service.get_episode(db, episode_id)
    if episode is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Episode not found")
    played = await podcasts_service.played_episode_ids(db, current_user, [str(episode.id)])
    return _episode_response(episode, str(episode.id) in played)


@router.post("/episodes/{episode_id}/played", status_code=204)
async def mark_episode_played(
    episode_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Mark an episode as played for the current user (idempotent)."""
    _check_podcasts(config)
    episode = await podcasts_service.mark_episode_played(db, current_user, episode_id)
    if episode is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Episode not found")


@router.delete("/episodes/{episode_id}/played", status_code=204)
async def mark_episode_unplayed(
    episode_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Clear the played mark for an episode."""
    _check_podcasts(config)
    episode = await podcasts_service.mark_episode_unplayed(db, current_user, episode_id)
    if episode is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Episode not found")
