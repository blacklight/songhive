"""
User profile routes.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.schema import SonghiveConfig
from ...federation.actors import sync_user_actor
from ...models.user import User, UserRole
from ...models.user_link import UserLink
from ...services import audit
from ...services.auth import get_user_by_username
from ...users.manager import PasswordChangeError, change_user_password, update_profile
from ...users.tokens import revoke_all_user_refresh_tokens
from .._common import client_ip
from ..deps import get_config, get_current_user, get_db, get_redis
from ..middleware.rate_limit import rate_limit_account

router = APIRouter(prefix="/users")


class UserLinkBase(BaseModel):
    """Shared link schema with validation."""

    name: str = Field(..., min_length=1, max_length=64)
    url: str = Field(..., min_length=1, max_length=512)

    @field_validator("name", "url", mode="before")
    @classmethod
    def _strip_whitespace(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("url")
    @classmethod
    def _validate_url_scheme(cls, value: str) -> str:
        if not value.startswith(("https://", "http://")):
            raise ValueError("Link URL must start with http:// or https://")
        return value


class UserLinkInput(UserLinkBase):
    """Link payload used in profile update requests."""


class UserLinkOutput(UserLinkBase):
    """Link item as it appears in profile responses."""

    model_config = ConfigDict(from_attributes=True)


class UserResponse(BaseModel):
    """Authenticated user profile response, including the internal user id."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    display_name: Optional[str] = None
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    role: Optional[UserRole] = None
    links: List[UserLinkOutput] = Field(default_factory=list)


class PublicUserResponse(BaseModel):
    """Public user profile response (internal id excluded)."""

    model_config = ConfigDict(from_attributes=True)

    username: str
    display_name: Optional[str] = None
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    links: List[UserLinkOutput] = Field(default_factory=list)


class UserProfileUpdate(BaseModel):
    display_name: Optional[str] = Field(None, max_length=128)
    bio: Optional[str] = None
    avatar_url: Optional[str] = Field(None, max_length=512)
    links: Optional[List[UserLinkInput]] = None

    @field_validator("avatar_url", mode="before")
    @classmethod
    def _strip_avatar_url(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("avatar_url")
    @classmethod
    def _validate_avatar_url_scheme(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if not value.startswith(("https://", "http://")):
            raise ValueError("Avatar URL must start with http:// or https://")
        return value


class ChangePasswordRequest(BaseModel):
    """Request body for changing the authenticated user's password."""

    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=1)


class ChangePasswordResponse(BaseModel):
    """Response returned after a successful password change."""

    success: bool = True


@router.get("/me", response_model=UserResponse)
async def get_current_user_profile(current_user: User = Depends(get_current_user)):
    """Get the current authenticated user's profile."""
    return UserResponse.model_validate(current_user)


@router.patch(
    "/me",
    response_model=UserResponse,
    dependencies=[Depends(rate_limit_account)],
)
async def update_current_user_profile(
    update: UserProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Update the current authenticated user's profile."""
    updates = update.model_dump(exclude_unset=True)
    if "links" in updates and updates["links"] is not None:
        updates["links"] = [UserLink(name=link["name"], url=link["url"]) for link in updates["links"]]

    await update_profile(db, current_user, updates)
    await db.commit()  # Persist profile changes before optional actor sync

    if config.federation.enabled:
        await sync_user_actor(current_user, config)

    return UserResponse.model_validate(current_user)


@router.post(
    "/me/password",
    response_model=ChangePasswordResponse,
    dependencies=[Depends(rate_limit_account)],
)
async def change_my_password(
    body: ChangePasswordRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    """Change the authenticated user's password."""
    try:
        await change_user_password(
            db,
            current_user,
            body.current_password,
            body.new_password,
        )
    except PasswordChangeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    await revoke_all_user_refresh_tokens(redis, current_user.id)
    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="user.change_password",
        target_type="user",
        target_id=current_user.id,
        details={},
        ip_address=client_ip(request),
    )
    await db.commit()

    return ChangePasswordResponse()


@router.get("/{username}", response_model=PublicUserResponse)
async def get_user(username: str, db: AsyncSession = Depends(get_db)):
    """Get a user profile by username."""
    user = await get_user_by_username(db, username)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return PublicUserResponse.model_validate(user)
