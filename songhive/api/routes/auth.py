"""
Authentication routes: login, register, token management.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr

from ..deps import get_config

router = APIRouter(prefix="/auth")


class RegisterRequest(BaseModel):
    username: str
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(body: RegisterRequest, config=Depends(get_config)):
    """Register a new user account."""
    # TODO: implement registration logic


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest):
    """Authenticate and receive a JWT token."""
    # TODO: implement login logic


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token():
    """Refresh an existing JWT token."""
    # TODO: implement token refresh
