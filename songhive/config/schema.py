"""
Configuration schema for Songhive, defined as a Pydantic settings model.
"""

import json
import re
from enum import Enum
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import urlparse, urlunparse

from pydantic import Field, field_serializer, field_validator
from pydantic_settings import BaseSettings


def _redact_database_url(url: str) -> str:
    """Return a copy of a database URL with the password component redacted."""
    parsed = urlparse(url)
    if not parsed.password:
        return url
    netloc = f"{parsed.username or ''}:***@{parsed.hostname or ''}"
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))


def _app_short_version() -> str:
    from ..version import __version__

    return ".".join(__version__.split("+", maxsplit=1)[0].split(".")[:2])


def get_default_user_agent() -> str:
    return f"Songhive/{_app_short_version()} (https://git.fabiomanganiello.com/songhive)"


_ENGINE_KWARG_NAMES = ("pool_size", "max_overflow", "pool_timeout", "pool_recycle", "pool_pre_ping")


def database_engine_kwargs(database) -> dict:
    """SQLAlchemy ``create_async_engine`` kwargs for a database config object.

    Reads the ``pool_*`` attributes off ``database``, falling back to the
    ``DatabaseConfig`` defaults for any that are missing — so duck-typed
    config objects (e.g. ``types.SimpleNamespace`` in tests) work too.
    """
    defaults = DatabaseConfig.model_fields
    return {name: getattr(database, name, defaults[name].default) for name in _ENGINE_KWARG_NAMES}


def database_task_engine_kwargs(database) -> dict:
    """Pool kwargs for short-lived ``asyncio.run`` loops (Celery tasks, CLI).

    Each task's engine lives for one ``asyncio.run`` and is disposed after;
    a task rarely holds more than one or two connections at once, so a small
    pool keeps the cluster-wide connection budget (workers x loops x pool
    cap) far below Postgres ``max_connections`` while ``pool_timeout`` still
    bounds queueing.
    """
    return {
        **database_engine_kwargs(database),
        "pool_size": 1,
        "max_overflow": 2,
    }


class DatabaseConfig(BaseSettings):
    """Database configuration."""

    url: str = Field(
        default="postgresql+asyncpg://songhive:songhive@localhost:5432/songhive",
        description="SQLAlchemy database URL",
    )
    pool_size: int = Field(default=5, description="Connection pool size")
    max_overflow: int = Field(default=10, description="Max overflow connections")
    pool_timeout: float = Field(
        default=5.0,
        description=(
            "Seconds a request may wait for a pooled database connection "
            "before failing fast with 503. Bounds how long a traffic spike "
            "can queue inside the app instead of exhausting the database's "
            "connection limit. Kept below typical remote fetch timeouts "
            "(Mastodon gives up around 10 s): work nobody receives is shed "
            "early as a retryable 503 instead."
        ),
    )
    pool_recycle: int = Field(
        default=1800,
        description="Seconds after which a pooled connection is recycled",
    )
    pool_pre_ping: bool = Field(
        default=True,
        description="Test pooled connections for liveness on checkout",
    )

    @field_serializer("url", when_used="always")
    def _redact_url(self, value: str, *_, **__) -> str:
        return _redact_database_url(value)

    def __repr_args__(self):
        for name, value in super().__repr_args__():
            if name == "url" and isinstance(value, str):
                yield name, _redact_database_url(value)
            else:
                yield name, value


class RedisConfig(BaseSettings):
    """Redis configuration."""

    url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL",
    )


class CeleryConfig(BaseSettings):
    """Celery worker configuration."""

    broker_url: str = Field(
        default="redis://localhost:6379/1",
        description="Celery broker URL",
    )
    result_backend: str = Field(
        default="redis://localhost:6379/2",
        description="Celery result backend URL",
    )
    cleanup_orphaned_files_schedule: str = Field(
        default="0 3 * * *",
        description="Crontab expression for the orphaned-files cleanup task",
    )


class StorageConfig(BaseSettings):
    """Media storage configuration."""

    backend: Literal["local", "s3"] = Field(
        default="local",
        description="Storage backend type",
    )
    local_path: Path = Field(
        default=Path("/var/lib/songhive/media"),
        description="Local storage base path",
    )
    s3_endpoint: Optional[str] = Field(default=None, description="S3 endpoint URL")
    s3_bucket: Optional[str] = Field(default=None, description="S3 bucket name")
    s3_access_key: Optional[str] = Field(default=None, description="S3 access key")
    s3_secret_key: Optional[str] = Field(
        default=None,
        description="S3 secret key",
        repr=False,
        exclude=True,
    )
    s3_region: Optional[str] = Field(default=None, description="S3 region")
    cdn_prefix: Optional[str] = Field(default=None, description="CDN URL prefix for serving files")
    max_upload_size: Optional[int] = Field(
        default=500 * 1024 * 1024,
        description="Maximum upload size in bytes; defaults to 500 MiB",
    )
    max_bulk_upload_files: int = Field(
        default=50,
        ge=1,
        description="Maximum number of files allowed in a single bulk upload request",
    )
    max_bulk_upload_total_size: Optional[int] = Field(
        default=5 * 1024 * 1024 * 1024,
        description="Maximum total size in bytes for a single bulk upload request; defaults to 5 GiB",
    )
    upload_quota: Optional[int] = Field(
        default=None,
        ge=0,
        description=(
            "Default per-user upload quota in bytes, applied to media uploads by "
            "non-admin users. None (the default) means unlimited. Admins can "
            "override it per user — higher, lower, or no quota at all."
        ),
    )


