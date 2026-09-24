"""
Outbound follow relationships — local users following local or remote actors.

Pubby's follower/request tables track the *inbound* side (who follows our
actors). This module owns the *outbound* side persisted in the ``follows``
table:

- ``follow_user`` resolves the target (local username, ``@user@domain``
  handle or actor URL), records the relationship and performs the
  appropriate handshake — for a remote actor a signed ``Follow`` activity
  is enqueued on ``deliver_activity`` and the row stays ``pending`` until
  the remote ``Accept``/``Reject`` arrives; for a local target the pubby
  follower/request rows are written directly and the owner's approval
  policy applies immediately.
- ``unfollow_user`` reverses it — a ``Undo(Follow)`` is delivered for
  remote targets, pubby rows are dropped for local ones, and the ``Follow``
  row is removed.
- ``apply_follow_decision`` folds inbound ``Accept``/``Reject`` activities
  (matched by activity id or embedded ``Follow``) back into the row.
- ``actor_is_followed`` gates remote-activity materialization: only actors
  followed by some local user get their inbound objects stored.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from urllib.parse import urlparse

from fastapi import HTTPException
from pubby import Follower, FollowRequest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config.schema import SonghiveConfig
from ..federation._common import get_inbox_url
from ..federation.activities import create_follow_activity, create_undo_activity
from ..federation.actors import get_federation_storage, user_to_actor_document
from ..models.follow import FOLLOW_STATE_ACCEPTED, FOLLOW_STATE_PENDING, Follow
from ..models.user import FollowersApproval, User
from . import federation as federation_service
from . import moderation as moderation_service
from . import remote_content
from .auth import get_user_by_username
from .remote_content import RemoteActorResult

logger = logging.getLogger(__name__)


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


async def get_follow(session: AsyncSession, user_id: str, target_actor_url: str) -> Optional[Follow]:
    """Return the follow row of ``user_id`` on ``target_actor_url``, if any."""
    # ``no_autoflush`` keeps pending actor-provisioning mutations from
    # flushing here — on SQLite a write transaction held by this session
    # would block the sync pubby-storage writes that follow.
    with session.no_autoflush:
        return await session.scalar(
            select(Follow).where(Follow.user_id == user_id, Follow.target_actor_url == target_actor_url)
        )


async def actor_is_followed(session: AsyncSession, actor_url: str) -> bool:
    """Return whether any local user follows (or requested to follow) ``actor_url``."""
    return await session.scalar(select(Follow.id).where(Follow.target_actor_url == actor_url).limit(1)) is not None


async def object_is_followed(session: AsyncSession, object_urls: List[str]) -> bool:
    """
    Return whether any local user follows any of the given object URLs.

    Object follows (e.g. a follow of a remote music library) record the
    followed *object* URL in ``target_actor_url`` — this checks the same
    column against a set of candidate URLs drawn from an inbound activity's
    object fields.
    """
    urls = [u for u in object_urls if isinstance(u, str) and u]
    if not urls:
        return False
    return await session.scalar(select(Follow.id).where(Follow.target_actor_url.in_(urls)).limit(1)) is not None


async def follow_states_for(session: AsyncSession, user_id: str, actor_urls: List[str]) -> dict:
    """Return ``{target_actor_url: state}`` for ``user_id``'s follows on ``actor_urls``."""
    if not actor_urls:
        return {}
    return dict(
        (
            await session.execute(
                select(Follow.target_actor_url, Follow.state).where(
                    Follow.user_id == user_id, Follow.target_actor_url.in_(actor_urls)
                )
            )
        ).all()  # type: ignore
    )


async def list_user_follows(
    session: AsyncSession,
    user_id: str,
    *,
    states: Optional[Tuple[str, ...]] = None,
) -> List[Follow]:
    """Return ``user_id``'s follow rows (newest first), optionally filtered by state."""
    query = select(Follow).where(Follow.user_id == user_id).order_by(Follow.created_at.desc())
    if states:
        query = query.where(Follow.state.in_(states))
    return list((await session.execute(query)).scalars())


