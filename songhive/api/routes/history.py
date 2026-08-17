"""
Listening history routes.
"""

from typing import List

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter(prefix="/history")


class HistoryEntry(BaseModel):
    id: str
    track_id: str
    created_at: str


@router.get("/", response_model=List[HistoryEntry])
async def list_history(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List listening history for the current user."""
    # TODO: implement
    return []


@router.post("/{track_id}", status_code=201)
async def record_listen(track_id: str):
    """Record a track listen event."""
    # TODO: implement
