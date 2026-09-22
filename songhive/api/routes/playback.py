"""Playback session routes."""

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.user import User
from ...services.playback import (
    get_or_create_session,
    handle_command,
    select_outputs,
    session_state_dict,
)
from ..deps import get_current_user, get_db

router = APIRouter(prefix="/playback")


class OutputSelection(BaseModel):
    """Request body for selecting session outputs."""

    output_ids: list[str]
    connection_id: Optional[str] = None


class CommandRequest(BaseModel):
    """Request body for a playback command."""

    command: str
    args: dict = {}
    connection_id: Optional[str] = None


@router.get("/session")
async def get_session(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the current playback session for the user."""
    session = await get_or_create_session(db, current_user)
    return await session_state_dict(db, session)


@router.post("/session/outputs")
async def set_session_outputs(
    body: OutputSelection,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Attach one or more outputs to the user's session."""
    session = await get_or_create_session(db, current_user)
    session = await select_outputs(
        db,
        session,
        body.output_ids,
        current_user,
        connection_id=body.connection_id,
    )
    await db.commit()
    return await session_state_dict(db, session)


@router.post("/session/command")
async def post_command(
    body: CommandRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Dispatch a playback command to the user's session."""
    session = await get_or_create_session(db, current_user)
    await handle_command(db, session, body.command, body.args or {}, body.connection_id)
    await db.commit()
    return await session_state_dict(db, session)