async def count_user_follows(
    session: AsyncSession,
    user_id: str,
    *,
    states: Optional[Tuple[str, ...]] = None,
) -> int:
    """Return the number of actors ``user_id`` follows, optionally filtered by state."""
    query = select(func.count(Follow.id)).where(Follow.user_id == user_id)
    if states:
        query = query.where(Follow.state.in_(states))
    return int(await session.scalar(query) or 0)


async def follows_count_map(session: AsyncSession, user_ids: List[str]) -> dict:
    """Return ``{user_id: accepted_follow_count}`` for the given users."""
    if not user_ids:
        return {}
    return dict(
        (
            await session.execute(
                select(Follow.user_id, func.count(Follow.id))
                .where(Follow.user_id.in_(user_ids), Follow.state == FOLLOW_STATE_ACCEPTED)
                .group_by(Follow.user_id)
            )
        ).all()  # type: ignore
    )


async def _local_actor_data(user: User, config: SonghiveConfig) -> dict:
    """Return the user's actor document for pubby/notification display fields."""
    return user_to_actor_document(user, config.federation.instance_domain or "")


async def _notify_local_follow(
    session: AsyncSession,
    config: SonghiveConfig,
    *,
    follower: User,
    target: User,
    storage,
    activity_id: Optional[str],
) -> None:
    """Create the target's ``follow`` notification for a local-to-local follow."""
    from ..federation.notifications import _create_follow_inbox_notification

    actor_data = await _local_actor_data(follower, config)
    display_name = federation_service._actor_doc_display_name(actor_data)
    avatar_url = federation_service._actor_doc_avatar_url(actor_data)
    payload = {"actor_name": follower.username, "activity_id": activity_id}
    if display_name:
        payload["actor_display_name"] = display_name
    if avatar_url:
        payload["actor_avatar_url"] = avatar_url
    await _create_follow_inbox_notification(
        session,
        actor_url=follower.actor_url or "",
        recipient=target,
        payload=payload,
        obj=target.actor_url,
        instance_domain=config.federation.instance_domain,
        storage=storage,
    )


def _deliver(activity: dict, inbox_url: str, user: User) -> None:
    """Enqueue a signed activity for remote delivery."""
    from ..tasks.federation import deliver_activity

    assert user.actor_url and user.private_key_pem  # checked by callers
    deliver_activity.delay(activity, inbox_url, f"{user.actor_url}#main-key", user.private_key_pem)  # type: ignore


async def _follow_local(
    session: AsyncSession,
    config: SonghiveConfig,
    user: User,
    target: User,
    *,
    force_manual: bool = False,
) -> Follow:
    """Record a local-to-local follow honoring the target's approval policy.

    ``force_manual`` — set when the follower is admin-limited — degrades
    an ``accept`` policy to ``manual`` so the follow lands as a pending
    approval request.
    """
    policy = target.followers_approval
    if policy == FollowersApproval.REJECT.value:
        raise FollowError(status_code=403, detail="This user is not accepting followers")

    assert user.actor_url and target.actor_url
    now = datetime.now(timezone.utc)
    activity = create_follow_activity(user.actor_url, target.actor_url)
    storage = await asyncio.to_thread(get_federation_storage, config.database.url)
    actor_data = await _local_actor_data(user, config)
    inbox_url = get_inbox_url(config.federation.instance_domain or "", user.username)

    if force_manual or policy == FollowersApproval.MANUAL.value:
        state = FOLLOW_STATE_PENDING
        await asyncio.to_thread(
            storage.store_follow_request,
            FollowRequest(
                actor_id=user.actor_url,
                target_actor_id=target.actor_url,
                inbox=inbox_url,
                actor_data=actor_data,
                activity=activity,
                requested_at=now,
            ),
        )
    else:
        state = FOLLOW_STATE_ACCEPTED
        await asyncio.to_thread(
            storage.store_follower,
            Follower(
                actor_id=user.actor_url,
                inbox=inbox_url,
                followed_at=now,
                actor_data=actor_data,
                target_actor_id=target.actor_url,
            ),
        )

    row = Follow(
        user_id=user.id,
        actor_url=user.actor_url,
        target_actor_url=target.actor_url,
        target_user_id=target.id,
        state=state,
        activity_id=activity["id"],
        actor_data=_remote_actor_snapshot(
            RemoteActorResult(
                actor_url=target.actor_url,
                username=target.username,
                domain=config.federation.instance_domain or "",
                display_name=target.display_name,
                avatar_url=target.avatar_url,
                inbox_url=inbox_url,
            )
        ),
        inbox_url=inbox_url,
        accepted_at=now if state == FOLLOW_STATE_ACCEPTED else None,
    )
    session.add(row)
    await session.flush()
    await _notify_local_follow(
        session, config, follower=user, target=target, storage=storage, activity_id=activity["id"]
    )
    return row


