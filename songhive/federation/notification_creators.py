"""Inbox notification creation — follow, quote, reply, mention."""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.notification import Notification, NotificationType
from ..models.user import FollowersApproval, User
from ..services import federation as federation_service
from ..services.activities import (
    _actor_doc_avatar_url,
    _actor_doc_display_name,
)
from ..services.notifications import (
    _push_updated,
    create_notification,
)
from .notification_helpers import (
    _activity_audience_urls,
    _actor_name,
    _create_or_update_quote_notification,
    _note_snapshot,
    _object_is_public,
    _resolve_local_object,
    _target_owner_users,
)

logger = logging.getLogger(__name__)


async def resolve_inbox_recipients(
    session: AsyncSession,
    *,
    activity: dict,
    instance_domain: Optional[str] = None,
) -> List[User]:
    """
    Return the local users an inbound activity addresses or targets.

    Used for shared-inbox deliveries (``/ap/inbox``), where no single
    recipient username is known: recipients are the local users found in
    the activity's audience fields — ``to``/``cc`` addressees, ``Mention``
    tag targets, a string ``object`` such as a Follow target — plus the
    local owners of the objects it targets (``inReplyTo``, quote, or
    Like/Announce object). Owner-via-target resolution is skipped for
    non-public ``Create`` objects so a restricted reply cannot notify a
    user outside its audience.
    """
    candidates = _activity_audience_urls(activity)
    if not candidates:
        return []
    rows = await session.execute(select(User).where(User.actor_url.in_(candidates)))
    recipients = list(rows.scalars().all())
    seen = {str(user.id) for user in recipients}
    obj = activity.get("object")
    resolve_target_owners = activity.get("type") != "Create" or _object_is_public(obj)
    if resolve_target_owners:
        for user in await _target_owner_users(session, candidates, instance_domain=instance_domain):
            if str(user.id) not in seen:
                seen.add(str(user.id))
                recipients.append(user)

    # Suspended local users receive no inbound activity.
    from ..services import moderation as moderation_service

    recipients = [user for user in recipients if not await moderation_service.user_is_suspended(session, user.id)]
    return recipients


async def _create_follow_inbox_notification(
    session: AsyncSession,
    *,
    actor_url: str,
    recipient: User,
    payload: Dict[str, Any],
    obj: Optional[Any] = None,
    instance_domain: Optional[str] = None,
    storage: Optional[Any] = None,
) -> None:
    """
    Create notifications for an incoming ``Follow`` activity.

    The Follow object is normally the followed actor (the recipient);
    object-scoped Follows (FEP-efda thread subscriptions, e.g. Friendica)
    target a local object instead. Object follows carry the resolved
    ``target_*`` fields so clients can render "followed your post", and only
    the object's owner is notified — a personal-inbox delivery to someone else
    is misaddressed.

    When the recipient's approval policy held the follow as a pending
    request, the payload carries ``follow_request_pending`` so clients can
    render accept/reject controls. Rejected follows produce no
    notification.
    """
    target = obj if isinstance(obj, str) else obj.get("id") if isinstance(obj, dict) else None
    actor_follow = not (isinstance(target, str) and target) or target == recipient.actor_url
    if actor_follow:
        if recipient.followers_approval == FollowersApproval.REJECT.value:
            return
        if storage is not None:
            request = await asyncio.to_thread(storage.get_follow_request, actor_url, recipient.actor_url or "")
            if request is not None:
                payload = {**payload, "follow_request_pending": True}
    elif isinstance(target, str) and target:
        resolved = await _resolve_local_object(session, target, instance_domain)
        if resolved is not None:
            owners = await _target_owner_users(session, {target}, instance_domain=instance_domain)
            if not any(str(owner.id) == str(recipient.id) for owner in owners):
                return
            payload = {
                **payload,
                "target_url": target,
                **{f"target_{key}": value for key, value in resolved.items()},
            }

    await create_notification(
        session,
        user_id=recipient.id,
        type=NotificationType.FOLLOW,
        actor_url=actor_url,
        source_url=actor_url,
        payload=payload,
    )


