"""
User profile routes.
"""

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/users")


class UserResponse(BaseModel):
    id: str
    username: str
    display_name: Optional[str] = None
    bio: Optional[str] = None
    avatar_url: Optional[str] = None


@router.get("/{username}", response_model=UserResponse)
async def get_user(username: str):
    """Get a user profile by username."""
    # TODO: implement


@router.get("/me", response_model=UserResponse)
async def get_current_user_profile():
    """Get the current authenticated user's profile."""
    # TODO: implement
