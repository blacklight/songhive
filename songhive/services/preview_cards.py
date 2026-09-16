"""
Preview card service: extract the first link from activity content and
fetch/cache its OpenGraph metadata.

Mirroring Mastodon, only the first pure URL in a post is sent to the card
pipeline — mention and hashtag anchors are never candidates. Local posts
extract URLs from their raw ``content_source`` (mention/hashtag links are
generated markup and never appear in the source); remote posts parse the
anchors in the stored HTML ``content``, skipping ``mention``/``hashtag``
classes and ``rel="tag"`` links.

Fetched metadata is stored in a ``preview_cards`` row keyed by the
(normalized) URL and shared across activities — the card is re-fetched only
when the cached row is older than ``PREVIEW_CARD_MAX_AGE`` at post time,
never on view. When the page yields no usable metadata the card falls back
to ``<title>`` and finally to the URL's domain.

The remote fetch is guarded: only ``http(s)`` URLs whose host does not
resolve to a private/reserved address are requested, redirects are
re-validated hop by hop, and the response body is capped at
``MAX_DOCUMENT_BYTES``.
"""

import asyncio
import html
import ipaddress
import logging
import re
import socket
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urldefrag, urljoin, urlparse

import requests
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_default_user_agent
from ..models.activity import Activity
from ..models.preview_card import PreviewCard
from ..models.user import User
from .settings import get_setting

logger = logging.getLogger(__name__)

# Cards older than this are re-fetched when a new post links the same URL.
PREVIEW_CARD_MAX_AGE = timedelta(hours=24)
FETCH_TIMEOUT = (5.0, 10.0)
MAX_REDIRECTS = 5
MAX_DOCUMENT_BYTES = 1024 * 1024
MAX_TITLE_LENGTH = 512
MAX_DESCRIPTION_LENGTH = 1000

# Bare http(s) URLs in raw post text.  ``content_source`` is the author's
# raw text — it never contains the rendered mention/hashtag anchors, so
# anything matched here is a URL the author actually typed.
_URL_RE = re.compile(r'https?://[^\s<>"\']+')

# Anchor classes that mark a link as a mention or hashtag rather than a
# content URL (Mastodon emits ``u-url mention``/``mention hashtag``).
_NON_CARD_CLASSES = ("mention", "hashtag", "u-url")


def _clean_text(value: str, limit: int) -> str:
    """Collapse whitespace runs and cap ``value`` at ``limit`` characters."""
    return re.sub(r"\s+", " ", value).strip()[:limit]


def _normalize_url(url: str) -> Optional[str]:
    """Return the canonical fetch key for ``url``, or ``None`` when unusable."""
    url, _ = urldefrag(url.strip().rstrip(".,;:!?'\""))
    # Unbalanced trailing ``)`` is almost always the closing paren of a
    # ``[text](url)`` Markdown link or prose wrap, not part of the URL.
    while url.endswith(")") and url.count(")") > url.count("("):
        url = url[:-1]
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or len(url) > 2048:
        return None
    return url


def _host_is_public(hostname: str) -> bool:
    """Return whether every resolved address of ``hostname`` is globally routable."""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        # Unresolvable hosts cannot be fetched and are not an SSRF risk.
        return True
    try:
        addresses = {ipaddress.ip_address(info[4][0]) for info in infos}
    except ValueError:
        return False
    return all(ip.is_global for ip in addresses) if addresses else False


def _url_allowed(url: str) -> bool:
    """Return whether fetching ``url`` is safe (http(s), public host)."""
    parsed = urlparse(url)
    hostname: Optional[str] = parsed.hostname
    if parsed.scheme not in ("http", "https") or not hostname:
        return False
    try:
        return ipaddress.ip_address(hostname).is_global
    except ValueError:
        pass
    return _host_is_public(hostname)


class _DoneParsing(Exception):
    """Raised to abort head parsing once ``<body>`` (or ``</head>``) is reached."""