async def _follow_remote(
    session: AsyncSession,
    config: SonghiveConfig,
    user: User,
    actor: RemoteActorResult,
) -> Follow:
    """Record a remote follow and deliver the ``Follow`` activity."""
    assert user.actor_url and user.private_key_pem
    inbox_url = actor.inbox_url or await asyncio.to_thread(
        federation_service.resolve_actor_inbox,
        actor.actor_url,
        config,
        key_id=f"{user.actor_url}#main-key",
        private_key_pem=user.private_key_pem,
    )
    if not inbox_url:
        raise FollowError(status_code=502, detail="Could not resolve the remote actor's inbox")

    activity_id = f"{user.actor_url}/activities/{uuid.uuid4()}"
    activity = create_follow_activity(user.actor_url, actor.actor_url, activity_id=activity_id)
    row = Follow(
        user_id=user.id,
        actor_url=user.actor_url,
        target_actor_url=actor.actor_url,
        target_user_id=None,
        state=FOLLOW_STATE_PENDING,
        activity_id=activity_id,
        actor_data=_remote_actor_snapshot(actor),
        inbox_url=inbox_url,
    )
    session.add(row)
    await session.flush()
    _deliver(activity, inbox_url, user)
    return row


def _remote_object_actor_url(remote_object) -> Optional[str]:
    """Return the controlling actor URL of a cached remote object.

    Federated music resources name their owner through ``actor`` (Funkwhale
    ``Library``) or ``attributedTo``; the cache row's ``actor_url`` is the
    resolved fallback.
    """
    payload = remote_object.payload if isinstance(remote_object.payload, dict) else {}
    for key in ("actor", "attributedTo"):
        url = remote_content._as_url(payload.get(key))
        if url:
            return url
    return remote_object.actor_url or None


