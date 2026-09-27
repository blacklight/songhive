"""
TIDAL stream resolution: playbackinfo manifests → ``ExternalStream``.

Two manifest shapes exist:

- **BTS** (``application/vnd.tidal.bts``) — a JSON manifest with a single
  seekable CDN URL (FLAC/AAC). Exposed as a ``kind="url"`` stream; the
  client's ``Range`` header is forwarded by the serving layer.
- **MPD** (``application/dash+xml``) — DASH with an init segment followed
  by numbered media segments. Exposed as a ``kind="iterator"`` stream that
  yields the segments concatenated into a fragmented MP4 (no seeking).

The resolved manifest choice is cached in Redis for 60 s per
``(track id, quality)`` so a client's probe + range burst costs a single
``playbackinfopostpaywall`` call. Anything longer-lived would outlive the
signed URLs' validity.
"""

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Optional

import httpx
from redis.asyncio import Redis

from ..errors import ExternalItemNotFound, ExternalPermissionDenied
from ..types import ExternalItemRef, ExternalStream
from .api import TidalApiClient, decode_bts_manifest, decode_mpd_manifest, is_allowed_media_url
from .conf import max_rps, mpd_mode
from .session import effective_quality, session_for_config

logger = logging.getLogger(__name__)

_MANIFEST_PREFIX = "songhive:tidal:manifest:"
_MANIFEST_TTL_SECONDS = 60
_SEGMENT_TIMEOUT_SECONDS = 60
_SEGMENT_CHUNK_BYTES = 256 * 1024


async def open_stream(
    config: dict,
    item: ExternalItemRef,
    *,
    range: Optional[tuple[int, int]] = None,
    redis: Optional[Redis] = None,
) -> ExternalStream:
    """Resolve the track's playback manifest into an ``ExternalStream``."""
    quality = effective_quality(config)
    manifest = await _resolve_manifest(config, item.provider_key, quality, redis)

    urls = manifest.get("urls") or []
    mode = manifest.get("mode")
    if manifest.get("encrypted") or not urls or mode not in ("bts", "mpd"):
        raise ExternalItemNotFound(
            "TIDAL stream is not available for this track",
            provider_key=item.provider_key,
        )

    for url in urls:
        if not is_allowed_media_url(url):
            raise ExternalPermissionDenied(
                "TIDAL returned a stream URL outside the media CDN",
                operation="open_stream",
            )

    if mode == "mpd":
        if mpd_mode(config) == "remux":
            # Seekable playback: ffmpeg stream-copies the segments into the
            # remote-audio cache once per (track, quality), then serves the
            # remuxed file from disk.
            from .remux import remux_stream

            return await remux_stream(item.provider_key, quality, urls, codec=manifest.get("codec"))
        # Segmented fMP4: concatenate init + media segments. Range requests
        # are not satisfiable against a joined stream.
        return ExternalStream(
            kind="iterator",
            iterator=_dash_segments(urls),
            content_type=manifest.get("mime_type") or "audio/mp4",
            size=None,
            supports_range=False,
            headers={},
        )

    return ExternalStream(
        kind="url",
        url=urls[0],
        content_type=manifest.get("mime_type") or "audio/flac",
        size=item.size,
        supports_range=True,
        headers={},
        safe_to_redirect=bool(config.get("redirect_streams")),
    )


async def _resolve_manifest(
    config: dict,
    track_id: str,
    quality: str,
    redis: Optional[Redis],
) -> dict:
    """Return the manifest descriptor, using the 60 s Redis micro-cache."""
    cache_key = f"{_MANIFEST_PREFIX}{track_id}:{quality}"
    if redis is not None:
        try:
            raw = await redis.get(cache_key)
            if raw:
                cached = json.loads(raw)
                if isinstance(cached, dict):
                    return cached
        except Exception:
            logger.debug("TIDAL manifest cache entry unreadable; refetching")

    manifest = await _fetch_manifest(config, track_id, quality, redis)

    if redis is not None:
        try:
            await redis.set(cache_key, json.dumps(manifest), ex=_MANIFEST_TTL_SECONDS)
        except Exception:
            logger.debug("Failed to cache TIDAL manifest", exc_info=True)
    return manifest


async def _fetch_manifest(config: dict, track_id: str, quality: str, redis: Optional[Redis]) -> dict:
    """Call ``playbackinfopostpaywall`` and decode the manifest payload."""
    session = await session_for_config(config, redis)
    client = TidalApiClient(session, max_rps=max_rps(config))

    response = await client.get_json(
        client.playback_info_path(track_id),
        params={
            "playbackmode": "STREAM",
            "audioquality": quality,
            "assetpresentation": "FULL",
        },
    )
    data = response.data if isinstance(response.data, dict) else {}
    mime = str(data.get("manifestMimeType") or "")
    blob = data.get("manifest")
    if not isinstance(blob, str) or not blob:
        raise ExternalItemNotFound(
            "TIDAL returned no stream manifest for this track",
            provider_key=track_id,
        )

    if "vnd.tidal.bts" in mime:
        return _decode_bts(blob, track_id)
    if "dash" in mime:
        return await asyncio.to_thread(_decode_mpd, blob, track_id)
    raise ExternalItemNotFound(
        f"TIDAL manifest type {mime!r} is unsupported",
        provider_key=track_id,
    )


