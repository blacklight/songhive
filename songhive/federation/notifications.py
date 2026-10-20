"""
Notification hooks for incoming ActivityPub activities.

Maps federated interactions to user notifications — see
``notification_helpers`` (payload/snapshot helpers),
``notification_creators`` (follow/quote/reply/mention creation), and
``notification_lifecycle`` (Create handling, retraction, updates) for the
implementation. Public names are re-exported here for compatibility.
"""

from .notification_creators import (  # noqa: F401
    _create_follow_inbox_notification,
    _create_quote_inbox_notification,
    _process_incoming_quote_notification,
    _process_incoming_reply_notification,
    create_inbox_notifications,
    resolve_follow_request_notification,
    resolve_inbox_recipients,
)
from .notification_helpers import (  # noqa: F401
    _create_or_update_quote_notification,
    _note_snapshot,
    strip_quote_fallback,
)
from .notification_lifecycle import (  # noqa: F401
    _handle_inbox_create,
    _notify_like_announce,
    retract_inbox_notifications,
    update_inbox_notifications,
)