async def follow_remote_object(
    session: AsyncSession,
    config: SonghiveConfig,
    user: User,
    object_url: str,
) -> Follow:
    """
    Follow a remote object — typically a federated music ``Library``.

    The ``Follow`` targets the object URL itself (object-scoped follow,
    FEP-efda style), not an actor, and is delivered to the object's
    controlling actor inbox — for a Funkwhale library that is
    ``library.actor``. The ``Follow`` row keys ``target_actor_url`` on the
    object URL so the remote ``Accept`` — issued by the controlling actor,
    which differs from the object id for libraries — is matched back by
    :func:`apply_follow_decision`.
    """
    if not config.federation.enabled or not config.federation.instance_domain:
        raise FollowError(status_code=400, detail="Federation is not enabled")
    await moderation_service.assert_not_suspended(session, user)
    federation_service.ensure_user_actor(user, config)
    if not user.actor_url or not user.private_key_pem:
        raise FollowError(status_code=400, detail="User has no federation actor credentials")
    await moderation_service.load_instance_policies(session)

    result = await remote_content.dereference_remote_object(session, config, object_url)
    remote_object = result.remote_object
    if remote_object.unavailable_at is not None:
        raise FollowError(status_code=404, detail="Remote object is no longer available")
    if remote_object.resource_type is None:
        raise FollowError(status_code=422, detail="Remote object is not a followable resource")

    controller_url = _remote_object_actor_url(remote_object)
    if not controller_url:
        raise FollowError(status_code=422, detail="Remote object has no controlling actor")
    if controller_url == user.actor_url:
        raise FollowError(status_code=400, detail="Cannot follow your own resource")

    existing = await get_follow(session, user.id, remote_object.canonical_url)
    if existing is not None:
        return existing

    inbox_url: Optional[str] = None
    try:
        controller = await remote_content.lookup_remote_actor(session, config, controller_url)
        inbox_url = controller.inbox_url
    except Exception:
        inbox_url = None
    if not inbox_url:
        inbox_url = await asyncio.to_thread(
            federation_service.resolve_actor_inbox,
            controller_url,
            config,
            key_id=f"{user.actor_url}#main-key",
            private_key_pem=user.private_key_pem,
        )
    if not inbox_url:
        raise FollowError(status_code=502, detail="Could not resolve the remote object's inbox")

    activity_id = f"{user.actor_url}/activities/{uuid.uuid4()}"
    activity = create_follow_activity(user.actor_url, remote_object.canonical_url, activity_id=activity_id)
    row = Follow(
        user_id=user.id,
        actor_url=user.actor_url,
        target_actor_url=remote_object.canonical_url,
        target_user_id=None,
        state=FOLLOW_STATE_PENDING,
        activity_id=activity_id,
        actor_data={
            "id": remote_object.canonical_url,
            "type": remote_object.object_type,
            "preferredUsername": remote_object.name or remote_object.canonical_url,
            "name": remote_object.name or remote_object.canonical_url,
            "url": remote_object.canonical_url,
            "inbox": inbox_url,
        },
        inbox_url=inbox_url,
    )
    session.add(row)
    await session.flush()
    _deliver(activity, inbox_url, user)
    return row


async def _followed_object_actor(session: AsyncSession, object_url: str) -> Optional[str]:
    """Return the controlling actor URL of a followed remote object."""
    from ..models.remote_object import RemoteObject

    row = await session.scalar(select(RemoteObject).where(RemoteObject.canonical_url == object_url))
    if row is None:
        return None
    return _remote_object_actor_url(row)


async def follow_user(
    session: AsyncSession,
    config: SonghiveConfig,
    user: User,
    raw_target: str,
) -> Follow:
    """
    Follow a local or remote actor, returning the stored ``Follow`` row.

    Local targets apply the owner's ``followers_approval`` policy
    immediately — ``accept`` stores the pubby follower row and marks the
    relationship ``accepted``; ``manual`` stores a pending pubby request
    and keeps the row ``pending`` until the owner decides; ``reject``
    fails with 403. Remote targets enqueue a signed ``Follow`` activity
    and stay ``pending`` until the remote decision is applied by
    :func:`apply_follow_decision`.

    Re-following an already followed actor returns the existing row.
    """
    if not config.federation.enabled or not config.federation.instance_domain:
        raise FollowError(status_code=400, detail="Federation is not enabled")
    await moderation_service.assert_not_suspended(session, user)
    federation_service.ensure_user_actor(user, config)
    if not user.actor_url or not user.private_key_pem:
        raise FollowError(status_code=400, detail="User has no federation actor credentials")

    # Refresh the instance-policy snapshot so remote target resolution
    # honors database defederation.
    await moderation_service.load_instance_policies(session)

    target = await _resolve_target(session, config, raw_target)
    if target.actor_url == user.actor_url:
        raise FollowError(status_code=400, detail="Cannot follow yourself")

    target_user_id = str(target.local_user.id) if target.local_user is not None else None
    if target_user_id is not None:
        if await moderation_service.user_is_suspended(session, target_user_id):
            raise FollowError(status_code=403, detail="This account is suspended")
    elif await moderation_service.actor_is_suspended(session, target.actor_url):
        raise FollowError(status_code=403, detail="This account is suspended")
    if await moderation_service.block_exists_between(
        session,
        user,
        target_actor_url=target.actor_url,
        target_user_id=target_user_id,
    ):
        raise FollowError(status_code=403, detail="Cannot follow this actor")

    existing = await get_follow(session, user.id, target.actor_url)
    if existing is not None:
        return existing

    if target.is_local:
        assert target.local_user is not None
        return await _follow_local(
            session,
            config,
            user,
            target.local_user,
            force_manual=await moderation_service.user_is_limited(session, user.id),
        )
    assert target.remote_actor is not None
    return await _follow_remote(session, config, user, target.remote_actor)


