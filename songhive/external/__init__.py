"""
External-library adapter package.

Importing this package registers all built-in adapters so that the API,
worker, and CLI consistently resolve the same provider types.
"""

from ._dropbox import DROPBOX_OAUTH_SPEC, DropboxExternalAdapter
from ._local import LocalExternalAdapter
from ._s3 import S3ExternalAdapter
from ._sftp import SFTPExternalAdapter
from ._webdav import WebDAVExternalAdapter
from .oauth import register_oauth_provider
from .registry import register_external_adapter

register_external_adapter("dropbox", DropboxExternalAdapter)
register_external_adapter("local", LocalExternalAdapter)
register_external_adapter("s3", S3ExternalAdapter)
register_external_adapter("sftp", SFTPExternalAdapter)
register_external_adapter("webdav", WebDAVExternalAdapter)

register_oauth_provider(DROPBOX_OAUTH_SPEC)
