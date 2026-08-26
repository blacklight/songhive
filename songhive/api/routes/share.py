"""
Public short-URL resolver for share tokens.

``GET /api/v1/share/{token}`` maps a raw token to the matching item and
returns one of:

* an HTML preview page for web browsers / social media crawlers;
* a 302 redirect to the public API JSON representation when the client sends
  ``Accept: application/json`` (used by the web UI's share preview);
* a 302 redirect to a direct audio download when ``?download=true`` is used
  or the shared item is an audio file.
"""

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.stored_file import StoredFile
from ...services import acl, music, sharing
from ..deps import get_db
from ..middleware.rate_limit import rate_limit
from ..share_page import render_share_page

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/share")

_not_found = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Not found",
)


def _wants_json(request: Request) -> bool:
    """Return ``True`` when the client explicitly requested JSON over HTML."""
    accept = request.headers.get("accept", "")
    if not accept:
        return False
    # API clients (including the web UI) send ``Accept: application/json``.
    # Browsers typically send ``text/html`` as the preferred type.
    return "application/json" in accept and "text/html" not in accept


def _is_audio_file(item: Any) -> bool:
    """Return ``True`` if ``item`` is a stored audio file."""
    return isinstance(item, StoredFile) and item.content_type.startswith("audio/")


def _audio_download_url(item_type: str, item: Any, token: str) -> Optional[str]:
    """Return a direct download URL for a track or audio file, or ``None``."""
    if item_type == "file" and isinstance(item, StoredFile):
        return f"/api/v1/files/{item.id}/download?token={token}&disposition=attachment"
    if item_type == "track" and getattr(item, "audio_file_id", None):
        return f"/api/v1/files/{item.audio_file_id}/download?token={token}&disposition=attachment"
    return None


def _set_share_cookie(response: RedirectResponse, token: str, request: Request) -> None:
    """Set the short-lived ``share_token`` cookie used by resource routes."""
    response.set_cookie(
        key="share_token",
        value=token,
        max_age=300,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        path="/",
    )


async def _load_shared_item(db: AsyncSession, item_type: str, item_id: str) -> Optional[Any]:
    """Load the item referenced by a valid share token, with useful relations."""
    if item_type == "track":
        return await music.get_track(db, item_id, include={"artist", "album"})
    if item_type == "album":
        return await music.get_album(db, item_id, include={"artist", "tracks"})
    if item_type == "artist":
        return await music.get_artist(db, item_id, include={"tracks", "albums"})
    if item_type == "playlist":
        return await music.get_playlist(db, item_id, include={"tracks"})
    if item_type == "library":
        return await music.get_library(db, item_id, include={"tracks"})
    if item_type == "radio":
        return await music.get_radio(db, item_id)
    if item_type == "file":
        return await db.get(StoredFile, item_id)
    return None


def _html_not_found(request: Request, token: str) -> HTMLResponse:
    """Return an HTML 404 page for an invalid or expired share link."""
    return HTMLResponse(
        content=render_share_page(None, "", token, request),
        status_code=status.HTTP_404_NOT_FOUND,
    )


@router.get("/{token}", dependencies=[Depends(rate_limit)])
async def resolve_share_url(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    download: bool = Query(False),
):
    """Resolve a raw share token to the item it grants access to.

    Browser requests receive an HTML preview page. API clients that send
    ``Accept: application/json`` are redirected to the item's public JSON
    endpoint. Audio files and the ``?download=true`` flag redirect to the
    direct download URL.
    """
    wants_json = _wants_json(request)

    row = await sharing.get_valid_share_token(db, token)
    if row is None:
        if wants_json:
            raise _not_found
        return _html_not_found(request, token)

    plural = acl.get_item_plural(row.item_type)
    if plural is None:
        if wants_json:
            raise _not_found
        return _html_not_found(request, token)

    if wants_json:
        target = f"/api/v1/{plural}/{row.item_id}"
        response = RedirectResponse(target, status_code=status.HTTP_302_FOUND)
        _set_share_cookie(response, token, request)
        return response

    item = await _load_shared_item(db, row.item_type, row.item_id)
    if item is None:
        return _html_not_found(request, token)

    if download:
        download_target = _audio_download_url(row.item_type, item, token)
        if download_target is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This item cannot be downloaded directly",
            )
        return RedirectResponse(download_target, status_code=status.HTTP_302_FOUND)

    # Audio file shares are direct download links by default.
    if _is_audio_file(item):
        audio_target = _audio_download_url(row.item_type, item, token)
        if audio_target is not None:
            return RedirectResponse(audio_target, status_code=status.HTTP_302_FOUND)

    html = render_share_page(item, row.item_type, token, request)
    return HTMLResponse(content=html, status_code=status.HTTP_200_OK)
