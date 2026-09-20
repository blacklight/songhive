"""
Podcast service: follow RSS/Atom feeds and keep a local episode catalog.

Podcast audio is never downloaded — episodes store the remote enclosure URL
and playback streams straight from the source. What Songhive persists is
only feed metadata: show-level fields (title, author, artwork, categories,
explicit flag) and per-episode metadata (title, description, publication
date, duration, enclosure URL/type/size, season/episode numbers).

Feed fetches follow the same SSRF discipline as ``services.preview_cards``:
only ``http(s)`` URLs whose host resolves to public addresses are requested,
every redirect hop is re-validated, and the response body is capped at
``podcasts.max_feed_bytes``. Conditional ``ETag``/``Last-Modified``
validators are replayed so periodic refreshes that hit a ``304`` cost one
header exchange instead of a full re-parse.

OPML import/export covers the standard ``<outline type="rss" xmlUrl="...">``
shape, including outlines nested inside folder outlines.
"""

import asyncio
import ipaddress
import json
import logging
import re
import socket
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import List, Optional
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree
from xml.sax.saxutils import escape, quoteattr

import requests
from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_default_user_agent
from ..config.schema import SonghiveConfig
from ..models.podcast import Podcast, PodcastEpisode, PodcastSubscription
from ..models.user import User

logger = logging.getLogger(__name__)

MAX_REDIRECTS = 5
MAX_EPISODES_PER_FETCH = 2000
MAX_TITLE_LENGTH = 512
MAX_DESCRIPTION_LENGTH = 100_000
MAX_URL_LENGTH = 2048

_ITUNES_NS = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}"


class FeedFetchError(Exception):
    """Raised when a podcast feed cannot be fetched or parsed."""


@dataclass
class ParsedEpisode:
    """One episode entry extracted from a feed document."""

    guid: str
    title: str
    audio_url: str
    description: Optional[str] = None
    link: Optional[str] = None
    image_url: Optional[str] = None
    audio_type: Optional[str] = None
    audio_length: Optional[int] = None
    duration_seconds: Optional[int] = None
    published_at: Optional[datetime] = None
    season_number: Optional[int] = None
    episode_number: Optional[int] = None
    episode_type: Optional[str] = None


@dataclass
class ParsedFeed:
    """Show-level metadata and episodes extracted from a feed document."""

    title: str
    episodes: List[ParsedEpisode] = field(default_factory=list)
    description: Optional[str] = None
    author: Optional[str] = None
    link: Optional[str] = None
    image_url: Optional[str] = None
    language: Optional[str] = None
    categories: List[str] = field(default_factory=list)
    explicit: bool = False


@dataclass
class FetchResult:
    """Outcome of a conditional feed fetch."""

    # True when the server answered 304 — the stored catalog is still fresh.
    not_modified: bool = False
    body: Optional[bytes] = None
    final_url: Optional[str] = None
    etag: Optional[str] = None
    last_modified: Optional[str] = None


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


def url_allowed(url: str) -> bool:
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


def normalize_feed_url(url: str) -> str:
    """Normalize a user-supplied feed URL into its canonical storage form."""
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise FeedFetchError("Feed URL must be an absolute http(s) URL")
    if len(url) > MAX_URL_LENGTH:
        raise FeedFetchError("Feed URL is too long")
    return url


