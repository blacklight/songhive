"""
Read-side follow queries — extracted from ``follows.py``.

Re-exported from ``songhive.services.follows`` so existing callers keep
working unchanged.
"""

from typing import List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.follow import FOLLOW_STATE_ACCEPTED, Follow


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
