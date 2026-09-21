"""
Scrobbling to Audioscrobbler-compatible services (Last.fm, Libre.fm).

Both services expose the same signed REST API (the "Audioscrobbler 2.0"
protocol): the instance acts as the API application — the admin registers an
API key pair per service under ``[scrobbling]`` — and each user authorizes
through ``auth.getMobileSession`` (username + password → a permanent session
key, stored Fernet-encrypted on the ``ScrobbleConfig`` row).

Two submissions go out per play:

* ``track.updateNowPlaying`` when playback actually starts — reported
  explicitly by the web player (``POST /scrobbling/now-playing/{track}`` on
  its first ``play`` event) or by Subsonic clients
  (``scrobble.view?submission=false``). Stream/download requests never
  trigger it: clients prefetch and cache audio they never play.
* ``track.scrobble`` once the play counts as a full listen — whichever of the
  user's ``min_seconds``/``min_percent`` thresholds is crossed first. All
  listen-recording paths funnel through ``services.streaming.record_listen``,
  which enqueues the submission task.

Actual HTTP work happens in ``tasks/scrobbling.py`` on the Celery worker so
request paths never block on the scrobble service.
"""

import asyncio
import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import requests
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_default_user_agent
from ..config.schema import SonghiveConfig
from ..models.scrobble import ScrobbleConfig
from ..models.track import Track
from ..models.user import User
from .secrets import decrypt_secret, encrypt_secret

logger = logging.getLogger(__name__)

DEFAULT_MIN_SECONDS = 30
DEFAULT_MIN_PERCENT = 25

MIN_SECONDS_RANGE = (1, 3600)
MIN_PERCENT_RANGE = (1, 100)

SERVICES: Dict[str, Dict[str, str]] = {
    "lastfm": {"name": "Last.fm", "api_url": "https://ws.audioscrobbler.com/2.0/"},
    "librefm": {"name": "Libre.fm", "api_url": "https://libre.fm/2.0/"},
}


class ScrobblerError(Exception):
    """Raised when the scrobble service rejects a request or misbehaves."""


class ScrobblerTemporaryError(ScrobblerError):
    """Raised for transient failures (network, 5xx) — safe to retry."""


def service_api_url(service: str) -> str:
    """Return the Audioscrobbler API endpoint for ``service``."""
    meta = SERVICES.get(service)
    if meta is None:
        raise ScrobblerError(f"Unknown scrobble service: {service!r}")
    return meta["api_url"]


def _api_credentials(config: SonghiveConfig, service: str) -> Tuple[Optional[str], Optional[str]]:
    """Return the ``(api_key, api_secret)`` pair configured for ``service``."""
    scrobbling = config.scrobbling
    if service == "lastfm":
        return scrobbling.lastfm_api_key or None, scrobbling.lastfm_api_secret or None
    if service == "librefm":
        return scrobbling.librefm_api_key or None, scrobbling.librefm_api_secret or None
    return None, None


def service_available(config: SonghiveConfig, service: str) -> bool:
    """Return True when the instance has an API key pair for ``service``."""
    if not config.scrobbling.enabled:
        return False
    key, secret = _api_credentials(config, service)
    return bool(key and secret)


def list_services(config: SonghiveConfig) -> list[dict[str, Any]]:
    """Describe every known service and whether the instance can use it."""
    return [
        {
            "id": service,
            "name": meta["name"],
            "available": service_available(config, service),
        }
        for service, meta in SERVICES.items()
    ]


