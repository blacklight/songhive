"""
Radio routes - dynamic radio generation.
"""

from typing import List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter(prefix="/radios")


class RadioResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None


@router.get("/", response_model=List[RadioResponse])
async def list_radios(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List configured radios."""
    # TODO: implement
    return []


@router.post("/", response_model=RadioResponse, status_code=201)
async def create_radio():
    """Create a new radio configuration."""
    # TODO: implement


@router.get("/{radio_id}/tracks")
async def get_radio_tracks(radio_id: str, count: int = Query(10, ge=1, le=50)):
    """Get the next batch of tracks for a radio."""
    # TODO: implement
    return []
