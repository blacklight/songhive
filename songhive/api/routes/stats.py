"""
Personal listening statistics routes.
"""

from datetime import datetime, timedelta, timezone
from typing import List, Literal, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.user import User
from ...services import listening_stats
from ...services.storage import StorageService
from ..deps import get_current_user, get_db, get_storage_service

router = APIRouter(prefix="/stats")

PeriodGroupByParam = Literal["day", "week", "month", "year"]
ReleaseGroupByParam = Literal["decade", "year"]


class TopEntry(BaseModel):
    """A ranked name with its play count and entity link/artwork."""

    name: str
    play_count: int
    url: Optional[str] = None
    image_url: Optional[str] = None
    artist_name: Optional[str] = None
    artist_url: Optional[str] = None


class TopStatsResponse(BaseModel):
    """Top played artists, albums, tracks and genres."""

    artists: List[TopEntry]
    albums: List[TopEntry]
    tracks: List[TopEntry]
    genres: List[TopEntry]


class BucketCount(BaseModel):
    """A calendar bucket with its play count."""

    bucket: str
    count: int


class GenreCount(BaseModel):
    """A genre name with its play count inside a bucket."""

    name: str
    count: int


class GenreBucket(BaseModel):
    """A calendar bucket with per-genre play counts."""

    bucket: str
    genres: List[GenreCount]


class ClockEntry(BaseModel):
    """An hour-of-day segment with its play count."""

    hour: int
    count: int


def _period(from_dt: Optional[datetime], to_dt: Optional[datetime]) -> tuple[datetime, datetime]:
    """Resolve the stats period: ``to`` defaults to now, ``from`` to ``to - 30 days``."""
    end = to_dt or datetime.now(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    start = from_dt or (end - timedelta(days=30))
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if start > end:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="`from` must not be after `to`",
        )
    return start, end


def _timezone(name: Optional[str]) -> ZoneInfo:
    """Resolve an IANA timezone name, defaulting to UTC."""
    if not name:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown timezone: {name}",
        )


@router.get("/top", response_model=TopStatsResponse)
async def top_stats(
    from_dt: Optional[datetime] = Query(None, alias="from"),
    to_dt: Optional[datetime] = Query(None, alias="to"),
    limit: int = Query(10, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
):
    """Top played artists, albums, tracks and genres over the period."""
    start, end = _period(from_dt, to_dt)
    return await listening_stats.top_listens(db, str(current_user.id), start, end, storage, limit)


@router.get("/plays", response_model=List[BucketCount])
async def plays_stats(
    group_by: PeriodGroupByParam = Query("day"),
    from_dt: Optional[datetime] = Query(None, alias="from"),
    to_dt: Optional[datetime] = Query(None, alias="to"),
    tz: Optional[str] = Query(None),
    week_start: int = Query(0, ge=0, le=6),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Histogram of plays per day/week/month/year bucket."""
    start, end = _period(from_dt, to_dt)
    return await listening_stats.plays_per_period(
        db, str(current_user.id), start, end, group_by, _timezone(tz), week_start
    )


@router.get("/genres-timeline", response_model=List[GenreBucket])
async def genres_timeline_stats(
    group_by: PeriodGroupByParam = Query("week"),
    from_dt: Optional[datetime] = Query(None, alias="from"),
    to_dt: Optional[datetime] = Query(None, alias="to"),
    tz: Optional[str] = Query(None),
    week_start: int = Query(0, ge=0, le=6),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Stacked timeline of plays per genre per bucket."""
    start, end = _period(from_dt, to_dt)
    return await listening_stats.genres_timeline(
        db, str(current_user.id), start, end, group_by, _timezone(tz), week_start
    )


@router.get("/releases", response_model=List[BucketCount])
async def releases_stats(
    group_by: ReleaseGroupByParam = Query("decade"),
    from_dt: Optional[datetime] = Query(None, alias="from"),
    to_dt: Optional[datetime] = Query(None, alias="to"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Histogram of plays grouped by release decade or year."""
    start, end = _period(from_dt, to_dt)
    return await listening_stats.release_histogram(db, str(current_user.id), start, end, group_by)


@router.get("/clock", response_model=List[ClockEntry])
async def clock_stats(
    from_dt: Optional[datetime] = Query(None, alias="from"),
    to_dt: Optional[datetime] = Query(None, alias="to"),
    tz: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Plays grouped by local hour of day (24 entries)."""
    start, end = _period(from_dt, to_dt)
    return await listening_stats.listening_clock(db, str(current_user.id), start, end, _timezone(tz))