async def resolve_follow_request_notification(
    session: AsyncSession,
    *,
    recipient: User,
    actor_url: str,
    status: str,
) -> Optional[Notification]:
    """
    Mark a pending follow-request notification as resolved.

    The requester's ``follow`` notification carrying
    ``follow_request_pending`` is rewritten with
    ``follow_request_status`` set to ``accepted`` or ``rejected`` so
    clients can drop the action buttons, and the change is pushed to live
    clients. Returns the updated row, ``None`` when none is pending.
    """
    rows = await session.execute(
        select(Notification).where(
            Notification.user_id == recipient.id,
            Notification.type == NotificationType.FOLLOW,
            Notification.actor_url == actor_url,
        )
    )
    for notification in rows.scalars():
        payload = dict(notification.payload or {})
        if not payload.pop("follow_request_pending", None):
            continue
        payload["follow_request_status"] = status
        notification.payload = payload
        await session.flush()
        _push_updated({str(recipient.id): [notification]})
        return notification
    return None


async def _create_quote_inbox_notification(
    session: AsyncSession,
    *,
    actor_url: str,
    recipient: User,
    activity: Dict[str, Any],
    payload: Dict[str, Any],
    activity_id: Optional[str] = None,
    obj: Optional[Any] = None,
    instance_domain: Optional[str] = None,
) -> None:
    """
    FEP-044f: ``object`` is the quoted post, ``instrument`` the
    quoting post (a bare id, or the embedded object when the remote
    server includes it). Pubby auto-approves the request; this
    notification is what tells the quoted post's owner it happened —
    the quote's own ``Create`` may never reach this inbox. The
    instrument id is used as ``source_url`` so a later ``Create``
    notification for the same note dedupes onto this row while it
    stays unseen.
    """

    quoted_uri = obj if isinstance(obj, str) else obj.get("id") if isinstance(obj, dict) else None
    if not isinstance(quoted_uri, str) or not quoted_uri:
        return
    resolved = await _resolve_local_object(session, quoted_uri, instance_domain)
    if resolved is None:
        # The quoted object is unknown — the request does not concern
        # a local post of this recipient.
        return
    owners = await _target_owner_users(session, {quoted_uri}, instance_domain=instance_domain)
    if not any(str(owner.id) == str(recipient.id) for owner in owners):
        # The quoted object is local but belongs to someone else — the
        # request was misaddressed to this recipient's inbox.
        return
    instrument = activity.get("instrument")
    instrument_doc = instrument if isinstance(instrument, dict) else {}
    quoting_id = instrument_doc.get("id") if instrument_doc else instrument
    source_url = quoting_id if isinstance(quoting_id, str) and quoting_id else activity_id
    target_fields = {f"target_{key}": value for key, value in resolved.items()}
    await _create_or_update_quote_notification(
        session,
        recipient=recipient,
        actor_url=actor_url,
        source_url=source_url,
        payload={
            **payload,
            **_note_snapshot(instrument_doc),
            "target_url": quoted_uri,
            **target_fields,
        },
    )


async def _process_incoming_quote_notification(
    session: AsyncSession,
    *,
    actor_url: str,
    recipient: User,
    payload: Dict[str, Any],
    quote_target: str,
    source_url: Optional[str],
    instance_domain: Optional[str],
    note: Dict[str, Any],
    self_fields: Dict[str, Any],
) -> bool:
    """
    FEP-044f: ``object`` is the quoted post, ``instrument`` the
    quoting post (a bare id, or the embedded object when the remote
    server includes it). Pubby auto-approves the request; this
    notification is what tells the quoted post's owner it happened —
    the quote's own ``Create`` may never reach this inbox. The
    instrument id is used as ``source_url`` so a later ``Create``
    notification for the same note dedupes onto this row while it
    stays unseen.
    """
    resolved = await _resolve_local_object(session, quote_target, instance_domain)
    owners = await _target_owner_users(session, {quote_target}, instance_domain=instance_domain)
    if resolved is not None and any(str(owner.id) == str(recipient.id) for owner in owners):
        # Only the quoted post's owner gets "quoted your post" — a
        # quote of someone else's post that merely tags the recipient
        # stays a ``mention``.
        target_fields = {f"target_{key}": value for key, value in resolved.items()}
        return (
            await _create_or_update_quote_notification(
                session,
                recipient=recipient,
                actor_url=actor_url,
                source_url=source_url,
                payload={
                    **payload,
                    **note,
                    **self_fields,
                    "target_url": quote_target,
                    **target_fields,
                },
            )
            is not None
        )

    return False


