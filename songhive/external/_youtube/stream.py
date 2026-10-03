"""
Audio and video stream resolution for YouTube items.

The default rendition is the best audio-only format (players treat the
track like any other audio file). With ``variant="video"`` — opt-in per
request, gated by ``video_playback`` — the best combined video+audio format
is resolved instead, so the embedded player can render the music video.

Resolution delegates to :func:`ytdlp.resolve_stream_descriptor`, which
caches the signed googlevideo URL in Redis; the item stays a
``kind="url"`` ``ExternalStream`` so Tornado proxies the bytes (cookies +
googlevideo URLs aren't safe to hand to a browser directly).
"""

import logging
from typing import Optional

from redis.asyncio import Redis

from ..errors import ExternalPermissionDenied
from ..types import ExternalItemRef, ExternalStream
from .conf import video_playback_allowed
from .ytdlp import resolve_stream_descriptor

logger = logging.getLogger(__name__)


async def open_stream(
    config: dict,
    item: ExternalItemRef,
    *,
    variant: str = "audio",
    range: Optional[tuple[int, Optional[int]]] = None,
    redis: Optional[Redis] = None,
) -> ExternalStream:
    """Resolve ``item`` to a direct YouTube media URL."""
    if variant == "video" and not video_playback_allowed():
        raise ExternalPermissionDenied(
            "Video playback is disabled for YouTube on this instance",
            operation="open_stream",
        )
    descriptor = await resolve_stream_descriptor(
        config,
        item.provider_key,
        variant=variant,
        redis=redis,
    )
    return ExternalStream(
        kind="url",
        url=descriptor.url,
        content_type=descriptor.mime,
        size=descriptor.size,
        supports_range=True,
        headers=descriptor.http_headers or {},
        # googlevideo URLs are tied to the requesting session's cookies and
        # IP — proxy through Tornado rather than redirecting the browser.
        safe_to_redirect=False,
    )
