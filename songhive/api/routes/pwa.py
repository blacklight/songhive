"""
Progressive Web App support routes.

Serves a dynamic web app manifest so the PWA name and theme can follow the
configured instance name and the user's current theme preference.
"""

import re
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from ...config.schema import SonghiveConfig
from ..deps import get_config

router = APIRouter(tags=["pwa"])

_THEME_COLORS: dict[str, dict[str, str]] = {
    "light": {
        "theme_color": "#f9f8f7",
        "background_color": "#f9f8f7",
    },
    "dark": {
        "theme_color": "#1f2927",
        "background_color": "#1f2927",
    },
}

_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

_ICON_SIZES = [72, 96, 128, 144, 152, 192, 384, 512]


def _short_name(name: str) -> str:
    """Return a short_name that fits PWA home-screen recommendations."""
    if len(name) <= 12:
        return name
    return f"{name[:11]}…"


def _manifest_icons() -> list[dict[str, Any]]:
    """Build the standard icon list for the PWA manifest."""
    icons: list[dict[str, Any]] = [
        {
            "src": "/pwa/apple-touch-icon.png",
            "sizes": "180x180",
            "type": "image/png",
            "purpose": "any",
        },
    ]
    for size in _ICON_SIZES:
        icons.append(
            {
                "src": f"/pwa/pwa-{size}x{size}.png",
                "sizes": f"{size}x{size}",
                "type": "image/png",
                "purpose": "any",
            }
        )
    for size in (192, 512):
        icons.append(
            {
                "src": f"/pwa/maskable-{size}x{size}.png",
                "sizes": f"{size}x{size}",
                "type": "image/png",
                "purpose": "maskable any",
            }
        )
    return icons


def _manifest(
    request: Request,
    config: SonghiveConfig,
    theme: str,
    accent: str | None,
) -> dict[str, Any]:
    """Build a web app manifest for the current instance."""
    colors = _THEME_COLORS.get(theme, _THEME_COLORS["light"])
    theme_color = accent if accent and _HEX_COLOR_RE.match(accent) else colors["theme_color"]
    name = config.federation.instance_name or "Songhive"
    short_name = _short_name(name)

    if config.federation.instance_domain:
        site_url = f"https://{config.federation.instance_domain}"
    else:
        site_url = f"{request.url.scheme}://{request.url.hostname}"
        if request.url.port:
            site_url = f"{site_url}:{request.url.port}"

    return {
        "id": site_url,
        "name": name,
        "short_name": short_name,
        "description": config.federation.instance_description or "A federated music sharing service",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "any",
        "theme_color": theme_color,
        "background_color": colors["background_color"],
        "lang": "en",
        "dir": "ltr",
        "categories": ["music", "entertainment"],
        "icons": _manifest_icons(),
    }


@router.get("/manifest.webmanifest")
async def pwa_manifest(
    request: Request,
    config: SonghiveConfig = Depends(get_config),
    theme: str = Query(default="light", pattern=r"^(light|dark)$"),
    accent: str | None = Query(default=None, max_length=7),
):
    """Return the web app manifest for this Songhive instance."""
    manifest = _manifest(request, config, theme, accent)
    return JSONResponse(
        content=manifest,
        media_type="application/manifest+json",
    )


@router.get("/manifest.json")
async def pwa_manifest_json(
    request: Request,
    config: SonghiveConfig = Depends(get_config),
    theme: str = Query(default="light", pattern=r"^(light|dark)$"),
    accent: str | None = Query(default=None, max_length=7),
):
    """Return the same manifest as JSON for clients that prefer .json."""
    manifest = _manifest(request, config, theme, accent)
    return JSONResponse(content=manifest)
