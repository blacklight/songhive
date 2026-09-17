"""
SSRF-guarded remote fetch helpers for explicit remote content lookup.

Every remote dereference goes through :func:`guarded_fetch`, which enforces:

- ``http``/``https`` schemes only;
- no localhost names, IP literals, or DNS results that resolve to
  loopback/private/link-local/multicast/unspecified addresses (this covers
  the cloud metadata address ``169.254.169.254``);
- re-validation of every redirect target (SSRF and caller-supplied checks),
  capped at ``MAX_REDIRECTS`` hops;
- connect/read timeouts and a response body size cap;
- an allowlist of ActivityPub-compatible response content types.

The functions are synchronous (``requests``-based) and must be invoked
through ``asyncio.to_thread`` from async code, matching the codebase's
federation conventions.
"""

import ipaddress
import json
import logging
import socket
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urljoin, urlparse

import requests

from ..config.schema import get_default_user_agent

logger = logging.getLogger(__name__)

FETCH_TIMEOUT = 20.0
MAX_REDIRECTS = 3
MAX_BODY_BYTES = 1024 * 1024  # 1 MiB — ActivityPub documents are small

#: Response media types considered ActivityPub-compatible.
ALLOWED_CONTENT_TYPES = frozenset(
    {
        "application/activity+json",
        "application/ld+json",
        "application/jrd+json",
        "application/json",
        "text/json",
    }
)

_AP_ACCEPT = (
    "application/activity+json, "
    'application/ld+json; profile="https://www.w3.org/ns/activitystreams", '
    "application/json;q=0.8"
)


class FetchError(Exception):
    """A guarded fetch failed or was rejected before/during the request."""

    def __init__(self, message: str, *, status_code: int = 502, url: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.url = url


class FetchNotFound(FetchError):
    """The remote server answered 404/410 — the object is gone."""

    def __init__(self, message: str, *, url: Optional[str] = None):
        super().__init__(message, status_code=404, url=url)


@dataclass
class FetchResult:
    """A successful guarded fetch."""

    url: str  # final URL after redirects
    status_code: int
    content_type: str
    body: bytes
    headers: dict = field(default_factory=dict)

    def json(self):
        """Decode the body as JSON, raising ``FetchError`` on bad payloads."""
        try:
            return json.loads(self.body.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            raise FetchError(f"Invalid JSON in remote document: {exc}", url=self.url) from exc


def _host_is_public(hostname: str) -> bool:
    """Return whether every resolved address of ``hostname`` is globally routable."""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        # Unresolvable hosts cannot be fetched and are not an SSRF risk.
        return True
    try:
        addresses = {ipaddress.ip_address(info[4][0]) for info in infos}
    except ValueError:
        return False
    return all(ip.is_global for ip in addresses) if addresses else False


def validate_fetch_url(url: str) -> None:
    """
    Reject ``url`` unless fetching it is safe from an SSRF standpoint.

    Only ``http``/``https`` URLs on public hosts pass: localhost-style names,
    IP literals and DNS answers that are loopback/private/link-local/
    multicast/unspecified are refused, which includes the cloud metadata
    address ``169.254.169.254``. Raises ``FetchError`` (status 400) on
    rejection.
    """
    parsed = urlparse(url)
    hostname = parsed.hostname
    if parsed.scheme not in ("http", "https") or not hostname:
        raise FetchError(f"Unsupported URL scheme or host: {url}", status_code=400, url=url)

    host = hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        raise FetchError(f"Refusing to fetch localhost URL: {url}", status_code=400, url=url)

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if not ip.is_global:
            raise FetchError(f"Refusing to fetch non-public address: {url}", status_code=400, url=url)
        return

    if not _host_is_public(host):
        raise FetchError(f"Refusing to fetch host on a non-public address: {url}", status_code=400, url=url)


def guarded_fetch(
    url: str,
    *,
    method: str = "GET",
    headers: Optional[dict] = None,
    timeout: float = FETCH_TIMEOUT,
    max_bytes: int = MAX_BODY_BYTES,
    max_redirects: int = MAX_REDIRECTS,
    check_url: Optional[Callable[[str], None]] = None,
    sign: Optional[Callable[[str, dict], dict]] = None,
) -> FetchResult:
    """
    Fetch ``url`` with SSRF protection, returning a ``FetchResult``.

    Every hop — the initial URL and each redirect target — is validated by
    :func:`validate_fetch_url` and the optional ``check_url`` callback (used
    by callers to re-apply domain allow/block rules per hop). Redirects are
    followed manually, capped at ``max_redirects``.

    ``sign`` is an optional callable invoked per hop as
    ``sign(url, headers) -> dict``; its return value is merged into the
    request headers. Use it for HTTP signatures so each redirect target is
    signed for its own host.

    ``FetchNotFound`` is raised for 404/410 responses; ``FetchError`` for
    other non-2xx responses, disallowed content types, oversized bodies,
    and network failures.
    """
    if method != "GET":
        raise FetchError(f"Unsupported fetch method: {method}", status_code=400, url=url)

    base_headers = {
        "Accept": _AP_ACCEPT,
        "User-Agent": get_default_user_agent(),
    }
    if headers:
        base_headers.update(headers)

    session = requests.Session()
    current = url
    try:
        for _ in range(max_redirects + 1):
            validate_fetch_url(current)
            if check_url is not None:
                check_url(current)
            request_headers = dict(base_headers)
            if sign is not None:
                request_headers.update(sign(current, request_headers) or {})
            try:
                response = session.get(
                    current,
                    timeout=timeout,
                    allow_redirects=False,
                    stream=True,
                    headers=request_headers,
                )
            except FetchError:
                raise
            except requests.RequestException as exc:
                raise FetchError(f"Remote fetch failed: {exc}", url=current) from exc

            try:
                if response.is_redirect or response.is_permanent_redirect:
                    location = response.headers.get("Location")
                    if not location:
                        raise FetchError("Redirect without Location header", url=current)
                    current = urljoin(current, location)
                    continue

                if response.status_code in (404, 410):
                    raise FetchNotFound(f"Remote object not found ({response.status_code})", url=current)
                if response.status_code != 200:
                    raise FetchError(
                        f"Remote server returned {response.status_code}",
                        status_code=502,
                        url=current,
                    )

                content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
                if content_type and content_type not in ALLOWED_CONTENT_TYPES:
                    raise FetchError(f"Unexpected remote content type: {content_type}", url=current)

                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_content(64 * 1024):
                    chunks.append(chunk)
                    size += len(chunk)
                    if size >= max_bytes:
                        break
                body = b"".join(chunks)[:max_bytes]
                return FetchResult(
                    url=current,
                    status_code=response.status_code,
                    content_type=content_type,
                    body=body,
                    headers={
                        "etag": response.headers.get("ETag"),
                        "last_modified": response.headers.get("Last-Modified"),
                    },
                )
            finally:
                response.close()
    finally:
        session.close()

    raise FetchError(f"Too many redirects fetching {url}", status_code=502, url=url)
