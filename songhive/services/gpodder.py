"""
GPodder-compatible podcast subscription synchronization.

Each user may link one GPodder-compatible account — either a gpodder.net
API server (gpodder.net itself, opodsync) or a Nextcloud instance running
the ``gpoddersync`` app, which replicates the add/remove/timestamp diff
format under different paths and without auth/device endpoints — and pick
a mode:

* ``pull`` — apply subscription changes received from the server; local
  follows/unfollows are never uploaded.
* ``bidirectional`` — apply remote changes *and* upload local ones.

Sync state is per ``PodcastSyncConfig`` row: ``last_sync_timestamp`` is the
opaque server-issued marker replayed as ``since``, and ``last_synced_at``
(the wall-clock start of the last successful sync) is the watermark that
``PodcastSyncEvent`` rows are diffed against for pending local changes.

Conflict resolution: the API reports remote add/remove lists without
per-item timestamps, so a remote change is conservatively dated to the
start of the sync window — a pending local change on the same feed URL is
treated as the most recent one and wins. Local changes are applied by
uploading them after the pull, so the server converges on the same outcome.

The first sync has no shared baseline: it unions both subscription lists —
remote additions are applied locally and feeds only present locally are
uploaded — and pending local events are left for the next regular sync.

All requests go through the same SSRF discipline as feed fetches: the
configured server URL and every redirect hop must resolve to a public
address. Credentials authenticate via HTTP Basic, with the session-cookie
login endpoint as fallback for servers that require it.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple
from urllib.parse import quote, urljoin, urlparse, urlunparse

import requests
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_default_user_agent
from ..config.schema import SonghiveConfig
from ..models.podcast import (
    Podcast,
    PodcastSubscription,
    PodcastSyncConfig,
    PodcastSyncEvent,
)
from ..models.user import User
from . import podcasts as podcasts_service
from .podcasts import FeedFetchError, url_allowed

logger = logging.getLogger(__name__)

MAX_REDIRECTS = 5
MAX_SYNC_URLS = 10_000
DEFAULT_DEVICE_ID = "songhive"
SYNC_MODES = ("pull", "bidirectional")
SERVER_TYPES = ("gpodder", "nextcloud")
# Nextcloud app route root — matches both ``/index.php/apps/gpoddersync``
# and the pretty-URL form ``/apps/gpoddersync``.
NEXTCLOUD_APP_SUFFIX = "/apps/gpoddersync"


class GPodderError(Exception):
    """Raised when communication with the sync server fails."""


@dataclass
class SubscriptionChanges:
    """Add/remove subscription diff reported by the server."""

    add: List[str] = field(default_factory=list)
    remove: List[str] = field(default_factory=list)
    timestamp: Optional[float] = None
    update_urls: List[Tuple[str, str]] = field(default_factory=list)


@dataclass
class SyncResult:
    """Outcome of one sync run."""

    subscribed: int = 0
    unsubscribed: int = 0
    pushed_adds: int = 0
    pushed_removes: int = 0
    errors: List[str] = field(default_factory=list)


def normalize_server_url(url: str, server_type: str = "gpodder") -> str:
    """Normalize a user-supplied sync server URL into canonical form."""
    url = url.strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise GPodderError("Server URL must be an absolute http(s) URL")
    if server_type == "gpodder" and parsed.path not in ("", "/"):
        # gpodder.net API paths are appended at the root — a sub-path would
        # silently produce wrong endpoints, so reject it up front. The
        # Nextcloud app, by contrast, lives under a path by definition.
        raise GPodderError("Server URL must not contain a path")
    # Query/fragment have no meaning in a base URL and would corrupt
    # appended paths — drop them.
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def api_base_url(server_url: str, server_type: str) -> str:
    """Resolve the API base URL requests are built on for ``server_type``."""
    url = normalize_server_url(server_url, server_type)
    if server_type != "nextcloud":
        return url
    # Accept the gpoddersync app URL verbatim — including pasted endpoint
    # URLs like ``…/apps/gpoddersync/subscriptions`` — and otherwise treat
    # the input as the Nextcloud root (keeping any subdirectory prefix).
    marker = url.find(NEXTCLOUD_APP_SUFFIX)
    if marker != -1:
        return url[: marker + len(NEXTCLOUD_APP_SUFFIX)]
    return f"{url}/index.php{NEXTCLOUD_APP_SUFFIX}"


class GPodderClient:
    """Minimal client for the subscription-sync slice of the GPodder API.

    Requests authenticate with HTTP Basic; on a ``401`` the client falls
    back to ``POST /api/2/auth/{username}/login.json`` (which accepts Basic
    and returns a ``sessionid`` cookie) and retries once, covering servers
    that only honour cookie auth.
    """

    def __init__(
        self,
        server_url: str,
        username: str,
        password: Optional[str],
        *,
        timeout: float,
        server_type: str = "gpodder",
    ):
        self._base = api_base_url(server_url, server_type)
        self._username = username
        self._timeout = timeout
        self._session = requests.Session()
        self._session.auth = (username, password or "")

    def close(self) -> None:
        self._session.close()

    def login(self) -> None:
        """Establish a session cookie via the auth endpoint."""
        self._request("POST", f"/api/2/auth/{quote(self._username, safe='')}/login.json", _retried=True)

    def update_device(self, device_id: str, *, caption: str = "Songhive", device_type: str = "server") -> None:
        """Register/update the device so it shows up with a friendly name."""
        self._request(
            "PUT",
            self._device_path(device_id, "devices"),
            json_body={"caption": caption, "type": device_type},
        )

    def get_subscription_changes(self, device_id: str, since: float) -> SubscriptionChanges:
        """Fetch subscription changes since the server-issued ``since`` marker."""
        data = self._request(
            "GET",
            self._device_path(device_id, "subscriptions"),
            params={"since": since},
        )
        return self._parse_changes(data)

    def upload_subscription_changes(
        self,
        device_id: str,
        add: List[str],
        remove: List[str],
    ) -> SubscriptionChanges:
        """Upload local add/remove deltas; returns the new server timestamp."""
        data = self._request(
            "POST",
            self._device_path(device_id, "subscriptions"),
            json_body={"add": add, "remove": remove},
        )
        return self._parse_changes(data)

    def _device_path(self, device_id: str, section: str) -> str:
        user = quote(self._username, safe="")
        device = quote(device_id, safe="")
        return f"/api/2/{section}/{user}/{device}.json"

    @staticmethod
    def _parse_changes(data) -> SubscriptionChanges:
        if not isinstance(data, dict):
            raise GPodderError("Unexpected sync response shape")
        changes = SubscriptionChanges()
        for key, target in (("add", changes.add), ("remove", changes.remove)):
            values = data.get(key) or []
            if isinstance(values, list):
                target.extend(str(url) for url in values[:MAX_SYNC_URLS])
        timestamp = data.get("timestamp")
        if timestamp is not None:
            try:
                changes.timestamp = float(timestamp)
            except (TypeError, ValueError):
                pass
        for pair in data.get("update_urls") or []:
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                changes.update_urls.append((str(pair[0]), str(pair[1])))
        return changes

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body=None,
        params=None,
        _retried: bool = False,
    ):
        url = urljoin(self._base + "/", path.lstrip("/"))
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            if not url_allowed(current):
                raise GPodderError(f"Refusing to contact non-public sync URL: {current}")
            try:
                response = self._session.request(
                    method,
                    current,
                    json=json_body,
                    params=params,
                    timeout=self._timeout,
                    allow_redirects=False,
                    headers={"User-Agent": f"{get_default_user_agent()} (gpodder sync)"},
                )
            except requests.RequestException as exc:
                raise GPodderError(f"Cannot reach sync server: {exc}") from exc
            try:
                if response.is_redirect or response.is_permanent_redirect:
                    location = response.headers.get("Location")
                    if not location:
                        raise GPodderError("Sync server redirect without Location header")
                    current = urljoin(current, location)
                    continue
                if response.status_code == 401:
                    if _retried:
                        raise GPodderError("Sync authentication failed — check username and password")
                    self.login()
                    return self._request(method, path, json_body=json_body, params=params, _retried=True)
                if response.status_code >= 400:
                    raise GPodderError(f"Sync request failed with HTTP {response.status_code}")
                if not response.content:
                    return None
                try:
                    return response.json()
                except ValueError as exc:
                    raise GPodderError("Sync server returned invalid JSON") from exc
            finally:
                response.close()
        raise GPodderError(f"Too many redirects contacting sync server: {url}")


class NextcloudGPodderClient(GPodderClient):
    """Client for the Nextcloud ``gpoddersync`` app.

    The app replicates the gpodder.net add/remove/timestamp diff format but
    under different paths, authenticates via plain HTTP Basic (an app
    password — no session login endpoint), and tracks subscriptions
    per-account rather than per-device, so ``device_id`` is ignored.
    """

    def __init__(self, server_url: str, username: str, password: Optional[str], *, timeout: float):
        super().__init__(server_url, username, password, timeout=timeout, server_type="nextcloud")

    def login(self) -> None:
        """Verify credentials — the app has no dedicated auth endpoint."""
        self._request("GET", "/subscriptions", params={"since": 0}, _retried=True)

    def update_device(self, device_id: str, *, caption: str = "Songhive", device_type: str = "server") -> None:
        """gpoddersync has no device API — subscriptions are per-account."""

    def get_subscription_changes(self, device_id: str, since: float) -> SubscriptionChanges:
        data = self._request("GET", "/subscriptions", params={"since": since})
        return self._parse_changes(data)

    def upload_subscription_changes(
        self,
        device_id: str,
        add: List[str],
        remove: List[str],
    ) -> SubscriptionChanges:
        data = self._request("POST", "/subscription_change/create", json_body={"add": add, "remove": remove})
        return self._parse_changes(data)


def make_client(config_row: PodcastSyncConfig, config: SonghiveConfig) -> GPodderClient:
    """Build a client for a stored sync configuration (test seam)."""
    client_cls = NextcloudGPodderClient if config_row.server_type == "nextcloud" else GPodderClient
    return client_cls(
        config_row.server_url,
        config_row.username,
        config_row.password,
        timeout=config.podcasts.request_timeout_seconds,
    )


async def get_sync_config(db: AsyncSession, user: User) -> Optional[PodcastSyncConfig]:
    """Return the user's sync configuration, or None."""
    return await db.scalar(select(PodcastSyncConfig).where(PodcastSyncConfig.user_id == user.id))


