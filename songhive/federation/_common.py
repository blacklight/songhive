from typing import Union

from ..models.track import Track


def get_actor_url(domain: str, username: str) -> str:
    """Get the ActivityPub actor URL for a local user."""
    return f"https://{domain}/users/{username}"


def get_inbox_url(domain: str, username: str) -> str:
    """Get the ActivityPub inbox URL for a local user."""
    return f"https://{domain}/users/{username}/inbox"


def get_outbox_url(domain: str, username: str) -> str:
    """Get the ActivityPub outbox URL for a local user."""
    return f"https://{domain}/users/{username}/outbox"


def get_tag_url(domain: str, name: str) -> str:
    """Get the local URL of a tag page."""
    return f"https://{domain}/tags/{name}"


def get_track_url(track: Union[Track, str], domain: str) -> str:
    track_id = track.id if isinstance(track, Track) else track
    return f"https://{domain}/tracks/{track_id}"


def get_stream_url(track: Track, domain: str) -> str:
    track_url = get_track_url(track=track, domain=domain)
    return f"https://{domain}/api/v1/files/{track.audio_file_id}/download" if track.audio_file_id else track_url