class FederationConfig(BaseSettings):
    """ActivityPub federation configuration."""

    enabled: bool = Field(default=True, description="Enable federation")
    instance_domain: str = Field(
        default="",
        description="Public domain of this instance; when empty, share URLs fall back to the request base URL",
    )
    instance_name: str = Field(
        default="Songhive",
        description="Display name of this instance",
    )
    instance_description: str = Field(
        default="A federated music sharing service",
        description="Instance description",
    )
    contact_name: str = Field(
        default="",
        description="Display name of the contact person for this instance",
    )
    contact_email: str = Field(
        default="",
        description="Contact email address for this instance",
    )
    contact_url: str = Field(
        default="",
        description="Contact/profile URL of the contact person for this instance",
    )
    private_key_path: Optional[Path] = Field(
        default=None,
        description="Path to the ActivityPub actor private key PEM file; a key is generated here if missing",
    )
    allowed_instances: list[str] = Field(
        default_factory=list,
        description=(
            "ActivityPub instances allowed to federate with this server. "
            "When empty, all instances are allowed except those in blocked_instances."
        ),
    )
    blocked_instances: list[str] = Field(
        default_factory=list,
        description="ActivityPub instances that are always blocked from federation.",
    )
    remote_search_access: Literal["disabled", "authenticated", "public"] = Field(
        default="authenticated",
        description=(
            "Who may perform explicit remote (federated) lookups: 'disabled' "
            "turns remote search off, 'authenticated' restricts it to "
            "logged-in users, 'public' also allows anonymous lookups."
        ),
    )
    fetch_timeout_seconds: float = Field(
        default=20.0,
        ge=1.0,
        le=300.0,
        description=(
            "Timeout in seconds for each outbound remote fetch (WebFinger, "
            "actor and object dereferencing). Applied per request hop."
        ),
    )
    remote_activity_retention_days: int = Field(
        default=30,
        ge=1,
        description=(
            "Remote activities older than this many days are pruned by the "
            "prune-remote-activities task when no local user has interacted "
            "with them. Pruned objects can always be retrieved again through "
            "explicit remote URL lookup."
        ),
    )
    remote_activity_prune_schedule: Optional[str] = Field(
        default=None,
        description=(
            "Optional 5-field cron expression (e.g. '0 4 * * *') scheduling "
            "automatic remote-activity pruning. Empty/unset means the prune "
            "task only runs manually (admin endpoint, CLI, or task call)."
        ),
    )
    document_cache_ttl_seconds: float = Field(
        default=60.0,
        ge=0.0,
        le=3600.0,
        description=(
            "Seconds a served ActivityPub document (actor, WebFinger, object, "
            "collection or nodeinfo dereference) may be reused from the "
            "in-process cache. When a post is boosted, hundreds of remote "
            "instances dereference the same URLs at once; the cache collapses "
            "that stampede into a single render. 0 disables caching; misses "
            "(404s) are cached for at most 15 seconds regardless."
        ),
    )
    edge_cache_ttl_seconds: float = Field(
        default=15.0,
        ge=0.0,
        le=3600.0,
        description=(
            "Cap on how long the reverse proxy's edge cache (nginx "
            "proxy_cache) may serve a dereference document. The app emits "
            "X-Accel-Expires bounded by both the document's remaining "
            "in-process freshness and this cap, so the edge TTL can never "
            "outlive the app's freshness policy — app-side invalidation "
            "cannot reach the edge cache, which is why the cap is kept "
            "short. Must match docker/nginx.conf's proxy_cache_valid "
            "fallback. 0 disables edge caching entirely."
        ),
    )


