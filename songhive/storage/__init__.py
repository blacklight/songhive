from typing import TYPE_CHECKING

from .base import StorageBackend
from .local import LocalStorage
from .s3 import S3Storage

if TYPE_CHECKING:
    from ..config.schema import StorageConfig

__all__ = ["StorageBackend", "LocalStorage", "S3Storage", "get_storage"]


def get_storage(config: "StorageConfig") -> StorageBackend:
    """Create a storage backend from the provided storage configuration."""
    if config.backend == "s3":
        if not config.s3_bucket:
            raise ValueError("S3 backend requires s3_bucket to be set")
        return S3Storage(
            bucket=config.s3_bucket,
            endpoint_url=config.s3_endpoint,
            access_key=config.s3_access_key,
            secret_key=config.s3_secret_key,
            region=config.s3_region,
            max_upload_size=config.max_upload_size,
        )
    return LocalStorage(base_path=config.local_path, max_upload_size=config.max_upload_size)