async def resolve_unfollow_target(session: AsyncSession, config: SonghiveConfig, raw: str) -> str:
    """
    Map an unfollow input to the stored ``target_actor_url``.

    Bare local usernames and local handles map through the users table;
    anything else is assumed to already be an actor URL — clients send the
    canonical URL returned by follow/list operations, and remote handles
    cannot be resolved without a network round-trip the unfollow path
    should not depend on.
    """
    text = (raw or "").strip()
    if not text:
        raise FollowError(status_code=422, detail="Follow target is required")
    if text.startswith(("http://", "https://")):
        return text

    username: Optional[str] = None
    if "@" not in text:
        username = text
    else:
        target = remote_content.parse_remote_target(text, config)
        if target.kind == remote_content.RemoteTargetKind.LOCAL:
            username = (target.username or "").lstrip("@") or None
    if username:
        with session.no_autoflush:
            local = await get_user_by_username(session, username)
        if local is not None and local.actor_url:
            return local.actor_url
    return text


async def unfollow_user(
    session: AsyncSession,
    config: SonghiveConfig,
    user: User,
    target_actor_url: str,
) -> None:
    """
    Unfollow ``target_actor_url``, raising 404 when no relationship exists.

    For local targets the pubby follower/request rows are dropped and the
    target's ``follow`` notification is retracted. For remote targets a
    signed ``Undo(Follow)`` embedding the original activity is delivered
    to the recorded inbox before the row is removed.
    """
    if not config.federation.enabled or not config.federation.instance_domain:
        raise FollowError(status_code=400, detail="Federation is not enabled")

    row = await get_follow(session, user.id, target_actor_url)
    if row is None:
        raise FollowError(status_code=404, detail="Not following this actor")

    if row.target_user_id is not None:
        storage = await asyncio.to_thread(get_federation_storage, config.database.url)
        await asyncio.to_thread(storage.remove_follower, user.actor_url or "", row.target_actor_url)
        await asyncio.to_thread(storage.remove_follow_request, user.actor_url or "", row.target_actor_url)
        target = await session.get(User, row.target_user_id)
        if target is not None:
            from ..models.notification import NotificationType
            from . import notifications as notifications_service

            await notifications_service.retract_notifications(
                session,
                user_id=target.id,
                type=NotificationType.FOLLOW,
                actor_urls=[user.actor_url or ""],
            )
    else:
        if not user.actor_url or not user.private_key_pem:
            raise FollowError(status_code=400, detail="User has no federation actor credentials")
        inbox_url = row.inbox_url or await asyncio.to_thread(
            federation_service.resolve_actor_inbox,
            row.target_actor_url,
            config,
            key_id=f"{user.actor_url}#main-key",
            private_key_pem=user.private_key_pem,
        )
        if inbox_url:
            follow_doc = {
                "@context": "https://www.w3.org/ns/activitystreams",
                "type": "Follow",
                "actor": user.actor_url,
                "object": row.target_actor_url,
            }
            if row.activity_id:
                follow_doc["id"] = row.activity_id
            _deliver(create_undo_activity(user.actor_url, follow_doc), inbox_url, user)
        else:
            logger.warning(
                "Dropping follow %s without delivering Undo(Follow): no inbox for %s",
                row.id,
                row.target_actor_url,
            )

    await session.delete(row)
    await session.flush()


