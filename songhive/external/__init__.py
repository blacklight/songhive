"""
External-library adapter package.

Importing this package registers all built-in adapters so that the API,
worker, and CLI consistently resolve the same provider types.
"""

from ._local import LocalExternalAdapter
from .registry import register_external_adapter

register_external_adapter("local", LocalExternalAdapter)
