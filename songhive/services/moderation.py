"""
Moderation domain service.

Implements the Mastodon-style moderation feature set on top of the
models in ``models/moderation.py``:

- **User-level mute/block** — a local user's personal moderation of any
  actor. Mutes only hide the target's activity for the muter; blocks are
  reciprocal cuts (the target cannot see, reach, or interact with the
  blocker) and sever any follow relationship between the two.
- **Admin-level limit/suspend** — instance-wide actions against local
  users and remote actors. Limited actors must go through follow
  approval to follow local users and their activities only federate to
  their followers; suspended actors cannot interact at all, lose every
  local follow relationship, and have their content hidden.
- **Instance-level defederate/followers-only** — per-domain policies
  layered over the configured allow/block lists. Defederation cuts
  federation both ways and hides the domain's actors; followers-only
  restricts the domain's activities to local followers.

Enforcement helpers used elsewhere:

- :func:`moderation_context` + :func:`moderation_filter` /
  :func:`activity_hidden` implement the visibility rules for list
  queries and single-activity checks;
- :func:`assert_not_suspended` / :func:`assert_interaction_allowed`
  guard activity creation and interactions;
- :func:`notification_suppressed` funnels notification creation;
- :func:`sever_relationships` wipes the follow graph for suspensions
  and blocks.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Set, cast

from fastapi import HTTPException
from sqlalchemy import and_, delete, or_, select, true
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from ..config.schema import SonghiveConfig
from ..federation.fetch import FetchError
from ..models.activity import Activity
from ..models.follow import FOLLOW_STATE_ACCEPTED, Follow
from ..models.moderation import (
    ADMIN_ACTION_LIMIT,
    ADMIN_ACTION_SUSPEND,
    INSTANCE_ACTION_DEFEDERATE,
    INSTANCE_ACTION_FOLLOWERS_ONLY,
    USER_MODERATION_BLOCK,
    AdminUserModeration,
    InstanceModeration,
    UserModeration,
)
from ..models.notification import NotificationType
from ..models.user import User
from . import federation as federation_service

logger = logging.getLogger(__name__)


class ModerationError(HTTPException):
    """A moderation operation failed with an HTTP status."""


def _user_actor_urls(user: User) -> Set[str]:
    """Return every actor identifier a local user's activities may carry."""
    urls = {f"urn:songhive:user:{user.username}"}
    if user.actor_url:
        urls.add(user.actor_url)
    return urls


def local_actor_url(user: User) -> str:
    """Return the actor identifier used for the user's own activities."""
    return user.actor_url or f"urn:songhive:user:{user.username}"


# ---------------------------------------------------------------------------
# Instance policy snapshot
# ---------------------------------------------------------------------------


async def load_instance_policies(session: AsyncSession) -> dict:
    """
    Load every ``InstanceModeration`` row and refresh the sync snapshot.

    Returns ``{domain: action}``. The snapshot installed through
    ``federation.set_db_instance_policies`` lets synchronous paths — the
    Celery delivery loop, pubby inbox adapters — apply the policies
    without an async session.
    """
    rows = (await session.execute(select(InstanceModeration))).scalars().all()
    policies = {row.domain: row.action for row in rows}
    federation_service.set_db_instance_policies(policies)
    return policies


# ---------------------------------------------------------------------------
# User-level mute/block CRUD
# ---------------------------------------------------------------------------


async def get_user_moderation(
    session: AsyncSession,
    user_id: str,
    target_actor_url: str,
    kind: Optional[str] = None,
) -> Optional[UserModeration]:
    """Return the user's moderation row on ``target_actor_url``, if any."""
    stmt = select(UserModeration).where(
        UserModeration.user_id == user_id,
        UserModeration.target_actor_url == target_actor_url,
    )
    if kind is not None:
        stmt = stmt.where(UserModeration.kind == kind)
    return await session.scalar(stmt.limit(1))