class DownloadsConfig(BaseSettings):
    """Bulk download archive configuration."""

    enabled: bool = Field(
        default=True,
        description="Enable bulk download archives (ZIP generation for tracks, albums, artists, playlists)",
    )
    max_items: int = Field(
        default=500,
        ge=1,
        description="Maximum number of items a single archive request may resolve to",
    )
    max_concurrent_archives: int = Field(
        default=2,
        ge=1,
        description="Maximum number of archive builds running concurrently across the instance",
    )
    max_active_per_user: int = Field(
        default=2,
        ge=1,
        description="Maximum number of pending/processing archive requests a single user may hold",
    )
    retention_hours: int = Field(
        default=24,
        ge=1,
        description="Hours a completed archive is kept before the periodic cleanup deletes it",
    )
    stale_run_hours: int = Field(
        default=6,
        ge=1,
        description=(
            "Hours after which an archive still marked pending/processing is "
            "considered abandoned (worker crash) and failed by the cleanup task"
        ),
    )
    fetch_attempts: int = Field(
        default=3,
        ge=1,
        description="Attempts per item when fetching remote/external audio before marking it failed",
    )
    fetch_backoff_seconds: float = Field(
        default=2.0,
        ge=0.0,
        description="Base delay in seconds for exponential backoff between item fetch attempts",
    )
    fetch_timeout_seconds: float = Field(
        default=30.0,
        ge=1.0,
        description="Timeout in seconds for each remote audio fetch hop",
    )
    max_item_bytes: int = Field(
        default=2 * 1024 * 1024 * 1024,
        ge=1024 * 1024,
        description="Maximum bytes fetched for a single remote/external item",
    )


class FeedsConfig(BaseSettings):
    """RSS/Atom feed configuration."""

    enabled: bool = Field(
        default=True,
        description="Enable RSS/Atom feeds under /feeds and <link> feed discovery on object pages",
    )
    max_items: int = Field(
        default=20,
        ge=1,
        le=500,
        description="Maximum number of items returned in a feed",
    )


class PodcastsConfig(BaseSettings):
    """Podcast subscription configuration."""

    enabled: bool = Field(
        default=True,
        description="Enable podcast RSS subscriptions under /api/v1/podcasts",
    )
    refresh_interval_minutes: int = Field(
        default=240,
        ge=15,
        description=(
            "Minimum minutes between automatic feed refreshes for a followed "
            "podcast. The periodic scan task only refetches feeds whose last "
            "fetch is older than this."
        ),
    )
    request_timeout_seconds: float = Field(
        default=15.0,
        ge=1.0,
        description="Timeout in seconds for podcast feed HTTP requests",
    )
    max_feed_bytes: int = Field(
        default=10 * 1024 * 1024,
        ge=1024,
        description="Maximum response body size accepted when fetching a podcast feed",
    )
    gpodder_sync_interval_minutes: int = Field(
        default=30,
        ge=5,
        description=(
            "Minimum minutes between automatic GPodder subscription syncs "
            "per user. The periodic scan only syncs accounts whose last "
            "attempt is older than this."
        ),
    )


class WebmentionsConfig(BaseSettings):
    """Webmention (W3C recommendation) configuration."""

    enabled: bool = Field(
        default=True,
        description=(
            "Enable Webmention support: incoming mentions are accepted at "
            "/webmentions and outgoing mentions are sent for URLs found in "
            "public local activities. Requires federation.instance_domain to "
            "be set so URLs can be built and validated."
        ),
    )
    request_timeout: float = Field(
        default=15.0,
        ge=1.0,
        description="Timeout in seconds for outgoing Webmention HTTP requests",
    )
    discovery_max_bytes: int = Field(
        default=1024 * 1024,
        ge=1024,
        description="Maximum response body size fetched for endpoint discovery and source parsing",
    )


class RegistrationMode(str, Enum):
    """Allowed user registration modes."""

    OPEN = "open"
    INVITE_ONLY = "invite-only"
    APPROVAL_REQUIRED = "approval-required"
    PAID = "paid"
    CLOSED = "closed"


class AuthConfig(BaseSettings):
    """Authentication configuration."""

    registration_mode: RegistrationMode = Field(
        default=RegistrationMode.OPEN,
        description="How new user registration is handled",
    )
    require_email_verification: bool = Field(
        default=False,
        description="Require email verification before a registered account can log in",
    )
    access_token_expiry_minutes: int = Field(
        default=15,
        description="JWT access token expiry in minutes",
    )
    refresh_token_expiry_days: int = Field(
        default=30,
        description="Refresh token expiry in days",
    )
    password_reset_token_expiry_minutes: int = Field(
        default=30,
        description="Password reset token expiry in minutes",
    )
    rate_limit_enabled: bool = Field(
        default=True,
        description="Enable rate limiting on sensitive authentication endpoints",
    )
    rate_limit_requests: int = Field(
        default=10,
        description="Max requests allowed in a rate limit window",
    )
    rate_limit_window_seconds: int = Field(
        default=60,
        description="Rate limit window in seconds",
    )
    rate_limit_media_requests: int = Field(
        default=60,
        ge=0,
        description=(
            "Max file/track download requests allowed in a rate limit window "
            "(per user or IP, per file); 0 disables download limiting. "
            "image/* file downloads are always exempt."
        ),
    )
    trusted_proxy_hops: int = Field(
        default=0,
        description=(
            "Number of trusted proxy hops for X-Forwarded-For and X-Forwarded-Proto " "parsing; 0 disables header trust"
        ),
    )
    cookie_secure: Optional[bool] = Field(
        default=None,
        description=(
            "Whether auth cookies get the Secure flag. When None, it is "
            "inferred from server.debug (Secure unless debug is enabled)."
        ),
    )
    cookie_samesite: Literal["lax", "strict", "none"] = Field(
        default="lax",
        description="SameSite attribute for auth cookies",
    )
    cookie_domain: Optional[str] = Field(
        default=None,
        description="Domain attribute for auth cookies (e.g. cross-subdomain deployments)",
    )
    secret_key: str = Field(
        description="Secret key for JWT signing",
        repr=False,
        exclude=True,
    )

    @field_validator("secret_key", mode="after")
    @classmethod
    def _validate_secret_key(cls, value: str) -> str:
        if value in {"change-me-in-production", "your-secret-key-here"}:
            raise ValueError(
                "JWT secret_key is set to a known placeholder. "
                "Generate a strong random key and set it explicitly, e.g.:\n"
                'python -c "import secrets; print(secrets.token_urlsafe(64))"'
            )
        if len(value.encode("utf-8")) < 32:
            raise ValueError("JWT secret_key must be at least 32 bytes long")
        return value