async def upsert_sync_config(
    db: AsyncSession,
    user: User,
    *,
    server_type: str,
    server_url: str,
    username: str,
    password: Optional[str],
    device_id: str,
    mode: str,
    enabled: bool,
) -> tuple[PodcastSyncConfig, bool]:
    """Create or update the user's sync config; returns ``(row, created)``."""
    row = await get_sync_config(db, user)
    created = row is None
    if row is None:
        row = PodcastSyncConfig(user_id=user.id)
        db.add(row)
    new_server_url = normalize_server_url(server_url, server_type)
    new_device_id = device_id.strip() or DEFAULT_DEVICE_ID
    if (
        created
        or row.server_type != server_type
        or row.server_url != new_server_url
        or row.username != username
        or row.device_id != new_device_id
    ):
        # A different account/device invalidates the server-side watermark —
        # the next sync starts from a union baseline again.
        row.last_sync_timestamp = None
        row.last_synced_at = None
        row.last_attempt_at = None
        row.last_error = None
    row.server_type = server_type
    row.server_url = new_server_url
    row.username = username
    # An omitted password keeps the stored one; an empty string clears it.
    if password is not None:
        row.password = password or None
    row.device_id = new_device_id
    row.mode = mode
    row.enabled = enabled
    await db.flush()
    return row, created