class ScrobblerClient:
    """Minimal client for the Audioscrobbler 2.0 scrobble API.

    Every call is a form-encoded POST signed with ``api_sig`` — the MD5 of
    the alphabetically sorted ``name`` + ``value`` concatenation followed by
    the shared secret.
    """

    def __init__(
        self,
        api_url: str,
        api_key: str,
        api_secret: str,
        *,
        session_key: Optional[str] = None,
        timeout: float = 15.0,
    ):
        self._api_url = api_url
        self._api_key = api_key
        self._api_secret = api_secret
        self._session_key = session_key
        self._timeout = timeout
        self._session = requests.Session()

    def close(self) -> None:
        self._session.close()

    @staticmethod
    def _signature(params: Dict[str, str], secret: str) -> str:
        """Compute the ``api_sig`` MD5 signature over the sorted params."""
        raw = "".join(f"{key}{params[key]}" for key in sorted(params)) + secret
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def _post(self, params: Dict[str, Any], *, sign: bool = True) -> dict:
        payload = {key: str(value) for key, value in params.items() if value is not None}
        payload["api_key"] = self._api_key
        if sign:
            payload["api_sig"] = self._signature(payload, self._api_secret)
        payload["format"] = "json"
        try:
            response = self._session.post(
                self._api_url,
                data=payload,
                timeout=self._timeout,
                headers={"User-Agent": f"{get_default_user_agent()} (scrobbler)"},
            )
        except requests.RequestException as exc:
            raise ScrobblerTemporaryError(f"Cannot reach scrobble service: {exc}") from exc
        try:
            if response.status_code >= 500:
                raise ScrobblerTemporaryError(f"Scrobble service returned HTTP {response.status_code}")
            try:
                data = response.json()
            except ValueError as exc:
                raise ScrobblerError("Scrobble service returned invalid JSON") from exc
            if not isinstance(data, dict):
                raise ScrobblerError("Unexpected scrobble response shape")
            if "error" in data:
                code = data.get("error")
                message = data.get("message") or f"error {code}"
                raise ScrobblerError(f"Scrobble service rejected the request: {message} (code {code})")
            if response.status_code >= 400:
                raise ScrobblerError(f"Scrobble service returned HTTP {response.status_code}")
            return data
        finally:
            response.close()

    def get_mobile_session(self, username: str, password: str) -> Tuple[str, str]:
        """Exchange username/password for a session key; returns ``(key, name)``."""
        data = self._post(
            {
                "method": "auth.getMobileSession",
                "username": username,
                "password": password,
            }
        )
        session = data.get("session") or {}
        key, name = session.get("key"), session.get("name")
        if not key or not name:
            raise ScrobblerError("Scrobble service did not return a session")
        return str(key), str(name)

    def update_now_playing(
        self,
        *,
        artist: str,
        track: str,
        album: Optional[str] = None,
        album_artist: Optional[str] = None,
        duration: Optional[int] = None,
        track_number: Optional[int] = None,
        mbid: Optional[str] = None,
    ) -> None:
        """Submit a ``track.updateNowPlaying`` for the authenticated user."""
        if not self._session_key:
            raise ScrobblerError("No scrobble session key configured")
        self._post(
            {
                "method": "track.updateNowPlaying",
                "sk": self._session_key,
                "artist": artist,
                "track": track,
                "album": album,
                "albumArtist": album_artist,
                "duration": duration,
                "trackNumber": track_number,
                "mbid": mbid,
            }
        )

    def scrobble(
        self,
        *,
        timestamp: int,
        artist: str,
        track: str,
        album: Optional[str] = None,
        album_artist: Optional[str] = None,
        duration: Optional[int] = None,
        track_number: Optional[int] = None,
        mbid: Optional[str] = None,
    ) -> None:
        """Submit a ``track.scrobble`` for the authenticated user."""
        if not self._session_key:
            raise ScrobblerError("No scrobble session key configured")
        self._post(
            {
                "method": "track.scrobble",
                "sk": self._session_key,
                "timestamp": timestamp,
                "artist": artist,
                "track": track,
                "album": album,
                "albumArtist": album_artist,
                "duration": duration,
                "trackNumber": track_number,
                "mbid": mbid,
            }
        )


def make_client(config: SonghiveConfig, service: str, session_key: Optional[str] = None) -> ScrobblerClient:
    """Build a client for ``service`` (test seam)."""
    api_key, api_secret = _api_credentials(config, service)
    if not api_key or not api_secret:
        raise ScrobblerError(f"Scrobble service {service!r} is not configured on this instance")
    return ScrobblerClient(
        service_api_url(service),
        api_key,
        api_secret,
        session_key=session_key,
        timeout=config.scrobbling.request_timeout_seconds,
    )


def make_session_client(config: SonghiveConfig, row: ScrobbleConfig) -> ScrobblerClient:
    """Build an authenticated client from a stored config row (test seam)."""
    try:
        session_key = decrypt_secret(row.session_key)
    except Exception as exc:
        raise ScrobblerError("Stored scrobble session key cannot be decrypted") from exc
    return make_client(config, row.service, session_key=session_key)


async def get_config_row(db: AsyncSession, user: User) -> Optional[ScrobbleConfig]:
    """Return the user's scrobble configuration, or None."""
    return await db.scalar(select(ScrobbleConfig).where(ScrobbleConfig.user_id == user.id))


async def has_active_config(db: AsyncSession, user_id: str) -> bool:
    """Return True when the user has an enabled scrobble config with a session."""
    return bool(
        await db.scalar(
            select(ScrobbleConfig.id).where(
                ScrobbleConfig.user_id == user_id,
                ScrobbleConfig.enabled.is_(True),
            )
        )
    )


