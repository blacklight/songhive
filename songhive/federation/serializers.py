"""
Serializers: convert internal models to ActivityPub objects.
"""

import html
from datetime import datetime, timezone
from functools import partial
from typing import Optional

from pubby import allow_public_quotes
from pubby.content import (
    build_hashtag_tags,
    format_duration,
    is_linkable_url,
    render_link_anchor,
    set_object_content,
)
from sqlalchemy import inspect as sa_inspect

from ..models import Album, Artist, StoredFile, Track, Visibility
from ..services.genres import extract_genres_from_track, genres_to_tags
from ._common import get_stream_url, get_tag_url, get_track_url

# Namespaced keys stamped on attachment docs produced by
# ``stored_file_to_attachment``/``track_to_attachment`` so edits can tell
# user-managed attachments (replaced through ``media_ids``/``track_ids``)
# from entity-owned ones (e.g. the published track's own ``Audio`` doc,
# which must survive an attachment edit). Remote servers ignore them.
ATTACHMENT_FILE_ID_KEY = "songhive:fileId"
ATTACHMENT_TRACK_ID_KEY = "songhive:trackId"
# Additional namespaced keys carrying the structured track metadata the
# flat ``name`` cannot express, so music-aware consumers can render a rich
# player without guessing at the ``{artist} - {title}`` label.
ATTACHMENT_TITLE_KEY = "songhive:trackTitle"
ATTACHMENT_ARTIST_KEY = "songhive:artistName"
ATTACHMENT_ALBUM_KEY = "songhive:albumName"
ATTACHMENT_TRACK_URL_KEY = "songhive:trackUrl"

# The Funkwhale ActivityPub context — served on music entity documents so
# the ``Track``/``Album``/``Artist``/``Library`` types and the ``track``/
# ``album``/``artists``/``bitrate``/``size``/``position``/``disc``/
# ``released``/``musicbrainzId`` fields expand to their ``fw:`` IRIs when
# Funkwhale (or another Songhive) parses the document.
FUNKWHALE_CONTEXT = "https://funkwhale.audio/ns"
MUSIC_ENTITY_CONTEXT = [
    "https://www.w3.org/ns/activitystreams",
    "https://w3id.org/security/v1",
    FUNKWHALE_CONTEXT,
    {
        "manuallyApprovesFollowers": "as:manuallyApprovesFollowers",
        "Hashtag": "as:Hashtag",
    },
]


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

# Every federated post object is stamped with ``pubby.allow_public_quotes``
# — the FEP-044f ``interactionPolicy`` allowing anyone to quote without
# manual approval. Mastodon reads ``interactionPolicy.canQuote`` to decide
# whether its Quote action is enabled — without it the menu reads "You are
# not allowed to quote this post" — and whether an incoming quote needs a
# ``QuoteAuthorization`` stamp to leave its pending state (pubby
# auto-approves those requests).


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


def _entity_published(entity) -> str:
    """Return an ISO-8601 ``published`` stamp from an entity's ``created_at``."""
    published = getattr(entity, "created_at", None)
    if published is None:
        published = datetime.now(timezone.utc)
    elif published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    return published.isoformat()


def _stored_file_image_doc(stored_file, domain: str) -> dict:
    """Return an ActivityStreams ``Image`` doc for a stored file."""
    image = {"type": "Image", "url": _file_download_url(stored_file.id, domain)}
    if stored_file.content_type:
        image["mediaType"] = stored_file.content_type
    return image


def artist_to_music_object(
    artist: Artist,
    domain: str,
    actor_url: Optional[str] = None,
    *,
    include_context: bool = False,
) -> dict:
    """
    Serialize an Artist to a federated music ``Artist`` document.

    Emits the Funkwhale-dialect shape — ``type: Artist`` under the
    ``funkwhale.audio/ns`` context — which Funkwhale imports through its
    ``ArtistSerializer`` and Songhive instances cache as a remote artist
    resource. ``actor_url`` is the owning/publishing actor, recorded as
    ``attributedTo``; artists have no local owner, so routes pass the
    instance actor.
    """
    doc: dict = {
        "type": "Artist",
        "id": f"https://{domain}/artists/{artist.id}",
        "name": artist.name,
        "published": _entity_published(artist),
        "musicbrainzId": artist.musicbrainz_id or None,
        "attributedTo": actor_url,
        "tag": [],
        "image": None,
    }
    if artist.bio:
        doc["summary"] = artist.bio

    unloaded: frozenset = getattr(sa_inspect(artist), "unloaded", frozenset())
    image_file = getattr(artist, "image_file", None) if "image_file" not in unloaded else None
    if image_file is not None:
        doc["image"] = _stored_file_image_doc(image_file, domain)
    elif artist.image_url:
        doc["image"] = {"type": "Image", "url": artist.image_url}

    if include_context:
        doc["@context"] = MUSIC_ENTITY_CONTEXT
    return doc