async def delete_sync_config(db: AsyncSession, user: User) -> bool:
    """Delete the user's sync config and its change log. Returns False if absent."""
    row = await get_sync_config(db, user)
    if row is None:
        return False
    await db.delete(row)
    for event in (await db.scalars(select(PodcastSyncEvent).where(PodcastSyncEvent.user_id == user.id))).all():
        await db.delete(event)
    await db.flush()
    return True


async def verify_credentials(config_row: PodcastSyncConfig, config: SonghiveConfig) -> None:
    """Check that the stored credentials authenticate against the server."""
    client = make_client(config_row, config)
    try:
        await asyncio.to_thread(client.login)
        try:
            await asyncio.to_thread(
                client.update_device,
                config_row.device_id,
                caption="Songhive",
                device_type="server",
            )
        except GPodderError:
            # Device registration is cosmetic — some minimal servers don't
            # implement it. Auth itself already succeeded.
            logger.debug("Could not register gpodder device %s", config_row.device_id)
    finally:
        client.close()


async def pending_local_changes(db: AsyncSession, user: User, config_row: PodcastSyncConfig) -> dict[str, str]:
    """Return the latest ``local`` action per feed URL since the last sync."""
    conditions = [
        PodcastSyncEvent.user_id == user.id,
        PodcastSyncEvent.origin == "local",
    ]
    if config_row.last_synced_at is not None:
        conditions.append(PodcastSyncEvent.created_at > config_row.last_synced_at)
    rows = (
        await db.scalars(select(PodcastSyncEvent).where(*conditions).order_by(PodcastSyncEvent.created_at.asc()))
    ).all()
    pending: dict[str, str] = {}
    for event in rows:
        pending[event.feed_url] = event.action
    return pending


async def subscribed_feed_urls(db: AsyncSession, user: User) -> List[str]:
    """Return the feed URLs the user is currently subscribed to."""
    rows = await db.scalars(
        select(Podcast.feed_url)
        .join(PodcastSubscription, PodcastSubscription.podcast_id == Podcast.id)
        .where(PodcastSubscription.user_id == user.id)
    )
    return list(rows)


