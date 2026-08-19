"""
S3-compatible object storage backend.
"""

import tempfile
from pathlib import Path
from typing import BinaryIO, Optional

from .base import StorageBackend


class S3Storage(StorageBackend):
    """Store media files on S3-compatible object storage."""

    def __init__(
        self,
        bucket: str,
        endpoint_url: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        region: Optional[str] = None,
    ):
        self.bucket = bucket
        self.endpoint_url = endpoint_url
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region
        self._client = None

    def _get_client(self):
        """Lazily create the S3 client."""
        if self._client is None:
            import boto3

            kwargs = {}
            if self.endpoint_url:
                kwargs["endpoint_url"] = self.endpoint_url
            if self.region:
                kwargs["region_name"] = self.region
            if self.access_key and self.secret_key:
                kwargs["aws_access_key_id"] = self.access_key
                kwargs["aws_secret_access_key"] = self.secret_key

            self._client = boto3.client("s3", **kwargs)
        return self._client

    async def store(self, file: BinaryIO, path: str, content_type: Optional[str] = None) -> str:
        """Upload a file to S3."""
        client = self._get_client()
        extra_args = {}
        if content_type:
            extra_args["ContentType"] = content_type

        client.upload_fileobj(file, self.bucket, path, ExtraArgs=extra_args)
        return path

    async def retrieve(self, path: str) -> Optional[Path]:
        """Download a file from S3 to a temporary location."""
        client = self._get_client()
        try:
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=Path(path).suffix)
            client.download_fileobj(self.bucket, path, tmp)
            tmp.close()
            return Path(tmp.name)
        except client.exceptions.NoSuchKey:
            return None

    async def delete(self, path: str) -> bool:
        """Delete a file from S3."""
        client = self._get_client()
        try:
            client.delete_object(Bucket=self.bucket, Key=path)
            return True
        except Exception:
            return False

    async def exists(self, path: str) -> bool:
        """Check if a file exists in S3."""
        client = self._get_client()
        try:
            client.head_object(Bucket=self.bucket, Key=path)
            return True
        except Exception:
            return False

    async def url(self, path: str, cdn_prefix: Optional[str] = None) -> str:
        """Return the public URL for a stored S3 path."""
        if cdn_prefix:
            return f"{cdn_prefix.rstrip('/')}/{path}"
        if self.endpoint_url:
            return f"{self.endpoint_url.rstrip('/')}/{self.bucket}/{path}"
        return f"https://{self.bucket}.s3.{self.region or 'us-east-1'}.amazonaws.com/{path}"
