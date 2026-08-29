"""
Serializers: convert internal models to ActivityPub objects.
"""

from typing import Optional

from sqlalchemy import inspect as sa_inspect

from ..models._enums import Visibility
from ..models.artist import Artist
from ..models.track import Track
from ..services.genres import extract_genres_from_track, genres_to_hashtags
from ._common import get_stream_url, get_track_url


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

    obj = {
        "type": "Audio",
        "id": object_id,
        "name": track.title,
        "url": [
            {"type": "Link", "href": stream_url, "mediaType": media_type},
            {"type": "Link", "href": track_url, "mediaType": "text/html"},
        ],
    }

    artist_url = f"https://{domain}/artists/{artist.id}"
    if actor_url:
        obj["attributedTo"] = [artist_url, actor_url]
    else:
        obj["attributedTo"] = artist_url

    if track.duration:
        minutes = int(track.duration // 60)
        seconds = int(track.duration % 60)
        obj["duration"] = f"PT{minutes}M{seconds}S"

    if track.genre:
        genre_names = extract_genres_from_track(track)
        if genre_names:
            obj["tag"] = [{"type": "Hashtag", "name": f"#{hashtag}"} for hashtag in genres_to_hashtags(genre_names)]

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
