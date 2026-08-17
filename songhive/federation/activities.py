"""
Activity creation and processing for federation.
"""

from typing import Optional


def create_audio_activity(
    actor_url: str,
    track_url: str,
    stream_url: str,
    title: str,
    *,
    description: Optional[str] = None,
    duration: Optional[float] = None,
) -> dict:
    """
    Create a Create(Audio) activity for publishing a track.

    When rendered on Mastodon/compatible software, this should appear as
    a post with an embedded audio element and a link to the track.
    """
    audio_object = {
        "@context": "https://www.w3.org/ns/activitystreams",
        "type": "Audio",
        "id": track_url,
        "name": title,
        "url": [
            {"type": "Link", "href": stream_url, "mediaType": "audio/mpeg"},
            {"type": "Link", "href": track_url, "mediaType": "text/html"},
        ],
    }

    if description:
        audio_object["content"] = description
    if duration:
        # ISO 8601 duration
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
