"""
Serializers: convert internal models to ActivityPub objects.
"""

import html
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

from ..models._enums import Visibility
from ..models.artist import Artist
from ..models.track import Track
from ..services.genres import extract_genres_from_track, genres_to_hashtags
from ._common import get_hashtag_url, get_stream_url, get_track_url


def set_audio_description(obj: dict, description: Optional[str], domain: str) -> None:
    """
    Render a track description into the object's ``content`` field.

    The description is HTML-escaped, with http(s) URLs and ``#hashtags``
    linkified so remote servers render them as usable links.  Hashtags found
    in the text are also appended to the object's ``tag`` list.

    The rendered text is also mirrored into ``summary``: Mastodon-family
    servers treat ``Audio`` as a "converted" object type and render
    ``name``/``summary``/``url`` instead of ``content``, so without the
    mirror the post text is silently dropped there.
    """
    set_object_content(obj, description or "", partial(get_hashtag_url, domain))
    mirror_content_to_summary(obj)


def mirror_content_to_summary(obj: dict) -> None:
    """
    Mirror ``content`` into ``summary`` on ``Audio`` objects (or clear it).

    Mastodon-style "converted" object types (``Audio``, ``Video``, ``Image``,
    ``Article``, ``Page``, ``Event``) ignore ``content`` and render
    ``name`` + ``summary`` + ``url`` as the post body.  Mirroring keeps the
    post text visible there while remaining a no-op for other object types.
    """
    if obj.get("type") != "Audio":
        return
    if obj.get("content"):
        obj["summary"] = obj["content"]
    else:
        obj.pop("summary", None)


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

    # Prefer the persisted MIME type; only fall back to the related StoredFile
    # when it is already loaded to avoid an implicit query in async contexts.
    audio_file_content_type = None
    if not track.audio_mime_type:
        track_state = sa_inspect(track)
        if "audio_file" not in getattr(track_state, "unloaded", {}):
            audio_file = getattr(track, "audio_file", None)
            if audio_file is not None:
                audio_file_content_type = audio_file.content_type

    media_type = track.audio_mime_type or audio_file_content_type or "audio/mpeg"

    # Mastodon-family servers render ``Audio`` (a "converted" object type)
    # as ``<h2>{name}</h2>`` + ``summary`` + the object ``url``,
    # interpolating ``name`` into the markup unescaped: emitting an anchor
    # turns the post header into an "{artist} - {title}" link to the track
    # page.  The label and href are escaped; ``name`` falls back to escaped
    # plain text if the URL is not linkable.
    title_label = f"{artist.name} - {track.title}"
    name = render_link_anchor(track_url, label=title_label) if is_linkable_url(track_url) else html.escape(title_label)

    obj = {
        "type": "Audio",
        "id": object_id,
        "name": name,
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
    }

    artist_url = f"https://{domain}/artists/{artist.id}"
    if actor_url:
        obj["attributedTo"] = [artist_url, actor_url]
    else:
        obj["attributedTo"] = artist_url

    if track.duration:
        obj["duration"] = format_duration(track.duration)

    if track.genre:
        genre_names = extract_genres_from_track(track)
        if genre_names:
            genre_tags = list(dict.fromkeys(genres_to_hashtags(genre_names)))
            obj["tag"] = build_hashtag_tags(genre_tags, partial(get_hashtag_url, domain))

    set_audio_description(obj, getattr(track, "description", None), domain)

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