async def set_user_moderation(
    session: AsyncSession,
    user: User,
    *,
    target_actor_url: str,
    target_user_id: Optional[str],
    kind: str,
    actor_data: Optional[dict] = None,
) -> UserModeration:
    """Record (idempotently) a mute or block of an actor by ``user``."""
    row = await get_user_moderation(session, user.id, target_actor_url, kind)
    if row is not None:
        return row
    row = UserModeration(
        user_id=user.id,
        target_actor_url=target_actor_url,
        target_user_id=target_user_id,
        kind=kind,
        actor_data=actor_data,
    )
    session.add(row)
    await session.flush()
    return row


async def clear_user_moderation(
    session: AsyncSession,
    user_id: str,
    target_actor_url: str,
    kind: str,
) -> bool:
    """Remove the user's ``kind`` moderation row on the target. Returns whether one existed."""
    row = await get_user_moderation(session, user_id, target_actor_url, kind)
    if row is None:
        return False
    await session.delete(row)
    await session.flush()
    return True


async def list_user_moderations(
    session: AsyncSession,
    user_id: str,
    kind: Optional[str] = None,
) -> List[UserModeration]:
    """Return the user's moderation rows (newest first), optionally filtered by kind."""
    stmt = select(UserModeration).where(UserModeration.user_id == user_id).order_by(UserModeration.created_at.desc())
    if kind is not None:
        stmt = stmt.where(UserModeration.kind == kind)
    return list((await session.execute(stmt)).scalars())


async def user_moderation_map(
    session: AsyncSession,
    user_id: str,
    actor_urls: Iterable[str],
) -> dict:
    """Return ``{target_actor_url: {kinds}}`` for the user's moderation rows on ``actor_urls``."""
    urls = [url for url in actor_urls if url]
    if not urls:
        return {}
    rows = (
        await session.execute(
            select(UserModeration.target_actor_url, UserModeration.kind).where(
                UserModeration.user_id == user_id,
                UserModeration.target_actor_url.in_(urls),
            )
        )
    ).all()
    result: dict = {}
    for actor_url, kind in rows:
        result.setdefault(actor_url, set()).add(kind)
    return result


# ---------------------------------------------------------------------------
# Admin-level user moderation CRUD
# ---------------------------------------------------------------------------


async def get_admin_user_moderation(session: AsyncSession, actor_url: str) -> Optional[AdminUserModeration]:
    """Return the admin moderation row on ``actor_url``, if any."""
    return await session.scalar(
        select(AdminUserModeration).where(AdminUserModeration.target_actor_url == actor_url).limit(1)
    )


async def admin_user_moderation_map(session: AsyncSession, actor_urls: Iterable[str]) -> dict:
    """Return ``{target_actor_url: AdminUserModeration}`` for ``actor_urls``."""
    urls = [url for url in actor_urls if url]
    if not urls:
        return {}
    rows = (
        (await session.execute(select(AdminUserModeration).where(AdminUserModeration.target_actor_url.in_(urls))))
        .scalars()
        .all()
    )
    return {row.target_actor_url: row for row in rows}


async def set_admin_user_moderation(
    session: AsyncSession,
    *,
    target_actor_url: str,
    target_user_id: Optional[str],
    action: str,
    reason: Optional[str],
    actor_data: Optional[dict],
    admin: User,
) -> AdminUserModeration:
    """Record (idempotently) an admin limit or suspend of an actor."""
    row = await get_admin_user_moderation(session, target_actor_url)
    if row is not None:
        row.action = action
        row.reason = reason
        row.actor_data = actor_data or row.actor_data
        if target_user_id is not None:
            row.target_user_id = target_user_id
        row.created_by = admin.id
    else:
        row = AdminUserModeration(
            target_actor_url=target_actor_url,
            target_user_id=target_user_id,
            action=action,
            reason=reason,
            actor_data=actor_data,
            created_by=admin.id,
        )
        session.add(row)
    await session.flush()
    return row


