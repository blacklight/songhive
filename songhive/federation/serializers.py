"""
Serializers: convert internal models to ActivityPub objects.
"""

import html
from datetime import datetime, timezone
from functools import partial
from typing import Optional

from pubby.content import (
    build_hashtag_tags,
    format_duration,
    is_linkable_url,
    render_link_anchor,
    set_object_content,
)
from sqlalchemy import inspect as sa_inspect

from ..models import Artist, Track, Visibility
from ..services.genres import extract_genres_from_track, genres_to_tags
from ._common import get_stream_url, get_tag_url, get_track_url


def set_post_content(
    obj: dict,
    description: Optional[str],
    domain: str,
    link_href: Optional[str] = None,
) -> None:
    """
    Render a track description into the object's ``content`` field.

    The description is HTML-escaped, with http(s) URLs and ``#tags``
    linkified so remote servers render them as usable links.  Tags found
    in the text are also appended to the object's ``tag`` list.

    :func:`normalize_post_content` is applied afterwards so the rendered
    body ends with the track-page link and any stale ``summary`` is removed.
    ``link_href`` is forwarded as the link's target — pass the track page
    URL explicitly for objects (e.g. ``Note`` shares) whose ``url`` is the
    object's own id rather than the track page.
    """
    set_object_content(obj, description or "", partial(get_tag_url, domain))
    normalize_post_content(obj, link_href=link_href)


# Object types Songhive emits as federated posts and normalizes content for.
# ``Audio`` is the published track object; ``Note`` is the shape used for
# shares, since Mastodon renders ``content`` only for ``Note``/``Question``
# objects (``Audio`` is a "converted" type rendered from name/summary/url).
_POST_OBJECT_TYPES = frozenset({"Audio", "Note"})


def normalize_post_content(obj: dict, link_href: Optional[str] = None) -> None:
    """
    Normalize a federated post object's body for remote renderers.

    Ensures ``content`` ends with a ``{artist} - {title}`` link to the
    object's page — the anchor that used to be emitted as the object's
    ``name``.  The link target is ``link_href`` when given, otherwise the
    ``text/html`` entry of the object's ``url``.  It is appended rather than
    prepended so that on Akkoma — which already renders ``name`` as its own
    ``<p><a href="{url}">{name}</a></p>`` header — the same link does not
    appear twice in a row at the top of the post.  For ``Note`` objects the
    link is also the only in-body link to the track page that Mastodon
    renders: a ``Note``'s ``url`` is the share's own object id (not shown in
    the post body), so callers pass the track page as ``link_href``.

    Any ``summary`` is removed: Akkoma and Mastodon both treat a non-empty
    ``summary`` as a content warning, so mirroring the rendered body into it
    surfaced the raw post HTML as a CW header.  The function is idempotent —
    a previously appended link block is stripped before re-appending, so
    repeated content re-renders (edits, metadata re-syncs) never duplicate
    it.  No-op for objects that are not ``Audio``/``Note`` or for which no
    linkable page URL can be found.
    """
    if obj.get("type") not in _POST_OBJECT_TYPES:
        return
    obj.pop("summary", None)
    block = _track_link_block(obj, link_href)
    if block is None:
        return
    body = obj.get("content")
    if not isinstance(body, str):
        body = ""
    if body.endswith(block):
        body = body[: -len(block)]
    obj["content"] = f"{body}{block}"


def _track_link_block(obj: dict, link_href: Optional[str] = None) -> Optional[str]:
    """
    Return the ``<p><a href="{track page}">{artist} - {title}</a></p>`` block
    appended to a post object's ``content``, or ``None`` when it cannot be
    built from the object's ``name`` and a page URL.

    The link target is ``link_href`` when given; otherwise it is derived
    from the object's ``url`` — the ``text/html`` entry of a ``Link`` list
    (``Audio`` objects) or a plain-string ``url`` pointing at a page other
    than the object itself (legacy ``Note`` objects stored the track page
    there; current ``Note`` objects carry their own object id as ``url`` and
    must get the page through ``link_href``).
    """
    name = obj.get("name")
    if not isinstance(name, str) or not name:
        return None
    href: Optional[str] = link_href
    if href is None:
        urls = obj.get("url")
        if isinstance(urls, str):
            # A plain-string ``url`` equal to ``id`` is the object's own
            # self-referential object URL (a share's id), not a page to
            # link to — unlike an ``Audio`` object, whose ``id`` may
            # legitimately be the track page.
            if urls != obj.get("id"):
                href = urls
        elif isinstance(urls, list):
            href = next(
                (
                    entry.get("href")
                    for entry in urls
                    if isinstance(entry, dict) and (entry.get("mediaType") or entry.get("mimeType")) == "text/html"
                ),
                None,
            )
    if not isinstance(href, str) or not is_linkable_url(href):
        return None
    # ``name`` is stored HTML-escaped; unescape the label first so
    # ``render_link_anchor``'s own escaping does not double-escape it.
    return f"<p>{render_link_anchor(href, label=html.unescape(name))}</p>"