class EmailConfig(BaseSettings):
    """Email (SMTP) configuration."""

    smtp_host: Optional[str] = Field(default=None, description="SMTP server hostname")
    smtp_port: int = Field(default=587, description="SMTP server port")
    smtp_username: Optional[str] = Field(default=None, description="SMTP username")
    smtp_password: Optional[str] = Field(
        default=None,
        description="SMTP password",
        repr=False,
        exclude=True,
    )
    smtp_tls: bool = Field(default=True, description="Use TLS for the SMTP connection")
    from_address: Optional[str] = Field(
        default=None,
        description="From address for outgoing emails",
    )


class NotificationsConfig(BaseSettings):
    """User notification delivery and retention configuration."""

    retention_days: int = Field(
        default=90,
        ge=1,
        description="Days to keep seen notifications before purging",
    )
    purge_hour: int = Field(
        default=3,
        ge=0,
        le=23,
        description="Hour of day (UTC) when seen notifications are purged",
    )
    digest_hour: int = Field(
        default=8,
        ge=0,
        le=23,
        description="Hour of day (UTC) when notification digest emails are sent",
    )
    vapid_public_key: Optional[str] = Field(
        default=None,
        description="VAPID public key (base64url) for Web Push notifications",
    )
    vapid_private_key: Optional[str] = Field(
        default=None,
        description="VAPID private key (base64url) for Web Push notifications",
        repr=False,
        exclude=True,
    )
    vapid_subscriber: Optional[str] = Field(
        default=None,
        description="VAPID subscriber contact (mailto: or https: URL) for push claim",
    )
    push_enabled: bool = Field(
        default=True,
        description="Allow Web Push notifications when VAPID keys are configured",
    )
    push_timeout: float = Field(
        default=15.0,
        ge=1.0,
        description="Timeout in seconds for each outbound Web Push request",
    )


class ServerConfig(BaseSettings):
    """Server configuration."""

    host: str = Field(default="0.0.0.0", description="Bind address")
    port: int = Field(default=8000, description="Listen port")
    num_workers: int = Field(default=1, description="Number of worker processes")
    debug: bool = Field(default=False, description="Enable debug mode")
    cors_origins: list[str] = Field(
        default_factory=list,
        description=(
            'Allowed CORS origins. Use ["*"] to allow all origins. '
            "A comma-separated string or JSON list is also accepted from environment variables."
        ),
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value):
        if isinstance(value, str):
            return cls._split_cors_origins(value)
        if isinstance(value, list):
            return [
                item
                for sub in (cls._split_cors_origins(v) if isinstance(v, str) else [v] for v in value)
                for item in sub
            ]
        return value

    @classmethod
    def _split_cors_origins(cls, value: str) -> list[str]:
        value = value.strip()
        try:
            parsed = json.loads(value)
            return list(parsed) if isinstance(parsed, list) else [value]
        except json.JSONDecodeError:
            return [item.strip() for item in value.split(",") if item.strip()]


class MusicBrainzConfig(BaseSettings):
    """MusicBrainz enrichment configuration."""

    enabled: bool = Field(default=True, description="Enable MusicBrainz enrichment")
    user_agent: str = Field(
        default=get_default_user_agent(),
        description="User-Agent sent to MusicBrainz",
    )
    rate_limit_per_second: float = Field(
        default=1.0,
        description="Maximum MusicBrainz requests per second",
    )
    fetch_cover_art: bool = Field(
        default=True,
        description="Fetch cover art from the Cover Art Archive",
    )
    fetch_artist_images: bool = Field(
        default=True,
        description="Fetch artist images from MusicBrainz URL relations",
    )


