"""
Public instance metadata routes.

These endpoints provide a Mastodon-compatible instance API that is available
regardless of whether ActivityPub federation is enabled. When federation is
enabled, the same paths are registered earlier than Pubby's Mastodon binding,
so these routes take precedence.
"""

from typing import Any, List, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.schema import RegistrationMode, SonghiveConfig
from ...models.user import User
from ...version import __version__
from ..deps import get_config, get_db

v1_router = APIRouter(prefix="/instance", tags=["instance"])
v2_router = APIRouter(prefix="/instance", tags=["instance"])

# NOTE: use "" rather than "/" as the route path so the Mount created by
# ``app.include_router(..., prefix="/api/v1")`` matches ``/api/v1/instance``
# without a trailing slash. This lets Songhive's own instance metadata take
# precedence over the Mastodon-compatible routes registered by pubby.


class _Stats(BaseModel):
    user_count: int = 0
    status_count: int = 0
    domain_count: int = 0


class _Urls(BaseModel):
    streaming_api: str = ""


class _V1StatusConfig(BaseModel):
    max_characters: int = 500
    max_media_attachments: int = 4


class _V1MediaConfig(BaseModel):
    supported_mime_types: List[str] = Field(
        default_factory=lambda: [
            "image/jpeg",
            "image/png",
            "image/gif",
            "image/webp",
            "video/mp4",
            "audio/mpeg",
        ]
    )
    image_size_limit: int = 10485760
    video_size_limit: int = 41943040


class _V1PollConfig(BaseModel):
    max_options: int = 4
    max_characters_per_option: int = 50
    min_expiration: int = 300
    max_expiration: int = 2629746


class _V1Configuration(BaseModel):
    statuses: _V1StatusConfig = Field(default_factory=_V1StatusConfig)
    media_attachments: _V1MediaConfig = Field(default_factory=_V1MediaConfig)
    polls: _V1PollConfig = Field(default_factory=_V1PollConfig)


class InstanceV1(BaseModel):
    """Mastodon-compatible ``/api/v1/instance`` response."""

    uri: str
    title: str
    description: str
    short_description: str
    email: str = ""
    version: str
    songhive_version: str
    urls: _Urls = Field(default_factory=_Urls)
    stats: _Stats
    thumbnail: Optional[str] = None
    languages: List[str] = Field(default_factory=lambda: ["en"])
    registrations: bool
    approval_required: bool
    invites_enabled: bool
    configuration: _V1Configuration = Field(default_factory=_V1Configuration)
    contact_account: Any = None
    rules: List[Any] = Field(default_factory=list)


class _V2Thumbnail(BaseModel):
    url: str = ""


class _V2UsageUsers(BaseModel):
    active_month: int = 0


class _V2Usage(BaseModel):
    users: _V2UsageUsers = Field(default_factory=_V2UsageUsers)


class _V2Urls(BaseModel):
    streaming: str = ""


class _V2AccountsConfig(BaseModel):
    max_featured_tags: int = 0


class _V2StatusConfig(BaseModel):
    max_characters: int = 500
    max_media_attachments: int = 4
    characters_reserved_per_url: int = 23


class _V2MediaConfig(BaseModel):
    supported_mime_types: List[str] = Field(
        default_factory=lambda: [
            "image/jpeg",
            "image/png",
            "image/gif",
            "image/webp",
            "video/mp4",
            "audio/mpeg",
        ]
    )
    image_size_limit: int = 10485760
    video_size_limit: int = 41943040


class _V2PollConfig(BaseModel):
    max_options: int = 4
    max_characters_per_option: int = 50
    min_expiration: int = 300
    max_expiration: int = 2629746


class _V2TranslationConfig(BaseModel):
    enabled: bool = False


