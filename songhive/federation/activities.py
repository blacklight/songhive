"""
Activity creation and processing for federation.
"""

import uuid
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Tuple

from pubby import build_like_activity
from pubby.content import format_duration

from ..models._enums import Visibility
from ..models.artist import Artist
from ..models.track import Track
from ._common import get_stream_url, get_track_url
from .serializers import set_audio_description, track_to_audio_object

AS_PUBLIC = "https://www.w3.org/ns/activitystreams#Public"


def create_audio_activity(
    actor_url: str,
    track: Track,
    artist: Artist,
    domain: str,
    *,
    description: Optional[str] = None,
    duration: Optional[float] = None,
    ap_object_id: Optional[str] = None,
) -> Optional[dict]:
    """
    Create a Create(Audio) activity for publishing a track.

    Non-public tracks produce no activity. The audio stream URL points to the
    public download endpoint for the track's audio file, and is also included
    as a ``Document`` attachment. The object's ``content`` is rendered from
    the track's ``description`` (escaped HTML with linkified URLs and
    hashtags); ``description`` overrides it when provided.
    """
    if track.visibility != Visibility.PUBLIC.value:
        return None

    stream_url = get_stream_url(track=track, domain=domain)
    audio_object = track_to_audio_object(
        track,
        artist,
        domain,
        stream_url,
        actor_url=actor_url,
        ap_object_id=ap_object_id,
    )
    if audio_object is None:
        return None

    if description:
        set_audio_description(audio_object, description, domain)

    if duration is not None:
        audio_object["duration"] = format_duration(duration)

    return {
        "@context": "https://www.w3.org/ns/activitystreams",
        "type": "Create",
        "actor": actor_url,
        "object": audio_object,
        "to": [AS_PUBLIC],
        "cc": [f"{actor_url}/followers"],
    }


def activity_audience(
    visibility: "Visibility | str",
    actor_url: str,
    mention_actor_urls: Iterable[str] = (),
) -> Tuple[List[str], List[str]]:
    """
    Map an activity visibility to ActivityPub ``(to, cc)`` addressing.

    ``public`` addresses the ActivityStreams public collection with the
    actor's followers in ``cc``; ``followers`` addresses the followers
    collection only; ``mentioned`` addresses the mentioned actors only.
    ``private`` and ``local`` are not federated and produce an empty audience.
    """
    value = Visibility(visibility)
    if value == Visibility.PUBLIC:
        return [AS_PUBLIC], [f"{actor_url}/followers"]
    if value == Visibility.FOLLOWERS:
        return [f"{actor_url}/followers"], []
    if value == Visibility.MENTIONED:
        return sorted(set(mention_actor_urls)), []
    return [], []


def create_visibility_update_activity(
    actor_url: str,
    object_id: str,
    visibility: "Visibility | str",
    mention_actor_urls: Iterable[str] = (),
) -> dict:
    """
    Create an ``Update`` activity announcing a new audience for ``object_id``.

    The embedded object is a partial representation carrying only the fields
    that changed: the ``to``/``cc`` audience derived from ``visibility``.
    """
    to, cc = activity_audience(visibility, actor_url, mention_actor_urls)
    now = datetime.now(timezone.utc).isoformat()

    return {
        "@context": "https://www.w3.org/ns/activitystreams",
        "id": f"{actor_url}/activities/{uuid.uuid4()}",
        "type": "Update",
        "actor": actor_url,
        "published": now,
        "to": to,
        "cc": cc,
        "object": {
            "id": object_id,
            "attributedTo": actor_url,
            "to": to,
            "cc": cc,
        },
    }


def create_like_activity(
    actor_url: str,
    object_id: str,
    visibility: "Visibility | str",
    mention_actor_urls: Iterable[str] = (),
    activity_id: Optional[str] = None,
    published: Optional[datetime] = None,
) -> dict:
    """
    Create a ``Like`` activity targeting ``object_id``.

    The ``to``/``cc`` audience is derived from ``visibility`` via
    :func:`activity_audience`; ``mention_actor_urls`` should carry the liked
    object's author (and any other directly addressed actors) so
    ``mentioned``-visibility likes are addressed to them. ``activity_id`` may
    be supplied to reuse the stored activity's ``source_id`` as the
    ActivityPub ``id``, making the object dereferenceable via the federation
    object route.

    This is a thin Songhive adapter around ``pubby.build_like_activity``:
    it keeps the ``Visibility``-to-audience mapping local and delegates the
    generic payload construction to Pubby.
    """
    to, cc = activity_audience(visibility, actor_url, mention_actor_urls)
    if published is None:
        published = datetime.now(timezone.utc)

    return build_like_activity(
        actor_id=actor_url,
        object_id=object_id,
        to=to,
        cc=cc,
        activity_id=activity_id,
        published=published,
        context="https://www.w3.org/ns/activitystreams",
    )


def create_tombstone_delete_activity(actor_url: str, object_id: str) -> dict:
    """
    Create a ``Delete`` activity whose object is a ``Tombstone`` for
    ``object_id``.

    This lets remote instances remove the cached object without blocking
    future re-publication with a different object id.
    """
    now = datetime.now(timezone.utc).isoformat()

    return {
        "@context": "https://www.w3.org/ns/activitystreams",
        "id": f"{actor_url}/activities/{uuid.uuid4()}",
        "type": "Delete",
        "actor": actor_url,
        "published": now,
        "to": [AS_PUBLIC],
        "cc": [f"{actor_url}/followers"],
        "object": {
            "id": object_id,
            "type": "Tombstone",
        },
    }


def create_delete_activity(
    actor_url: str, track: Track, domain: str, ap_object_id: Optional[str] = None
) -> Optional[dict]:
    """
    Create a Delete activity for a previously published track.

    The object is a ``Tombstone`` pointing to the same ActivityPub object id
    that was used in the original ``Create(Audio)`` activity.
    """
    if not track:
        return None

    object_id = ap_object_id or track.federation_object_id or get_track_url(track=track, domain=domain)
    return create_tombstone_delete_activity(actor_url, object_id)


def create_update_actor_activity(actor_url: str, actor_document: dict) -> dict:
    """
    Create an Update activity wrapping the actor's ``Person`` document.

    Delivered to follower inboxes so remote instances refresh their cached
    copy of the actor profile (display name, bio, avatar and profile links).
    """
    now = datetime.now(timezone.utc).isoformat()

    return {
        "@context": "https://www.w3.org/ns/activitystreams",
        "id": f"{actor_url}/activities/{uuid.uuid4()}",
        "type": "Update",
        "actor": actor_url,
        "published": now,
        "to": [AS_PUBLIC],
        "cc": [f"{actor_url}/followers"],
        "object": actor_document,
    }