async def _process_incoming_reply_notification(
    session: AsyncSession,
    *,
    actor_url: str,
    recipient: User,
    payload: Dict[str, Any],
    in_reply_to: str,
    source_url: Optional[str],
    instance_domain: Optional[str],
    note: Dict[str, Any],
    self_fields: Dict[str, Any],
) -> bool:
    """
    FEP-044f: ``object`` is the replied-to post, ``instrument`` the
    replying post (a bare id, or the embedded object when the remote
    server includes it). Pubby auto-approves the request; this
    notification is what tells the replied-to post's owner it happened
    — the reply's own ``Create`` may never reach this inbox. The
    instrument id is used as ``source_url`` so a later ``Create``
    notification for the same note dedupes onto this row while it
    stays unseen.
    """

    resolved = await _resolve_local_object(session, in_reply_to, instance_domain)
    owners = await _target_owner_users(session, {in_reply_to}, instance_domain=instance_domain)
    if resolved is not None and any(str(owner.id) == str(recipient.id) for owner in owners):
        # Only the replied-to post's owner gets "replied to your
        # post" — a reply to someone else's post that merely tags
        # the recipient stays a ``mention``.
        target_fields = {f"target_{key}": value for key, value in resolved.items()}
        return (
            await create_notification(
                session,
                user_id=recipient.id,
                type=NotificationType.REPLY,
                actor_url=actor_url,
                source_url=source_url,
                payload={
                    **payload,
                    **note,
                    **self_fields,
                    "target_url": in_reply_to,
                    **target_fields,
                },
            )
            is not None
        )

    return False


async def create_inbox_notifications(
    session: AsyncSession,
    *,
    activity: dict,
    recipient: User,
    actor_doc: Optional[dict] = None,
    instance_domain: Optional[str] = None,
    storage: Optional[Any] = None,
) -> None:
    """
    Create notifications for an incoming federated activity.

    ``recipient`` is the local user whose inbox was addressed. Unknown or
    unsupported activity types are ignored.

    ``actor_doc`` is the actor's cached ActivityPub document (populated by
    the inbox signature verification); its ``name``/``icon`` enrich the
    payload's ``actor_display_name``/``actor_avatar_url`` so clients can
    render a user card. ``instance_domain`` enables resolving the activity's
    object to a local item (``item_*``/``local_url`` payload fields).
    ``storage`` is the pubby backend used to flag follow notifications
    whose request is still pending approval.
    """
    actor_url = activity.get("actor")
    if not isinstance(actor_url, str) or not actor_url:
        return

    activity_id = activity.get("id")
    if not isinstance(activity_id, str) or not activity_id:
        activity_id = None

    payload = _actor_payload(actor_url, actor_doc, activity_id)
    await _dispatch_inbox_activity(
        session,
        activity=activity,
        activity_id=activity_id,
        actor_url=actor_url,
        recipient=recipient,
        payload=payload,
        instance_domain=instance_domain,
        storage=storage,
    )


async def _dispatch_inbox_activity(
    session: AsyncSession,
    *,
    activity: dict,
    activity_id: Optional[str],
    actor_url: str,
    recipient: User,
    payload: dict,
    instance_domain: Optional[str],
    storage: Optional[Any],
) -> None:
    """Route the incoming activity to the matching notification creator."""
    activity_type = activity.get("type")
    base: Dict[str, Any] = {
        "actor_url": actor_url,
        "recipient": recipient,
        "payload": payload,
        "obj": activity.get("object"),
        "instance_domain": instance_domain,
    }

    if activity_type == "Follow":
        await _create_follow_inbox_notification(session, **base, storage=storage)
        return

    if activity_type in ("Like", "Announce"):
        from .notification_lifecycle import _notify_like_announce

        await _notify_like_announce(session, **base, activity_type=activity_type)
        return

    if activity_type == "QuoteRequest":
        await _create_quote_inbox_notification(session, **base, activity=activity, activity_id=activity_id)
        return

    if activity_type != "Create" or not isinstance(base["obj"], dict):
        return

    from .notification_lifecycle import _handle_inbox_create

    await _handle_inbox_create(session, **base)


def _actor_payload(actor_url: str, actor_doc: Optional[dict], activity_id: Optional[str]) -> Dict[str, Any]:
    """Base notification payload enriched from the cached actor document."""
    payload: Dict[str, Any] = {"actor_name": _actor_name(actor_url, actor_doc), "activity_id": activity_id}
    actor_handle = federation_service.actor_doc_handle(actor_doc, actor_url)
    if actor_handle:
        payload["actor_handle"] = actor_handle
    if isinstance(actor_doc, dict):
        display_name = _actor_doc_display_name(actor_doc)
        avatar_url = _actor_doc_avatar_url(actor_doc)
        if display_name:
            payload["actor_display_name"] = display_name
        if avatar_url:
            payload["actor_avatar_url"] = avatar_url
    return payload
