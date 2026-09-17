"""
Webmention Celery tasks: incoming processing and outgoing delivery.

``process_incoming_webmention`` runs the library's incoming processor — source
fetch, target verification, Microformats2 parsing, and update/delete handling
— then materializes processed mentions as ``webmention`` activities (and owner
notifications) and retracts the materialized rows for removed ones.

``process_outgoing_webmentions`` renders the activity's ``h-entry`` source
page and delegates endpoint discovery plus delivery to the library's outgoing
processor. A deleted or non-public activity renders as an empty document so
previously delivered targets receive re-verification requests that resolve to
a gone source. Afterwards the activity's ``webmentions_sent`` flag is kept in
sync with the stored outgoing rows so later edits/removals still enqueue.
"""

import asyncio
import logging
from typing import Any, List

import requests

from webmentions import ContentTextFormat, Webmention, WebmentionDirection, WebmentionException

from ..config import load_config
from ..config.schema import SonghiveConfig
from ..models.activity import Activity
from ..models.base import dispose_and_reset, get_session, init_db
from ..models.user import User
from ..services.federation import extract_domain, is_domain_allowed
from ..webmentions.handler import create_webmentions_handler
from ..webmentions.render import render_outgoing_source_page
from ..webmentions.service import (
    WEBMENTION_SENT_KEY,
    activity_tag_names,
    materialize_webmention,
    outgoing_source_url,
    retract_webmention,
    webmentions_enabled,
)
from ..webmentions.storage import create_webmentions_storage
from .celery import celery_app

logger = logging.getLogger(__name__)

# Sentinel distinguishing "activity row not committed yet" (retry) from
# "nothing to send" (terminal).
_MISSING = object()


async def _apply_incoming(
    config: SonghiveConfig,
    processed: List[Webmention],
    deleted: List[Webmention],
) -> None:
    """Materialize processed mentions and retract removed ones."""
    try:
        async with get_session() as session:
            for mention in processed:
                try:
                    await materialize_webmention(session, mention, config)
                except Exception:
                    logger.exception("Failed to materialize webmention %s -> %s", mention.source, mention.target)
                    await session.rollback()
            for mention in deleted:
                try:
                    await retract_webmention(session, mention)
                except Exception:
                    logger.exception("Failed to retract webmention %s -> %s", mention.source, mention.target)
                    await session.rollback()
            await session.commit()
    finally:
        await dispose_and_reset()


@celery_app.task(
    bind=True,
    name="songhive.tasks.webmentions.process_incoming",
    autoretry_for=(requests.RequestException,),
    retry_backoff=True,
    max_retries=3,
)
def process_incoming_webmention(_, source: str, target: str) -> None:
    """Validate, fetch, and apply an incoming Webmention."""
    config = load_config([])
    if not webmentions_enabled(config):
        return
    source_domain = extract_domain(source)
    if source_domain and not is_domain_allowed(source_domain, config):
        logger.info("Dropping webmention from blocked domain %s", source_domain)
        return

    storage = create_webmentions_storage(config.database.url)
    processed: List[Webmention] = []
    deleted: List[Webmention] = []
    handler = create_webmentions_handler(
        config,
        storage,
        on_mention_processed=processed.append,
        on_mention_deleted=deleted.append,
    )
    try:
        handler.process_incoming_webmention(source, target)
    except WebmentionException as exc:
        logger.info("Rejected webmention %s -> %s: %s", source, target, exc.message)
        return
    except Exception:
        logger.exception("Failed to process webmention %s -> %s", source, target)
        return

    if not (processed or deleted):
        return
    init_db(config.database.url)
    asyncio.run(_apply_incoming(config, processed, deleted))


async def _prepare_outgoing(config: SonghiveConfig, activity_id: str) -> Any:
    """Load the activity and build ``(source_url, html)`` for delivery.

    Returns ``_MISSING`` when the row does not exist yet (the enqueueing
    request may still be committing — the task retries), and ``None`` when
    the activity is not a local one. Deleted or non-public activities render
    as an empty document so stored targets receive removal notifications.
    """
    try:
        async with get_session() as session:
            activity = await session.get(Activity, activity_id)
            if activity is None:
                return _MISSING
            if activity.source_type != "local":
                return None
            source_url = outgoing_source_url(config, str(activity.id))
            if activity.deleted_at is not None or activity.visibility != "public":
                return source_url, ""
            author = await session.get(User, activity.owner_user_id) if activity.owner_user_id else None
            tag_names = await activity_tag_names(session, activity)
            return source_url, render_outgoing_source_page(activity, author, config, tag_names=tag_names)
    finally:
        await dispose_and_reset()


async def _finalize_outgoing(config: SonghiveConfig, activity_id: str, sent: bool) -> None:
    """Record whether the activity has stored outgoing webmention targets."""
    try:
        async with get_session() as session:
            activity = await session.get(Activity, activity_id)
            if activity is None:
                return
            payload = dict(activity.payload or {})
            if bool(payload.get(WEBMENTION_SENT_KEY)) == sent:
                return
            payload[WEBMENTION_SENT_KEY] = sent
            activity.payload = payload
            await session.commit()
    finally:
        await dispose_and_reset()


@celery_app.task(bind=True, name="songhive.tasks.webmentions.process_outgoing", max_retries=3)
def process_outgoing_webmentions(self, activity_id: str) -> None:
    """Discover remote endpoints and deliver Webmentions for an activity's URLs."""
    config = load_config([])
    if not webmentions_enabled(config):
        return
    init_db(config.database.url)

    prepared = asyncio.run(_prepare_outgoing(config, activity_id))
    if prepared is _MISSING:
        # The enqueueing transaction may not have committed yet.
        raise self.retry(countdown=5)
    if prepared is None:
        return
    source_url, text = prepared

    storage = create_webmentions_storage(config.database.url)
    handler = create_webmentions_handler(config, storage)
    try:
        handler.process_outgoing_webmentions(source_url, text=text, text_format=ContentTextFormat.HTML)
    except Exception:
        logger.exception("Failed to process outgoing webmentions for activity %s", activity_id)
        return

    try:
        sent = bool(storage.retrieve_webmentions(source_url, direction=WebmentionDirection.OUT))
    except Exception:
        sent = False
    # ``_prepare_outgoing`` disposed the engine globals — re-init them.
    init_db(config.database.url)
    asyncio.run(_finalize_outgoing(config, activity_id, sent))