async def apply_local_follow_decision(
    session: AsyncSession,
    *,
    actor_url: str,
    target_actor_url: str,
    accept: bool,
) -> bool:
    """
    Fold a local target's follow-request decision into the requester's row.

    Called by the follow-request accept/reject endpoints so a local
    requester's ``Follow`` row tracks the owner's decision — the federated
    ``Accept``/``Reject`` path only applies to remote deliveries.
    """
    row = await session.scalar(
        select(Follow).where(
            Follow.actor_url == actor_url,
            Follow.target_actor_url == target_actor_url,
            Follow.target_user_id.isnot(None),
        )
    )
    if row is None:
        return False
    if accept:
        row.state = FOLLOW_STATE_ACCEPTED
        row.accepted_at = datetime.now(timezone.utc)
    else:
        await session.delete(row)
    await session.flush()
    return True


async def apply_follow_decision(session: AsyncSession, *, activity: dict) -> bool:
    """
    Apply an inbound ``Accept``/``Reject`` answering a ``Follow`` we sent.

    The object may embed the original ``Follow`` activity (the common
    case) or be its bare id. Rows match on the deciding actor being the
    follow target and either the wrapped activity id or — for embedded
    objects — the follower's actor URL. ``Accept`` marks the row
    ``accepted``; ``Reject`` removes it. Returns whether a row matched.
    """
    if activity.get("type") not in ("Accept", "Reject"):
        return False
    actor = activity.get("actor")
    if not isinstance(actor, str) or not actor:
        return False

    obj = activity.get("object")
    follow_id: Optional[str] = None
    inner_actor: Optional[str] = None
    inner_object: Optional[str] = None
    if isinstance(obj, str):
        follow_id = obj
    elif isinstance(obj, dict):
        if obj.get("type") != "Follow":
            return False
        inner_object_raw = obj.get("object")
        if isinstance(inner_object_raw, dict):
            inner_object_raw = inner_object_raw.get("id")
        inner_object = inner_object_raw if isinstance(inner_object_raw, str) else None
        inner = obj.get("actor")
        inner_actor = inner if isinstance(inner, str) else None
        follow_id = obj.get("id") if isinstance(obj.get("id"), str) else None
    else:
        return False

    # The decision is only legitimate when issued by the followed target's
    # controlling actor. For actor follows that is the target itself; for
    # object follows (e.g. a Funkwhale library) it is the object's owning
    # actor — Funkwhale issues ``Accept`` from ``library.actor``, which
    # differs from the followed library URL.
    candidate_targets = {actor}
    if inner_object:
        candidate_targets.add(inner_object)
    rows = (await session.execute(select(Follow).where(Follow.target_actor_url.in_(candidate_targets)))).scalars()
    matched = False
    for row in rows:
        if actor != row.target_actor_url:
            object_actor = await _followed_object_actor(session, row.target_actor_url)
            # Legitimate when the deciding actor is the followed object's
            # owning actor, or when it lives on the object's instance —
            # Funkwhale answers library follows from ``library.actor``, an
            # actor id not always present in the library document.
            if not (
                (object_actor is not None and actor == object_actor)
                or (
                    inner_object == row.target_actor_url
                    and federation_service.extract_domain(actor)
                    == federation_service.extract_domain(row.target_actor_url)
                )
            ):
                continue
        if (follow_id and row.activity_id == follow_id) or (inner_actor is not None and inner_actor == row.actor_url):
            pass
        else:
            continue
        matched = True
        if activity["type"] == "Accept":
            row.state = FOLLOW_STATE_ACCEPTED
            row.accepted_at = datetime.now(timezone.utc)
        else:
            await session.delete(row)
    if matched:
        await session.flush()
    return matched


def follow_row_handle(row: Follow) -> Optional[str]:
    """Return a ``user@domain``-style handle for a followed actor."""
    actor_data = row.actor_data or {}
    username = actor_data.get("preferredUsername")
    domain = federation_service.extract_domain(row.target_actor_url)
    if username and domain:
        return f"{username}@{domain}"
    return None
