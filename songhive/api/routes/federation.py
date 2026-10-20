"""
Per-user ActivityPub federation routes and WebFinger discovery.

Routers live in thematic submodules; registration order below matches the
original file order so route matching precedence is unchanged.
"""

from fastapi import APIRouter

from .federation_discovery import router as discovery_router
from .federation_libraries import router as libraries_router
from .federation_music import router as music_router
from .federation_objects import router as objects_router
from .federation_social import router as social_router

router = APIRouter(include_in_schema=False)
router.include_router(objects_router)
router.include_router(social_router)
router.include_router(discovery_router)
router.include_router(music_router)
router.include_router(libraries_router)
