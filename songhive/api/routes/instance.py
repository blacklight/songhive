"""
Public instance metadata routes.

These endpoints provide a Mastodon-compatible instance API that is available
regardless of whether ActivityPub federation is enabled. When federation is
enabled, the same paths are registered earlier than Pubby's Mastodon binding,
so these routes take precedence.
"""

import json
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.schema import RegistrationMode, SonghiveConfig
from ...federation import get_actor_url
from ...models.user import FollowersApproval, User, UserRole
from ...services import auth as auth_service
from ...services import music
from ...services import settings as settings_service
from ...version import __version__
from ..deps import get_config, get_current_user_optional, get_db, get_redis

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


class StaffAccount(BaseModel):
    """A staff (admin) account advertised by the instance."""

    username: str
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    acct: str
    url: str
    actor_url: Optional[str] = None


class InstanceContact(BaseModel):
    """Configured contact person for the instance."""

    name: str = ""
    email: str = ""
    url: str = ""


class InstanceV1(BaseModel):
    """Mastodon-compatible ``/api/v1/instance`` response."""

    uri: str
    title: str
    description: str
    short_description: str
    email: str = ""
    version: str
    songhive_version: str
    federation_enabled: bool = False
    urls: _Urls = Field(default_factory=_Urls)
    stats: _Stats
    thumbnail: Optional[str] = None
    languages: List[str] = Field(default_factory=lambda: ["en"])
    registrations: bool
    approval_required: bool
    invites_enabled: bool
    configuration: _V1Configuration = Field(default_factory=_V1Configuration)
    contact_account: Any = None
    contact: Optional[InstanceContact] = None
    staff_accounts: List[StaffAccount] = Field(default_factory=list)
    rules: List[Any] = Field(default_factory=list)
    # Username configured for single-user mode; ``/`` redirects anonymous
    # visitors to ``/@{single_user}`` when set.
    single_user: Optional[str] = None


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
    staff_accounts: List[StaffAccount] = Field(default_factory=list)
    rules: List[Any] = Field(default_factory=list)
    single_user: Optional[str] = None


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


async def _admin_users(db: AsyncSession) -> List[User]:
    """Return the active admin users, oldest first."""
    result = await db.execute(
        select(User)
        .where(User.role == UserRole.ADMIN.value, User.is_active.is_(True))
        .order_by(User.created_at.asc(), User.username.asc())
    )
    return list(result.scalars().all())


def _base_url(request: Request, config: SonghiveConfig) -> str:
    """Return the public base URL (https) for links advertised by the instance."""
    if config.federation.instance_domain:
        return f"https://{config.federation.instance_domain}"
    return str(request.base_url).rstrip("/")


def _user_to_staff_account(user: User, request: Request, config: SonghiveConfig) -> StaffAccount:
    """
    Map a local admin user to its public staff representation.

    ``acct`` carries the fully-qualified ``user@domain`` handle when
    federation is available and falls back to the bare username otherwise.
    ``url`` points at the user's ``/@{username}`` profile page.
    """
    federated = config.federation.enabled and bool(config.federation.instance_domain)
    domain = config.federation.instance_domain
    actor_url = (user.actor_url or get_actor_url(domain, user.username)) if federated else None
    return StaffAccount(
        username=user.username,
        display_name=user.display_name or user.username,
        avatar_url=user.avatar_url,
        acct=f"{user.username}@{domain}" if federated else user.username,
        url=f"{_base_url(request, config)}/@{user.username}",
        actor_url=actor_url,
    )


def _user_to_mastodon_account(user: User, request: Request, config: SonghiveConfig) -> dict[str, Any]:
    """
    Map a local user to a minimal Mastodon Account entity.

    Used for the ``contact_account``/``contact.account`` fields of the
    instance responses, which Mastodon-compatible clients expect to hold a
    full Account shape.
    """
    federated = config.federation.enabled and bool(config.federation.instance_domain)
    domain = config.federation.instance_domain
    profile_url = f"{_base_url(request, config)}/@{user.username}"
    uri = (user.actor_url or get_actor_url(domain, user.username)) if federated else profile_url
    avatar = user.avatar_url or ""
    created = user.created_at.isoformat() if user.created_at else None
    return {
        "id": str(user.id),
        "username": user.username,
        "acct": f"{user.username}@{domain}" if federated else user.username,
        "display_name": user.display_name or user.username,
        "note": user.bio or "",
        "url": profile_url,
        "uri": uri,
        "avatar": avatar,
        "avatar_static": avatar,
        "header": "",
        "header_static": "",
        "locked": user.followers_approval == FollowersApproval.MANUAL.value,
        "created_at": created,
        "last_status_at": None,
        "followers_count": 0,
        "following_count": 0,
        "statuses_count": 0,
        "fields": [],
        "emojis": [],
        "bot": False,
        "group": False,
        "discoverable": True,
        "noindex": False,
    }


