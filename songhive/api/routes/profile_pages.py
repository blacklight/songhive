"""
Always-mounted user profile page routes.

``/users/{username}`` and ``/@{username}`` are served here so they work with
and without federation enabled. ActivityPub clients receive the ``Person``
JSON when federation is configured; browsers receive a redirect or the SPA
shell with ``rel="me"`` and ``rel="alternate"`` discovery hints.
"""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any, Optional, Sequence
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from ...federation import get_actor_url, get_mastodon_actor_url
from ...federation.actors import user_to_actor_document
from ...models.user import User
from ...services import settings as settings_service
from ...services.auth import get_user_by_username
from ...services.federation import ensure_user_actor
from ..deps import get_current_user_optional, get_db, get_redis
from ..semantic_meta import feed_link_tags, inject_head_tags, public_base_url, user_head_tags

router = APIRouter(include_in_schema=False)

ACTIVITY_JSON = "application/activity+json"
LD_JSON = "application/ld+json"


def _accepts_activitypub(request: Request) -> bool:
    """Return True when the client requests an ActivityPub document."""
    accept = request.headers.get("accept", "")
    return ACTIVITY_JSON in accept or LD_JSON in accept


def _accepts_html(request: Request) -> bool:
    """Return True when the client can also render an HTML page."""
    accept = request.headers.get("accept", "")
    return "text/html" in accept or "*/*" in accept


async def _get_active_user(db: AsyncSession, username: str) -> Any:
    """Return an active user or raise 404."""
    user = await get_user_by_username(db, username)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


def _spa_index_path() -> Path:
    """Return the path of the built SPA entry point."""
    return Path(__file__).resolve().parents[2] / "static" / "index.html"


def _spa_response(
    alternate_url: Optional[str] = None,
    me_urls: Optional[Sequence[str]] = None,
    extra_tags: Optional[Sequence[str]] = None,
) -> HTMLResponse:
    """
    Serve the SPA shell for browser requests.

    Injects ``rel="me"`` links for the user's profile fields and, when
    federation is enabled, the ``rel="alternate"`` discovery hints that
    remote servers use to find the ActivityPub actor document. ``extra_tags``
    carries additional ``<head>`` markup such as OpenGraph metadata.
    """
    index = _spa_index_path()
    if not index.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    body = index.read_text(encoding="utf-8")
    headers: dict[str, str] = {}
    tags: list[str] = []

    if me_urls:
        for url in me_urls:
            tags.append(f'<link rel="me" href="{escape(url, quote=True)}">')
    if alternate_url:
        tag = f'<link rel="alternate" type="{ACTIVITY_JSON}" href="{escape(alternate_url, quote=True)}">'
        tags.append(tag)
        headers["Link"] = f'<{alternate_url}>; rel="alternate"; type="{ACTIVITY_JSON}"'
    if extra_tags:
        tags.extend(extra_tags)
    if tags:
        body = inject_head_tags(body, list(tags))

    return HTMLResponse(content=body, headers=headers)


def _user_spa_response(
    user: User,
    request: Request,
    alternate_url: Optional[str] = None,
    domain: Optional[str] = None,
) -> HTMLResponse:
    """Return the SPA shell annotated with the user's ``rel="me"`` and OpenGraph tags."""
    me_urls: list[str] = [link.url for link in user.links or [] if link.url]
    if alternate_url:
        me_urls.append(alternate_url)
        if domain:
            me_urls.append(get_mastodon_actor_url(domain, user.username))
    config = request.app.state.config
    base_url = public_base_url(request, config)
    og_tags = user_head_tags(user, base_url, config.federation.instance_name)
    if config.feeds.enabled:
        og_tags += feed_link_tags("user", user.username, base_url)
    return _spa_response(alternate_url=alternate_url, me_urls=me_urls, extra_tags=og_tags)


def _federation_enabled_config(request: Request) -> tuple[bool, Optional[str]]:
    """Return (enabled, domain) for the current app config."""
    config = request.app.state.config
    return config.federation.enabled and bool(config.federation.instance_domain), config.federation.instance_domain


@router.get("/")
async def get_home_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Serve the SPA home page, or redirect to the single-user profile.

    When the ``single_user_username`` setting names a user, anonymous
    visitors are redirected to ``/@{username}`` so the profile becomes the
    instance landing page. Authenticated users keep the regular home page —
    single-user mode is a presentation choice for logged-out visitors, not
    an ACL. ActivityPub clients are never redirected: ``/`` has no
    ActivityPub representation, so they keep getting the SPA shell exactly
    as the default handler served it before.

    In single-user mode every ``/`` response also advertises ``rel="me"``
    links pointing at the user's profile — in both the ``/@{username}`` and
    ``/users/{username}`` forms — in the body and the ``Link`` header, so
    link verification (e.g. on Mastodon) works even when only the instance
    base URL is referenced.
    """
    username = await settings_service.get_setting(db, redis, "single_user_username")
    if isinstance(username, str) and username:
        base_url = public_base_url(request, request.app.state.config)
        me_urls = [f"{base_url}/@{quote(username)}", f"{base_url}/users/{quote(username)}"]
        link_header = ", ".join(f'<{url}>; rel="me"' for url in me_urls)
        if user is None and not _accepts_activitypub(request):
            return RedirectResponse(
                url=f"/@{username}",
                status_code=status.HTTP_302_FOUND,
                headers={"Link": link_header},
            )
        response = _spa_response(me_urls=me_urls)
        response.headers["Link"] = link_header
        return response

    return _spa_response()


@router.get("/users/{username}")
async def get_user_actor_or_redirect(
    username: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Serve the ActivityPub actor for AP clients, redirect browsers to /@user."""
    user = await _get_active_user(db, username)
    ap_enabled, domain = _federation_enabled_config(request)

    if _accepts_activitypub(request):
        if not ap_enabled or not domain:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        ensure_user_actor(user, request.app.state.config)
        actor = user_to_actor_document(user, domain)
        return JSONResponse(content=actor, media_type=ACTIVITY_JSON)

    query = request.url.query
    target = f"/@{username}"
    if query:
        target = f"{target}?{query}"
    return RedirectResponse(url=target, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.get("/@{username}")
async def get_user_profile_page(
    username: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Serve the user profile SPA or the ActivityPub actor."""
    user = await _get_active_user(db, username)
    ap_enabled, domain = _federation_enabled_config(request)

    if _accepts_activitypub(request):
        if not ap_enabled or not domain:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        ensure_user_actor(user, request.app.state.config)
        actor = user_to_actor_document(user, domain)
        return JSONResponse(content=actor, media_type=ACTIVITY_JSON)

    alternate_url = get_actor_url(domain, user.username) if ap_enabled and domain else None
    return _user_spa_response(user, request, alternate_url=alternate_url, domain=domain)