def fetch_feed(
    url: str,
    *,
    timeout: float,
    max_bytes: int,
    etag: Optional[str] = None,
    last_modified: Optional[str] = None,
) -> FetchResult:
    """
    Fetch ``url`` under SSRF guards, returning the feed body and validators.

    Each redirect hop is re-validated against the private-address guard so a
    public feed URL cannot redirect into internal address space. When
    ``etag``/``last_modified`` are supplied they are sent as conditional
    headers and a ``304`` yields ``FetchResult(not_modified=True)``.

    Synchronous; run it in a thread when called from async code.
    """
    if not url_allowed(url):
        raise FeedFetchError(f"Refusing to fetch non-public URL: {url}")

    session = requests.Session()
    current = url
    try:
        for _ in range(MAX_REDIRECTS + 1):
            if not url_allowed(current):
                raise FeedFetchError(f"Refusing to fetch non-public URL: {current}")
            headers = {
                "User-Agent": f"{get_default_user_agent()} (podcast feed)",
                "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*;q=0.1",
            }
            if etag:
                headers["If-None-Match"] = etag
            if last_modified:
                headers["If-Modified-Since"] = last_modified

            response = session.get(current, timeout=timeout, allow_redirects=False, stream=True, headers=headers)
            try:
                if response.is_redirect or response.is_permanent_redirect:
                    location = response.headers.get("Location")
                    if not location:
                        raise FeedFetchError("Feed redirect without Location header")
                    current = urljoin(current, location)
                    continue

                if response.status_code == 304:
                    return FetchResult(not_modified=True, final_url=current)

                if response.status_code != 200:
                    raise FeedFetchError(f"Feed fetch failed with HTTP {response.status_code}")

                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_content(64 * 1024):
                    chunks.append(chunk)
                    size += len(chunk)
                    if size >= max_bytes:
                        break
                return FetchResult(
                    body=b"".join(chunks)[:max_bytes],
                    final_url=current,
                    etag=response.headers.get("ETag"),
                    last_modified=response.headers.get("Last-Modified"),
                )
            finally:
                response.close()
        raise FeedFetchError(f"Too many redirects fetching feed: {url}")
    finally:
        session.close()


_HTML_BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "figcaption",
    "figure",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "td",
    "th",
    "tr",
    "ul",
}
_HTML_SKIP_TAGS = {"script", "style", "template", "head", "title"}


class _HTMLToText(HTMLParser):
    """Extract text from an HTML fragment, keeping block-level boundaries."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: List[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, _attrs: list) -> None:
        if tag in _HTML_SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _HTML_BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _HTML_SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
        elif tag in _HTML_BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._chunks.append(data)

    def text(self) -> str:
        return "".join(self._chunks)


def _clean_text(value: Optional[str], limit: int = MAX_DESCRIPTION_LENGTH) -> Optional[str]:
    """Strip markup, collapse whitespace runs and cap at ``limit`` characters."""
    if value is None:
        return None
    if "<" in value or "&" in value:
        extractor = _HTMLToText()
        extractor.feed(value)
        extractor.close()
        value = extractor.text()
    text = re.sub(r"\s+", " ", value).strip()
    return text[:limit] if text else None


def _child_text(element: ElementTree.Element, *names: str) -> Optional[str]:
    """Return the stripped text of the first matching child among ``names``."""
    for name in names:
        child = element.find(name)
        if child is None:
            continue
        if child.text and child.text.strip():
            return child.text.strip()
        # Atom ``type="xhtml"`` payloads are inline elements, not text.
        inner = " ".join(part.strip() for part in child.itertext() if part.strip())
        if inner:
            return inner
    return None


def _parse_date(value: Optional[str]) -> Optional[datetime]:
    """Parse an RFC 822 (RSS) or ISO 8601 (Atom) date into a tz-aware datetime."""
    if not value:
        return None
    value = value.strip()
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        parsed = None
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


_DURATION_HMS_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:\.\d+)?$")


def _parse_duration(value: Optional[str]) -> Optional[int]:
    """Parse an itunes:duration value (seconds, MM:SS or HH:MM:SS) into seconds."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return int(value)
    match = _DURATION_HMS_RE.match(value)
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return (int(hours) if hours else 0) * 3600 + int(minutes) * 60 + int(seconds)