def album_to_music_object(
    album: Album,
    artist: Optional[Artist],
    domain: str,
    actor_url: Optional[str] = None,
    *,
    include_context: bool = False,
) -> dict:
    """
    Serialize an Album to a federated music ``Album`` document.

    ``artists`` always carries at least one entry — Funkwhale's
    ``AlbumSerializer`` requires it — so a missing artist relation produces
    a stub artist document. ``released`` is derived from ``release_year``
    (a year-only value serializes as January 1st).
    """
    artists = (
        [artist_to_music_object(artist, domain, actor_url)]
        if artist is not None
        else [
            {
                "type": "Artist",
                "id": f"https://{domain}/artists/{album.artist_id}",
                "name": "Unknown artist",
                "published": _entity_published(album),
                "musicbrainzId": None,
                "attributedTo": actor_url,
                "tag": [],
                "image": None,
            }
        ]
    )
    doc: dict = {
        "type": "Album",
        "id": f"https://{domain}/albums/{album.id}",
        "name": album.title,
        "published": _entity_published(album),
        "musicbrainzId": album.musicbrainz_id or None,
        "released": f"{album.release_year}-01-01" if album.release_year else None,
        "attributedTo": actor_url,
        "artists": artists,
        "tag": [],
        "image": None,
    }
    if album.description:
        doc["summary"] = album.description

    unloaded: frozenset = getattr(sa_inspect(album), "unloaded", frozenset())
    cover_file = getattr(album, "cover_file", None) if "cover_file" not in unloaded else None
    if cover_file is not None:
        doc["image"] = _stored_file_image_doc(cover_file, domain)
    elif album.cover_url:
        doc["image"] = {"type": "Image", "url": album.cover_url}

    if include_context:
        doc["@context"] = MUSIC_ENTITY_CONTEXT
    return doc


def track_to_music_track_object(
    track: Track,
    artist: Optional[Artist],
    domain: str,
    actor_url: Optional[str] = None,
) -> dict:
    """
    Serialize a Track to a federated music ``Track`` document.

    This is the structured music metadata embedded in an ``Audio`` object's
    ``track`` field (and served standalone for ``type: Track`` fetches) —
    not the ``Audio`` object itself. ``album`` is required by Funkwhale's
    ``TrackSerializer``; a track without an album gets a synthesized
    single-track album document.
    """
    unloaded = _unloaded_attrs(track)
    track_url = get_track_url(track=track, domain=domain)
    album = _loaded_album(track, unloaded)
    if album is not None:
        album_doc = album_to_music_object(album, getattr(album, "artist", None) or artist, domain, actor_url)
    else:
        album_doc = {
            "type": "Album",
            "id": f"{track_url}/album",
            "name": track.title,
            "published": _entity_published(track),
            "musicbrainzId": None,
            "released": f"{track.release_year}-01-01" if track.release_year else None,
            "attributedTo": actor_url,
            "artists": [artist_to_music_object(artist, domain, actor_url)] if artist is not None else [],
            "tag": [],
            "image": None,
        }

    doc: dict = {
        "type": "Track",
        "id": track_url,
        "name": track.title,
        "published": _entity_published(track),
        "musicbrainzId": track.musicbrainz_id or None,
        "position": track.track_number,
        "disc": track.disc_number,
        "license": None,
        "copyright": None,
        "artists": [artist_to_music_object(artist, domain, actor_url)] if artist is not None else [],
        "album": album_doc,
        "attributedTo": actor_url,
        "tag": [],
        "image": None,
    }
    if track.description:
        doc["summary"] = track.description
    return doc


