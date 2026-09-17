"""
Factory for a configured ``WebmentionsHandler``.

The handler facade wires the library's incoming/outgoing processors to the
configured storage, instance domain, timeouts, and User-Agent. The
library's built-in SSRF protection (``ssrf_protection=True``,
``webmentions.handlers._fetch.fetch_guarded``) stays enabled: every
fetched URL — including each redirect hop — is validated against
non-public addresses and response bodies are capped, on both the incoming
source-parse path and the outgoing discovery/delivery path. Policy
violations raise ``ValueError`` and surface as rejected Webmentions;
network errors propagate as ``requests.RequestException`` so the task's
``autoretry_for`` can retry transient failures.

``exclude_local_targets=True`` makes the outgoing processor drop targets
that point back at this instance — local mentions are already handled by
Songhive's own mention pipeline, and self-delivered Webmentions would only
duplicate them.
"""

from typing import Callable, Optional

from webmentions import Webmention, WebmentionsHandler

from ..config.schema import SonghiveConfig, get_default_user_agent


def create_webmentions_handler(
    config: SonghiveConfig,
    storage,
    *,
    on_mention_processed: Optional[Callable[[Webmention], None]] = None,
    on_mention_deleted: Optional[Callable[[Webmention], None]] = None,
) -> WebmentionsHandler:
    """Build a handler bound to the instance's public domain."""
    domain = config.federation.instance_domain.strip()
    return WebmentionsHandler(
        storage,
        base_urls=[f"https://{domain}", f"http://{domain}"],
        http_timeout=config.webmentions.request_timeout,
        max_discovery_response_bytes=config.webmentions.discovery_max_bytes,
        max_source_response_bytes=config.webmentions.discovery_max_bytes,
        ssrf_protection=True,
        exclude_local_targets=True,
        user_agent=get_default_user_agent(),
        on_mention_processed=on_mention_processed,
        on_mention_deleted=on_mention_deleted,
    )
