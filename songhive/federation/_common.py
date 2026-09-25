from typing import Optional, Union

from sqlalchemy import inspect as sa_inspect

from ..models.external_item import ExternalItem
from ..models.external_track import ExternalTrack
from ..models.track import Track


def get_actor_url(domain: str, username: str) -> str:
    """Get the ActivityPub actor URL for a local user."""
    return f"https://{domain}/users/{username}"


def get_mastodon_actor_url(domain: str, username: str) -> str:
    """
    Get the ActivityPub actor URL for a local user, in the
    https://mastodon.social/@username common with Mastodon instances.
    """
    return f"https://{domain}/@{username}"


def get_inbox_url(domain: str, username: str) -> str:
    """Get the ActivityPub inbox URL for a local user."""
    return f"{get_actor_url(domain, username)}/inbox"


def get_outbox_url(domain: str, username: str) -> str:
    """Get the ActivityPub outbox URL for a local user."""
    return f"{get_actor_url(domain, username)}/outbox"


def get_tag_url(domain: str, name: str) -> str:
    """Get the local URL of a tag page."""
    return f"https://{domain}/tags/{name}"


def get_track_url(track: Union[Track, str], domain: str) -> str:
    track_id = track.id if isinstance(track, Track) else track
    return f"https://{domain}/tracks/{track_id}"


def active_external_track(track: Track) -> Optional[ExternalTrack]:
    """
    Return the track's ``ExternalTrack`` row when loaded and ``active``.

    The relationship is skipped rather than lazily loaded: federation
    serializers run in async contexts where an implicit load would raise
    ``MissingGreenlet``.
    """
    if "external_track" in getattr(sa_inspect(track), "unloaded", frozenset()):
        return None
    external_track = getattr(track, "external_track", None)
    if external_track is not None and external_track.state == "active":
        return external_track
    return None


def active_external_item(track: Track) -> Optional[ExternalItem]:
    """
    Return the track's ``ExternalItem`` row when loaded and ``active``.

    Same unloaded-relationship guard as ``active_external_track`` — used for
    entity-backed providers (e.g. Jellyfin) whose references live on
    ``external_items`` rather than ``external_tracks``.
    """
    if "external_item" in getattr(sa_inspect(track), "unloaded", frozenset()):
        return None
    external_item = getattr(track, "external_item", None)
    if external_item is not None and external_item.state == "active":
        return external_item
    return None


def get_track_download_path(track: Track) -> Optional[str]:
    """
    Return the API path serving the track's audio bytes, or ``None``.

    Stored files download through the files endpoint; tracks backed by an
    active external-library item (e.g. WebDAV) stream through the track
    download endpoint, which resolves and proxies the provider's bytes.
    ``None`` means the track has no playable media.
    """
    if track.audio_file_id:
        return f"/api/v1/files/{track.audio_file_id}/download"
    if active_external_track(track) is not None or active_external_item(track) is not None:
        return f"/api/v1/tracks/{track.id}/download"
    return None


def get_stream_url(track: Track, domain: str) -> Optional[str]:
    """
    Return the absolute URL serving the track's audio bytes, or ``None``.

    ``None`` means the track has no playable media — callers must not
    advertise the HTML track page as an audio resource.
    """
    path = get_track_download_path(track)
    return f"https://{domain}{path}" if path is not None else None