class ImportConfig(BaseSettings):
    """Bulk/directory import configuration."""

    scan_roots: list[str] = Field(
        default_factory=list,
        description=(
            "Allowed filesystem roots for directory scans. "
            "A comma-separated string or JSON list is also accepted from environment variables."
        ),
    )
    bulk_import_sync_threshold: int = Field(
        default=20,
        description="Maximum number of files handled synchronously in a bulk upload",
    )

    @field_validator("scan_roots", mode="before")
    @classmethod
    def _parse_scan_roots(cls, value):
        if isinstance(value, str):
            return ServerConfig._split_cors_origins(value)
        if isinstance(value, list):
            return [
                item
                for sub in (ServerConfig._split_cors_origins(v) if isinstance(v, str) else [v] for v in value)
                for item in sub
            ]
        return value


def _require_auth_secret_key() -> AuthConfig:
    """Raise a clear error when no JWT secret key has been configured."""
    raise ValueError(
        "JWT auth.secret_key is not configured. "
        "Set SONGHIVE_AUTH__SECRET_KEY or add auth.secret_key to config.toml. "
        'Generate a key with: python -c "import secrets; print(secrets.token_urlsafe(64))"'
    )


class StreamingConfig(BaseSettings):
    """Streaming and transcoding configuration."""

    ffmpeg_path: Optional[str] = Field(
        default=None,
        description="Path to the ffmpeg binary; auto-detected from PATH when None",
    )
    max_bitrate: str = Field(
        default="320k",
        description="Instance-wide maximum bitrate ceiling",
    )
    max_bitrate_by_role: dict[str, str] = Field(
        default_factory=lambda: {"user": "192k", "moderator": "256k", "admin": "320k"},
        description="Per-role bitrate ceiling keyed by User.role",
    )
    default_bitrate: str = Field(
        default="192k",
        description="Default bitrate for transcoded streams",
    )
    chunk_size: int = Field(
        default=64 * 1024,
        description="Chunk size in bytes for stream responses",
    )
    transcode_cache_enabled: bool = Field(
        default=True,
        description="Whether to cache transcoded output files",
    )


class TidalConfig(BaseSettings):
    """Instance-level defaults for the TIDAL external-library provider."""

    client_id: str = Field(
        default="",
        description="Override TIDAL client id for device auth; empty uses tidalapi's built-in.",
    )
    client_secret: str = Field(
        default="",
        description="Override TIDAL client secret for device auth; empty uses tidalapi's built-in.",
    )
    client_id_pkce: str = Field(
        default="",
        description="Override TIDAL PKCE client id; empty uses tidalapi's built-in.",
    )
    client_secret_pkce: str = Field(
        default="",
        description="Override TIDAL PKCE client secret; empty uses tidalapi's built-in.",
    )
    stream_policy: str = Field(
        default="owner",
        description=(
            "Who may stream TIDAL tracks: 'owner' (library owner only), "
            "'listener_account' (each listener streams through their own TIDAL library), "
            "or 'anyone' (any ACL-authorized local user streams through the owner's session)."
        ),
    )
    playlist_ttl_seconds: int = Field(
        default=21600,
        ge=0,
        description="Default TTL for cached playlist contents (lazy fetch refresh interval).",
    )
    minimum_playlist_ttl_seconds: int = Field(
        default=300,
        ge=0,
        description="Lower bound a library-level playlist_ttl_seconds override may set.",
    )
    album_contents_ttl_seconds: int = Field(
        default=2592000,
        ge=0,
        description="TTL for cached album contents (album track listings rarely change).",
    )
    catalog_ttl_seconds: int = Field(
        default=2592000,
        ge=0,
        description="TTL for catalog artist/album/playlist payloads; track payloads are immutable.",
    )
    lazy_contents_wait_seconds: int = Field(
        default=10,
        ge=0,
        description="How long first-load handlers may wait for an in-flight contents refresh.",
    )
    max_requests_per_second: float = Field(
        default=5.0,
        gt=0,
        description="Per-account client-side throttle for outbound TIDAL API calls.",
    )
    request_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        description="HTTP timeout applied to every outbound TIDAL request, including token refresh.",
    )
    allow_downloads: bool = Field(
        default=False,
        description="Whether TIDAL tracks may be downloaded (FLAC/AAC). Off by default for ToS safety.",
    )
    download_format: str = Field(
        default="flac",
        description="Container for TIDAL downloads: 'flac' or 'aac'.",
    )
    remote_cache_dir: Optional[Path] = Field(
        default=None,
        description="Directory for the remote-audio remux cache; stream_temp_dir/remote-audio when None.",
    )
    remote_cache_max_bytes: int = Field(
        default=500 * 1024 * 1024,
        ge=0,
        description="Maximum total bytes held by the remote-audio remux cache.",
    )
    remote_cache_retention_seconds: int = Field(
        default=7 * 24 * 3600,
        ge=0,
        description="How long unused remote-audio remux artifacts are retained.",
    )