def _parse_int(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    try:
        return int(value.strip())
    except (TypeError, ValueError):
        return None


def _parse_explicit(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in ("yes", "true", "explicit")


def _parse_episode_type(value: Optional[str]) -> Optional[str]:
    value = (value or "").strip().lower()
    return value if value in ("full", "trailer", "bonus") else None


def _image_url(element: ElementTree.Element, base_url: str) -> Optional[str]:
    """Return an image URL from itunes:image/@href or RSS <image><url>."""
    itunes_image = element.find(f"{_ITUNES_NS}image")
    if itunes_image is not None:
        href = (itunes_image.get("href") or "").strip()
        if href:
            resolved = urljoin(base_url, href)
            if urlparse(resolved).scheme in ("http", "https"):
                return resolved[:MAX_URL_LENGTH]
    image = element.find("image")
    if image is not None:
        url = _child_text(image, "url")
        if url:
            resolved = urljoin(base_url, url)
            if urlparse(resolved).scheme in ("http", "https"):
                return resolved[:MAX_URL_LENGTH]
    return None


def _channel_categories(channel: ElementTree.Element) -> List[str]:
    """Collect iTunes (nested) and plain RSS categories, deduplicated."""
    categories: list[str] = []
    seen: set[str] = set()

    def _add(value: Optional[str]) -> None:
        text = (value or "").strip()
        if text and text.lower() not in seen:
            seen.add(text.lower())
            categories.append(text[:128])

    for category in channel.iter(f"{_ITUNES_NS}category"):
        _add(category.get("text"))
        # Nested <itunes:category> sub-elements are visited by ``iter`` too,
        # so subcategories are collected automatically.
    for category in channel.findall("category"):
        _add(category.text)
    return categories


def _item_enclosure(item: ElementTree.Element, base_url: str) -> tuple[Optional[str], Optional[str], Optional[int]]:
    """Return ``(url, mime_type, length)`` for an RSS <enclosure>."""
    enclosure = item.find("enclosure")
    if enclosure is None:
        return None, None, None
    url = (enclosure.get("url") or "").strip()
    if not url:
        return None, None, None
    resolved = urljoin(base_url, url)
    if urlparse(resolved).scheme not in ("http", "https"):
        return None, None, None
    return (
        resolved[:MAX_URL_LENGTH],
        (enclosure.get("type") or "").strip()[:128] or None,
        _parse_int(enclosure.get("length")),
    )


def _parse_rss_item(item: ElementTree.Element, base_url: str) -> Optional[ParsedEpisode]:
    """Parse an RSS <item> into a ParsedEpisode, or None without audio."""
    audio_url, audio_type, audio_length = _item_enclosure(item, base_url)
    if audio_url is None:
        return None

    guid = _child_text(item, "guid") or audio_url
    title = _clean_text(_child_text(item, "title"), MAX_TITLE_LENGTH) or audio_url
    description = _clean_text(_child_text(item, f"{_ITUNES_NS}summary", "description", f"{_CONTENT_NS}encoded"))
    link = _child_text(item, "link")
    if link:
        link = urljoin(base_url, link)[:1024]

    return ParsedEpisode(
        guid=guid[:512],
        title=title,
        audio_url=audio_url,
        description=description,
        link=link,
        image_url=_image_url(item, base_url),
        audio_type=audio_type,
        audio_length=audio_length,
        duration_seconds=_parse_duration(_child_text(item, f"{_ITUNES_NS}duration")),
        published_at=_parse_date(_child_text(item, "pubDate", f"{_ITUNES_NS}pubDate")),
        season_number=_parse_int(_child_text(item, f"{_ITUNES_NS}season")),
        episode_number=_parse_int(_child_text(item, f"{_ITUNES_NS}episode")),
        episode_type=_parse_episode_type(_child_text(item, f"{_ITUNES_NS}episodeType")),
    )


def _atom_link(entry: ElementTree.Element, rel: str, base_url: str) -> Optional[str]:
    """Return the href of an Atom <link> with the given rel (default rel=alternate)."""
    for link in entry.findall(f"{_ATOM_NS}link"):
        link_rel = (link.get("rel") or "alternate").strip()
        if link_rel != rel:
            continue
        href = (link.get("href") or "").strip()
        if href:
            resolved = urljoin(base_url, href)
            if urlparse(resolved).scheme in ("http", "https"):
                return resolved
    return None


def _atom_audio_link(entry: ElementTree.Element, base_url: str) -> tuple[Optional[str], Optional[str], Optional[int]]:
    """Return ``(url, mime_type, length)`` for an Atom enclosure/audio link."""
    for link in entry.findall(f"{_ATOM_NS}link"):
        rel = (link.get("rel") or "alternate").strip()
        mime = (link.get("type") or "").strip().lower()
        if rel == "enclosure" or (rel == "alternate" and mime.startswith("audio/")):
            href = (link.get("href") or "").strip()
            if not href:
                continue
            resolved = urljoin(base_url, href)
            if urlparse(resolved).scheme in ("http", "https"):
                return resolved[:MAX_URL_LENGTH], mime[:128] or None, _parse_int(link.get("length"))
    return None, None, None


def _parse_atom_entry(entry: ElementTree.Element, base_url: str) -> Optional[ParsedEpisode]:
    """Parse an Atom <entry> into a ParsedEpisode, or None without audio."""
    audio_url, audio_type, audio_length = _atom_audio_link(entry, base_url)
    if audio_url is None:
        return None

    guid = _child_text(entry, f"{_ATOM_NS}id") or audio_url
    title = _clean_text(_child_text(entry, f"{_ATOM_NS}title"), MAX_TITLE_LENGTH) or audio_url
    description = _clean_text(_child_text(entry, f"{_ATOM_NS}summary", f"{_ATOM_NS}content", f"{_ITUNES_NS}summary"))

    return ParsedEpisode(
        guid=guid[:512],
        title=title,
        audio_url=audio_url,
        description=description,
        link=_atom_link(entry, "alternate", base_url),
        image_url=_image_url(entry, base_url),
        audio_type=audio_type,
        audio_length=audio_length,
        duration_seconds=_parse_duration(_child_text(entry, f"{_ITUNES_NS}duration")),
        published_at=_parse_date(
            _child_text(entry, f"{_ATOM_NS}published", f"{_ATOM_NS}updated", f"{_ITUNES_NS}pubDate")
        ),
        season_number=_parse_int(_child_text(entry, f"{_ITUNES_NS}season")),
        episode_number=_parse_int(_child_text(entry, f"{_ITUNES_NS}episode")),
        episode_type=_parse_episode_type(_child_text(entry, f"{_ITUNES_NS}episodeType")),
    )


def _parse_rss(root: ElementTree.Element, base_url: str) -> ParsedFeed:
    """Parse an RSS 2.0 document."""
    channel = root.find("channel")
    if channel is None:
        raise FeedFetchError("RSS document has no <channel>")

    title = _clean_text(_child_text(channel, "title"), MAX_TITLE_LENGTH)
    if not title:
        raise FeedFetchError("Feed has no title")

    episodes: list[ParsedEpisode] = []
    for item in channel.findall("item"):
        episode = _parse_rss_item(item, base_url)
        if episode is not None:
            episodes.append(episode)
        if len(episodes) >= MAX_EPISODES_PER_FETCH:
            break

    return ParsedFeed(
        title=title,
        episodes=episodes,
        description=_clean_text(_child_text(channel, f"{_ITUNES_NS}summary", "description")),
        author=_clean_text(_child_text(channel, f"{_ITUNES_NS}author", "managingEditor"), MAX_TITLE_LENGTH),
        link=urljoin(base_url, _child_text(channel, "link") or "")[:1024] or None,
        image_url=_image_url(channel, base_url),
        language=_clean_text(_child_text(channel, "language"), 32),
        categories=_channel_categories(channel),
        explicit=_parse_explicit(_child_text(channel, f"{_ITUNES_NS}explicit")),
    )


def _parse_atom(root: ElementTree.Element, base_url: str) -> ParsedFeed:
    """Parse an Atom document (e.g. feeds served as application/atom+xml)."""
    title = _clean_text(_child_text(root, f"{_ATOM_NS}title"), MAX_TITLE_LENGTH)
    if not title:
        raise FeedFetchError("Feed has no title")

    author_el = root.find(f"{_ATOM_NS}author")
    author = _clean_text(_child_text(author_el, f"{_ATOM_NS}name"), MAX_TITLE_LENGTH) if author_el is not None else None
    if author is None:
        author = _clean_text(_child_text(root, f"{_ITUNES_NS}author"), MAX_TITLE_LENGTH)

    episodes: list[ParsedEpisode] = []
    for entry in root.findall(f"{_ATOM_NS}entry"):
        episode = _parse_atom_entry(entry, base_url)
        if episode is not None:
            episodes.append(episode)
        if len(episodes) >= MAX_EPISODES_PER_FETCH:
            break

    image = _atom_link(root, "icon", base_url) or _image_url(root, base_url)

    return ParsedFeed(
        title=title,
        episodes=episodes,
        description=_clean_text(_child_text(root, f"{_ATOM_NS}subtitle", f"{_ITUNES_NS}summary")),
        author=author,
        link=_atom_link(root, "alternate", base_url),
        image_url=image,
        language=None,
        categories=_channel_categories(root),
        explicit=_parse_explicit(_child_text(root, f"{_ITUNES_NS}explicit")),
    )


def parse_feed(body: bytes, base_url: str) -> ParsedFeed:
    """
    Parse an RSS or Atom podcast feed document.

    ``base_url`` is the final (post-redirect) feed URL used to resolve
    relative links and image/enclosure URLs.
    """
    # Cheap entity-expansion guard: podcast feeds never legitimately need a
    # DOCTYPE, and refusing one sidesteps entity amplification entirely.
    head = body[:4096].lstrip()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
        raise FeedFetchError("Feed documents with a DOCTYPE are not accepted")

    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as exc:
        raise FeedFetchError(f"Feed is not valid XML: {exc}") from exc

    if root.tag == "rss" or root.tag == f"{_ATOM_NS}rss":
        return _parse_rss(root, base_url)
    if root.tag == f"{_ATOM_NS}feed":
        return _parse_atom(root, base_url)
    # Some feeds omit the version attribute or use a namespaced root.
    if root.tag.endswith("rss"):
        return _parse_rss(root, base_url)
    raise FeedFetchError(f"Unsupported feed format: <{root.tag}>")


def podcast_due_for_refresh(podcast: Podcast, interval: timedelta, now: Optional[datetime] = None) -> bool:
    """Return whether the podcast's feed is due for an automatic refresh."""
    if podcast.last_fetched_at is None:
        return True
    now = now or datetime.now(timezone.utc)
    return now - podcast.last_fetched_at >= interval


async def _apply_feed(db: AsyncSession, podcast: Podcast, parsed: ParsedFeed) -> int:
    """Update ``podcast`` fields and upsert episodes; returns new episode count."""
    podcast.title = parsed.title[:512]
    podcast.description = parsed.description
    podcast.author = parsed.author
    podcast.link = parsed.link
    podcast.image_url = parsed.image_url
    podcast.language = parsed.language
    podcast.categories = json.dumps(parsed.categories) if parsed.categories else None
    podcast.explicit = parsed.explicit

    rows = (await db.scalars(select(PodcastEpisode).where(PodcastEpisode.podcast_id == podcast.id))).all()
    existing = {episode.guid: episode for episode in rows}
    seen: set[str] = set()
    new_count = 0
    for parsed_episode in parsed.episodes:
        guid = parsed_episode.guid
        if guid in seen:
            continue
        seen.add(guid)
        episode = existing.get(guid)
        if episode is None:
            episode = PodcastEpisode(podcast_id=podcast.id, guid=guid)
            db.add(episode)
            new_count += 1
        episode.title = parsed_episode.title
        episode.description = parsed_episode.description
        episode.link = parsed_episode.link
        episode.image_url = parsed_episode.image_url
        episode.audio_url = parsed_episode.audio_url
        episode.audio_type = parsed_episode.audio_type
        episode.audio_length = parsed_episode.audio_length
        episode.duration_seconds = parsed_episode.duration_seconds
        episode.published_at = parsed_episode.published_at
        episode.season_number = parsed_episode.season_number
        episode.episode_number = parsed_episode.episode_number
        episode.episode_type = parsed_episode.episode_type

    # Episodes that vanished from the feed are dropped — the source no
    # longer serves them and streaming their dead URLs would just error.
    for guid, episode in existing.items():
        if guid not in seen:
            await db.delete(episode)
    return new_count


async def get_podcast(db: AsyncSession, podcast_id: str) -> Optional[Podcast]:
    """Return a podcast by id, or None."""
    return await db.get(Podcast, podcast_id)


async def get_podcast_by_feed_url(db: AsyncSession, feed_url: str) -> Optional[Podcast]:
    """Return the podcast row for ``feed_url``, or None."""
    return await db.scalar(select(Podcast).where(Podcast.feed_url == feed_url))


async def refresh_podcast(
    db: AsyncSession,
    podcast: Podcast,
    config: SonghiveConfig,
    *,
    force: bool = False,
) -> int:
    """
    Fetch and re-parse ``podcast``'s feed, updating metadata and episodes.

    Returns the number of newly added episodes. Raises ``FeedFetchError``
    on failure after recording it on ``podcast.last_error`` — the row keeps
    its last-good catalog either way. Skips the network round-trip entirely
    when the server answers ``304`` or the feed was fetched within the
    configured refresh interval (unless ``force``).
    """
    if not force and not podcast_due_for_refresh(podcast, timedelta(minutes=config.podcasts.refresh_interval_minutes)):
        return 0

    try:
        result = await asyncio.to_thread(
            fetch_feed,
            podcast.feed_url,
            timeout=config.podcasts.request_timeout_seconds,
            max_bytes=config.podcasts.max_feed_bytes,
            etag=podcast.etag if not force else None,
            last_modified=podcast.last_modified if not force else None,
        )
    except FeedFetchError as exc:
        podcast.last_fetched_at = datetime.now(timezone.utc)
        podcast.last_error = str(exc)[:2000]
        await db.flush()
        raise
    except Exception as exc:
        podcast.last_fetched_at = datetime.now(timezone.utc)
        podcast.last_error = str(exc)[:2000]
        await db.flush()
        raise FeedFetchError(str(exc)) from exc

    podcast.last_fetched_at = datetime.now(timezone.utc)
    if result.not_modified:
        podcast.last_error = None
        await db.flush()
        return 0

    if result.etag:
        podcast.etag = result.etag
    if result.last_modified:
        podcast.last_modified = result.last_modified
    # If the feed permanently moved, keep the canonical URL in sync so
    # duplicate subscriptions converge on one row.
    if result.final_url and result.final_url != podcast.feed_url:
        existing = await get_podcast_by_feed_url(db, result.final_url)
        if existing is None:
            podcast.feed_url = result.final_url

    try:
        parsed = parse_feed(result.body or b"", result.final_url or podcast.feed_url)
    except FeedFetchError as exc:
        podcast.last_error = str(exc)[:2000]
        await db.flush()
        raise

    new_count = await _apply_feed(db, podcast, parsed)
    podcast.last_error = None
    await db.flush()
    return new_count


async def subscribe(
    db: AsyncSession,
    user: User,
    feed_url: str,
    config: SonghiveConfig,
) -> tuple[Podcast, bool]:
    """
    Follow ``feed_url`` for ``user``.

    The feed is fetched and parsed synchronously so the subscription
    response carries real show metadata. Returns ``(podcast, created)``
    where ``created`` marks a brand-new subscription; following a feed the
    user already follows is idempotent.
    """
    feed_url = normalize_feed_url(feed_url)
    podcast = await get_podcast_by_feed_url(db, feed_url)
    is_new = False

    if podcast is not None:
        existing = await db.scalar(
            select(PodcastSubscription).where(
                PodcastSubscription.user_id == user.id,
                PodcastSubscription.podcast_id == podcast.id,
            )
        )
        if existing is not None:
            return podcast, False

    if podcast is None:
        podcast = Podcast(feed_url=feed_url, title=feed_url)
        db.add(podcast)
        try:
            async with db.begin_nested():
                await db.flush()
            is_new = True
        except IntegrityError:
            # Concurrent subscribe created the row for this feed_url.
            podcast = await get_podcast_by_feed_url(db, feed_url)
            assert podcast is not None

    try:
        await refresh_podcast(db, podcast, config, force=True)
    except FeedFetchError:
        # A brand-new feed we could not even fetch once is useless — drop
        # the podcast row so a later subscribe retries cleanly. An existing
        # podcast keeps its last-good catalog and merely records the error.
        if is_new:
            await db.delete(podcast)
            await db.flush()
        raise

    subscription = PodcastSubscription(user_id=user.id, podcast_id=podcast.id)
    db.add(subscription)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        return podcast, False
    return podcast, True


async def unsubscribe(db: AsyncSession, user: User, podcast_id: str) -> bool:
    """Remove ``user``'s subscription to ``podcast_id``. Returns False if absent."""
    subscription = await db.scalar(
        select(PodcastSubscription).where(
            PodcastSubscription.user_id == user.id,
            PodcastSubscription.podcast_id == podcast_id,
        )
    )
    if subscription is None:
        return False
    await db.delete(subscription)
    await db.flush()
    return True


async def is_subscribed(db: AsyncSession, user: Optional[User], podcast_id: str) -> bool:
    """Return whether ``user`` follows ``podcast_id``."""
    if user is None:
        return False
    return (
        await db.scalar(
            select(func.count())
            .select_from(PodcastSubscription)
            .where(
                PodcastSubscription.user_id == user.id,
                PodcastSubscription.podcast_id == podcast_id,
            )
        )
        or 0
    ) > 0


async def list_subscribed_podcasts(
    db: AsyncSession,
    user: User,
    *,
    limit: int = 100,
    offset: int = 0,
) -> tuple[List[Podcast], int]:
    """Return ``(podcasts, total)`` for feeds ``user`` follows, newest first."""
    base = select(Podcast).join(
        PodcastSubscription,
        PodcastSubscription.podcast_id == Podcast.id,
    )
    filtered = base.where(PodcastSubscription.user_id == user.id)
    total = await db.scalar(select(func.count()).select_from(filtered.subquery())) or 0
    rows = (
        await db.scalars(filtered.order_by(PodcastSubscription.created_at.desc()).offset(offset).limit(limit))
    ).all()
    return list(rows), total


async def list_episodes(
    db: AsyncSession,
    podcast_id: str,
    *,
    limit: int = 50,
    offset: int = 0,
    oldest_first: bool = False,
) -> tuple[List[PodcastEpisode], int]:
    """Return ``(episodes, total)`` for ``podcast_id``, newest-first by default."""
    total = (
        await db.scalar(select(func.count()).select_from(PodcastEpisode).where(PodcastEpisode.podcast_id == podcast_id))
        or 0
    )
    if oldest_first:
        ordering: list[ColumnElement] = [
            PodcastEpisode.published_at.asc().nulls_last(),
            PodcastEpisode.created_at.asc(),
        ]
    else:
        ordering = [
            PodcastEpisode.published_at.desc().nulls_last(),
            PodcastEpisode.created_at.desc(),
        ]
    rows = (
        await db.scalars(
            select(PodcastEpisode)
            .where(PodcastEpisode.podcast_id == podcast_id)
            .order_by(*ordering)
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return list(rows), total


async def episode_counts(db: AsyncSession, podcast_ids: List[str]) -> dict[str, int]:
    """Return a ``podcast_id → episode count`` map for the given podcasts."""
    if not podcast_ids:
        return {}
    rows = await db.execute(
        select(PodcastEpisode.podcast_id, func.count())
        .where(PodcastEpisode.podcast_id.in_(podcast_ids))
        .group_by(PodcastEpisode.podcast_id)
    )
    return dict(rows.all())  # type: ignore


async def due_podcast_ids(db: AsyncSession, interval: timedelta, *, limit: int = 500) -> List[str]:
    """Return ids of podcasts due for a refresh — followed ones only."""
    cutoff = datetime.now(timezone.utc) - interval
    followed = select(PodcastSubscription.podcast_id).distinct().scalar_subquery()
    rows = await db.scalars(
        select(Podcast.id)
        .where(
            Podcast.id.in_(followed),
            or_(Podcast.last_fetched_at.is_(None), Podcast.last_fetched_at <= cutoff),
        )
        .limit(limit)
    )
    return list(rows)


def render_opml(title: str, podcasts: List[Podcast]) -> str:
    """Serialise ``podcasts`` as an OPML 2.0 subscription list."""
    outlines = []
    for podcast in sorted(podcasts, key=lambda p: p.title.lower()):
        attrs = {
            "type": "rss",
            "text": podcast.title,
            "title": podcast.title,
            "xmlUrl": podcast.feed_url,
        }
        if podcast.link:
            attrs["htmlUrl"] = podcast.link
        outlines.append(f"    <outline {' '.join(f'{k}={quoteattr(v)}' for k, v in attrs.items())} />")
    body = "\n".join(outlines)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<opml version="2.0">\n'
        "  <head>\n"
        f"    <title>{escape(title)}</title>\n"
        "  </head>\n"
        "  <body>\n"
        f"{body}\n"
        "  </body>\n"
        "</opml>\n"
    )


def parse_opml(data: bytes) -> List[tuple[str, Optional[str]]]:
    """
    Parse an OPML document into ``(feed_url, title)`` pairs.

    Only outlines carrying an ``xmlUrl`` are considered feed entries;
    container/folder outlines are traversed transparently. Feed URLs are
    deduplicated while preserving document order.
    """
    if len(data) > 4 * 1024 * 1024:
        raise FeedFetchError("OPML document is too large")
    head = data[:4096].lstrip()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
        raise FeedFetchError("OPML documents with a DOCTYPE are not accepted")
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        raise FeedFetchError(f"OPML document is not valid XML: {exc}") from exc
    if root.tag.lower() != "opml":
        raise FeedFetchError("Not an OPML document")

    entries: list[tuple[str, Optional[str]]] = []
    seen: set[str] = set()
    for outline in root.iter("outline"):
        xml_url = (outline.get("xmlUrl") or "").strip()
        if not xml_url:
            continue
        parsed = urlparse(xml_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            continue
        if xml_url in seen:
            continue
        seen.add(xml_url)
        title = (outline.get("title") or outline.get("text") or "").strip() or None
        entries.append((xml_url, title))
    return entries