async def clear_admin_user_moderation(session: AsyncSession, actor_url: str) -> Optional[AdminUserModeration]:
    """Remove the admin moderation row on ``actor_url``. Returns the removed row, if any."""
    row = await get_admin_user_moderation(session, actor_url)
    if row is None:
        return None
    await session.delete(row)
    await session.flush()
    return row


async def list_admin_user_moderations(session: AsyncSession, action: Optional[str] = None) -> List[AdminUserModeration]:
    """Return admin user-moderation rows (newest first), optionally filtered by action."""
    stmt = select(AdminUserModeration).order_by(AdminUserModeration.created_at.desc())
    if action is not None:
        stmt = stmt.where(AdminUserModeration.action == action)
    return list((await session.execute(stmt)).scalars())


async def actor_is_suspended(session: AsyncSession, actor_url: str) -> bool:
    """Return whether ``actor_url`` carries an admin suspend action."""
    return (
        await session.scalar(
            select(AdminUserModeration.id)
            .where(
                AdminUserModeration.target_actor_url == actor_url,
                AdminUserModeration.action == ADMIN_ACTION_SUSPEND,
            )
            .limit(1)
        )
        is not None
    )


async def actor_is_limited(session: AsyncSession, actor_url: str) -> bool:
    """Return whether ``actor_url`` carries an admin limit action."""
    return (
        await session.scalar(
            select(AdminUserModeration.id)
            .where(
                AdminUserModeration.target_actor_url == actor_url,
                AdminUserModeration.action == ADMIN_ACTION_LIMIT,
            )
            .limit(1)
        )
        is not None
    )


async def user_is_suspended(session: AsyncSession, user_id: str) -> bool:
    """Return whether the local user carries an admin suspend action."""
    return (
        await session.scalar(
            select(AdminUserModeration.id)
            .where(
                AdminUserModeration.target_user_id == user_id,
                AdminUserModeration.action == ADMIN_ACTION_SUSPEND,
            )
            .limit(1)
        )
        is not None
    )


async def user_is_limited(session: AsyncSession, user_id: str) -> bool:
    """Return whether the local user carries an admin limit action."""
    return (
        await session.scalar(
            select(AdminUserModeration.id)
            .where(
                AdminUserModeration.target_user_id == user_id,
                AdminUserModeration.action == ADMIN_ACTION_LIMIT,
            )
            .limit(1)
        )
        is not None
    )


# ---------------------------------------------------------------------------
# Instance moderation CRUD
# ---------------------------------------------------------------------------


async def get_instance_moderation(session: AsyncSession, domain: str) -> Optional[InstanceModeration]:
    """Return the instance moderation row for ``domain``, if any."""
    normalized = federation_service.normalize_instance_domain(domain)
    return await session.scalar(select(InstanceModeration).where(InstanceModeration.domain == normalized).limit(1))


async def set_instance_moderation(
    session: AsyncSession,
    *,
    domain: str,
    action: str,
    reason: Optional[str],
    admin: User,
) -> InstanceModeration:
    """Record (idempotently) an admin domain policy; refreshes the sync snapshot."""
    normalized = federation_service.normalize_instance_domain(domain)
    row = await session.scalar(select(InstanceModeration).where(InstanceModeration.domain == normalized).limit(1))
    if row is not None:
        row.action = action
        row.reason = reason
        row.created_by = admin.id
    else:
        row = InstanceModeration(
            domain=normalized,
            action=action,
            reason=reason,
            created_by=admin.id,
        )
        session.add(row)
    await session.flush()
    await load_instance_policies(session)
    return row


async def clear_instance_moderation(session: AsyncSession, domain: str) -> bool:
    """Remove the instance moderation row for ``domain``. Returns whether one existed."""
    row = await get_instance_moderation(session, domain)
    if row is None:
        return False
    await session.delete(row)
    await session.flush()
    await load_instance_policies(session)
    return True


