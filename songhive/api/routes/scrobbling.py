"""
Scrobbling routes — per-user Audioscrobbler service config under
``/api/v1/scrobbling``.

Users connect a Last.fm or Libre.fm account with their service credentials;
the password is exchanged for a session key (``auth.getMobileSession``) and
never stored. The session key itself is stored Fernet-encrypted and is never
returned by any endpoint.
"""

from datetime import datetime
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.schema import SonghiveConfig
from ...models.scrobble import ScrobbleConfig
from ...models.track import Track
from ...models.user import User
from ...services import acl, scrobbler
from ...services.scrobbler import ScrobblerError, ScrobblerTemporaryError
from ..deps import get_config, get_current_user, get_db

router = APIRouter(prefix="/scrobbling")


class ScrobbleServiceInfo(BaseModel):
    """A scrobble service the instance can submit to."""

    id: str
    name: str
    available: bool


class ScrobbleThresholdsResponse(BaseModel):
    """Effective listen thresholds for the current user.

    ``min_percent`` is ``None`` when the user has no active scrobble config —
    clients then fall back to their local history default.
    """

    min_seconds: float
    min_percent: Optional[float] = None


class ScrobbleConfigResponse(BaseModel):
    """The caller's scrobble configuration (session key never returned)."""

    service: str
    username: str
    enabled: bool
    min_seconds: int
    min_percent: int
    last_scrobbled_at: Optional[datetime] = None
    last_error: Optional[str] = None


class ScrobbleStatusResponse(BaseModel):
    """Instance capabilities plus the caller's configuration."""

    enabled: bool
    services: List[ScrobbleServiceInfo]
    thresholds: ScrobbleThresholdsResponse
    config: Optional[ScrobbleConfigResponse] = None


class ScrobbleConnectRequest(BaseModel):
    """Connect a scrobble account by exchanging credentials for a session."""

    service: Literal["lastfm", "librefm"]
    username: str
    password: str


class ScrobbleSettingsRequest(BaseModel):
    """Update the caller's scrobble toggle and listen thresholds."""

    enabled: bool = True
    min_seconds: int = Field(
        default=scrobbler.DEFAULT_MIN_SECONDS, ge=scrobbler.MIN_SECONDS_RANGE[0], le=scrobbler.MIN_SECONDS_RANGE[1]
    )
    min_percent: int = Field(
        default=scrobbler.DEFAULT_MIN_PERCENT, ge=scrobbler.MIN_PERCENT_RANGE[0], le=scrobbler.MIN_PERCENT_RANGE[1]
    )


def _check_scrobbling(config: SonghiveConfig) -> None:
    """Raise 404 when the scrobbling feature is disabled on the instance."""
    if not config.scrobbling.enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def _config_response(row: ScrobbleConfig) -> ScrobbleConfigResponse:
    return ScrobbleConfigResponse(
        service=row.service,
        username=row.username,
        enabled=row.enabled,
        min_seconds=row.min_seconds,
        min_percent=row.min_percent,
        last_scrobbled_at=row.last_scrobbled_at,
        last_error=row.last_error,
    )


async def _thresholds_response(db: AsyncSession, user: User) -> ScrobbleThresholdsResponse:
    min_seconds, min_percent = await scrobbler.thresholds_for(db, user)
    return ScrobbleThresholdsResponse(min_seconds=min_seconds, min_percent=min_percent)


@router.get("/", response_model=ScrobbleStatusResponse)
async def get_scrobbling_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """
    Return the scrobbling capabilities of this instance and the caller's
    configuration, including the effective listen thresholds the player
    should report history/scrobbles at.
    """
    row = await scrobbler.get_config_row(db, current_user)
    return ScrobbleStatusResponse(
        enabled=config.scrobbling.enabled,
        services=[ScrobbleServiceInfo(**s) for s in scrobbler.list_services(config)],
        thresholds=await _thresholds_response(db, current_user),
        config=_config_response(row) if row is not None else None,
    )


@router.post("/connect", response_model=ScrobbleConfigResponse, status_code=201)
async def connect_scrobble_account(
    body: ScrobbleConnectRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """
    Connect the caller's Last.fm/Libre.fm account.

    The credentials are verified against the service through
    ``auth.getMobileSession`` before anything is persisted; the returned
    session key is stored encrypted and the password is discarded.
    """
    _check_scrobbling(config)
    if not scrobbler.service_available(config, body.service):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{body.service} is not configured on this instance",
        )
    if not body.username.strip() or not body.password:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="username and password are required",
        )
    try:
        row = await scrobbler.connect(
            db,
            current_user,
            service=body.service,
            username=body.username.strip(),
            password=body.password,
            config=config,
        )
    except ScrobblerTemporaryError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not reach scrobble service: {exc}",
        ) from exc
    except ScrobblerError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Could not authenticate with scrobble service: {exc}",
        ) from exc
    return _config_response(row)


@router.put("/", response_model=ScrobbleConfigResponse)
async def update_scrobble_settings(
    body: ScrobbleSettingsRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Update the caller's scrobble toggle and listen thresholds."""
    _check_scrobbling(config)
    row = await scrobbler.get_config_row(db, current_user)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scrobbling is not configured")
    row = await scrobbler.update_settings(
        db,
        row,
        enabled=body.enabled,
        min_seconds=body.min_seconds,
        min_percent=body.min_percent,
    )
    return _config_response(row)


@router.post("/now-playing/{track_id}", status_code=204)
async def report_now_playing(
    track_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """
    Report that the caller started playing a track.

    Players call this on actual playback start — the stream endpoint cannot
    serve as the signal because clients prefetch and cache audio ahead of
    playback. Submissions go out only when the caller has an active scrobble
    config; otherwise this is a no-op.
    """
    _check_scrobbling(config)
    track = await db.get(Track, track_id)
    if track is None or not await acl.can_access(db, current_user, "track", track_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await scrobbler.maybe_enqueue_now_playing(db, str(current_user.id), track_id)


@router.delete("/", status_code=204)
async def delete_scrobble_config(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Remove the caller's scrobble configuration and stored session key."""
    _check_scrobbling(config)
    if not await scrobbler.delete_config(db, current_user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scrobbling is not configured")
