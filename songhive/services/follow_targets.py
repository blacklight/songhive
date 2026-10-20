"""
Follow-target resolution — extracted from ``follows.py``.

Re-exported from ``songhive.services.follows`` so existing callers keep
working unchanged.
"""

from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..config.schema import SonghiveConfig
from ..models.user import User
from . import federation as federation_service
from . import remote_content
from .auth import get_user_by_username
from .remote_content import RemoteActorResult


class FollowError(HTTPException):
    """A follow/unfollow operation failed with an HTTP status."""


@dataclass
class ResolvedTarget:
    """A follow target resolved to a local user or remote actor."""

    actor_url: str
    local_user: Optional[User] = None
    remote_actor: Optional[RemoteActorResult] = None

    @property
    def is_local(self) -> bool:
        return self.local_user is not None


def _remote_actor_snapshot(actor: RemoteActorResult) -> dict:
    """Build a display-suitable actor document from a normalized remote actor."""
    doc: dict = {
        "id": actor.actor_url,
        "type": "Person",
        "preferredUsername": actor.username,
        "name": actor.display_name or actor.username,
        "url": actor.profile_url or actor.actor_url,
        "inbox": actor.inbox_url,
    }
    if actor.avatar_url:
        doc["icon"] = {"type": "Image", "url": actor.avatar_url}
    return doc


async def _resolve_target(session: AsyncSession, config: SonghiveConfig, raw: str) -> ResolvedTarget:
    """
    Resolve a follow target input to a local user or remote actor.

    Accepts bare local usernames, ``@user@domain`` handles, actor/profile
    URLs (local or remote). Local inputs resolve through the users table;
    remote inputs through :func:`remote_content.lookup_remote_actor`,
    which applies the domain allow/block rules and actor cache.
    """
    text = (raw or "").strip()
    if not text:
        raise FollowError(status_code=422, detail="Follow target is required")

    username: Optional[str] = None
    if not text.startswith(("http://", "https://")) and "@" not in text:
        username = text
    else:
        target = remote_content.parse_remote_target(text, config)
        if target.kind == remote_content.RemoteTargetKind.LOCAL:
            if target.username:
                username = target.username.lstrip("@")
            elif target.url:
                match = remote_content._ACTOR_PATH_RE.match(urlparse(target.url).path or "/")
                if match:
                    username = (match.group("name") or match.group("name2") or "").lstrip("@")
            if not username:
                raise FollowError(status_code=404, detail="User not found")
        else:
            actor = await remote_content.lookup_remote_actor(session, config, text)
            if actor.unavailable:
                raise FollowError(status_code=404, detail="Remote actor is no longer available")
            return ResolvedTarget(actor_url=actor.actor_url, remote_actor=actor)

    # ``no_autoflush`` keeps pending actor-provisioning mutations from
    # flushing here — on SQLite a write transaction held by this session
    # would block the sync pubby-storage writes that follow.
    with session.no_autoflush:
        local = await get_user_by_username(session, username)
    if local is None:
        raise FollowError(status_code=404, detail="User not found")
    federation_service.ensure_user_actor(local, config)
    if not local.actor_url:
        raise FollowError(status_code=422, detail="Target user has no federation actor")
    return ResolvedTarget(actor_url=local.actor_url, local_user=local)