class ExternalLibrariesConfig(BaseSettings):
    """External library configuration."""

    allow_user_created_libraries: bool = Field(
        default=False,
        description="Whether non-admin users may create external libraries.",
    )
    allowed_user_providers: list[str] = Field(
        default_factory=list,
        description=(
            "Provider types non-admin users may configure when "
            "allow_user_created_libraries=true; empty means all user_configurable providers."
        ),
    )
    denied_user_providers: list[str] = Field(
        default_factory=list,
        description="Provider types non-admin users may never configure, regardless of allowed_user_providers.",
    )
    allow_admin_library_index_inclusion: bool = Field(
        default=True,
        description="Whether admins may opt admin-managed external libraries into the default /libraries view.",
    )
    allow_destructive_delete: bool = Field(
        default=False,
        description="Global kill-switch for destructive provider-side deletion.",
    )
    minimum_sync_interval_seconds: int = Field(
        default=300,
        ge=0,
        description="Minimum number of seconds between consecutive sync runs for a library.",
    )
    max_concurrent_syncs: int = Field(
        default=4,
        ge=1,
        description="Maximum number of external library syncs that may run concurrently.",
    )
    stream_temp_dir: Optional[Path] = Field(
        default=None,
        description="Directory for external stream temp/cache files; system temp when None.",
    )
    stream_max_proxy_bytes: Optional[int] = Field(
        default=256 * 1024 * 1024,
        description="Max bytes Songhive will proxy through memory before spilling to disk.",
    )
    stream_proxy_timeout_seconds: int = Field(
        default=60,
        ge=1,
        description="Timeout in seconds for proxied external stream responses.",
    )
    local_roots: list[str] = Field(
        default_factory=list,
        description=(
            "Filesystem roots a 'local' external library may point at; "
            "empty denies all. A comma-separated string or JSON list is also "
            "accepted from environment variables."
        ),
    )
    tidal: TidalConfig = Field(
        default_factory=TidalConfig,
        description="Instance-level defaults for the TIDAL provider.",
    )

    @field_validator("allowed_user_providers", "denied_user_providers", "local_roots", mode="before")
    @classmethod
    def _parse_providers(cls, value):
        if isinstance(value, str):
            return ServerConfig._split_cors_origins(value)
        if isinstance(value, list):
            return [
                item
                for sub in (ServerConfig._split_cors_origins(v) if isinstance(v, str) else [v] for v in value)
                for item in sub
            ]
        return value


class StreamsConfig(BaseSettings):
    """Server-side audio output stream configuration."""

    enabled: bool = Field(
        default=True,
        description="Enable server-side audio output streaming.",
    )
    allow_user_created_outputs: bool = Field(
        default=False,
        description="Whether non-admin users may create audio output streams.",
    )
    allowed_user_providers: list[str] = Field(
        default_factory=list,
        description=(
            "Provider types non-admin users may configure when "
            "allow_user_created_outputs=true; empty means all user_configurable providers."
        ),
    )
    denied_user_providers: list[str] = Field(
        default_factory=list,
        description="Provider types non-admin users may never configure, regardless of allowed_user_providers.",
    )
    allowed_output_hosts: list[str] = Field(
        default_factory=list,
        description=(
            "Hosts that user-created remote outputs may target (Icecast mounts, "
            "Snapcast TCP sources); empty means no extra restriction beyond "
            "allow_user_created_outputs. Admins are exempt."
        ),
    )
    icecast_ffmpeg_path: Optional[str] = Field(
        default=None,
        description=(
            "Path to the ffmpeg binary for ffmpeg-backed stream outputs "
            "(Icecast, native HTTP, Snapcast); falls back to streaming.ffmpeg_path."
        ),
    )
    background_idle_timeout_seconds: int = Field(
        default=900,
        ge=0,
        description="Seconds a background stream may stay idle before stopping.",
    )
    default_sample_rate: int = Field(
        default=44100,
        gt=0,
        description="Default output sample rate in Hz.",
    )
    default_bitrate: int = Field(
        default=192,
        gt=0,
        description="Default output bitrate in kbps.",
    )
    worker_poll_interval_seconds: float = Field(
        default=1.0,
        gt=0,
        description="Seconds between stream worker session discovery scans.",
    )
    worker_lock_ttl_seconds: int = Field(
        default=60,
        ge=10,
        description="Redis TTL for a stream worker output lock.",
    )
    http_stream_max_entries: int = Field(
        default=512,
        ge=16,
        description=(
            "Approximate MAXLEN of the Redis stream backing each native HTTP "
            "mount (each entry is one ~16 KiB encoded chunk)."
        ),
    )
    http_stream_burst_entries: int = Field(
        default=8,
        ge=0,
        description=(
            "How many recent stream entries a new HTTP listener is bursted "
            "with on connect (8 entries ≈ 128 KiB at 16 KiB per chunk)."
        ),
    )
    http_stream_max_lag_seconds: float = Field(
        default=5.0,
        ge=0,
        description=(
            "Maximum age of buffered audio delivered to an HTTP listener. "
            "Older chunks are skipped so a slow client jumps forward instead "
            "of accumulating latency; 0 disables lag dropping."
        ),
    )
    http_stream_max_listeners: int = Field(
        default=64,
        ge=0,
        description="Maximum concurrent listeners per native HTTP mount; 0 disables the cap.",
    )
    http_stream_metaint_bytes: int = Field(
        default=16384,
        ge=1024,
        description=(
            "ICY metadata interval in bytes for native HTTP mounts. Listeners "
            "that send Icy-MetaData: 1 receive a StreamTitle block every "
            "metaint bytes of audio."
        ),
    )

    @field_validator(
        "allowed_user_providers",
        "denied_user_providers",
        "allowed_output_hosts",
        mode="before",
    )
    @classmethod
    def _parse_providers(cls, value):
        if isinstance(value, str):
            return ServerConfig._split_cors_origins(value)
        if isinstance(value, list):
            return [
                item
                for sub in (ServerConfig._split_cors_origins(v) if isinstance(v, str) else [v] for v in value)
                for item in sub
            ]
        return value


