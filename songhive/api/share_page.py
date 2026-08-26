"""HTML preview renderer for public share links.

Public share URLs should be human-readable landing pages with OpenGraph
metadata, an audio player for tracks, and direct download links for files.
"""

import html
from typing import Any, Optional

from fastapi import Request

from ..models.album import Album
from ..models.artist import Artist
from ..models.playlist import Playlist
from ..models.radio import Radio
from ..models.stored_file import StoredFile
from ..models.track import Track


def _h(value: Optional[Any]) -> str:
    """Return a value as an HTML-escaped string."""
    return html.escape(str(value) if value is not None else "")


def _base_url(request: Request) -> str:
    """Return the base URL for the request without a trailing slash."""
    return str(request.base_url).rstrip("/")


def _abs_url(request: Request, path: str) -> str:
    """Return an absolute URL for a path starting with ``/``."""
    return _base_url(request) + path


def _file_url(file_id: str, token: str, disposition: str = "inline") -> str:
    """Return a share-token-authorized file download URL."""
    return f"/api/v1/files/{file_id}/download?token={token}&disposition={disposition}"


def _track_audio_url(track: Track, token: str, disposition: str = "inline") -> Optional[str]:
    """Return an authorized audio URL for a track, or ``None`` if it has no file."""
    if track.audio_file_id:
        return _file_url(track.audio_file_id, token, disposition)
    return None


def _format_duration(seconds: Optional[float]) -> str:
    """Format a duration in seconds as ``M:SS``."""
    if seconds is None:
        return ""
    total = int(seconds)
    minutes = total // 60
    secs = total % 60
    return f"{minutes}:{secs:02d}"


def _human_size(size: Optional[int]) -> str:
    """Return a human-readable byte size."""
    if size is None:
        return ""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} PB"


def _track_cover_url(track: Track) -> Optional[str]:
    """Return a cover image URL for a track from public metadata, if any."""
    if track.album and track.album.cover_url:
        return track.album.cover_url
    return None


def _album_cover_url(album: Album, request: Request, token: str) -> Optional[str]:
    """Return a cover image URL for an album."""
    if album.cover_url:
        return album.cover_url
    if album.cover_file_id:
        return _abs_url(request, _file_url(album.cover_file_id, token))
    return None


def _artist_image_url(artist: Artist) -> Optional[str]:
    """Return an image URL for an artist."""
    return artist.image_url


def _render_og_meta(
    title: str,
    description: str,
    url: str,
    image: Optional[str] = None,
    og_type: str = "website",
    audio: Optional[str] = None,
) -> str:
    """Build OpenGraph ``<meta>`` tags for social media previews."""
    tags = [
        f'<meta property="og:title" content="{_h(title)}">',
        f'<meta property="og:description" content="{_h(description)}">',
        f'<meta property="og:type" content="{_h(og_type)}">',
        f'<meta property="og:url" content="{_h(url)}">',
    ]
    if image:
        tags.append(f'<meta property="og:image" content="{_h(image)}">')
    if audio:
        tags.append(f'<meta property="og:audio" content="{_h(audio)}">')
    tags.extend(
        [
            '<meta name="twitter:card" content="summary_large_image">',
        ]
    )
    return "\n".join(tags)


def _render_page(
    request: Request,
    title: str,
    description: str,
    body: str,
    canonical_url: str,
    image: Optional[str] = None,
    og_type: str = "website",
    audio: Optional[str] = None,
) -> str:
    """Build a minimal, responsive HTML page around the given body."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{_h(title)}</title>
    {_render_og_meta(title, description, canonical_url, image, og_type, audio)}
    <style>
        :root {{
            color-scheme: light dark;
            --bg: #0f0f12;
            --surface: #1a1a20;
            --text: #e8e8ec;
            --muted: #a0a0a8;
            --accent: #6c7cff;
            --danger: #ff6b6b;
        }}
        body {{
            margin: 0;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.5;
        }}
        .container {{
            max-width: 720px;
            margin: 0 auto;
            padding: 2rem 1rem;
        }}
        .card {{
            background: var(--surface);
            border-radius: 1rem;
            padding: 1.5rem;
            box-shadow: 0 1px 3px rgba(0,0,0,0.3);
        }}
        .header {{
            display: flex;
            gap: 1.5rem;
            flex-wrap: wrap;
            align-items: flex-start;
            margin-bottom: 1.5rem;
        }}
        .cover {{
            width: 12rem;
            height: 12rem;
            object-fit: cover;
            border-radius: 0.75rem;
            background: #2a2a32;
        }}
        .info {{
            flex: 1;
            min-width: 16rem;
        }}
        h1 {{
            margin: 0 0 0.25rem 0;
            font-size: 2rem;
            word-break: break-word;
        }}
        .description {{
            color: var(--muted);
            margin: 0.5rem 0;
            word-break: break-word;
        }}
        .meta {{
            display: flex;
            flex-wrap: wrap;
            gap: 0.75rem;
            color: var(--muted);
            font-size: 0.875rem;
            margin: 0.75rem 0;
        }}
        .actions {{
            display: flex;
            gap: 0.75rem;
            flex-wrap: wrap;
            margin-top: 1rem;
        }}
        .btn {{
            display: inline-flex;
            align-items: center;
            gap: 0.5rem;
            padding: 0.5rem 1rem;
            border-radius: 0.5rem;
            background: var(--accent);
            color: #fff;
            text-decoration: none;
            font-weight: 500;
        }}
        .btn.secondary {{
            background: #2a2a32;
            color: var(--text);
        }}
        audio {{
            width: 100%;
            margin-top: 1rem;
        }}
        .track-list {{
            list-style: none;
            padding: 0;
            margin: 0;
        }}
        .track-list li {{
            padding: 0.75rem 0;
            border-bottom: 1px solid #2a2a32;
        }}
        .track-header {{
            display: flex;
            align-items: baseline;
            gap: 1rem;
            margin-bottom: 0.5rem;
        }}
        .track-number {{
            width: 1.5rem;
            color: var(--muted);
            text-align: right;
            flex-shrink: 0;
        }}
        .track-title {{
            flex: 1;
            min-width: 0;
            word-break: break-word;
        }}
        .track-duration {{
            color: var(--muted);
            font-size: 0.875rem;
            flex-shrink: 0;
        }}
        .track-player audio {{
            width: 100%;
            margin: 0;
        }}
        .error {{
            color: var(--danger);
        }}
        @media (max-width: 600px) {{
            .header {{
                flex-direction: column;
                align-items: center;
                text-align: center;
            }}
            .cover {{
                width: 100%;
                height: auto;
                max-width: 12rem;
            }}
            .info {{
                min-width: auto;
                width: 100%;
            }}
            .track-header {{
                gap: 0.5rem;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="card">
            {body}
        </div>
    </div>
</body>
</html>"""


