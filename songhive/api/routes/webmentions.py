"""
Webmention endpoints.

``POST /webmentions`` implements the W3C Webmention receiver: senders submit
``source``/``target`` form fields and get a 202 acknowledgement while the
``process_incoming_webmention`` Celery task fetches and verifies the source.
The endpoint URL is advertised through a ``Link`` response header on the
instance's HTML pages (see ``_setup_webmentions`` in ``api/app.py``).

``GET /webmentions/source/{activity_id}`` serves the Microformats2
``h-entry`` document remote servers fetch to verify outgoing mentions. It
renders only public, live local activities — a retracted or unfederated one
answers 410/404 so receivers drop the stored mention.
"""

import logging
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.schema import SonghiveConfig
from ...models.activity import Activity
from ...models.user import User
from ...services.federation import extract_domain, is_domain_allowed
from ...webmentions.render import render_outgoing_source_page
from ...webmentions.service import (
    activity_tag_names,
    webmention_endpoint_url,
    webmentions_enabled,
)
from ..deps import get_config, get_db
from ..middleware.rate_limit import rate_limit

logger = logging.getLogger(__name__)

router = APIRouter(include_in_schema=False)


def _check_enabled(config: SonghiveConfig) -> None:
    if not webmentions_enabled(config):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)


@router.post("/webmentions")
async def receive_webmention(
    source: Optional[str] = Form(default=None),
    target: Optional[str] = Form(default=None),
    config: SonghiveConfig = Depends(get_config),
    _: None = Depends(rate_limit),
):
    """Accept a Webmention for asynchronous processing (HTTP 202)."""
    _check_enabled(config)

    if not source or not target:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing source or target parameter",
        )
    for value in (source, target):
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="source and target must be absolute http(s) URLs",
            )

    domain = config.federation.instance_domain.strip()
    if (urlparse(target).hostname or "").lower() != domain.lower():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="target URL is not on this instance",
        )

    source_domain = extract_domain(source)
    if source_domain and not is_domain_allowed(source_domain, config):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    try:
        from ...tasks.webmentions import process_incoming_webmention

        process_incoming_webmention.delay(source, target)  # type: ignore[attr-defined]
    except Exception as exc:
        logger.warning("Could not queue incoming webmention %s -> %s: %s", source, target, exc)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE) from exc

    return JSONResponse(
        content={"status": "queued", "source": source, "target": target},
        status_code=status.HTTP_202_ACCEPTED,
    )


@router.get("/webmentions/source/{activity_id}")
async def get_outgoing_source(
    activity_id: str,
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Serve the h-entry source document for an activity's outgoing mentions."""
    _check_enabled(config)

    activity = await db.get(Activity, activity_id)
    if activity is None or activity.source_type != "local":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if activity.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_410_GONE)
    if activity.visibility != "public":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    author = await db.get(User, activity.owner_user_id) if activity.owner_user_id else None
    tag_names = await activity_tag_names(db, activity)
    page = render_outgoing_source_page(activity, author, config, tag_names=tag_names)

    from webmentions.server.adapters._common import append_link_header, webmention_link_header_value

    return HTMLResponse(
        content=page,
        headers={"Link": append_link_header(None, webmention_link_header_value(webmention_endpoint_url(config)))},
    )