class PaymentsConfig(BaseSettings):
    """Payments configuration (Stripe purchases and paid memberships)."""

    enabled: bool = Field(
        default=False,
        description="Enable payments: artist sales and paid registrations",
    )
    stripe_secret_key: Optional[str] = Field(
        default=None,
        description="Stripe secret API key (sk_...); required when payments are enabled",
        repr=False,
        exclude=True,
    )
    stripe_platform_webhook_secret: Optional[str] = Field(
        default=None,
        description="Signing secret (whsec_...) for the platform-account webhook endpoint",
        repr=False,
        exclude=True,
    )
    stripe_connect_webhook_secret: Optional[str] = Field(
        default=None,
        description="Signing secret (whsec_...) for the Connect (seller account) webhook endpoint",
        repr=False,
        exclude=True,
    )
    public_base_url: Optional[str] = Field(
        default=None,
        description=(
            "Canonical https:// public base URL used to build checkout, redeem, and email "
            "links. Falls back to federation.instance_domain when unset; payments requiring "
            "absolute URLs fail closed when neither resolves to a canonical https URL."
        ),
    )
    stripe_losses_collector: Literal["stripe", "application"] = Field(
        default="stripe",
        description=(
            "Connect defaults.responsibilities.losses_collector for new seller accounts. "
            "'stripe' is the only value platforms may use by default; 'application' requires "
            "Stripe approval for platform-managed risk"
        ),
    )
    supported_currencies: list[str] = Field(
        default_factory=lambda: ["usd"],
        description="ISO-4217 currencies accepted for sales (lowercase)",
    )
    min_price_minor: int = Field(
        default=50,
        ge=0,
        description="Minimum sale price in minor units of the sale currency",
    )
    max_price_minor: int = Field(
        default=1_000_000,
        ge=0,
        description="Maximum sale price in minor units of the sale currency",
    )
    default_membership_amount_minor: int = Field(
        default=500,
        ge=0,
        description="Default membership price in minor units (500 = $5.00)",
    )
    membership_currency: str = Field(
        default="USD",
        description="ISO-4217 currency for instance memberships",
    )
    membership_interval: Literal["week", "month", "year"] = Field(
        default="month",
        description="Membership billing interval",
    )
    membership_product_name: str = Field(
        default="Songhive membership",
        description="Stripe product name provisioned for membership billing",
    )
    membership_price_lookup_key: str = Field(
        default="songhive-membership-v1",
        description="Stable lookup key used to find or provision the membership Stripe Price",
    )
    sample_max_seconds: int = Field(
        default=120,
        ge=1,
        description="Maximum length of a sale sample in seconds",
    )
    default_sample_seconds: int = Field(
        default=30,
        ge=1,
        description="Sample length used when a sale does not configure one",
    )
    redeem_capability_ttl_days: int = Field(
        default=30,
        ge=1,
        description="Days a guest purchase redemption link stays valid",
    )
    billing_capability_ttl_seconds: int = Field(
        default=1800,
        ge=60,
        description="Lifetime of the billing capability issued to unpaid users at login",
    )
    guest_redemption_limit: int = Field(
        default=10,
        ge=1,
        description="Maximum number of times a guest capability may be redeemed",
    )
    membership_grace_hours: int = Field(
        default=72,
        ge=0,
        description="Hours of access preserved after paid_through when renewal payment fails",
    )
    artifact_ttl_hours: int = Field(
        default=24,
        ge=1,
        description="Hours a cached paid-download artifact (e.g. album ZIP) is retained",
    )
    fulfillment_lock_ttl_seconds: int = Field(
        default=300,
        ge=30,
        description="Redis TTL for the fulfillment/sample-generation locks",
    )
    checkout_session_ttl_hours: int = Field(
        default=23,
        ge=1,
        description="Hours a pending checkout order stays open before expiring",
    )

    @field_validator("supported_currencies", mode="before")
    @classmethod
    def _parse_currencies(cls, value):
        if isinstance(value, str):
            return ServerConfig._split_cors_origins(value)
        return value

    @field_validator("supported_currencies", mode="after")
    @classmethod
    def _normalize_currencies(cls, value: list[str]) -> list[str]:
        return [currency.strip().lower() for currency in value if currency and currency.strip()]

    @field_validator("public_base_url", mode="after")
    @classmethod
    def _normalize_public_base_url(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip().rstrip("/")
        return value or None


class SubsonicConfig(BaseSettings):
    """Subsonic API adapter configuration."""

    enabled: bool = Field(
        default=True,
        description=(
            "Expose a Subsonic-compatible API under /rest so Subsonic/"
            "OpenSubsonic clients can browse and stream the instance's library"
        ),
    )


class ScrobblingConfig(BaseSettings):
    """Scrobbling (Audioscrobbler-compatible services) configuration."""

    enabled: bool = Field(
        default=True,
        description=(
            "Allow users to scrobble plays to an Audioscrobbler-compatible "
            "service (Last.fm, Libre.fm) from /settings?tab=scrobbling"
        ),
    )
    request_timeout_seconds: float = Field(
        default=15.0,
        ge=1.0,
        description="Timeout in seconds for scrobble service HTTP requests",
    )
    # The instance acts as the API application: users authorize their account
    # through auth.getMobileSession and Songhive stores the returned session
    # key. A service is selectable only when its key pair is configured here.
    lastfm_api_key: str = Field(default="", description="Last.fm API key")
    lastfm_api_secret: str = Field(default="", description="Last.fm API shared secret")
    librefm_api_key: str = Field(default="", description="Libre.fm API key")
    librefm_api_secret: str = Field(default="", description="Libre.fm API shared secret")


def _bitrate_to_bits(value: str) -> int:
    """Parse a bitrate string such as '192k' or '1.5M' into bits per second.

    Returns 0 for empty or unparseable values so callers can fall back safely.
    """
    value = (value or "").strip().lower()
    if not value:
        return 0
    match = re.match(r"^(\d+(?:\.\d+)?)\s*([kmg])?$", value)
    if not match:
        return 0
    number = float(match.group(1))
    multiplier = {"k": 1_000, "m": 1_000_000, "g": 1_000_000_000}.get(match.group(2), 1)
    return int(number * multiplier)


def effective_bitrate(cfg: StreamingConfig, role: str, requested: Optional[str]) -> str:
    """Return the lowest of the requested, default, role, and instance bitrates."""
    role_max = cfg.max_bitrate_by_role.get(role, cfg.max_bitrate)
    candidates = [
        requested if requested is not None else cfg.default_bitrate,
        cfg.max_bitrate,
        role_max,
    ]
    valid = [(c, _bitrate_to_bits(c)) for c in candidates if _bitrate_to_bits(c) > 0]
    if not valid:
        return cfg.default_bitrate
    return min(valid, key=lambda pair: pair[1])[0]


class SonghiveConfig(BaseSettings):
    """
    Root configuration for Songhive.

    When built through :func:`songhive.config.loader.load_config`, sources are
    applied in the following priority order (highest first):

    1. Environment variables (SONGHIVE_ prefix)
    2. CLI arguments
    3. config.toml
    4. Field defaults
    """

    model_config = {
        "env_prefix": "SONGHIVE_",
        "env_nested_delimiter": "__",
        "enable_decoding": False,
    }

    server: ServerConfig = Field(default_factory=ServerConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    celery: CeleryConfig = Field(default_factory=CeleryConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    federation: FederationConfig = Field(default_factory=FederationConfig)
    webmentions: WebmentionsConfig = Field(default_factory=WebmentionsConfig)
    feeds: FeedsConfig = Field(default_factory=FeedsConfig)
    podcasts: PodcastsConfig = Field(default_factory=PodcastsConfig)
    auth: AuthConfig = Field(default_factory=_require_auth_secret_key)
    email: EmailConfig = Field(default_factory=EmailConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    musicbrainz: MusicBrainzConfig = Field(default_factory=MusicBrainzConfig)
    imports: ImportConfig = Field(default_factory=ImportConfig)
    downloads: DownloadsConfig = Field(default_factory=DownloadsConfig)
    payments: PaymentsConfig = Field(default_factory=PaymentsConfig)
    streaming: StreamingConfig = Field(default_factory=StreamingConfig)
    external_libraries: ExternalLibrariesConfig = Field(default_factory=ExternalLibrariesConfig)
    streams: StreamsConfig = Field(default_factory=StreamsConfig)
    subsonic: SubsonicConfig = Field(default_factory=SubsonicConfig)
    scrobbling: ScrobblingConfig = Field(default_factory=ScrobblingConfig)
