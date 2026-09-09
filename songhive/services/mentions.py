"""
Mention service: extract, resolve, and render ``@handle`` mentions.

Bare ``@user`` handles resolve against the local ``users`` table;
``@user@domain`` handles are resolved through Pubby's WebFinger client
(:func:`pubby.resolve_actor_url`) after the domain passes the configured
federation allow/block lists.  Rendering produces safe HTML via
``pubby.content`` helpers — mention handles become anchors while the
surrounding text is escaped and its URLs and ``#tags`` linkified.
"""

import asyncio
import html
import re
from dataclasses import dataclass
from functools import partial
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from pubby import resolve_actor_url
from pubby.content import (
    RenderedContent,
    build_hashtag_tags,
    render_link_anchor,
    render_post_html,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config.schema import SonghiveConfig
from ..federation import get_actor_url, get_tag_url
from ..models.user import User
from .federation import is_domain_blocked, normalize_instance_domain

__all__ = [
    "MENTION_REGEX",
    "ProcessedMentions",
    "ResolvedMention",
    "extract_mentions",
    "tag_url_factory",
    "process_mentions",
    "render_mentions",
    "resolve_mention",
    "resolve_mentions",
]

# Matches ``@user`` and ``@user@domain`` handles in free text.  The
# username charset mirrors the local ``USERNAME_PATTERN`` plus ``.`` for
# remote conventions; the ``(?<![\w@/])`` lookbehind keeps email addresses,
# handles inside URLs, and ``@@`` sequences from matching.
MENTION_REGEX = re.compile(r"(?<![\w@/])@([a-zA-Z0-9_.-]+(?:@[a-zA-Z0-9.\-]+)?)\b")


@dataclass(frozen=True)
class ResolvedMention:
    """
    A mention handle resolved to a local user and/or a remote actor URL.

    ``user_id`` is set when the handle resolved to a local user;
    ``actor_url`` carries the ActivityPub actor URL used for rendering,
    tagging, and delivery.
    """

    handle: str
    actor_url: Optional[str] = None
    user_id: Optional[str] = None

    def as_dict(self) -> dict:
        """Return the ``mentions=[...]`` row shape ``create_local_activity`` expects."""
        return {
            "handle": self.handle,
            "actor_url": self.actor_url,
            "user_id": self.user_id,
        }

    def to_tag(self) -> Optional[dict]:
        """Return an ActivityPub ``Mention`` tag, or ``None`` without an actor URL."""
        if not self.actor_url:
            return None
        return {"type": "Mention", "href": self.actor_url, "name": self.handle}


@dataclass(frozen=True)
class ProcessedMentions:
    """
    The result of running the mention pipeline over a piece of content.

    ``mentions`` feeds ``activity_mentions`` rows (via
    ``create_local_activity(mentions=[m.as_dict() for m in ...])``), ``html``
    is the rendered ``content``, ``tag_names`` the normalized tag names
    found in the text, and ``tags`` the ActivityPub ``tag`` list (``Mention``
    tags first, then ``Hashtag`` tags).
    """

    mentions: List[ResolvedMention]
    html: str
    tag_names: List[str]
    tags: List[dict]


def extract_mentions(text: str) -> List[str]:
    """
    Extract ``@handle`` mentions from ``text``.

    Returns the handles (including the leading ``@``) in first-seen order,
    deduplicated case-insensitively.
    """
    seen: set[str] = set()
    handles: List[str] = []
    for match in MENTION_REGEX.finditer(text or ""):
        handle = match.group(0)
        if handle.lower() in seen:
            continue
        seen.add(handle.lower())
        handles.append(handle)
    return handles


def _split_handle(handle: str) -> Tuple[str, Optional[str]]:
    """Split a handle into ``(username, domain)``; ``domain`` is ``None`` for local handles."""
    body = handle[1:] if handle.startswith("@") else handle
    if "@" not in body:
        return body, None
    username, domain = body.split("@", 1)
    return username, domain


def _local_actor_url(user: User, domain: str) -> Optional[str]:
    """Return the user's actor URL, deriving the canonical local URL when the
    user has not been provisioned yet."""
    if user.actor_url:
        return user.actor_url
    return get_actor_url(domain, user.username) if domain else None


async def _resolve_local_mention(
    session: AsyncSession,
    username: str,
    domain: str,
) -> Optional[ResolvedMention]:
    """Resolve a bare ``@user`` handle against local users."""
    result = await session.execute(select(User).where(func.lower(User.username) == username.lower()))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        return None
    return ResolvedMention(
        handle=f"@{user.username}",
        actor_url=_local_actor_url(user, domain),
        user_id=user.id,
    )


def _is_local_domain(domain: str, config: SonghiveConfig) -> bool:
    """Return whether ``domain`` refers to this instance."""
    instance_domain = (config.federation.instance_domain or "").strip()
    if not instance_domain:
        return False
    return normalize_instance_domain(domain) == normalize_instance_domain(instance_domain)


def _can_resolve_remote(config: SonghiveConfig, domain: str) -> bool:
    """
    Return whether a remote ``domain`` may be resolved via WebFinger.

    Remote resolution requires federation to be enabled, a dotted domain
    (matching Pubby's mention semantics), and a domain that is not blocked or
    excluded by the allow-list.
    """
    if not config.federation.enabled or "." not in domain:
        return False
    return not is_domain_blocked(domain, config)


async def resolve_mention(
    session: AsyncSession,
    handle: str,
    config: SonghiveConfig,
    *,
    timeout: int = 10,
) -> Optional[ResolvedMention]:
    """
    Resolve a single ``@handle`` mention to a local user or remote actor URL.

    Bare ``@user`` handles — and ``@user@domain`` handles whose domain is this
    instance's — resolve against the local ``users`` table.  Other domains are
    resolved via ``pubby.resolve_actor_url`` in a thread (the lookup is
    synchronous and ``requests``-based); blocked or non-federated handles are
    dropped.  A failed WebFinger lookup yields Pubby's conventional fallback
    actor URL (``https://{domain}/@{username}``), never an exception.
    """
    username, domain = _split_handle(handle)
    if domain is None or _is_local_domain(domain, config):
        return await _resolve_local_mention(session, username, (config.federation.instance_domain or "").strip())
    if not _can_resolve_remote(config, domain):
        return None
    actor_url = await asyncio.to_thread(resolve_actor_url, username, domain, timeout=timeout)
    return ResolvedMention(handle=f"@{username}@{domain}", actor_url=actor_url)


async def resolve_mentions(
    session: AsyncSession,
    text: str,
    config: SonghiveConfig,
    *,
    timeout: int = 10,
) -> List[ResolvedMention]:
    """
    Extract and resolve every mention in ``text``.

    Local lookups run sequentially on ``session``; remote WebFinger lookups
    are fanned out with ``asyncio.gather`` so several remote handles do not
    serialize on each other's timeouts.  Returns the resolved mentions in
    first-seen order, dropping handles that resolve to nothing.
    """
    resolved: Dict[str, Optional[ResolvedMention]] = {}
    remote: List[Tuple[str, str, str]] = []
    instance_domain = (config.federation.instance_domain or "").strip()

    for handle in extract_mentions(text):
        key = handle.lower()
        username, domain = _split_handle(handle)
        if domain is None or _is_local_domain(domain, config):
            resolved[key] = await _resolve_local_mention(session, username, instance_domain)
        elif _can_resolve_remote(config, domain):
            remote.append((key, username, domain))
        else:
            resolved[key] = None

    if remote:
        actor_urls = await asyncio.gather(
            *(asyncio.to_thread(resolve_actor_url, username, domain, timeout=timeout) for _, username, domain in remote)
        )
        for (key, username, domain), actor_url in zip(remote, actor_urls):
            resolved[key] = ResolvedMention(handle=f"@{username}@{domain}", actor_url=actor_url)

    mentions: List[ResolvedMention] = []
    emitted: set[str] = set()
    for handle in extract_mentions(text):
        mention = resolved[handle.lower()]
        if mention is None or mention.handle.lower() in emitted:
            continue
        emitted.add(mention.handle.lower())
        mentions.append(mention)
    return mentions


def tag_url_factory(domain: str) -> Callable[[str], str]:
    """Return a ``tag_url`` callback for ``pubby.content`` renderers."""
    if domain:
        return partial(get_tag_url, domain)
    return lambda name: f"/tags/{name}"


def render_mentions(
    text: str,
    mentions: Iterable[ResolvedMention],
    *,
    domain: str = "",
) -> RenderedContent:
    """
    Render ``text`` as safe HTML with mention handles linked to actor URLs.

    Resolved mentions become anchors via ``pubby.render_link_anchor``; the
    text between them is rendered by ``pubby.render_post_html`` (escaped, with
    URLs and ``#tags`` linkified).  Unresolved handles — and resolved
    mentions without an actor URL — are emitted as inert escaped text.
    ``domain`` is the instance domain used to build tag links.
    """
    by_handle = {mention.handle.lower(): mention for mention in mentions}
    tag_url = tag_url_factory(domain)
    parts: List[str] = []
    tags: List[str] = []
    seen: set[str] = set()
    pos = 0

    def _render_segment(segment: str) -> None:
        rendered = render_post_html(segment, tag_url)
        parts.append(rendered.html)
        for name in rendered.hashtags:
            if name not in seen:
                seen.add(name)
                tags.append(name)

    for match in MENTION_REGEX.finditer(text or ""):
        _render_segment(text[pos : match.start()])
        pos = match.end()
        handle = match.group(0)
        mention = by_handle.get(handle.lower())
        if mention is not None and mention.actor_url:
            parts.append(render_link_anchor(mention.actor_url, label=handle))
        else:
            parts.append(html.escape(handle))

    _render_segment("" if text is None else text[pos:])
    return RenderedContent("".join(parts), tags)


async def process_mentions(
    session: AsyncSession,
    text: str,
    config: SonghiveConfig,
    *,
    timeout: int = 10,
) -> ProcessedMentions:
    """
    Extract, resolve, and render the mentions in ``text``.

    This is the single entry point activity creation and editing call before
    persisting or federating content: it returns the resolved mentions (for
    ``activity_mentions`` rows), the rendered ``html`` (for ``content``), the
    extracted ``tag_names``, and the ActivityPub ``tags`` (``Mention`` tags for
    resolved actors plus ``Hashtag`` tags for linkified tags).
    """
    mentions = await resolve_mentions(session, text, config, timeout=timeout)
    domain = (config.federation.instance_domain or "").strip()
    rendered = render_mentions(text, mentions, domain=domain)

    tags = [tag for mention in mentions if (tag := mention.to_tag()) is not None]
    tags.extend(build_hashtag_tags(rendered.hashtags, tag_url_factory(domain)))

    return ProcessedMentions(
        mentions=mentions,
        html=rendered.html,
        tag_names=rendered.hashtags,
        tags=tags,
    )
