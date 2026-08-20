"""
S3-compatible object storage backend using aioboto3.

Large files are uploaded using S3 multipart uploads so the whole object does
not have to be buffered in memory. Unknown-size streams are also streamed
through multipart to avoid unbounded memory growth. Retrieval streams from S3
into a temporary local file.
"""

import os
import stat
import tempfile
from pathlib import Path
from typing import BinaryIO, Optional

import aioboto3
import aiofiles
import aiofiles.os
from botocore.exceptions import BotoCoreError, ClientError

from .base import StorageBackend


class S3Storage(StorageBackend):
    """Store media files on S3-compatible object storage using aioboto3."""

    _MULTIPART_THRESHOLD = 5 * 1024 * 1024
    _MULTIPART_PART_SIZE = _MULTIPART_THRESHOLD
    _CHUNK_SIZE = 64 * 1024

    def __init__(
        self,
        bucket: str,
        *,
        endpoint_url: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        region: Optional[str] = None,
        max_upload_size: Optional[int] = None,
    ):
        super().__init__(max_upload_size=max_upload_size)
        self.bucket = bucket
        self.endpoint_url = endpoint_url
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region

    def _get_client(self):
        """Create an async S3 client context manager."""
        kwargs = {}
        if self.endpoint_url:
            kwargs["endpoint_url"] = self.endpoint_url
        if self.region:
            kwargs["region_name"] = self.region
        if self.access_key and self.secret_key:
            kwargs["aws_access_key_id"] = self.access_key
            kwargs["aws_secret_access_key"] = self.secret_key

        return aioboto3.Session().client("s3", **kwargs)

    @staticmethod
    def _is_missing_error(exc: ClientError) -> bool:
        """Return True when an S3 client error indicates the object is missing."""
        error_code = exc.response.get("Error", {}).get("Code")
        if error_code == "NoSuchBucket":
            return False
        if error_code in ("NoSuchKey", "NotFound"):
            return True
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        return status == 404

    async def store(self, file: BinaryIO, path: str, content_type: Optional[str] = None) -> str:
        """Upload a file to S3, using multipart uploads for large files."""
        size = self._file_size(file)
        self._rewind(file)
        self._check_upload_size(size)

        extra_args = {}
        if content_type:
            extra_args["ContentType"] = content_type

        async with self._get_client() as client:
            if size is not None and size <= self._MULTIPART_THRESHOLD:
                data = file.read(size) if size > 0 else b""
                self._check_upload_size(len(data))
                await client.put_object(
                    Bucket=self.bucket,
                    Key=path,
                    Body=data,
                    ContentLength=len(data),
                    **extra_args,
                )
                return path

            create_resp = await client.create_multipart_upload(
                Bucket=self.bucket,
                Key=path,
                **extra_args,
            )
            upload_id = create_resp["UploadId"]
            parts: list[dict[str, object]] = []
            part_number = 1
            part_buffer = bytearray()
            total = 0

            try:
                while chunk := file.read(self._CHUNK_SIZE):
                    total += len(chunk)
                    self._check_upload_size(total)
                    part_buffer.extend(chunk)

                    while len(part_buffer) >= self._MULTIPART_PART_SIZE:
                        part = bytes(part_buffer[: self._MULTIPART_PART_SIZE])
                        del part_buffer[: self._MULTIPART_PART_SIZE]
                        part_resp = await client.upload_part(
                            Bucket=self.bucket,
                            Key=path,
                            UploadId=upload_id,
                            PartNumber=part_number,
                            Body=part,
                        )
                        parts.append({"ETag": part_resp["ETag"], "PartNumber": part_number})
                        part_number += 1

                if part_buffer:
                    part_resp = await client.upload_part(
                        Bucket=self.bucket,
                        Key=path,
                        UploadId=upload_id,
                        PartNumber=part_number,
                        Body=bytes(part_buffer),
                    )
                    parts.append({"ETag": part_resp["ETag"], "PartNumber": part_number})
                    part_number += 1

                await client.complete_multipart_upload(
                    Bucket=self.bucket,
                    Key=path,
                    UploadId=upload_id,
                    MultipartUpload={"Parts": parts},
                )
            except Exception:
                await client.abort_multipart_upload(
                    Bucket=self.bucket,
                    Key=path,
                    UploadId=upload_id,
                )
                raise

        return path

    async def retrieve(self, path: str) -> Optional[Path]:
        """Download a file from S3 to a temporary location."""
        async with self._get_client() as client:
            try:
                response = await client.get_object(Bucket=self.bucket, Key=path)
            except client.exceptions.NoSuchKey:
                return None
            except ClientError as exc:
                if self._is_missing_error(exc):
                    return None
                raise

            body = response["Body"]
            fd, tmp_name = tempfile.mkstemp(suffix=Path(path).suffix)
            os.close(fd)
            os.chmod(tmp_name, stat.S_IRUSR | stat.S_IWUSR)
            tmp_path = Path(tmp_name)

            try:
                async with body:
                    async with aiofiles.open(tmp_path, "wb") as dest:
                        async for chunk in body.iter_chunks(self._CHUNK_SIZE):
                            await dest.write(chunk)
                return tmp_path
            except Exception:
                await aiofiles.os.remove(tmp_path)
                raise

    async def delete(self, path: str) -> bool:
        """Delete a file from S3."""
        async with self._get_client() as client:  # type: ignore
            try:
                await client.delete_object(Bucket=self.bucket, Key=path)
                return True
            except client.exceptions.NoSuchBucket:
                raise
            except ClientError as exc:
                if self._is_missing_error(exc):
                    return False
                raise
            except BotoCoreError:
                raise

    async def exists(self, path: str) -> bool:
        """Check if a file exists in S3."""
        async with self._get_client() as client:  # type: ignore
            try:
                await client.head_object(Bucket=self.bucket, Key=path)
                return True
            except client.exceptions.NoSuchKey:
                return False
            except client.exceptions.NoSuchBucket:
                raise
            except ClientError as exc:
                if self._is_missing_error(exc):
                    return False
                raise
            except BotoCoreError:
                raise

    async def url(self, path: str, cdn_prefix: Optional[str] = None) -> str:
        """Return the public URL for a stored S3 path."""
        if cdn_prefix:
            return f"{cdn_prefix.rstrip('/')}/{path}"
        if self.endpoint_url:
            return f"{self.endpoint_url.rstrip('/')}/{self.bucket}/{path}"
        return f"https://{self.bucket}.s3.{self.region or 'us-east-1'}.amazonaws.com/{path}"