class _V2Configuration(BaseModel):
    urls: _V2Urls = Field(default_factory=_V2Urls)
    accounts: _V2AccountsConfig = Field(default_factory=_V2AccountsConfig)
    statuses: _V2StatusConfig = Field(default_factory=_V2StatusConfig)
    media_attachments: _V2MediaConfig = Field(default_factory=_V2MediaConfig)
    polls: _V2PollConfig = Field(default_factory=_V2PollConfig)
    translation: _V2TranslationConfig = Field(default_factory=_V2TranslationConfig)


class _V2Registrations(BaseModel):
    enabled: bool
    approval_required: bool
    message: Optional[str] = None


class _V2Contact(BaseModel):
    email: str = ""
    account: Any = None


class InstanceV2(BaseModel):
    """Mastodon-compatible ``/api/v2/instance`` response."""

    domain: str
    title: str
    version: str
    songhive_version: str
    source_url: str = ""
    description: str
    usage: _V2Usage = Field(default_factory=_V2Usage)
    thumbnail: _V2Thumbnail = Field(default_factory=_V2Thumbnail)
    languages: List[str] = Field(default_factory=lambda: ["en"])
    configuration: _V2Configuration = Field(default_factory=_V2Configuration)
    registrations: _V2Registrations
    contact: _V2Contact = Field(default_factory=_V2Contact)
    rules: List[Any] = Field(default_factory=list)


def _instance_domain(request: Request, config: SonghiveConfig) -> str:
    """Return the public instance domain, falling back to the request host."""
    if config.federation.instance_domain:
        return config.federation.instance_domain
    return request.url.hostname or ""


def _registration_flags(mode: RegistrationMode) -> tuple[bool, bool, bool]:
    """Return ``(registrations, approval_required, invites_enabled)`` for a mode."""
    if mode == RegistrationMode.OPEN:
        return True, False, False
    if mode == RegistrationMode.INVITE_ONLY:
        return True, False, True
    if mode == RegistrationMode.APPROVAL_REQUIRED:
        return True, True, False
    return False, False, False


async def _user_count(db: AsyncSession) -> int:
    """Return the current number of active users."""
    result = await db.execute(select(func.count()).select_from(User))
    return result.scalar() or 0


@v1_router.get("", response_model=InstanceV1)
async def get_instance_v1(
    request: Request,
    config: SonghiveConfig = Depends(get_config),
    db: AsyncSession = Depends(get_db),
):
    """Return Mastodon-compatible instance metadata (v1)."""
    domain = _instance_domain(request, config)
    registrations, approval_required, invites_enabled = _registration_flags(config.auth.registration_mode)
    user_count = await _user_count(db)
    version = f"Songhive {__version__} (Mastodon-compatible)"

    return InstanceV1(
        uri=domain,
        title=config.federation.instance_name,
        description=config.federation.instance_description,
        short_description=config.federation.instance_description,
        version=version,
        songhive_version=__version__,
        stats=_Stats(
            user_count=user_count,
            status_count=0,
            domain_count=0,
        ),
        registrations=registrations,
        approval_required=approval_required,
        invites_enabled=invites_enabled,
    )


@v2_router.get("", response_model=InstanceV2)
async def get_instance_v2(
    request: Request,
    config: SonghiveConfig = Depends(get_config),
    db: AsyncSession = Depends(get_db),
):
    """Return Mastodon-compatible instance metadata (v2)."""
    domain = _instance_domain(request, config)
    registrations, approval_required, _ = _registration_flags(config.auth.registration_mode)
    user_count = await _user_count(db)
    version = f"Songhive {__version__} (Mastodon-compatible)"

    return InstanceV2(
        domain=domain,
        title=config.federation.instance_name,
        version=version,
        songhive_version=__version__,
        description=config.federation.instance_description,
        usage=_V2Usage(users=_V2UsageUsers(active_month=user_count)),
        registrations=_V2Registrations(
            enabled=registrations,
            approval_required=approval_required,
        ),
    )


@v1_router.get("/peers", response_model=List[str])
async def get_instance_peers(config: SonghiveConfig = Depends(get_config)):
    """Return a list of known peer instance domains."""
    if not config.federation.enabled:
        return []
    return list(config.federation.allowed_instances)
