"""
Library routes.
"""

from typing import List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter(prefix="/libraries")


class LibraryResponse(BaseModel):
    id: str
    name: str
    owner_id: str
    description: Optional[str] = None
    is_public: bool = False


@router.get("/", response_model=List[LibraryResponse])
async def list_libraries(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List libraries."""
    # TODO: implement
    return []


@router.post("/", response_model=LibraryResponse, status_code=201)
async def create_library():
    """Create a new library."""
    # TODO: implement


@router.get("/{library_id}", response_model=LibraryResponse)
async def get_library(library_id: str):
    """Get a library by ID."""
    # TODO: implement
