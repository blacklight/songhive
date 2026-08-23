"""
Activity creation and processing for federation.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from ..models._enums import Visibility
from ..models.artist import Artist
from ..models.track import Track
from ._common import get_stream_url, get_track_url
from .serializers import track_to_audio_object


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
    public download endpoint for the track's audio file.
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
        audio_object["content"] = description

    if duration is not None:
        minutes = int(duration // 60)
        seconds = int(duration % 60)
        audio_object["duration"] = f"PT{minutes}M{seconds}S"

    return {
        "@context": "https://www.w3.org/ns/activitystreams",
        "type": "Create",
        "actor": actor_url,
        "object": audio_object,
        "to": ["https://www.w3.org/ns/activitystreams#Public"],
        "cc": [f"{actor_url}/followers"],
    }


def create_delete_activity(
    actor_url: str, track: Track, domain: str, ap_object_id: Optional[str] = None
) -> Optional[dict]:
    """
    Create a Delete activity for a previously published track.

    The object is a ``Tombstone`` pointing to the same ActivityPub object id
    that was used in the original ``Create(Audio)`` activity. This lets remote
    instances remove the cached object without blocking future re-publication
    with a different object id.
    """
    if not track:
        return None

    object_id = ap_object_id or track.federation_object_id or get_track_url(track=track, domain=domain)
    now = datetime.now(timezone.utc).isoformat()

    return {
        "@context": "https://www.w3.org/ns/activitystreams",
        "id": f"{actor_url}/activities/{uuid.uuid4()}",
        "type": "Delete",
        "actor": actor_url,
        "published": now,
        "to": ["https://www.w3.org/ns/activitystreams#Public"],
        "cc": [f"{actor_url}/followers"],
        "object": {
            "id": object_id,
            "type": "Tombstone",
        },
    }
