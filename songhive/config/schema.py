"""
Configuration schema for Songhive, defined as a Pydantic settings model.
"""

import json
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


class DatabaseConfig(BaseSettings):
    """Database configuration."""

    url: str = Field(
        default="postgresql+asyncpg://songhive:songhive@localhost:5432/songhive",
        description="SQLAlchemy database URL",
    )
    pool_size: int = Field(default=5, description="Connection pool size")
    max_overflow: int = Field(default=10, description="Max overflow connections")

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
    private_key_path: Optional[Path] = Field(
        default=None,
        description="Path to the ActivityPub actor private key PEM file; a key is generated here if missing",
    )


class RegistrationMode(str, Enum):
    """Allowed user registration modes."""

    OPEN = "open"
    INVITE_ONLY = "invite-only"
    APPROVAL_REQUIRED = "approval-required"
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
    trusted_proxy_hops: int = Field(
        default=0,
        description="Number of trusted proxy hops for X-Forwarded-For parsing; 0 disables header trust",
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

    enabled: bool = Field(default=False, description="Enable MusicBrainz enrichment")
    user_agent: str = Field(
        default=f"Songhive/{_app_short_version()} ( https://git.fabiomanganiello.com/songhive )",
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
    auth: AuthConfig = Field(default_factory=_require_auth_secret_key)
    email: EmailConfig = Field(default_factory=EmailConfig)
    musicbrainz: MusicBrainzConfig = Field(default_factory=MusicBrainzConfig)
    imports: ImportConfig = Field(default_factory=ImportConfig)