def _unloaded_attrs(track: Track) -> frozenset:
    """
    Return the track's unloaded ORM attributes.

    ``audio_file`` and ``created_at`` are only read when already loaded:
    implicit queries would raise ``MissingGreenlet`` in the async contexts
    these serializers are called from.
    """
    return getattr(sa_inspect(track), "unloaded", frozenset())


def _track_media_type(track: Track, unloaded: frozenset) -> str:
    """
    Return the track's audio MIME type.

    Prefers the persisted ``audio_mime_type``; only falls back to the
    related ``StoredFile`` when it is already loaded.
    """
    audio_file_content_type = None
    if not track.audio_mime_type and "audio_file" not in unloaded:
        audio_file = getattr(track, "audio_file", None)
        if audio_file is not None:
            audio_file_content_type = audio_file.content_type

    return track.audio_mime_type or audio_file_content_type or "audio/mpeg"


def _track_published(track: Track, unloaded: frozenset) -> datetime:
    """
    Return a stable publication timestamp for the track object.

    The track creation time keeps ``published`` stable across
    re-serializations of the same object (Update rebuilds, the
    object-dereference route); a missing or unloaded value falls back to
    the current UTC time.
    """
    published = track.created_at if "created_at" not in unloaded else None
    if published is None:
        published = datetime.now(timezone.utc)
    elif published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    return published


def _track_name(track: Track, artist: Artist) -> str:
    """
    Return the plain-text ``{artist} - {title}`` object name.

    It used to carry an ``<a href="{track_url}">`` anchor because Mastodon
    interpolates ``name`` unescaped into the post header — but Akkoma wraps
    ``name`` in its own link to the object ``url``, which produced nested
    anchors.  The link lives at the end of ``content`` instead (see
    ``normalize_post_content``).  The label is HTML-escaped so a crafted
    artist/title cannot inject markup on servers that still interpolate
    ``name`` unescaped.
    """
    return html.escape(f"{artist.name} - {track.title}", quote=False)


def _track_attributed_to(artist: Artist, domain: str, actor_url: Optional[str]) -> "str | list":
    """
    Return the object's ``attributedTo``.

    The publishing actor leads when known: remote servers take its first
    entry as the object's author when importing a dereferenced object (e.g.
    Mastodon's URL-search fetch), and the artist page URL is not
    dereferenceable as an actor.
    """
    artist_url = f"https://{domain}/artists/{artist.id}"
    if actor_url:
        return [actor_url, artist_url]
    return artist_url


def _track_genre_tags(track: Track, domain: str) -> Optional[list]:
    """Return ``Hashtag`` tags for the track's genres, when it has any."""
    if not track.genre:
        return None
    genre_names = extract_genres_from_track(track)
    if not genre_names:
        return None
    genre_tags = list(dict.fromkeys(genres_to_tags(genre_names)))
    return build_hashtag_tags(genre_tags, partial(get_tag_url, domain))


