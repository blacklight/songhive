"""
Browser-session cookie persistence for the YouTube provider.

yt-dlp dropped OAuth login — the only usable credentials for gated or
age-restricted content are cookies. This module accepts a captured browser
session in either of the shapes users can realistically export:

- ``request_headers`` — the raw request headers of an authenticated
  ``music.youtube.com``/``youtube.com`` request (what ``ytmusicapi setup``
  asks for); the ``Cookie`` header is extracted automatically.
- ``cookies`` — a Netscape cookie-file export (the format ``yt-dlp
  --cookies`` consumes), or a bare ``name=value; ...`` Cookie header.

Whatever the shape, the result is normalized to Netscape text and handed to
``yt_dlp`` as an in-memory cookie file, so a session captured once at
connect time keeps feeding downloads and stream resolution.
"""

import io
from typing import Optional

_NETSCAPE_MARKER = "netscape"
_COOKIE_HEADER_KEYS = ("cookie", "cookies")
# Extra request headers ytmusicapi needs for browser auth.
_AUTH_HEADER_KEYS = (
    "authorization",
    "cookie",
    "x-goog-authuser",
    "x-goog-visitor-id",
    "x-youtube-client-name",
    "x-youtube-client-version",
    "x-origin",
    "origin",
    "referer",
    "user-agent",
)

_YOUTUBE_COOKIE_DOMAIN = ".youtube.com"


def parse_request_headers(text: str) -> dict[str, str]:
    """Parse a raw ``Name: value`` request-headers block into a dict."""
    headers: dict[str, str] = {}
    if not isinstance(text, str):
        return headers
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        name, _, value = line.partition(":")
        name = name.strip().lower()
        value = value.strip()
        if name and value and name not in headers:
            headers[name] = value
    return headers


def auth_headers_for_config(config: dict) -> Optional[dict[str, str]]:
    """
    Return the header dict ytmusicapi consumes for browser auth.

    The raw ``request_headers`` capture is filtered down to the headers the
    YouTube Music internals actually inspect, so no unrelated browser state
    (sec-fetch, tracing headers) is retained.
    """
    raw = config.get("request_headers") or config.get("auth_headers")
    if isinstance(raw, dict):
        headers = {str(k).lower(): str(v) for k, v in raw.items()}
    elif isinstance(raw, str):
        headers = parse_request_headers(raw)
    else:
        return None
    filtered = {k: v for k, v in headers.items() if k in _AUTH_HEADER_KEYS}
    if not filtered.get("cookie"):
        return None
    filtered.setdefault("x-goog-authuser", "0")
    return filtered


def cookie_header_from_headers(headers: dict[str, str]) -> Optional[str]:
    """Extract the ``Cookie`` header value from a parsed header dict."""
    for key in _COOKIE_HEADER_KEYS:
        value = headers.get(key)
        if isinstance(value, str) and "=" in value:
            return value
    return None


def _iter_cookie_pairs(cookie_header: str):
    """Yield ``(name, value)`` pairs from a raw Cookie header."""
    for part in cookie_header.split(";"):
        name, sep, value = part.partition("=")
        if not sep:
            continue
        name = name.strip()
        value = value.strip().strip('"')
        if name:
            yield name, value


def cookie_header_to_netscape(cookie_header: str) -> str:
    """Render a ``name=value; ...`` cookie header as Netscape cookie text."""
    lines = [
        "# Netscape HTTP Cookie File",
        "# Generated from a captured browser session by Songhive.",
        "",
    ]
    for name, value in _iter_cookie_pairs(cookie_header):
        # domain, include-subdomain, path, secure, expires, name, value
        lines.append(f"{_YOUTUBE_COOKIE_DOMAIN}\tTRUE\t/\tTRUE\t0\t{name}\t{value}")
    return "\n".join(lines) + "\n"


def _looks_like_netscape(text: str) -> bool:
    if _NETSCAPE_MARKER in text.splitlines()[0].lower() if text.splitlines() else False:
        return True
    # Netscape rows are tab-separated seven-field lines.
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return stripped.count("\t") >= 6
    return False


def netscape_cookies_for_config(config: dict) -> Optional[str]:
    """
    Resolve Netscape cookie text from the stored config, or ``None``.

    Resolution order: explicit ``cookies`` (Netscape text or a raw Cookie
    header), then the ``Cookie`` line of a pasted ``request_headers``
    capture.
    """
    raw = config.get("cookies")
    if isinstance(raw, str) and raw.strip():
        if _looks_like_netscape(raw.strip()):
            return raw
        return cookie_header_to_netscape(raw.strip())

    headers = auth_headers_for_config(config)
    if headers:
        cookie_header = cookie_header_from_headers(headers)
        if cookie_header:
            return cookie_header_to_netscape(cookie_header)
    return None


def cookie_file_for_ytdlp(config: dict):
    """Return an in-memory Netscape cookie file for ``YoutubeDL`` or ``None``."""
    text = netscape_cookies_for_config(config)
    if text is None:
        return None
    return io.StringIO(text)
