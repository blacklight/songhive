from ..models.track import Track


def get_track_url(track: Track, domain: str) -> str:
    return f"https://{domain}/tracks/{track.id}"


def get_stream_url(track: Track, domain: str) -> str:
    track_url = get_track_url(track=track, domain=domain)
    return f"https://{domain}/api/v1/files/{track.audio_file_id}/download" if track.audio_file_id else track_url
