"""
Serializers: convert internal models to ActivityPub objects.
"""

from ..models.artist import Artist
from ..models.track import Track


def track_to_audio_object(track: Track, artist: Artist, domain: str, stream_url: str) -> dict:
    """
    Serialize a Track to an ActivityPub Audio object.
    """
    track_url = f"https://{domain}/tracks/{track.id}"

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