def _render_header(
    request: Request,
    title: str,
    subtitle: Optional[str] = None,
    description: Optional[str] = None,
    image: Optional[str] = None,
    meta: Optional[list[str]] = None,
    actions: Optional[list[str]] = None,
) -> str:
    """Render the header block with cover, title, metadata, and actions."""
    meta_html = "\n".join(f"<span>{_h(m)}</span>" for m in (meta or []) if m)
    actions_html = "\n".join(actions or [])
    cover = f'<img class="cover" src="{_h(image)}" alt="{_h(title)}">' if image else ""
    subtitle_html = f'<p class="description">{_h(subtitle)}</p>' if subtitle else ""
    description_html = (
        f'<p class="description">{_h(description)}</p>' if description and not subtitle else subtitle_html
    )
    return f"""<div class="header">
    {cover}
    <div class="info">
        <h1>{_h(title)}</h1>
        {description_html}
        <div class="meta">{meta_html}</div>
        <div class="actions">{actions_html}</div>
    </div>
</div>"""


def _track_row(track: Track, token: str, request: Request) -> str:
    """Render a single track row for lists."""
    audio_url = _track_audio_url(track, token)
    number = track.track_number or ""
    duration = _format_duration(track.duration)
    number_html = f'<span class="track-number">{_h(number)}</span>' if number else ""
    player = (
        f'<div class="track-player"><audio controls src="{_h(audio_url)}" preload="none"></audio></div>'
        if audio_url
        else ""
    )
    return f"""<li>
    <div class="track-header">
        {number_html}
        <span class="track-title">{_h(track.title)}</span>
        <span class="track-duration">{_h(duration)}</span>
    </div>
    {player}
</li>"""


def _track_list(tracks: list[Track], token: str, request: Request) -> str:
    """Render a list of tracks."""
    rows = "".join(_track_row(t, token, request) for t in tracks if t)
    return f'<ul class="track-list">{rows}</ul>'


def _render_track(item: Track, request: Request, token: str) -> str:
    """Render an HTML preview for a shared track."""
    title = item.title or "Shared track"
    artist = item.artist.name if item.artist else None
    album = item.album.title if item.album else None
    description = f"{artist or 'Unknown artist'}{f' · {album}' if album else ''}"
    audio_url = _track_audio_url(item, token)
    download_url = _track_audio_url(item, token, "attachment")
    cover = _track_cover_url(item)
    canonical = _abs_url(request, f"/api/v1/share/{token}")
    image = _abs_url(request, cover) if cover and not cover.startswith("http") else cover

    meta = [m for m in [artist, album, _format_duration(item.duration)] if m]
    actions = []
    if audio_url:
        actions.append(f'<audio controls src="{_h(audio_url)}" preload="none"></audio>')
    if download_url:
        actions.append(f'<a class="btn secondary" href="{_h(download_url)}">Download</a>')

    body = _render_header(
        request,
        title,
        description=description,
        image=image,
        meta=meta,
        actions=actions,
    )
    return _render_page(
        request,
        title,
        description,
        body,
        canonical,
        image=image,
        og_type="music.song",
        audio=_abs_url(request, audio_url) if audio_url else None,
    )