def _instance_contact(config: SonghiveConfig) -> Optional[InstanceContact]:
    """Return the configured contact person, or None when nothing is set."""
    contact = InstanceContact(
        name=config.federation.contact_name,
        email=config.federation.contact_email,
        url=config.federation.contact_url,
    )
    if not (contact.name or contact.email or contact.url):
        return None
    return contact


async def _single_user(db: AsyncSession, redis: Redis) -> Optional[str]:
    """Return the configured single-user username, or ``None`` when unset."""
    value = await settings_service.get_setting(db, redis, "single_user_username")
    return value if isinstance(value, str) and value else None


@v1_router.get("", response_model=InstanceV1)
async def get_instance_v1(
    request: Request,
    config: SonghiveConfig = Depends(get_config),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    """Return Mastodon-compatible instance metadata (v1)."""
    domain = _instance_domain(request, config)
    registrations, approval_required, invites_enabled = _registration_flags(config.auth.registration_mode)
    user_count = await _user_count(db)
    admins = await _admin_users(db)
    single_user = await _single_user(db, redis)
    version = f"Songhive {__version__} (Mastodon-compatible)"

    return InstanceV1(
        uri=domain,
        title=config.federation.instance_name,
        description=config.federation.instance_description,
        short_description=config.federation.instance_description,
        email=config.federation.contact_email,
        version=version,
        songhive_version=__version__,
        federation_enabled=config.federation.enabled and bool(config.federation.instance_domain),
        stats=_Stats(
            user_count=user_count,
            status_count=0,
            domain_count=0,
        ),
        registrations=registrations,
        approval_required=approval_required,
        invites_enabled=invites_enabled,
        contact_account=_user_to_mastodon_account(admins[0], request, config) if admins else None,
        contact=_instance_contact(config),
        staff_accounts=[_user_to_staff_account(admin, request, config) for admin in admins],
        single_user=single_user,
    )


@v2_router.get("", response_model=InstanceV2)
async def get_instance_v2(
    request: Request,
    config: SonghiveConfig = Depends(get_config),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    """Return Mastodon-compatible instance metadata (v2)."""
    domain = _instance_domain(request, config)
    registrations, approval_required, _ = _registration_flags(config.auth.registration_mode)
    user_count = await _user_count(db)
    admins = await _admin_users(db)
    single_user = await _single_user(db, redis)
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
        contact=_V2Contact(
            email=config.federation.contact_email,
            account=_user_to_mastodon_account(admins[0], request, config) if admins else None,
        ),
        staff_accounts=[_user_to_staff_account(admin, request, config) for admin in admins],
        single_user=single_user,
    )


class InstanceStats(BaseModel):
    """Visibility-filtered content counts for the instance."""

    tracks: int = 0
    albums: int = 0
    artists: int = 0
    libraries: int = 0
    users: int = 0


@v1_router.get("/stats", response_model=InstanceStats)
async def get_instance_stats(
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Return the number of items visible to the requester.

    Only available when the ``public_stats_enabled`` setting is on. Counts
    are filtered by the same ACL rules as the list endpoints — anonymous
    callers see the public subset — and cached briefly per caller.
    """
    if not await settings_service.get_setting(db, redis, "public_stats_enabled"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    cache_key = f"instance_stats:{'anon' if user is None else user.id}"
    cached = await redis.get(cache_key)
    if cached is not None:
        try:
            return InstanceStats(**json.loads(cached))
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    _, user_count = await auth_service.list_public_users(db, user=user, limit=1)
    stats = InstanceStats(
        tracks=await music.count_tracks(db, user=user),
        albums=await music.count_albums(db, user=user),
        artists=await music.count_artists(db, user=user),
        libraries=await music.count_libraries(db, user=user),
        users=user_count,
    )
    await redis.set(cache_key, stats.model_dump_json(), ex=settings_service.SETTINGS_CACHE_TTL)
    return stats


@v1_router.get("/peers", response_model=List[str])
async def get_instance_peers(config: SonghiveConfig = Depends(get_config)):
    """Return a list of known peer instance domains."""
    if not config.federation.enabled:
        return []
    return list(config.federation.allowed_instances)
