"""
Activity creation and processing for federation.
"""

from typing import Optional

from ..models._enums import Visibility
from ..models.artist import Artist
from ..models.track import Track
from ._common import get_stream_url
from .serializers import track_to_audio_object


def create_audio_activity(
    actor_url: str,
    track: Track,
    artist: Artist,
    domain: str,
    *,
    description: Optional[str] = None,
    duration: Optional[float] = None,
) -> Optional[dict]:
    """
    Create a Create(Audio) activity for publishing a track.

    Non-public tracks produce no activity. The audio stream URL points to the
    public download endpoint for the track's audio file.
    """
    if track.visibility != Visibility.PUBLIC.value:
        return None

    stream_url = get_stream_url(track=track, domain=domain)
    audio_object = track_to_audio_object(track, artist, domain, stream_url)
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
