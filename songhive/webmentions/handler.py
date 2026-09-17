"""
Factory for a configured ``WebmentionsHandler``.

The handler facade wires the library's incoming/outgoing processors to the
configured storage, instance domain, timeouts, and User-Agent. The outgoing
processor is replaced by a subclass that drops targets pointing back at this
instance — local mentions are already handled by Songhive's own mention
pipeline, and self-delivered Webmentions would only duplicate them.
"""

from typing import Callable, Optional
from urllib.parse import urlparse

from webmentions import ContentTextFormat, Webmention, WebmentionsHandler
from webmentions.handlers._outgoing import OutgoingWebmentionsProcessor

from ..config.schema import SonghiveConfig, get_default_user_agent


class SonghiveOutgoingWebmentionsProcessor(OutgoingWebmentionsProcessor):
    """Outgoing processor that never delivers Webmentions to this instance."""

    def __init__(self, *args, local_domain: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self._local_domain = local_domain.lower()

    def _extract_targets(self, text: str, text_format: ContentTextFormat) -> set[str]:
        return {
            url
            for url in super()._extract_targets(text, text_format)
            if (urlparse(url).hostname or "").lower() != self._local_domain
        }


class SonghiveWebmentionsHandler(WebmentionsHandler):
    """``WebmentionsHandler`` with a same-domain-filtering outgoing processor."""

    def __init__(self, storage, *, local_domain: str = "", **kwargs):
        super().__init__(storage, **kwargs)
        outgoing: OutgoingWebmentionsProcessor = self.outgoing
        self.outgoing = SonghiveOutgoingWebmentionsProcessor(
            storage=storage,
            user_agent=outgoing._user_agent,
            http_timeout=outgoing._http_timeout,
            max_discovery_response_bytes=outgoing._max_discovery_response_bytes,
            on_mention_processed=outgoing._on_mention_processed,
            on_mention_deleted=outgoing._on_mention_deleted,
            local_domain=local_domain,
        )


def create_webmentions_handler(
    config: SonghiveConfig,
    storage,
    *,
    on_mention_processed: Optional[Callable[[Webmention], None]] = None,
    on_mention_deleted: Optional[Callable[[Webmention], None]] = None,
) -> SonghiveWebmentionsHandler:
    """Build a handler bound to the instance's public domain."""
    domain = config.federation.instance_domain.strip()
    return SonghiveWebmentionsHandler(
        storage,
        base_urls=[f"https://{domain}", f"http://{domain}"],
        http_timeout=config.webmentions.request_timeout,
        max_discovery_response_bytes=config.webmentions.discovery_max_bytes,
        user_agent=get_default_user_agent(),
        on_mention_processed=on_mention_processed,
        on_mention_deleted=on_mention_deleted,
        local_domain=domain,
    )
