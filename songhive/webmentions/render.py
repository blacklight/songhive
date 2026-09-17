"""
Render the Microformats2 ``h-entry`` source page for outgoing Webmentions.

Remote servers fetch this document to verify and display a mention sent by a
local activity. The markup deliberately mirrors what the incoming parser
understands: an ``h-entry`` with ``u-url``/``dt-published``/``p-author``
(h-card), the interaction property links (``u-like-of``, ``u-repost-of``,
``u-in-reply-to``, ``u-quotation-of``) recorded on the activity payload, the
post's sanitized HTML as ``e-content``, its plain text as ``p-summary`` (so
quote posts carry their text as the mention summary), and ``p-category``
links for its tags.
"""

import html
import re
from datetime import timezone
from typing import Iterable, Optional
from urllib.parse import quote

from ..config.schema import SonghiveConfig
from ..models.activity import Activity
from ..models.user import User

_TAG_RE = re.compile(r"<[^>]+>")


def _absolute_url(domain: str, value: Optional[str]) -> Optional[str]:
    """Make a possibly-relative local URL absolute on the instance domain."""
    if not value:
        return None
    if value.startswith(("http://", "https://")):
        return value
    if value.startswith("/"):
        return f"https://{domain}{value}"
    return None


def _plain_text(activity: Activity) -> str:
    """Return the activity's plain-text content for ``p-summary``."""
    if activity.content_source:
        return activity.content_source
    if activity.content:
        return _TAG_RE.sub("", activity.content)
    return ""


def _interaction_links(activity: Activity) -> list[str]:
    """Return the ``u-*`` property links recorded by an interaction marker."""
    payload = activity.payload if isinstance(activity.payload, dict) else {}
    marker = payload.get("webmention")
    if not isinstance(marker, dict):
        return []
    property_name = marker.get("property")
    target = marker.get("target")
    if not isinstance(property_name, str) or not isinstance(target, str) or not target:
        return []
    label = {
        "like-of": "Liked",
        "repost-of": "Reposted",
        "in-reply-to": "In reply to",
        "quotation-of": "Quoted",
    }.get(property_name, "Referenced")
    return [
        f'<a class="u-{html.escape(property_name, quote=True)}" '
        f'href="{html.escape(target, quote=True)}">{label} this</a>'
    ]


def render_outgoing_source_page(
    activity: Activity,
    author: Optional[User],
    config: SonghiveConfig,
    *,
    tag_names: Iterable[str] = (),
) -> str:
    """Render the h-entry HTML document served as the outgoing mention source."""
    domain = config.federation.instance_domain.strip()
    activity_url = f"https://{domain}/activities/{activity.id}"
    endpoint_url = f"https://{domain}/webmentions"

    published = activity.published_at
    if published is not None and published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)

    author_html = ""
    if author is not None:
        author_url = f"https://{domain}/@{author.username}"
        author_name = author.display_name or author.username
        photo = _absolute_url(domain, author.avatar_url)
        photo_html = f'<img class="u-photo" src="{html.escape(photo, quote=True)}" alt="" />' if photo else ""
        author_html = (
            f'<span class="p-author h-card">'
            f"{photo_html}"
            f'<a class="u-url" href="{html.escape(author_url, quote=True)}">'
            f'<span class="p-name">{html.escape(author_name)}</span></a></span>'
        )

    summary = _plain_text(activity)
    parts = [
        '<!DOCTYPE html><html><head><meta charset="utf-8" />',
        '<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover" />',
        """
        <style>
        body {
          width: 100vw;
          height: 100vh;
          background: #f0f0f0;
          display: flex;
          overflow: hidden;
        }

        .h-entry {
          background: white;
          display: flex;
          flex-direction: column;
          margin: auto;
          padding: 20px;
          gap: 10px;
          overflow: hidden;
          border: 1px solid #ddd;
          border-radius: 10px;
        }

        .p-author {
          display: flex;
          align-items: center;
          gap: 7.5px;
        }

        .u-photo {
          width: 32px;
          height: 32px;
          border-radius: 50%;
        }

        .dt-published {
          font-size: 0.85em;
          color: #666;
        }

        a, a:visited {
          color: #009;
          text-decoration: none;
        }

        a:hover {
          text-decoration: underline;
        }

        .hidden {
          display: none;
        }
        </style>
        """ f'<link rel="webmention" href="{html.escape(endpoint_url, quote=True)}" />',
        f"<title>{html.escape(summary[:80] or 'Songhive activity')}</title>",
        "</head><body>",
        '<article class="h-entry">',
    ]
    if author_html:
        parts.append(author_html)
    parts.extend(_interaction_links(activity))
    if summary:
        summary_classes = ["p-summary"]
        if activity.content:
            # Don't show the summary if there's a content
            summary_classes.append("hidden")
        parts.append(f'<p class="{' '.join(summary_classes)}">{html.escape(summary)}</p>')

    parts.append(f'<a class="u-url u-uid" href="{html.escape(activity_url, quote=True)}">Activity URL</a>')
    if published is not None:
        parts.append(
            f'<time class="dt-published" datetime="{published.isoformat()}">'
            f'at {published.astimezone().strftime("%Y-%m-%d %H:%M")}</time>'
        )
    if activity.content:
        parts.append(f'<div class="e-content">{activity.content}</div>')
    for name in tag_names:
        tag_url = f"https://{domain}/tags/{quote(name)}"
        parts.append(f'<a class="p-category" href="{html.escape(tag_url, quote=True)}">{html.escape(name)}</a>')
    parts.append("</article></body></html>")
    return "".join(parts)
