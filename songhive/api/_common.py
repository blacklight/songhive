"""Shared helpers used by both FastAPI routes and middleware."""

import ipaddress
import logging
from dataclasses import dataclass
from typing import Optional

from fastapi import Query, Request, Response

logger = logging.getLogger(__name__)


def client_ip(  # pylint: disable=too-many-branches
    request: Request,
    *,
    trusted_hops: Optional[int] = None,
) -> Optional[str]:
    """
    Return the client IP address, honoring common proxy headers.

    The lookup order is:

    1. ``X-Forwarded-For`` if ``trusted_hops`` is configured or the header
       is present (leftmost valid IP is treated as the originating client).
    2. ``X-Real-IP``.
    3. The RFC 7239 ``Forwarded`` header (``for=...`` parameter).
    4. ``request.client.host``.

    ``trusted_hops`` controls how many entries from the right of the
    ``X-Forwarded-For`` chain are skipped. A value of ``0`` means the header
    is not trusted and is ignored. If ``None`` (the default), the header is
    used without a configured trust depth and the leftmost valid address is
    returned.
    """
    forwarded: Optional[str] = request.headers.get("X-Forwarded-For")
    if forwarded and trusted_hops != 0:
        parts = [ip.strip() for ip in forwarded.split(",") if ip.strip()]
        if trusted_hops is not None and trusted_hops > 0:
            if len(parts) > trusted_hops:
                candidate = parts[-(trusted_hops + 1)]
                if _is_valid_ip(candidate):
                    return candidate
        else:
            for candidate in parts:
                if _is_valid_ip(candidate):
                    return candidate

    real_ip: Optional[str] = request.headers.get("X-Real-IP")
    if real_ip:
        candidate = real_ip.strip()
        if _is_valid_ip(candidate):
            return candidate

    forwarded_rfc: Optional[str] = request.headers.get("Forwarded")
    if forwarded_rfc:
        for directive in forwarded_rfc.replace(";", ",").split(","):
            directive = directive.strip()
            if directive.lower().startswith("for="):
                value = directive[4:].strip().strip('"')
                if value.startswith("["):
                    value = value.split("]")[0][1:]
                elif ":" in value:
                    value = value.rsplit(":", 1)[0]
                if value and value != "_hidden" and not value.startswith("_") and _is_valid_ip(value):
                    return value

    if request.client and request.client.host:
        return request.client.host

    return None


def _is_valid_ip(value: str) -> bool:
    """Return True if ``value`` is a valid IPv4 or IPv6 address."""
    try:
        ipaddress.ip_address(value)
    except ValueError:
        logger.debug("Ignoring invalid IP address %r", value)
        return False
    return True


@dataclass
class Pagination:
    """Pagination parameters and helper to set the ``X-Total-Count`` header."""

    limit: int
    offset: int

    def set_total(self, response: Response, total: int) -> None:
        """Set the ``X-Total-Count`` header on ``response``."""
        response.headers["X-Total-Count"] = str(total)


async def get_pagination(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> Pagination:
    """FastAPI dependency that parses ``limit``/``offset`` query params."""
    return Pagination(limit=limit, offset=offset)