def track_to_audio_object(
    track: Track,
    artist: Artist,
    domain: str,
    stream_url: Optional[str] = None,
    actor_url: Optional[str] = None,
    ap_object_id: Optional[str] = None,
) -> Optional[dict]:
    """
    Serialize a Track to an ActivityPub Audio object.

    Only ``public`` tracks are serialized into federation payloads. Callers
    should pass the public download URL for the audio file,
    ``https://{domain}/api/v1/files/{track.audio_file_id}/download``. If
    ``stream_url`` is not provided and the track has an ``audio_file_id``,
    the download URL is computed automatically.

    ``ap_object_id`` is the ActivityPub object id used in ``Create``/``Update``
    activities. When omitted the canonical track URL is used, which is the
    legacy behaviour. Per-publication ids are preferred so that a previous
    ``Delete(Tombstone)`` does not block re-publication of the same track.
    """
    if artist is None or getattr(track, "visibility", None) != Visibility.PUBLIC.value:
        return None

    track_url = get_track_url(track=track, domain=domain)
    stream_url = stream_url or get_stream_url(track=track, domain=domain)
    object_id = ap_object_id or track_url
    unloaded = _unloaded_attrs(track)
    media_type = _track_media_type(track, unloaded)

    # ``published`` feeds the remote post's date (Akkoma falls back to the
    # Unix epoch when it is missing).
    published = _track_published(track, unloaded)

    obj = {
        "type": "Audio",
        "id": object_id,
        "name": _track_name(track, artist),
        "published": published.isoformat(),
        # ``mimeType`` mirrors ``mediaType``: Mastodon's url_to_href reads the
        # non-standard ``mimeType`` key and falls back to "text/html" per link,
        # so without it the audio download URL would be picked for display
        # instead of the track page.
        "url": [
            {
                "type": "Link",
                "href": stream_url,
                "mediaType": media_type,
                "mimeType": media_type,
            },
            {
                "type": "Link",
                "href": track_url,
                "mediaType": "text/html",
                "mimeType": "text/html",
            },
        ],
        "attributedTo": _track_attributed_to(artist, domain, actor_url),
    }

    if track.duration:
        obj["duration"] = format_duration(track.duration)

    tags = _track_genre_tags(track, domain)
    if tags:
        obj["tag"] = tags

    set_post_content(obj, getattr(track, "description", None), domain, link_href=track_url)

    if stream_url:
        obj["attachment"] = [
            {
                "type": "Document",
                "mediaType": media_type,
                "url": stream_url,
                "name": track.title,
            }
        ]

    return obj


def track_to_note_object(
    track: Track,
    artist: Artist,
    domain: str,
    stream_url: Optional[str] = None,
    actor_url: Optional[str] = None,
    ap_object_id: Optional[str] = None,
    audio_object_id: Optional[str] = None,
    published: Optional[datetime] = None,
) -> Optional[dict]:
    """
    Serialize a Track to an ActivityPub ``Note`` carrying the audio.

    A ``Note`` is the only object shape whose ``content`` Mastodon renders
    (``Audio`` is a "converted" type synthesized from ``name``/``summary``/
    ``url``), so shares meant to display a post body are published as
    ``Create(Note)`` instead of ``Create(Audio)``.

    The track is still represented inside the object:

    - ``name`` stays ``{artist} - {title}`` — Akkoma renders it as its own
      linked header, while Mastodon ignores ``name`` on ``Note`` objects.
    - ``url`` is the share's own object id: a ``Note`` is a distinct object
      from the track's canonical ``Audio``, so it must not claim the track
      page as its ``url`` (the track page dereferences to the ``Audio``
      object — that is why a remote URL search for it returns the ``Audio``,
      never a share). The share's own object URL dereferences to this
      ``Note`` document, and browsers are redirected to the entity's
      activity feed by the object route.
    - ``attachment`` embeds the stream as an ``Audio``-typed media object so
      remote servers render an inline player; on Akkoma the generic ``Audio``
      type also survives the ``application/octet-stream`` mediaType rewrite
      for MIME values outside its table (the MastoAPI attachment ``type``
      falls back to the object's type and still resolves to ``audio``).
      ``audio_object_id`` sets the attachment's ``id`` to the published
      ``Audio`` object so music-aware servers can dereference the canonical
      track object.
    - ``content`` ends with the track-page link (``normalize_post_content``)
      so the post body carries a usable link on servers that do not surface
      ``url`` — e.g. Mastodon, which only renders ``content`` for a ``Note``.

    ``published`` defaults to the current time (a share is a new post); the
    caller passes the stored stamp when re-serializing an existing share so
    remote posts keep their original date.
    """
    if artist is None or getattr(track, "visibility", None) != Visibility.PUBLIC.value:
        return None

    track_url = get_track_url(track=track, domain=domain)
    stream_url = stream_url or get_stream_url(track=track, domain=domain)
    object_id = ap_object_id or track_url
    unloaded = _unloaded_attrs(track)
    media_type = _track_media_type(track, unloaded)

    if published is None:
        published = datetime.now(timezone.utc)
    elif published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)

    obj = {
        "type": "Note",
        "id": object_id,
        "name": _track_name(track, artist),
        "published": published.isoformat(),
        "url": object_id,
        "attributedTo": _track_attributed_to(artist, domain, actor_url),
    }

    tags = _track_genre_tags(track, domain)
    if tags:
        obj["tag"] = tags

    set_post_content(obj, getattr(track, "description", None), domain, link_href=track_url)

    if stream_url:
        attachment: dict = {
            "type": "Audio",
            "mediaType": media_type,
            "url": stream_url,
            "name": track.title,
        }
        if audio_object_id:
            attachment["id"] = audio_object_id
        if track.duration:
            attachment["duration"] = format_duration(track.duration)
        obj["attachment"] = [attachment]

    return obj
