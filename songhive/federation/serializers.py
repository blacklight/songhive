"""
Serializers: convert internal models to ActivityPub objects.
"""

from typing import Optional

from ..models._enums import Visibility
from ..models.artist import Artist
from ..models.track import Track
from ._common import get_stream_url, get_track_url


def track_to_audio_object(
    track: Track, artist: Artist, domain: str, stream_url: Optional[str] = None
) -> Optional[dict]:
    """
    Serialize a Track to an ActivityPub Audio object.

    Only ``public`` tracks are serialized into federation payloads. Callers
    should pass the public download URL for the audio file,
    ``https://{domain}/api/v1/files/{track.audio_file_id}/download``. If
    ``stream_url`` is not provided and the track has an ``audio_file_id``,
    the download URL is computed automatically.
    """
    if getattr(track, "visibility", None) != Visibility.PUBLIC.value:
        return None

    track_url = get_track_url(track=track, domain=domain)
    stream_url = stream_url or get_stream_url(track=track, domain=domain)
    obj = {
        "type": "Audio",
        "id": track_url,
        "name": track.title,
        "attributedTo": f"https://{domain}/artists/{artist.id}",
        "url": [
            {"type": "Link", "href": stream_url, "mediaType": "audio/mpeg"},
            {"type": "Link", "href": track_url, "mediaType": "text/html"},
        ],
    }

    if track.duration:
        minutes = int(track.duration // 60)
        seconds = int(track.duration % 60)
        obj["duration"] = f"PT{minutes}M{seconds}S"

    if track.genre:
        obj["tag"] = [{"type": "Hashtag", "name": f"#{track.genre}"}]

    return obj
