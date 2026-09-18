"""
Third-party API adapter package.

Adapters expose Songhive content through foreign API surfaces (Subsonic,
and in the future Jellyfin, Icecast, Mopidy, ...) so that ecosystem clients
can browse and stream from the instance. Importing this package registers
all built-in adapters so the app and the Tornado bootstrap consistently
resolve the same adapter set.
"""

from .registry import (
    adapter_tornado_routes,
    get_adapter,
    list_adapters,
    mount_adapters,
    register_adapter,
)
from .subsonic import SubsonicAdapter

register_adapter(SubsonicAdapter.name, SubsonicAdapter)

__all__ = [
    "adapter_tornado_routes",
    "get_adapter",
    "list_adapters",
    "mount_adapters",
    "register_adapter",
]
