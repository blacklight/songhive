"""
NodeInfo and WebFinger federation discovery.
"""

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pubby.cache import normalize_webfinger_resource

from ...federation import doc_cache, get_actor_url
from ...models.base import get_session
from ...services import moderation as moderation_service
from ...services.auth import get_user_by_username
from .federation_common import (
    ACTIVITY_JSON,
    JRD_JSON,
    _document_ttl,
    _federation_config,
    _serve_cached_document,
)
from .instance import _admin_users

router = APIRouter(include_in_schema=False)


@router.get("/nodeinfo/2.1")
@router.get("/nodeinfo/2.0")
@router.get("/nodeinfo/2.1.json")
@router.get("/nodeinfo/2.0.json")
async def nodeinfo_document(
    request: Request,
):
    """
    Return the NodeInfo document enriched with instance contact metadata.

    Songhive's router is mounted before pubby's bindings, so these routes
    take precedence over the plain documents registered by the pubby
    adapters. The base document still comes from the federation handler
    (usage stats, software name/version); ``metadata`` additionally carries
    the node name/description, the configured contact person
    (``maintainer``) and the actor URLs of the active admin accounts
    (``staffAccounts``), matching the conventions used by PeerTube and
    Friendica.
    """
    config = _federation_config(request)
    handler = getattr(request.app.state, "federation_handler", None)
    if handler is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    async def _render() -> dict:
        # ``get_nodeinfo_document`` performs synchronous storage calls.
        document = await asyncio.to_thread(handler.get_nodeinfo_document)
        metadata = document.setdefault("metadata", {})
        metadata["nodeName"] = config.federation.instance_name
        metadata["nodeDescription"] = config.federation.instance_description

        maintainer = {
            key: value
            for key, value in (
                ("name", config.federation.contact_name),
                ("email", config.federation.contact_email),
                ("url", config.federation.contact_url),
            )
            if value
        }
        if maintainer:
            metadata["maintainer"] = maintainer

        domain = config.federation.instance_domain
        async with get_session() as db:
            admins = await _admin_users(db)
        metadata["staffAccounts"] = [admin.actor_url or get_actor_url(domain, admin.username) for admin in admins]
        return document

    ttl = _document_ttl(config)
    document = await doc_cache.get_or_render(("nodeinfo",), ttl, _render)
    # NodeInfo is plain JSON, not an ActivityStreams document.
    return _serve_cached_document(request, ("nodeinfo",), ttl, document, media_type="application/json")


@router.get("/.well-known/webfinger")
async def webfinger(
    request: Request,
    resource: Optional[str] = None,
):
    """
    WebFinger discovery for local users and the instance actor.
    """
    if resource is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="resource parameter is required")

    config = _federation_config(request)
    domain = config.federation.instance_domain

    if not resource.lower().startswith("acct:"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    rest = resource[5:]
    if rest.startswith("@"):
        rest = rest[1:]
    if "@" not in rest:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    name, resource_domain = rest.split("@", 1)
    if resource_domain.lower() != domain.lower():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    instance_username = config.federation.instance_name.lower().replace(" ", "-")
    if name.lower() == instance_username:
        actor_url = f"https://{domain}/ap/actor"
        return JSONResponse(
            content={
                "subject": f"acct:{instance_username}@{domain}",
                "aliases": [actor_url],
                "links": [
                    {
                        "rel": "self",
                        "type": ACTIVITY_JSON,
                        "href": actor_url,
                    },
                    {
                        "rel": "http://webfinger.net/rel/profile-page",
                        "type": "text/html",
                        "href": actor_url,
                    },
                ],
            },
            media_type=JRD_JSON,
        )

    async def _render() -> Optional[dict]:
        async with get_session() as db:
            user = await get_user_by_username(db, name.lower())
            if user is None or not user.is_active:
                return None
            if await moderation_service.user_is_suspended(db, str(user.id)):
                return None
            username = user.username

        actor_url = get_actor_url(domain, username)
        return {
            "subject": f"acct:{username}@{domain}",
            "aliases": [actor_url],
            "links": [
                {
                    "rel": "self",
                    "type": ACTIVITY_JSON,
                    "href": actor_url,
                },
                {
                    "rel": "http://webfinger.net/rel/profile-page",
                    "type": "text/html",
                    "href": actor_url,
                },
            ],
        }

    ttl = _document_ttl(config)
    # ``acct:User@dom`` and ``ACCT:@user@dom`` spell the same resource —
    # normalizing to ``user@dom`` lets every accepted spelling share one
    # cache entry. The key stays in Songhive's own namespace: pubby's
    # ``webfinger_key`` lives under ``("pubby", …)`` for its adapter routes,
    # and mixing the two would let pubby's actor-update invalidation drop
    # Songhive's WebFinger entries (and vice versa) while storing a dict
    # under a key pubby's adapter uses for ``CachedResponse`` values.
    key = ("webfinger", normalize_webfinger_resource(resource))
    document = await doc_cache.get_or_render(key, ttl, _render)
    return _serve_cached_document(request, key, ttl, document, media_type=JRD_JSON)


# ---------------------------------------------------------------------------
# Federated music entities (Funkwhale-compatible dialect)
#
# Songhive artists/albums/libraries are published as dereferenceable
# ActivityPub music documents under ``/artists/{id}``, ``/albums/{id}`` and
# ``/libraries/{id}`` so remote instances — Funkwhale and other Songhive
# instances alike — can resolve, search and follow them. A user's implicit
# library of public tracks is served under ``{actor_url}/library``.
# ---------------------------------------------------------------------------

_LIBRARY_PAGE_SIZE = 100