class _HeadParser(HTMLParser):
    """Collect ``<meta>`` candidates and the ``<title>`` from a document head."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title_parts: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag == "body":
            raise _DoneParsing
        if tag == "title":
            self._in_title = True
            return
        if tag != "meta":
            return
        attr = dict(attrs)
        key = (attr.get("property") or attr.get("name") or "").strip().lower()
        content = attr.get("content")
        if key and content is not None and key not in self.meta:
            self.meta[key] = content

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "head":
            raise _DoneParsing

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)


def _parse_head(document: str) -> _HeadParser:
    """Parse the ``<head>`` of ``document``, tolerating malformed markup."""
    parser = _HeadParser()
    try:
        parser.feed(document)
    except _DoneParsing:
        pass
    except Exception as e:
        logger.debug("Unparseable preview document: %s", e, exc_info=True)
    return parser


def _meta(head: _HeadParser, *names: str) -> Optional[str]:
    """Return the first non-empty meta value among ``names``."""
    for name in names:
        value = head.meta.get(name)
        if value is not None and value.strip():
            return html.unescape(value)
    return None


def _fetch_document(url: str) -> tuple[str, Optional[str], str]:
    """
    Fetch ``url`` following redirects, returning ``(final_url, body, content_type)``.

    Each redirect hop is re-validated against the private-address guard so a
    public URL cannot redirect the fetch into the internal network. The body
    is ``None`` for non-2xx responses and capped at ``MAX_DOCUMENT_BYTES``.
    """
    session = requests.Session()
    current = url
    try:
        for _ in range(MAX_REDIRECTS + 1):
            if not _url_allowed(current):
                raise ValueError(f"Refusing to fetch non-public URL: {current}")
            response = session.get(
                current,
                timeout=FETCH_TIMEOUT,
                allow_redirects=False,
                stream=True,
                headers={
                    "User-Agent": f"{get_default_user_agent()} (preview card)",
                    "Accept": "text/html,application/xhtml+xml,*/*;q=0.1",
                },
            )
            try:
                if response.is_redirect or response.is_permanent_redirect:
                    location = response.headers.get("Location")
                    if not location:
                        return current, None, ""
                    current = urljoin(current, location)
                    continue
                if response.status_code != 200:
                    return current, None, ""
                content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_content(64 * 1024):
                    chunks.append(chunk)
                    size += len(chunk)
                    if size >= MAX_DOCUMENT_BYTES:
                        break
                body = b"".join(chunks)[:MAX_DOCUMENT_BYTES]
                return current, body.decode(response.encoding or "utf-8", errors="replace"), content_type
            finally:
                response.close()
    finally:
        session.close()
    return current, None, ""


def fetch_card_data(url: str) -> Optional[dict]:
    """
    Fetch ``url`` and return the card fields for it, or ``None``.

    ``None`` means the URL should not get a card at all (unusable or
    refused by the private-address guard). Any other failure — network
    errors, non-2xx, non-HTML bodies, missing metadata — still yields a
    card, falling back from OpenGraph to ``<title>`` to the bare domain.

    Synchronous; run it in a thread when called from async code.
    """
    normalized = _normalize_url(url)
    if normalized is None or not _url_allowed(normalized):
        return None

    domain = urlparse(normalized).hostname or normalized
    card = {
        "url": normalized,
        "title": None,
        "description": None,
        "image_url": None,
        "site_name": None,
        "type": "link",
    }

    final_url = None
    try:
        final_url, document, content_type = _fetch_document(normalized)
    except Exception as exc:
        logger.info("Preview card fetch failed for %s: %s", normalized, exc)
        document, content_type = None, ""

    if content_type.startswith("image/"):
        if final_url:
            card["image_url"] = final_url
    elif document and content_type in ("", "text/html", "application/xhtml+xml"):
        head = _parse_head(document)
        card["title"] = _meta(head, "og:title", "twitter:title")
        card["description"] = _meta(head, "og:description", "twitter:description", "description")
        card["site_name"] = _meta(head, "og:site_name")
        og_type = _meta(head, "og:type")
        if og_type:
            card["type"] = _clean_text(og_type, 32)
        image = _meta(head, "og:image", "og:image:url", "og:image:secure_url", "twitter:image")
        if image and final_url:
            resolved = urljoin(final_url, html.unescape(image.strip()))
            if urlparse(resolved).scheme in ("http", "https"):
                card["image_url"] = resolved[:2048]
        if card["title"] is None:
            title_text = "".join(head.title_parts)
            if title_text.strip():
                card["title"] = title_text

    title = card["title"]
    description = card["description"]
    site_name = card["site_name"]
    if title is None:
        card["title"] = domain
    else:
        card["title"] = _clean_text(title, MAX_TITLE_LENGTH)
    if description is not None:
        card["description"] = _clean_text(description, MAX_DESCRIPTION_LENGTH)
    if site_name is not None:
        card["site_name"] = _clean_text(site_name, 256)

    return card


class _AnchorParser(HTMLParser):
    """Collect ``href`` values of non-mention/non-hashtag anchors, in order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag != "a":
            return
        attr = dict(attrs)
        href = attr.get("href")
        if not href:
            return
        classes = (attr.get("class") or "").split()
        rels = (attr.get("rel") or "").split()
        if "tag" in rels or any(c in _NON_CARD_CLASSES for c in classes):
            return
        if href.startswith(("http://", "https://")):
            self.urls.append(href)