async def list_instance_moderations(session: AsyncSession, action: Optional[str] = None) -> List[InstanceModeration]:
    """Return instance moderation rows (newest first), optionally filtered by action."""
    stmt = select(InstanceModeration).order_by(InstanceModeration.created_at.desc())
    if action is not None:
        stmt = stmt.where(InstanceModeration.action == action)
    return list((await session.execute(stmt)).scalars())


# ---------------------------------------------------------------------------
# Block relationships
# ---------------------------------------------------------------------------


async def block_exists_between(
    session: AsyncSession,
    user: User,
    *,
    target_actor_url: str,
    target_user_id: Optional[str] = None,
) -> bool:
    """
    Return whether a block exists between ``user`` and the target — either direction.

    Blocks are stored per direction; a relationship is "blocked" when the
    user blocked the target *or* the (local) target blocked the user.
    """
    clauses = [
        and_(
            UserModeration.user_id == user.id,
            UserModeration.target_actor_url == target_actor_url,
        )
    ]
    if target_user_id is not None:
        clauses.append(
            and_(
                UserModeration.user_id == user.id,
                UserModeration.target_user_id == target_user_id,
            )
        )
        clauses.append(
            and_(
                UserModeration.user_id == target_user_id,
                or_(
                    UserModeration.target_actor_url.in_(_user_actor_urls(user)),
                    UserModeration.target_user_id == user.id,
                ),
            )
        )
    stmt = select(UserModeration.id).where(UserModeration.kind == USER_MODERATION_BLOCK, or_(*clauses)).limit(1)
    return (await session.execute(stmt)).scalar() is not None


async def actor_blocked_by_local(
    session: AsyncSession,
    target_user_id: str,
    actor_url: str,
) -> bool:
    """Return whether the local user ``target_user_id`` has blocked ``actor_url``."""
    return (
        await session.scalar(
            select(UserModeration.id)
            .where(
                UserModeration.user_id == target_user_id,
                UserModeration.target_actor_url == actor_url,
                UserModeration.kind == USER_MODERATION_BLOCK,
            )
            .limit(1)
        )
        is not None
    )


