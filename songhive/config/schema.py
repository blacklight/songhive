"""
Configuration schema for Songhive, defined as a Pydantic settings model.
"""

import json
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class DatabaseConfig(BaseSettings):
    """Database configuration."""

    url: str = Field(
        default="postgresql+asyncpg://songhive:songhive@localhost:5432/songhive",
        description="SQLAlchemy database URL",
    )
    pool_size: int = Field(default=5, description="Connection pool size")
    max_overflow: int = Field(default=10, description="Max overflow connections")


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
    s3_secret_key: Optional[str] = Field(default=None, description="S3 secret key")
    s3_region: Optional[str] = Field(default=None, description="S3 region")


class FederationConfig(BaseSettings):
    """ActivityPub federation configuration."""

    enabled: bool = Field(default=True, description="Enable federation")
    instance_domain: str = Field(
        default="localhost",
        description="Public domain of this instance",
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


class AuthConfig(BaseSettings):
    """Authentication configuration."""

    registration_enabled: bool = Field(
        default=True,
        description="Whether new user registration is open",
    )
    secret_key: str = Field(
        default="change-me-in-production",
        description="Secret key for JWT signing",
    )
    token_expiry_hours: int = Field(
        default=24,
        description="JWT token expiry in hours",
    )


class ServerConfig(BaseSettings):
    """Server configuration."""

    host: str = Field(default="0.0.0.0", description="Bind address")
    port: int = Field(default=8000, description="Listen port")
    num_workers: int = Field(default=1, description="Number of worker processes")
    debug: bool = Field(default=False, description="Enable debug mode")
    cors_origins: list[str] = Field(
        default_factory=lambda: ["*"],
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
        if value.startswith("[") and value.endswith("]"):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                pass
        return [item.strip() for item in value.split(",") if item.strip()]


class SonghiveConfig(BaseSettings):
    """
    Root configuration for Songhive.

    Loaded from (in priority order):
    1. Environment variables (SONGHIVE_ prefix)
    2. CLI arguments
    3. config.toml
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
    auth: AuthConfig = Field(default_factory=AuthConfig)