def _render_album(item: Album, request: Request, token: str) -> str:
    """Render an HTML preview for a shared album."""
    title = item.title or "Shared album"
    artist = item.artist.name if item.artist else None
    description = item.description
    meta = [m for m in [artist, str(item.release_year) if item.release_year else None] if m]
    cover = _album_cover_url(item, request, token)
    canonical = _abs_url(request, f"/api/v1/share/{token}")
    image = _abs_url(request, cover) if cover and not cover.startswith("http") else cover

    tracks = sorted(
        (t for t in (item.tracks or []) if t),
        key=lambda t: (t.disc_number or 0, t.track_number or 0, t.title or ""),
    )
    track_list = _track_list(tracks, token, request)
    body = (
        _render_header(
            request,
            title,
            subtitle=artist,
            description=description,
            image=image,
            meta=meta,
        )
        + f"<h2>Tracks</h2>\n{track_list}"
    )
    return _render_page(
        request,
        title,
        description or artist or "",
        body,
        canonical,
        image=image,
        og_type="music.album",
    )


def _render_artist(item: Artist, request: Request, token: str) -> str:
    """Render an HTML preview for a shared artist."""
    title = item.name or "Shared artist"
    image = _artist_image_url(item)
    canonical = _abs_url(request, f"/api/v1/share/{token}")
    image_abs = _abs_url(request, image) if image and not image.startswith("http") else image

    tracks = sorted(
        (t for t in (item.tracks or []) if t),
        key=lambda t: (t.album.title if t.album else "", t.track_number or 0),
    )[:50]
    body = (
        _render_header(
            request,
            title,
            image=image_abs,
        )
        + f"<h2>Tracks</h2>\n{_track_list(tracks, token, request)}"
    )
    return _render_page(
        request,
        title,
        "",
        body,
        canonical,
        image=image_abs,
        og_type="music.artist",
    )


def _render_playlist_or_library(item, request: Request, token: str, title: str) -> str:
    """Render an HTML preview for a shared playlist or library."""
    name = item.name
    owner = item.owner.username if item.owner else None
    description = item.description
    meta = [m for m in [owner] if m]
    canonical = _abs_url(request, f"/api/v1/share/{token}")

    if isinstance(item, Playlist):
        tracks = [pt.track for pt in (item.tracks or []) if pt and pt.track]
    else:
        tracks = [t for t in (item.tracks or []) if t]

    body = (
        _render_header(
            request,
            name,
            subtitle=title,
            description=description,
            meta=meta,
        )
        + f"<h2>Tracks</h2>\n{_track_list(tracks, token, request)}"
    )
    return _render_page(
        request,
        name,
        description or title,
        body,
        canonical,
        og_type="music.playlist",
    )


def _render_radio(item: Radio, request: Request, token: str) -> str:
    """Render an HTML preview for a shared radio station."""
    title = item.name or "Shared radio"
    owner = item.owner.username if item.owner else None
    description = item.description
    canonical = _abs_url(request, f"/api/v1/share/{token}")
    body = (
        _render_header(
            request,
            title,
            subtitle="Radio station",
            description=description,
            meta=[owner] if owner else [],
        )
        + "<p>Open the Songhive app to listen to this radio station.</p>"
    )
    return _render_page(
        request,
        title,
        description or "",
        body,
        canonical,
    )


def _render_file(item: StoredFile, request: Request, token: str) -> str:
    """Render an HTML preview for a shared file."""
    title = item.original_filename or f"File {item.id}"
    description = f"{item.content_type} · {_human_size(item.size)}"
    is_audio = item.content_type.startswith("audio/")
    canonical = _abs_url(request, f"/api/v1/share/{token}")
    download_url = _file_url(item.id, token, "attachment")
    audio_url = _file_url(item.id, token, "inline") if is_audio else None

    actions = [f'<a class="btn" href="{_h(download_url)}">Download</a>']
    if audio_url:
        actions.insert(
            0,
            f'<audio controls src="{_h(audio_url)}" preload="none"></audio>',
        )

    body = _render_header(
        request,
        title,
        description=description,
        actions=actions,
    )
    return _render_page(
        request,
        title,
        description,
        body,
        canonical,
        og_type="audio" if is_audio else "website",
        audio=_abs_url(request, audio_url) if audio_url else None,
    )


def _render_missing(request: Request) -> str:
    """Render a 404 HTML page for an invalid or expired share token."""
    title = "Link not found"
    body = f"""<h1 class="error">{_h(title)}</h1>
<p>The shared link is invalid, expired, or has been revoked.</p>"""
    return _render_page(
        request,
        title,
        "The shared link is invalid, expired, or has been revoked.",
        body,
        _base_url(request),
    )


def render_share_page(item: Any, item_type: str, token: str, request: Request) -> str:
    """Render the appropriate HTML preview for a shared item."""
    if item is None:
        return _render_missing(request)
    if item_type == "track":
        return _render_track(item, request, token)
    if item_type == "album":
        return _render_album(item, request, token)
    if item_type == "artist":
        return _render_artist(item, request, token)
    if item_type == "playlist":
        return _render_playlist_or_library(item, request, token, "Playlist")
    if item_type == "library":
        return _render_playlist_or_library(item, request, token, "Library")
    if item_type == "radio":
        return _render_radio(item, request, token)
    if item_type == "file":
        return _render_file(item, request, token)
    return _render_missing(request)