def _decode_bts(manifest_b64: str, track_id: str) -> dict:
    """Decode a BTS manifest into the normalized descriptor."""
    parsed = decode_bts_manifest(manifest_b64)
    if not parsed:
        raise ExternalItemNotFound(
            "TIDAL BTS manifest could not be decoded",
            provider_key=track_id,
        )
    encryption = str(parsed.get("encryptionType") or "NONE").upper()
    return {
        "mode": "bts",
        "urls": [str(u) for u in parsed.get("urls") or [] if isinstance(u, str)],
        "mime_type": parsed.get("mimeType") or "audio/flac",
        "encrypted": encryption not in ("", "NONE"),
    }


def _decode_mpd(manifest_b64: str, track_id: str) -> dict:
    """Decode a DASH manifest and expand its init + media segment URLs."""
    xml = decode_mpd_manifest(manifest_b64)
    if xml is None:
        raise ExternalItemNotFound(
            "TIDAL MPD manifest could not be decoded",
            provider_key=track_id,
        )
    rep, adaptation, template = _mpd_representation(xml)
    urls = _template_segment_urls(template)
    if not urls:
        raise ExternalItemNotFound(
            "TIDAL MPD manifest contains no segments",
            provider_key=track_id,
        )
    return {
        "mode": "mpd",
        "urls": urls,
        "mime_type": "audio/mp4",
        "codec": _mpd_codec(rep, adaptation),
        "encrypted": False,
    }


def _mpd_codec(rep: Any, adaptation: Any) -> Optional[str]:
    """Normalize the representation's ``codecs`` attr to flac/aac/other."""
    raw = getattr(rep, "codecs", None) or getattr(adaptation, "codecs", None)
    codec = str(raw).strip().lower() if raw else ""
    if not codec:
        return None
    if "flac" in codec:
        return "flac"
    if codec.startswith("mp4a") or "aac" in codec:
        return "aac"
    return "other"


def _mpd_representation(mpd_xml: str) -> tuple[Any, Any, Any]:
    """Parse an MPD and return ``(representation, adaptation_set, template)``."""
    try:
        from mpegdash.parser import MPEGDASHParser
    except ImportError as exc:  # pragma: no cover - tidalapi dependency
        raise ExternalItemNotFound("mpegdash is unavailable") from exc

    try:
        mpd = MPEGDASHParser.parse(mpd_xml)
        adaptation = mpd.periods[0].adaptation_sets[0]
        rep = adaptation.representations[0]
        template = rep.segment_templates[0]
    except (AttributeError, IndexError, TypeError) as exc:
        raise ExternalItemNotFound("TIDAL MPD manifest is malformed") from exc
    return rep, adaptation, template


def _mpd_segment_urls(mpd_xml: str) -> list[str]:
    """Return ``[init_url, seg_1, seg_2, ...]`` from a SegmentTemplate MPD."""
    _, _, template = _mpd_representation(mpd_xml)
    return _template_segment_urls(template)


def _template_segment_urls(template: Any) -> list[str]:
    """Expand a SegmentTemplate into ``[init_url, seg_1, seg_2, ...]``."""
    urls: list[str] = []
    init = getattr(template, "initialization", None)
    if init:
        urls.append(str(init))
    media = getattr(template, "media", None)
    if not media:
        return urls

    count = 0
    timelines = template.segment_timelines or []
    for timeline in timelines:
        for s in timeline.Ss or []:
            count += 1 + (s.r or 0)
    if count == 0:
        # No timeline: fall back to the duration/timescale estimate.
        duration_s = getattr(getattr(template, "duration", None), "total_seconds", lambda: 0)()
        timescale = getattr(template, "timescale", None) or 1
        segment_dur = getattr(template, "duration", None)
        if isinstance(segment_dur, (int, float)) and timescale:
            approx = duration_s if duration_s else 0
            count = int(approx // (segment_dur / timescale)) if approx else 0
    if count <= 0:
        return urls

    start = getattr(template, "start_number", None) or 1
    for index in range(start, start + count):
        urls.append(media.replace("$Number$", str(index)))
    return urls


async def _dash_segments(urls: list[str]) -> AsyncIterator[bytes]:
    """Yield the init segment followed by each media segment's bytes."""
    timeout = httpx.Timeout(_SEGMENT_TIMEOUT_SECONDS)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for url in urls:
            try:
                async with client.stream("GET", url) as response:
                    if response.status_code >= 400:
                        logger.warning(
                            "TIDAL segment fetch failed (%s): %s",
                            response.status_code,
                            url[:120],
                        )
                        return
                    async for chunk in response.aiter_bytes(_SEGMENT_CHUNK_BYTES):
                        if chunk:
                            yield chunk
            except httpx.HTTPError:
                logger.warning("TIDAL segment fetch failed: %s", url[:120], exc_info=True)
                return