async def _apply_update_urls(db: AsyncSession, update_urls: List[Tuple[str, str]]) -> None:
    """Apply server-side feed URL rewrites to local podcasts and events."""
    for old_url, new_url in update_urls:
        if not new_url or old_url == new_url:
            continue
        podcast = await podcasts_service.get_podcast_by_feed_url(db, old_url)
        if podcast is None:
            continue
        if await podcasts_service.get_podcast_by_feed_url(db, new_url) is not None:
            logger.info("Skipping feed_url rewrite %s -> %s: target exists", old_url, new_url)
            continue
        podcast.feed_url = new_url
        await db.execute(update(PodcastSyncEvent).where(PodcastSyncEvent.feed_url == old_url).values(feed_url=new_url))
    await db.flush()


async def sync_subscriptions(
    db: AsyncSession,
    user: User,
    config_row: PodcastSyncConfig,
    config: SonghiveConfig,
) -> SyncResult:
    """
    Run one subscription sync for ``user`` against their configured server.

    Applies remote adds/removes, then (in ``bidirectional`` mode) uploads
    pending local changes. Updates ``config_row`` timestamps; callers handle
    ``GPodderError`` for the failure path.
    """
    started_at = datetime.now(timezone.utc)
    client = make_client(config_row, config)
    first_sync = config_row.last_sync_timestamp is None
    result = SyncResult()
    try:
        changes = await asyncio.to_thread(
            client.get_subscription_changes,
            config_row.device_id,
            config_row.last_sync_timestamp or 0,
        )

        pending = await pending_local_changes(db, user, config_row)

        if first_sync:
            # No shared baseline: union remote additions in, never remove.
            remote_adds, remote_removes = changes.add, []
        elif config_row.mode == "bidirectional":
            # Pending local changes are treated as the most recent action —
            # the API reports no per-item remote timestamps, so a conflict
            # resolves to the local side and is pushed back below.
            remote_adds = [url for url in changes.add if url not in pending]
            remote_removes = [url for url in changes.remove if url not in pending]
        else:
            remote_adds, remote_removes = changes.add, changes.remove

        await _apply_update_urls(db, changes.update_urls)

        for url in remote_removes:
            podcast = await podcasts_service.get_podcast_by_feed_url(db, url)
            if podcast is not None and await podcasts_service.unsubscribe(db, user, str(podcast.id), origin="remote"):
                result.unsubscribed += 1
        for url in remote_adds:
            try:
                _, created = await podcasts_service.subscribe(db, user, url, config, origin="remote")
            except FeedFetchError as exc:
                result.errors.append(f"{url}: {exc}")
                continue
            if created:
                result.subscribed += 1

        new_timestamp = changes.timestamp
        if config_row.mode == "bidirectional":
            if first_sync:
                remote_urls = set(changes.add)
                push_adds = sorted(url for url in await subscribed_feed_urls(db, user) if url not in remote_urls)
                push_removes: List[str] = []
            else:
                push_adds = sorted(url for url, action in pending.items() if action == "add")
                push_removes = sorted(url for url, action in pending.items() if action == "remove")
            if push_adds or push_removes:
                uploaded = await asyncio.to_thread(
                    client.upload_subscription_changes,
                    config_row.device_id,
                    push_adds,
                    push_removes,
                )
                await _apply_update_urls(db, uploaded.update_urls)
                if uploaded.timestamp is not None:
                    new_timestamp = uploaded.timestamp
                result.pushed_adds = len(push_adds)
                result.pushed_removes = len(push_removes)

        config_row.last_sync_timestamp = new_timestamp
        config_row.last_synced_at = started_at
        config_row.last_error = None
        await db.flush()
        return result
    finally:
        client.close()


async def run_sync(
    db: AsyncSession,
    user: User,
    config_row: PodcastSyncConfig,
    config: SonghiveConfig,
) -> SyncResult:
    """
    Run ``sync_subscriptions`` with attempt bookkeeping.

    ``last_attempt_at`` stamps every run (success or failure) so the
    periodic scan can throttle; ``last_synced_at`` only moves on success so
    pending local events are never skipped after a failed run.
    """
    config_row.last_attempt_at = datetime.now(timezone.utc)
    try:
        return await sync_subscriptions(db, user, config_row, config)
    except (GPodderError, requests.RequestException) as exc:
        config_row.last_error = str(exc)[:2000]
        await db.flush()
        raise


async def due_sync_user_ids(db: AsyncSession, interval: timedelta, *, limit: int = 200) -> List[str]:
    """Return user ids whose enabled sync config is due for a run."""
    cutoff = datetime.now(timezone.utc) - interval
    rows = await db.scalars(
        select(PodcastSyncConfig.user_id)
        .where(
            PodcastSyncConfig.enabled.is_(True),
            or_(
                PodcastSyncConfig.last_attempt_at.is_(None),
                PodcastSyncConfig.last_attempt_at <= cutoff,
            ),
        )
        .limit(limit)
    )
    return [str(row) for row in rows]