def extract_first_url(content_source: Optional[str], content: Optional[str]) -> Optional[str]:
    """
    Return the first pure URL in a post, or ``None``.

    ``content_source`` (the author's raw text, present on local posts) is
    scanned first — mention and hashtag links never appear there. Otherwise,
    the rendered HTML ``content`` (remote posts, description-derived posts)
    is scanned, skipping anchors marked as mentions or hashtags.
    """
    if content_source:
        for match in _URL_RE.finditer(content_source):
            normalized = _normalize_url(match.group(0))
            if normalized is not None:
                return normalized
    if content:
        parser = _AnchorParser()
        try:
            parser.feed(content)
        except Exception as e:
            logger.debug("Unparseable activity content: %s", e, exc_info=True)
        for url in parser.urls:
            normalized = _normalize_url(url)
            if normalized is not None:
                return normalized
    return None


def _has_attachments(activity: Activity) -> bool:
    """Return whether the activity's embedded object carries attachments."""
    payload = activity.payload
    if not isinstance(payload, dict):
        return False
    obj = payload.get("object")
    if not isinstance(obj, dict):
        return False
    return bool(obj.get("attachment"))


async def process_activity_preview_card(session: AsyncSession, activity: Activity) -> None:
    """
    Resolve the preview card for ``activity``, linking or clearing it.

    No-op (leaving any existing link untouched) when the instance-level
    ``preview_cards_enabled`` setting is off or the local author opted out.
    Otherwise, the first content URL is extracted; when there is none — or
    the object carries attachments — the link is cleared. A fresh cached
    card is reused as-is; a stale or missing one is (re)fetched. Flushes
    without committing; the caller owns the transaction.
    """
    if not await get_setting(session, None, "preview_cards_enabled"):
        return
    if activity.source_type == "local" and activity.owner_user_id:
        owner = await session.get(User, activity.owner_user_id)
        if owner is not None and not owner.preview_cards_enabled:
            activity.preview_card_id = None
            return

    url = extract_first_url(activity.content_source, activity.content)
    if url is None or _has_attachments(activity):
        activity.preview_card_id = None
        return

    card = await session.scalar(select(PreviewCard).where(PreviewCard.url == url))
    if card is not None and datetime.now(timezone.utc) - card.fetched_at < PREVIEW_CARD_MAX_AGE:
        activity.preview_card_id = card.id
        return

    data = await asyncio.to_thread(fetch_card_data, url)
    if data is None:
        activity.preview_card_id = None
        return

    if card is None:
        card = PreviewCard(fetched_at=datetime.now(timezone.utc), **data)
        session.add(card)
        try:
            async with session.begin_nested():
                await session.flush()
        except IntegrityError:
            # Another worker created the card for this URL concurrently.
            card = await session.scalar(select(PreviewCard).where(PreviewCard.url == url))
    else:
        for key, value in data.items():
            if key != "url":
                setattr(card, key, value)
        card.fetched_at = datetime.now(timezone.utc)

    activity.preview_card_id = card.id if card is not None else None
    await session.flush()


def schedule_preview_card_fetch(
    activity: Activity,
    *,
    author: Optional[User] = None,
    force: bool = False,
) -> None:
    """
    Enqueue a preview-card fetch for ``activity`` — best-effort.

    ``force`` bypasses the cheap pre-checks and is used when content was
    edited, so a stale link gets cleared even when the URL disappeared.
    A broker failure is logged and dropped — the post itself never fails
    over a card.
    """
    if not force:
        if activity.source_type == "local" and author is not None and not author.preview_cards_enabled:
            return
        if _has_attachments(activity):
            return
        if extract_first_url(activity.content_source, activity.content) is None:
            return

    from ..tasks.preview_cards import fetch_preview_card

    try:
        fetch_preview_card.delay(str(activity.id))
    except Exception as exc:
        logger.warning("Cannot enqueue preview card fetch for %s: %s", activity.id, exc)