async def blocked_actor_urls(session: AsyncSession, user: User) -> Set[str]:
    """
    Return actor URLs in a block relationship with ``user`` — either direction.

    Covers actors the user blocked plus the actor URLs of local users who
    blocked them; used to cut delivery between blocked parties.
    """
    urls: Set[str] = set(
        (
            await session.execute(
                select(UserModeration.target_actor_url).where(
                    UserModeration.user_id == user.id,
                    UserModeration.kind == USER_MODERATION_BLOCK,
                )
            )
        )
        .scalars()
        .all()
    )
    blocker_ids = set(
        (
            await session.execute(
                select(UserModeration.user_id).where(
                    UserModeration.kind == USER_MODERATION_BLOCK,
                    or_(
                        UserModeration.target_actor_url.in_(_user_actor_urls(user)),
                        UserModeration.target_user_id == user.id,
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    blocker_ids.discard(user.id)
    if blocker_ids:
        blocker_urls = (
            (await session.execute(select(User.actor_url).where(User.id.in_(blocker_ids), User.actor_url.is_not(None))))
            .scalars()
            .all()
        )
        urls.update(u for u in blocker_urls if u is not None)
    return urls


async def assert_not_suspended(session: AsyncSession, user: User) -> None:
    """Raise 403 when ``user`` is admin-suspended."""
    if await user_is_suspended(session, user.id):
        raise ModerationError(status_code=403, detail="This account is suspended")


async def assert_interaction_allowed(
    session: AsyncSession,
    author: User,
    activity: Activity,
) -> None:
    """
    Raise 403 when ``author`` may not interact with ``activity``.

    Suspended authors cannot interact at all; a block between the author
    and the activity's author — in either direction — prevents
    interaction as well.
    """
    await assert_not_suspended(session, author)

    target_actor_url = activity.source_actor or ""
    target_user_id = str(activity.owner_user_id) if activity.owner_user_id else None
    if target_user_id is not None and target_user_id == str(author.id):
        return
    if not target_actor_url and target_user_id is None:
        return
    if target_user_id is not None:
        if await user_is_suspended(session, target_user_id):
            raise ModerationError(status_code=403, detail="This activity cannot be interacted with")
    elif await actor_is_suspended(session, target_actor_url):
        raise ModerationError(status_code=403, detail="This activity cannot be interacted with")
    if await block_exists_between(
        session,
        author,
        target_actor_url=target_actor_url,
        target_user_id=target_user_id,
    ):
        raise ModerationError(status_code=403, detail="This activity cannot be interacted with")


# ---------------------------------------------------------------------------
# Visibility context
# ---------------------------------------------------------------------------


@dataclass
class ModerationContext:
    """Precomputed moderation state for activity-visibility decisions."""

    # Actors (and their local user ids) whose activities are hidden.
    hidden_actor_urls: Set[str] = field(default_factory=set)
    hidden_user_ids: Set[str] = field(default_factory=set)
    # Domains whose actors and activities are invisible entirely.
    defederated_domains: Set[str] = field(default_factory=set)
    # Remote actors/domains gated to the viewer's followed actors.
    gated_actor_urls: Set[str] = field(default_factory=set)
    gated_domains: Set[str] = field(default_factory=set)
    # The viewer's accepted outbound follows — used to satisfy the gates.
    followed_actor_urls: Set[str] = field(default_factory=set)
    # Actors in a block relationship with the viewer (either direction) —
    # for interaction guards, not visibility.
    blocked_actor_urls: Set[str] = field(default_factory=set)


def _actor_url_on_domains(actor_url: str, domains: Set[str]) -> bool:
    """Return whether ``actor_url`` sits on one of ``domains``."""
    if not actor_url or not domains:
        return False
    return federation_service.extract_domain(actor_url) in domains


async def moderation_context(session: AsyncSession, user: Optional[User]) -> ModerationContext:
    """
    Build the :class:`ModerationContext` used by visibility queries.

    Loads — in a handful of small queries — the suspended actors hidden
    from everyone, the instance policies, and, for an authenticated
    viewer, their mutes/blocks, the blocks targeting them, and their
    accepted follows.
    """
    ctx = ModerationContext()

    admin_rows = (await session.execute(select(AdminUserModeration))).scalars().all()
    for row in admin_rows:
        if row.action == ADMIN_ACTION_SUSPEND:
            ctx.hidden_actor_urls.add(row.target_actor_url)
            if row.target_user_id:
                ctx.hidden_user_ids.add(str(row.target_user_id))
        elif row.action == ADMIN_ACTION_LIMIT:
            # A limited actor's content only reaches their followers.
            ctx.gated_actor_urls.add(row.target_actor_url)

    policies = await load_instance_policies(session)
    ctx.defederated_domains = {d for d, a in policies.items() if a == INSTANCE_ACTION_DEFEDERATE}
    ctx.gated_domains = {d for d, a in policies.items() if a == INSTANCE_ACTION_FOLLOWERS_ONLY}

    if user is None:
        return ctx

    # A limited actor still sees their own content.
    ctx.gated_actor_urls.difference_update(_user_actor_urls(user))

    followed = (
        (
            await session.execute(
                select(Follow.target_actor_url).where(
                    Follow.user_id == user.id,
                    Follow.state == FOLLOW_STATE_ACCEPTED,
                )
            )
        )
        .scalars()
        .all()
    )
    ctx.followed_actor_urls = set(followed)

    own_rows = (await session.execute(select(UserModeration).where(UserModeration.user_id == user.id))).scalars().all()
    for urow in own_rows:
        ctx.hidden_actor_urls.add(urow.target_actor_url)
        if urow.target_user_id:
            ctx.hidden_user_ids.add(str(urow.target_user_id))
        if urow.kind == USER_MODERATION_BLOCK:
            ctx.blocked_actor_urls.add(urow.target_actor_url)

    # Users who blocked the viewer: their activity is hidden from the
    # viewer — a block cuts both directions of visibility.
    blocker_rows = (
        (
            await session.execute(
                select(UserModeration).where(
                    UserModeration.kind == USER_MODERATION_BLOCK,
                    or_(
                        UserModeration.target_actor_url.in_(_user_actor_urls(user)),
                        UserModeration.target_user_id == user.id,
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    blocker_ids = {row.user_id for row in blocker_rows if row.user_id != user.id}
    blocker_ids.discard(user.id)
    if blocker_ids:
        ctx.hidden_user_ids.update(str(uid) for uid in blocker_ids)
        blocker_urls = (
            (await session.execute(select(User.actor_url).where(User.id.in_(blocker_ids), User.actor_url.is_not(None))))
            .scalars()
            .all()
        )
        ctx.hidden_actor_urls.update(url for url in blocker_urls if url is not None)
        ctx.blocked_actor_urls.update(url for url in blocker_urls if url)

    return ctx


def _source_on_domain_clause(domain: str) -> ColumnElement:
    """Return a predicate matching ``Activity.source_actor`` on ``domain``."""
    return or_(
        Activity.source_actor.like(f"%://{domain}/%"),
        Activity.source_actor.like(f"%://{domain}:%"),
        Activity.source_actor.in_([f"https://{domain}", f"http://{domain}", f"https://{domain}/", f"http://{domain}/"]),
    )


def moderation_filter(ctx: ModerationContext, reveal_actor_url: Optional[str] = None) -> ColumnElement:
    """
    Return a WHERE clause excluding activities hidden by ``ctx``.

    ``reveal_actor_url`` lifts only the followers-only *gate* for that
    actor — the explicit "show anyway" opt-in on a limited profile.
    Hidden (suspended/muted/blocked) and defederated content stays
    excluded.
    """
    clauses = []
    if ctx.hidden_actor_urls:
        clauses.append(
            or_(
                Activity.source_actor.is_(None),
                ~Activity.source_actor.in_(ctx.hidden_actor_urls),
            )
        )
    if ctx.hidden_user_ids:
        clauses.append(
            or_(
                Activity.owner_user_id.is_(None),
                ~Activity.owner_user_id.in_(ctx.hidden_user_ids),
            )
        )
    for domain in ctx.defederated_domains:
        clauses.append(~_source_on_domain_clause(domain))
    gated_terms: List[ColumnElement] = []
    if ctx.gated_actor_urls:
        gated_terms.append(Activity.source_actor.in_(ctx.gated_actor_urls))
    for domain in ctx.gated_domains:
        gated_terms.append(_source_on_domain_clause(domain))
    if gated_terms:
        gated = or_(*gated_terms)
        if reveal_actor_url:
            gated = and_(gated, Activity.source_actor != reveal_actor_url)
        if ctx.followed_actor_urls:
            clauses.append(or_(~gated, Activity.source_actor.in_(ctx.followed_actor_urls)))
        else:
            clauses.append(~gated)
    return and_(*clauses) if clauses else true()


def activity_hidden(ctx: ModerationContext, activity: Activity, reveal_actor_url: Optional[str] = None) -> bool:
    """Return whether ``activity`` is hidden under ``ctx`` (single-row check)."""
    source = activity.source_actor or ""
    if source and source in ctx.hidden_actor_urls:
        return True
    if activity.owner_user_id is not None and str(activity.owner_user_id) in ctx.hidden_user_ids:
        return True
    if _actor_url_on_domains(source, ctx.defederated_domains):
        return True
    return (source in ctx.gated_actor_urls or _actor_url_on_domains(source, ctx.gated_domains)) and (
        source != reveal_actor_url and source not in ctx.followed_actor_urls
    )


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


async def notification_suppressed(
    session: AsyncSession,
    recipient: User,
    actor_url: Optional[str],
    type: Optional[NotificationType] = None,
) -> bool:
    """
    Return whether a notification from ``actor_url`` to ``recipient`` is suppressed.

    Suppression applies when the recipient is suspended (they receive
    nothing), the actor is suspended, the recipient muted or blocked the
    actor, or the actor is a local user who blocked the recipient.
    Additionally, filterable types — everything but ``REPORT`` — from
    limited actors or actors on followers-only domains are dropped when
    the recipient does not follow them, mirroring Mastodon's
    ``for_limited_accounts: drop`` notification policy and matching the
    visibility gate that already hides the underlying content.
    """
    if await user_is_suspended(session, recipient.id):
        return True
    if not actor_url:
        return False
    if await actor_is_suspended(session, actor_url):
        return True
    if await get_user_moderation(session, recipient.id, actor_url) is not None:
        return True
    actor_user_id = await session.scalar(select(User.id).where(User.actor_url == actor_url))
    if actor_user_id is not None and await block_exists_between(
        session,
        recipient,
        target_actor_url=actor_url,
        target_user_id=str(actor_user_id),
    ):
        return True
    if type != NotificationType.REPORT:
        ctx = await moderation_context(session, recipient)
        gated = actor_url in ctx.gated_actor_urls or _actor_url_on_domains(actor_url, ctx.gated_domains)
        if gated and actor_url not in ctx.followed_actor_urls:
            return True
    return False


# ---------------------------------------------------------------------------
# Relationship severing
# ---------------------------------------------------------------------------


async def _remove_follow_rows(
    session: AsyncSession,
    *,
    actor_url: str,
    user_id: Optional[str],
) -> int:
    """Delete ``follows`` rows touching the moderated actor. Returns the count."""
    clauses = [Follow.target_actor_url == actor_url]
    if user_id is not None:
        clauses.append(Follow.user_id == user_id)
        clauses.append(Follow.target_user_id == user_id)
    result = cast(CursorResult, await session.execute(delete(Follow).where(or_(*clauses))))
    return int(result.rowcount or 0)


def _remove_pubby_follow_records(storage, actor_url: str) -> int:
    """
    Remove pubby follower/request records involving ``actor_url`` — synchronous.

    Covers both directions: records where the actor follows a local user
    (``actor_id``) and remote followers of the actor when they are local
    (``target_actor_id``).
    """
    removed = 0
    for follower in list(storage.get_followers()):
        if follower.actor_id == actor_url or follower.target_actor_id == actor_url:
            storage.remove_follower(follower.actor_id, follower.target_actor_id)
            removed += 1
    for request in list(storage.get_follow_requests()):
        if request.actor_id == actor_url or request.target_actor_id == actor_url:
            storage.remove_follow_request(request.actor_id, request.target_actor_id)
            removed += 1
    return removed


async def sever_relationships(
    session: AsyncSession,
    config: SonghiveConfig,
    *,
    actor_url: str,
    user_id: Optional[str],
) -> int:
    """
    Sever every local follow relationship involving the moderated actor.

    Removes their outbound ``follows`` rows, follows targeting them, and
    the pubby follower/request records on both directions. Returns the
    number of records removed. Pubby storage is synchronous — it runs in
    a thread.
    """
    removed = await _remove_follow_rows(session, actor_url=actor_url, user_id=user_id)
    try:
        from ..federation.actors import get_federation_storage

        storage = await asyncio.to_thread(get_federation_storage, config.database.url)
        removed += await asyncio.to_thread(_remove_pubby_follow_records, storage, actor_url)
    except Exception as exc:
        logger.warning("Failed to remove pubby follow records for %s: %s", actor_url, exc)
    return removed


async def sever_block_relationship(
    session: AsyncSession,
    config: SonghiveConfig,
    blocker: User,
    *,
    target_actor_url: str,
    target_user_id: Optional[str],
) -> None:
    """
    Sever the follow relationship between ``blocker`` and the blocked target.

    The blocker's own outbound follow goes through ``unfollow_user`` so
    remote targets receive the ``Undo(Follow)``. The target's inbound
    follow on the blocker is dropped from pubby storage — local targets
    also lose their ``follows`` row.
    """
    from . import follows as follows_service

    blocker_url = blocker.actor_url or ""
    existing = await follows_service.get_follow(session, blocker.id, target_actor_url)
    if existing is not None:
        try:
            await follows_service.unfollow_user(session, config, blocker, target_actor_url)
        except Exception as exc:
            logger.info("Could not unfollow %s while blocking: %s", target_actor_url, exc)
        # The blocker's row must go even when ``unfollow_user`` cannot run
        # (federation disabled) — a block always severs the relationship.
        await session.execute(delete(Follow).where(Follow.id == existing.id))
        await session.flush()

    # The target's follow *on* the blocker: remove pubby records and, for
    # a local target, their outbound row.
    if target_user_id is not None:
        await session.execute(
            delete(Follow).where(
                Follow.user_id == target_user_id,
                Follow.target_actor_url.in_(_user_actor_urls(blocker)),
            )
        )
        await session.flush()
    try:
        from ..federation.actors import get_federation_storage

        storage = await asyncio.to_thread(get_federation_storage, config.database.url)
        await asyncio.to_thread(storage.remove_follower, target_actor_url, blocker_url)
        await asyncio.to_thread(storage.remove_follow_request, target_actor_url, blocker_url)
    except Exception as exc:
        logger.warning(
            "Failed to remove pubby follow records between %s and %s: %s",
            target_actor_url,
            blocker_url,
            exc,
        )


# ---------------------------------------------------------------------------
# Target resolution helpers for API layers
# ---------------------------------------------------------------------------


def actor_data_for_local(user: User) -> dict:
    """Build a small actor snapshot for moderation-row display fields."""
    doc: dict = {
        "preferredUsername": user.username,
        "name": user.display_name or user.username,
    }
    if user.avatar_url:
        doc["icon"] = {"type": "Image", "url": user.avatar_url}
    return doc


async def resolve_moderation_target(
    session: AsyncSession,
    config: SonghiveConfig,
    raw: str,
):
    """
    Resolve a moderation target input to ``(actor_url, local_user, actor_data)``.

    Accepts local usernames, ``@user@domain`` handles, and actor/profile
    URLs. Remote lookups go through the remote-content service; when the
    input is already an HTTP(S) actor URL that cannot be dereferenced
    (e.g. its domain is defederated), it is still returned so the
    moderation row can target it.
    """
    from . import follows as follows_service

    text = (raw or "").strip()
    if not text:
        raise ModerationError(status_code=422, detail="Moderation target is required")

    # Bare usernames and local ``@user`` handles resolve straight to the
    # users table — federation-disabled local users have no actor_url but
    # are still valid targets via their URN actor id.
    stripped = text.lstrip("@")
    if not text.startswith(("http://", "https://")) and "@" not in stripped:
        from .auth import get_user_by_username

        local = await get_user_by_username(session, stripped)
        if local is None:
            raise ModerationError(status_code=404, detail="User not found")
        return local_actor_url(local), local, actor_data_for_local(local)

    try:
        target = await follows_service._resolve_target(session, config, text)
    except HTTPException:
        if text.startswith(("http://", "https://")):
            return text, None, None
        raise
    except FetchError:
        # Undereferenceable remote actor URLs (blocked domain, fetch
        # failure) are still valid moderation/report targets.
        if text.startswith(("http://", "https://")):
            return text, None, None
        raise

    if target.local_user is not None:
        local = target.local_user
        return local_actor_url(local), local, actor_data_for_local(local)
    if target.remote_actor is not None:
        return (
            target.remote_actor.actor_url,
            None,
            follows_service._remote_actor_snapshot(target.remote_actor),
        )
    return target.actor_url, None, None
