"""
User profile routes.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import get_current_user, get_db
from ...models.user import User
from ...services.auth import get_user_by_username

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


@router.get("/me", response_model=UserResponse)
async def get_current_user_profile(current_user: User = Depends(get_current_user)):
    """Get the current authenticated user's profile."""
    return UserResponse.model_validate(current_user)


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