async def connect(
    db: AsyncSession,
    user: User,
    *,
    service: str,
    username: str,
    password: str,
    config: SonghiveConfig,
) -> ScrobbleConfig:
    """
    Verify credentials against the service and store the session key.

    The password is exchanged for a permanent session key through
    ``auth.getMobileSession`` and never persisted.
    """
    client = make_client(config, service)
    try:
        session_key, remote_name = await asyncio.to_thread(client.get_mobile_session, username, password)
    finally:
        client.close()

    row = await get_config_row(db, user)
    if row is None:
        row = ScrobbleConfig(user_id=str(user.id))
        db.add(row)
    row.service = service
    row.username = remote_name
    row.session_key = encrypt_secret(session_key)
    row.enabled = True
    row.last_error = None
    await db.flush()
    return row


async def update_settings(
    db: AsyncSession,
    row: ScrobbleConfig,
    *,
    enabled: bool,
    min_seconds: int,
    min_percent: int,
) -> ScrobbleConfig:
    """Update the user's scrobble toggle and listen thresholds."""
    row.enabled = enabled
    row.min_seconds = min_seconds
    row.min_percent = min_percent
    await db.flush()
    return row


async def delete_config(db: AsyncSession, user: User) -> bool:
    """Delete the user's scrobble config. Returns False if absent."""
    row = await get_config_row(db, user)
    if row is None:
        return False
    await db.delete(row)
    await db.flush()
    return True


async def thresholds_for(db: AsyncSession, user: Optional[User]) -> Tuple[float, Optional[float]]:
    """
    Return the effective ``(min_seconds, min_percent)`` listen thresholds.

    Users without an active scrobble config keep the historical seconds-only
    threshold (``min_percent`` reported as ``None``); configured users get
    their own values so a play counts — and scrobbles — at the same point on
    every client.
    """
    if user is None:
        return float(DEFAULT_MIN_SECONDS), None
    row = await get_config_row(db, user)
    if row is None or not row.enabled or not row.session_key:
        return float(DEFAULT_MIN_SECONDS), None
    return float(row.min_seconds), float(row.min_percent)


def track_fields(track: Track) -> Dict[str, Any]:
    """Map a ``Track`` (with artist/album loaded) to scrobble API fields."""
    album = track.album
    album_artist = album.artist.name if album is not None and album.artist is not None else None
    return {
        "artist": track.artist.name if track.artist is not None else "",
        "track": track.title,
        "album": album.title if album is not None else None,
        "album_artist": album_artist,
        "duration": int(track.duration) if track.duration else None,
        "track_number": track.track_number,
        "mbid": track.musicbrainz_id,
    }


async def submit_now_playing(config: SonghiveConfig, row: ScrobbleConfig, track: Track) -> None:
    """Submit ``track.updateNowPlaying``; records ``last_error`` on failure."""
    client = make_session_client(config, row)
    try:
        await asyncio.to_thread(client.update_now_playing, **track_fields(track))
    except ScrobblerError as exc:
        row.last_error = str(exc)
        raise
    else:
        row.last_error = None
    finally:
        client.close()


async def submit_scrobble(
    config: SonghiveConfig,
    row: ScrobbleConfig,
    track: Track,
    played_at: int,
) -> None:
    """Submit ``track.scrobble``; records ``last_error``/``last_scrobbled_at``."""
    client = make_session_client(config, row)
    try:
        await asyncio.to_thread(client.scrobble, timestamp=played_at, **track_fields(track))
    except ScrobblerError as exc:
        row.last_error = str(exc)
        raise
    else:
        row.last_error = None
        row.last_scrobbled_at = datetime.now(timezone.utc)
    finally:
        client.close()


def enqueue_now_playing(user_id: str, track_id: str) -> None:
    """Enqueue a now-playing submission; failures never break playback paths."""
    try:
        from ..tasks.scrobbling import scrobble_now_playing

        scrobble_now_playing.delay(str(user_id), str(track_id))
    except Exception:
        logger.warning("Could not enqueue now-playing scrobble for track %s", track_id, exc_info=True)


async def maybe_enqueue_now_playing(db: AsyncSession, user_id: str, track_id: str) -> None:
    """Enqueue a now-playing submission when the user has an active scrobble config."""
    if await has_active_config(db, user_id):
        enqueue_now_playing(user_id, track_id)


def enqueue_scrobble(user_id: str, track_id: str, played_at: Optional[int] = None) -> None:
    """Enqueue a full scrobble submission; failures never break listen recording."""
    try:
        from ..tasks.scrobbling import scrobble_track

        scrobble_track.delay(str(user_id), str(track_id), played_at or int(time.time()))
    except Exception:
        logger.warning("Could not enqueue scrobble for track %s", track_id, exc_info=True)