def library_to_music_object(
    collection_id: str,
    name: str,
    actor_url: str,
    total_items: int,
    *,
    summary: Optional[str] = None,
    page_size: int = 100,
) -> dict:
    """
    Serialize a federated music ``Library`` collection document.

    ``collection_id`` is the library's canonical AP URL — either
    ``/libraries/{id}`` for a named library or ``{actor_url}/library`` for
    a user's implicit library of public tracks. The document is the
    collection index; items are served by ``music_collection_page`` at
    ``{collection_id}?page=N``. ``followers`` is dereferenceable and remote
    actors may ``Follow`` the library id (object-scoped follows) to receive
    ``Create(Audio)`` deliveries for new items.
    """
    last_page = max(1, -(-total_items // page_size)) if total_items else 1
    doc: dict = {
        "type": "Library",
        "id": collection_id,
        "name": name,
        "actor": actor_url,
        "attributedTo": actor_url,
        "followers": f"{collection_id}/followers",
        "audience": "https://www.w3.org/ns/activitystreams#Public",
        "totalItems": total_items,
        "first": f"{collection_id}?page=1",
        "current": f"{collection_id}?page=1",
        "last": f"{collection_id}?page={last_page}",
        "to": ["https://www.w3.org/ns/activitystreams#Public"],
    }
    if summary:
        doc["summary"] = summary
    return doc


def music_collection_page(
    collection_id: str,
    page: int,
    total_items: int,
    items: list,
    actor_url: Optional[str] = None,
    *,
    page_size: int = 100,
) -> dict:
    """
    Serialize a ``CollectionPage`` of federated music ``Audio`` items.

    Mirrors Funkwhale's library page shape — ``partOf`` the collection id,
    absolute ``?page=N`` links, embedded ``Audio`` items — so Funkwhale's
    ``CollectionPageSerializer`` accepts it when scanning a remote library.
    """
    last_page = max(1, -(-total_items // page_size)) if total_items else 1
    doc: dict = {
        "@context": MUSIC_ENTITY_CONTEXT,
        "id": f"{collection_id}?page={page}",
        "type": "CollectionPage",
        "partOf": collection_id,
        "totalItems": total_items,
        "first": f"{collection_id}?page=1",
        "last": f"{collection_id}?page={last_page}",
        "items": items,
    }
    if page > 1:
        doc["prev"] = f"{collection_id}?page={page - 1}"
    if page < last_page:
        doc["next"] = f"{collection_id}?page={page + 1}"
    if actor_url:
        doc["actor"] = actor_url
        doc["attributedTo"] = actor_url
    return doc


def track_to_audio_object(
    track: Track,
    artist: Artist,
    domain: str,
    stream_url: Optional[str] = None,
    actor_url: Optional[str] = None,
    ap_object_id: Optional[str] = None,
    library_url: Optional[str] = None,
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

    ``library_url`` identifies the federated music library the track belongs
    to — ``/libraries/{id}`` for a public library or the publisher's
    implicit ``{actor_url}/library`` collection. Together with the embedded
    ``track`` document and the integer ``duration``/``size``/``bitrate``
    fields it makes the object importable by Funkwhale's ``UploadSerializer``.
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

    # Integer seconds — Funkwhale's ``UploadSerializer.duration`` is an
    # IntegerField; an ISO-8601 duration would fail its import validation.
    duration_seconds = int(track.duration) if track.duration else 0
    audio_file = getattr(track, "audio_file", None) if "audio_file" not in unloaded else None
    size = int(audio_file.size) if audio_file is not None and audio_file.size else 0
    bitrate = int(size * 8 / duration_seconds) if size and duration_seconds else 0

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
        # Federated-music (Funkwhale dialect) fields.
        "duration": duration_seconds,
        "bitrate": bitrate,
        "size": size,
        "track": track_to_music_track_object(track, artist, domain, actor_url),
    }

    if library_url:
        obj["library"] = library_url
    if "updated_at" not in unloaded and getattr(track, "updated_at", None):
        obj["updated"] = track.updated_at.isoformat()

    tags = _track_genre_tags(track, domain)
    if tags:
        obj["tag"] = tags

    set_post_content(obj, getattr(track, "description", None), domain, link_href=track_url)

    if stream_url:
        audio_attachment: dict = {
            "type": "Document",
            "mediaType": media_type,
            "url": stream_url,
            "name": track.title,
        }
        _enrich_track_attachment(audio_attachment, track, artist, domain)
        obj["attachment"] = [audio_attachment]

    return allow_public_quotes(obj)


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
        _enrich_track_attachment(attachment, track, artist, domain)
        obj["attachment"] = [attachment]

    return allow_public_quotes(obj)


def _file_download_url(file_id: str, domain: str) -> str:
    """Return the public download URL for a stored file (relative without a domain)."""
    path = f"/api/v1/files/{file_id}/download"
    return f"https://{domain}{path}" if domain else path


def _loaded_album(track: Track, unloaded: frozenset) -> Optional[Album]:
    """Return the track's album when the relationship is already loaded."""
    if "album" in unloaded:
        return None
    return getattr(track, "album", None)


def _attachment_image(
    track: Track,
    album: Optional[Album],
    artist: Optional[Artist],
    domain: str,
    unloaded: frozenset,
) -> Optional[dict]:
    """
    Return an ActivityStreams ``Image`` for the track's cover art, if any.

    Resolution order: track image, then album cover (file, then remote
    URL), then artist image (file, then remote URL).
    Relationships that are not already loaded are skipped rather than
    lazily queried — these serializers run in async contexts where an
    implicit load would raise ``MissingGreenlet``.
    """
    image_file = getattr(track, "image_file", None) if "image_file" not in unloaded else None
    if image_file is None and album is not None:
        album_unloaded: frozenset = getattr(sa_inspect(album), "unloaded", frozenset())
        if "cover_file" not in album_unloaded:
            image_file = getattr(album, "cover_file", None)
    if image_file is not None:
        image: dict = {
            "type": "Image",
            "url": _file_download_url(image_file.id, domain),
        }
        if image_file.content_type:
            image["mediaType"] = image_file.content_type
        return image
    cover_url = getattr(album, "cover_url", None) if album is not None else None
    if cover_url:
        return {"type": "Image", "url": cover_url}
    if artist is not None:
        artist_unloaded: frozenset = getattr(sa_inspect(artist), "unloaded", frozenset())
        artist_image = getattr(artist, "image_file", None) if "image_file" not in artist_unloaded else None
        if artist_image is not None:
            artist_file_image: dict = {
                "type": "Image",
                "url": _file_download_url(artist_image.id, domain),
            }
            if artist_image.content_type:
                artist_file_image["mediaType"] = artist_image.content_type
            return artist_file_image
        if artist.image_url:
            return {"type": "Image", "url": artist.image_url}
    return None


def _enrich_track_attachment(
    attachment: dict,
    track: Track,
    artist: Optional[Artist],
    domain: str,
) -> None:
    """
    Stamp structured track metadata on an attachment doc.

    The namespaced ``songhive:*`` keys carry the split title/artist/album
    fields that the flat ``name`` label cannot express, plus the track page
    URL; a standard ``image`` entry carries the cover art. Together they
    let consumers (the Songhive web player first) render a rich audio
    player. Remote servers ignore unknown keys.
    """
    unloaded = _unloaded_attrs(track)
    album = _loaded_album(track, unloaded)
    attachment[ATTACHMENT_TITLE_KEY] = track.title
    if artist is not None:
        attachment[ATTACHMENT_ARTIST_KEY] = artist.name
    if album is not None:
        attachment[ATTACHMENT_ALBUM_KEY] = album.title
    attachment[ATTACHMENT_TRACK_URL_KEY] = (
        get_track_url(track=track, domain=domain) if domain else f"/tracks/{track.id}"
    )
    image = _attachment_image(track, album, artist, domain, unloaded)
    if image is not None:
        attachment["image"] = image


def stored_file_to_attachment(stored_file: StoredFile, domain: str = "") -> dict:
    """
    Serialize a stored file to an ActivityPub ``Document`` attachment.

    ``url`` is the file's public download endpoint — absolute when the
    instance ``domain`` is configured, relative otherwise (a relative URL is
    still usable by local API consumers). ``name`` carries the original
    filename for remote renderers that surface it as alt text.
    """
    attachment: dict = {
        "type": "Document",
        "mediaType": stored_file.content_type,
        "url": _file_download_url(stored_file.id, domain),
        ATTACHMENT_FILE_ID_KEY: str(stored_file.id),
    }
    if stored_file.original_filename:
        attachment["name"] = stored_file.original_filename
    return attachment


def track_to_attachment(
    track: Track,
    artist: Optional[Artist],
    domain: str = "",
    audio_object_id: Optional[str] = None,
) -> dict:
    """
    Serialize a hosted track as a status attachment.

    Tracks with an audio file become an ``Audio``-typed media object
    embedding the stream URL so remote servers render an inline player;
    ``audio_object_id`` links the attachment to the track's published
    ``Audio`` object when one exists. Tracks without audio degrade to a
    ``Document`` link to the track page. Unlike ``track_to_*_object`` this
    serializes non-public tracks too — the attachment points at
    access-controlled local endpoints and the author chose to reference it.
    """
    name = f"{artist.name} - {track.title}" if artist is not None else track.title
    if track.audio_file_id:
        stream_url = (
            get_stream_url(track=track, domain=domain) if domain else f"/api/v1/files/{track.audio_file_id}/download"
        )
        attachment: dict = {
            "type": "Audio",
            "mediaType": _track_media_type(track, _unloaded_attrs(track)),
            "url": stream_url,
            "name": name,
            ATTACHMENT_TRACK_ID_KEY: str(track.id),
        }
        if audio_object_id:
            attachment["id"] = audio_object_id
        if track.duration:
            attachment["duration"] = format_duration(track.duration)
        _enrich_track_attachment(attachment, track, artist, domain)
        return attachment

    track_url = get_track_url(track=track, domain=domain) if domain else f"/tracks/{track.id}"
    return {
        "type": "Document",
        "mediaType": "text/html",
        "url": track_url,
        "name": name,
        ATTACHMENT_TRACK_ID_KEY: str(track.id),
    }
