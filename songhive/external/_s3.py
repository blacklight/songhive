"""
S3-compatible object-storage external-library adapter.

Indexes audio objects stored under a bucket (optionally restricted to a
prefix) and serves them through presigned URLs or proxied streams.

Because object stores cannot be watched like a filesystem, change detection
relies on the object ETag reported by ``ListObjectsV2``: scheduled syncs list
the bucket (a cheap, paginated operation) and only download objects whose
identity changed, so unchanged buckets never incur per-object data transfer.
"""

import asyncio
import base64
import binascii
import dataclasses
import fnmatch
import hashlib
import logging
import mimetypes
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, AsyncIterator, Optional
from urllib.parse import urlparse

import aioboto3
import aiofiles
import aiofiles.os
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from ..config.constants import AUDIO_EXTENSIONS
from ..config.loader import load_config
from .base import ExternalLibraryAdapter
from .errors import (
    ExternalConfigError,
    ExternalItemNotFound,
    ExternalPermissionDenied,
    UnsupportedExternalOperation,
)
from .types import (
    ExternalHealth,
    ExternalItemRef,
    ExternalLibraryCapabilities,
    ExternalMutationResult,
    ExternalStream,
    ExternalTrackMetadata,
)

logger = logging.getLogger(__name__)

_DEFAULT_EXTENSIONS = frozenset(AUDIO_EXTENSIONS)
_CHUNK_SIZE = 64 * 1024
_DEFAULT_PRESIGN_EXPIRY_SECONDS = 3600
# SigV4 presigned URLs are limited to one week.
_MAX_PRESIGN_EXPIRY_SECONDS = 7 * 24 * 3600
_MIN_PRESIGN_EXPIRY_SECONDS = 60

# Cap concurrent ffmpeg invocations across all S3 library syncs.
_FFMPEG_SEMAPHORE = asyncio.Semaphore(2)


class S3ExternalAdapter(ExternalLibraryAdapter):
    """Adapter that indexes audio objects from an S3-compatible bucket."""

    provider_type = "s3"
    user_configurable = True

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _bucket(config: dict) -> str:
        bucket = config.get("bucket")
        if not isinstance(bucket, str) or not bucket.strip():
            raise ExternalConfigError(
                'config["bucket"] is required and must be a non-empty string',
                field="bucket",
            )
        return bucket.strip()

    @staticmethod
    def _prefix(config: dict) -> str:
        """Return the normalized key prefix, always empty or ``/``-terminated."""
        raw = config.get("prefix") or ""
        if not isinstance(raw, str):
            raise ExternalConfigError(
                'config["prefix"] must be a string',
                field="prefix",
            )
        prefix = raw.strip().lstrip("/")
        if prefix and not prefix.endswith("/"):
            prefix = f"{prefix}/"
        return prefix

    @staticmethod
    def _client_kwargs(config: dict) -> dict:
        """Build keyword arguments for ``aioboto3.Session().client``."""
        kwargs: dict[str, Any] = {}

        endpoint_url = config.get("endpoint_url")
        if endpoint_url:
            if not isinstance(endpoint_url, str):
                raise ExternalConfigError(
                    'config["endpoint_url"] must be a string',
                    field="endpoint_url",
                )
            parsed = urlparse(endpoint_url.strip())
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ExternalConfigError(
                    'config["endpoint_url"] must be a valid http(s) URL',
                    field="endpoint_url",
                )
            kwargs["endpoint_url"] = endpoint_url.strip()

        region = config.get("region")
        if region:
            if not isinstance(region, str):
                raise ExternalConfigError(
                    'config["region"] must be a string',
                    field="region",
                )
            kwargs["region_name"] = region.strip()

        access_key = config.get("access_key") or None
        secret_key = config.get("secret_key") or None
        if bool(access_key) != bool(secret_key):
            missing = "secret_key" if not secret_key else "access_key"
            raise ExternalConfigError(
                "access_key and secret_key must be provided together",
                field=missing,
            )
        if access_key and secret_key:
            kwargs["aws_access_key_id"] = str(access_key)
            kwargs["aws_secret_access_key"] = str(secret_key)

        if config.get("path_style"):
            kwargs["config"] = BotoConfig(s3={"addressing_style": "path"})

        return kwargs

    def _get_client(self, config: dict):
        """Create an async S3 client context manager for the given config."""
        return aioboto3.Session().client("s3", **self._client_kwargs(config))

    @staticmethod
    def _is_missing_error(exc: ClientError) -> bool:
        """Return True when an S3 client error indicates the object is missing."""
        error_code = exc.response.get("Error", {}).get("Code")
        if error_code in ("NoSuchKey", "NoSuchBucket", "NotFound", "404"):
            return True
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        return status == 404

    def _full_key(self, config: dict, provider_key: str) -> str:
        """Join a provider-relative key with the configured prefix."""
        if not provider_key:
            raise ExternalItemNotFound("Empty provider key", provider_key=provider_key)
        path = PurePosixPath(provider_key)
        if path.is_absolute() or ".." in path.parts:
            raise ExternalPermissionDenied(
                f"Invalid provider key: {provider_key}",
                operation="resolve_item_key",
            )
        return f"{self._prefix(config)}{provider_key}"

    @staticmethod
    def _scope_prefix(config: dict, scope: Optional[str]) -> str:
        """Return the effective list prefix for a scoped sync."""
        prefix = S3ExternalAdapter._prefix(config)
        if not scope:
            return prefix
        clean = scope.strip().lstrip("/").rstrip("/")
        parts = PurePosixPath(clean).parts if clean else ()
        if ".." in parts:
            raise ExternalConfigError(
                f"Scope {scope!r} is invalid",
                field="scope",
            )
        return f"{prefix}{clean}/" if clean else prefix

    @staticmethod
    def _is_excluded(provider_key: str, exclude: list[str]) -> bool:
        """Return True when the provider key matches any exclude pattern."""
        return any(
            fnmatch.fnmatch(provider_key, pattern) or fnmatch.fnmatch(PurePosixPath(provider_key).name, pattern)
            for pattern in exclude
        )

    @staticmethod
    def _mime_for_key(key: str) -> str:
        mime_type = mimetypes.guess_type(key)[0]
        if mime_type is None:
            suffix = PurePosixPath(key).suffix.lstrip(".").lower()
            mime_type = f"audio/{suffix}" if suffix else "application/octet-stream"
        return mime_type

    async def _stream_temp_dir(self) -> Optional[Path]:
        """Return the configured temp dir for downloads, creating it if needed."""
        temp_dir = load_config([]).external_libraries.stream_temp_dir
        if temp_dir is None:
            return None
        path = Path(temp_dir)
        await asyncio.to_thread(path.mkdir, parents=True, exist_ok=True)
        return path

    async def _download_to_temp(self, client: Any, bucket: str, key: str) -> tuple[Path, dict]:
        """Download an object to a local temp file and return ``(path, response)``."""
        try:
            response = await client.get_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            if self._is_missing_error(exc):
                raise ExternalItemNotFound(
                    f"Object not found: {key}",
                    provider_key=key,
                ) from exc
            raise

        temp_dir = await self._stream_temp_dir()
        fd, tmp_name = tempfile.mkstemp(
            dir=str(temp_dir) if temp_dir else None,
            prefix="s3-ext-",
            suffix=PurePosixPath(key).suffix,
        )
        os.close(fd)
        os.chmod(tmp_name, 0o600)
        tmp_path = Path(tmp_name)

        body = response["Body"]
        try:
            async with body:
                async with aiofiles.open(tmp_path, "wb") as dest:
                    async for chunk in body.iter_chunks(_CHUNK_SIZE):
                        await dest.write(chunk)
        except Exception:
            await aiofiles.os.remove(tmp_path)
            raise

        return tmp_path, response

    # ------------------------------------------------------------------
    # Adapter interface
    # ------------------------------------------------------------------

    async def validate_config(self, config: dict) -> ExternalLibraryCapabilities:
        """Validate credentials/bucket access and return capabilities."""
        bucket = self._bucket(config)
        prefix = self._prefix(config)

        try:
            async with self._get_client(config) as client:
                await client.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1)
        except ExternalConfigError:
            raise
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "Unknown")
            raise ExternalConfigError(
                f"Cannot access bucket {bucket!r}: {code}",
                field="bucket",
            ) from exc
        except BotoCoreError as exc:
            raise ExternalConfigError(
                f"Cannot connect to S3 endpoint: {exc}",
                field="endpoint_url",
            ) from exc
        except Exception as exc:
            raise ExternalConfigError(
                f"Cannot access bucket {bucket!r}: {exc}",
                field="bucket",
            ) from exc

        self._capabilities = ExternalLibraryCapabilities(
            list_items=True,
            read_bytes=True,
            stream_url=bool(config.get("presigned_urls", True)),
            range_read=True,
            download=True,
            compute_hash=bool(config.get("allow_hashing", True)),
            read_tags=True,
            write_tags=bool(config.get("allow_write_tags")),
            rename_source=bool(config.get("allow_rename_source")),
            delete_source=bool(config.get("allow_delete_source")),
            detect_changes=True,
            validate_config=True,
            limits={"checksum_algorithm": "sha256"},
        )
        assert self._capabilities  # for mypy
        return self._capabilities

    async def iter_items(
        self,
        config: dict,
        since: Optional[datetime] = None,
        scope: Optional[str] = None,
    ) -> AsyncIterator[ExternalItemRef]:
        """Yield audio objects under the configured bucket/prefix."""
        bucket = self._bucket(config)
        prefix = self._prefix(config)
        list_prefix = self._scope_prefix(config, scope)

        extensions = frozenset(config.get("extensions", _DEFAULT_EXTENSIONS))
        extension_set = {ext.lstrip(".").lower() for ext in extensions}
        recursive = bool(config.get("recursive", True))
        exclude = list(config.get("exclude", []))

        kwargs: dict[str, Any] = {"Bucket": bucket, "Prefix": list_prefix}
        if not recursive:
            kwargs["Delimiter"] = "/"

        try:
            async with self._get_client(config) as client:
                continuation: Optional[str] = None
                while True:
                    if continuation:
                        kwargs["ContinuationToken"] = continuation
                    response = await client.list_objects_v2(**kwargs)
                    for obj in response.get("Contents", []):
                        key = obj["Key"]
                        provider_key = key[len(prefix) :] if prefix and key.startswith(prefix) else key
                        if not provider_key:
                            continue
                        suffix = provider_key.rsplit(".", 1)[-1].lower() if "." in provider_key else ""
                        if suffix not in extension_set:
                            continue
                        if self._is_excluded(provider_key, exclude):
                            continue

                        mtime = obj.get("LastModified")
                        if since is not None and mtime is not None and mtime <= since:
                            continue

                        etag = obj.get("ETag")
                        if isinstance(etag, str):
                            etag = etag.strip('"')

                        yield ExternalItemRef(
                            provider_key=provider_key,
                            display_path=provider_key,
                            etag=etag,
                            mtime=mtime,
                            size=obj.get("Size"),
                            mime_type=self._mime_for_key(provider_key),
                            checksum=None,
                            sha256=None,
                        )

                    if not response.get("IsTruncated"):
                        break
                    continuation = response.get("NextContinuationToken")
                    if not continuation:
                        break
        except ExternalConfigError:
            raise
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "Unknown")
            raise ExternalConfigError(
                f"Cannot list bucket {bucket!r}: {code}",
                field="bucket",
            ) from exc

    async def read_metadata(self, config: dict, item: ExternalItemRef) -> ExternalTrackMetadata:
        """Download an object to a temp file and read its embedded tags."""
        bucket = self._bucket(config)
        key = self._full_key(config, item.provider_key)

        async with self._get_client(config) as client:
            tmp_path, response = await self._download_to_temp(client, bucket, key)

        metadata = None

        try:
            from ._audio import track_metadata_from_file

            metadata = track_metadata_from_file(tmp_path, item.provider_key)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        assert metadata  # for mypy
        raw = dict(metadata.raw_metadata or {})
        mimetype = response.get("ContentType") or raw.get("mimetype") or item.mime_type
        raw["mimetype"] = mimetype
        raw["bucket"] = bucket
        raw["key"] = key
        return dataclasses.replace(metadata, raw_metadata=raw)

    async def open_stream(
        self,
        config: dict,
        item: ExternalItemRef,
        *,
        range: Optional[tuple[int, int]] = None,
    ) -> ExternalStream:
        """Return a presigned URL stream, or a proxied byte iterator."""
        bucket = self._bucket(config)
        key = self._full_key(config, item.provider_key)
        content_type = item.mime_type or self._mime_for_key(item.provider_key)

        if config.get("presigned_urls", True):
            expiry = int(config.get("presigned_expiry_seconds") or _DEFAULT_PRESIGN_EXPIRY_SECONDS)
            expiry = max(_MIN_PRESIGN_EXPIRY_SECONDS, min(expiry, _MAX_PRESIGN_EXPIRY_SECONDS))
            async with self._get_client(config) as client:
                url = await client.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": bucket, "Key": key},
                    ExpiresIn=expiry,
                )
            return ExternalStream(
                kind="url",
                url=url,
                content_type=content_type,
                size=item.size,
                supports_range=True,
                headers={},
                temporary=False,
                safe_to_redirect=True,
            )

        size: Optional[int] = item.size
        get_kwargs: dict[str, Any] = {"Bucket": bucket, "Key": key}
        content_range: Optional[str] = None
        if range is not None:
            start, end = range
            get_kwargs["Range"] = f"bytes={start}-{end}"
            size = end - start + 1
            content_range = f"bytes {start}-{end}/{item.size if item.size is not None else '*'}"

        async def _iter_s3() -> AsyncIterator[bytes]:
            async with self._get_client(config) as client:
                try:
                    response = await client.get_object(**get_kwargs)
                except ClientError as exc:
                    if self._is_missing_error(exc):
                        raise ExternalItemNotFound(
                            f"Object not found: {key}",
                            provider_key=item.provider_key,
                        ) from exc
                    raise
                body = response["Body"]
                async with body:
                    async for chunk in body.iter_chunks(_CHUNK_SIZE):
                        yield chunk

        return ExternalStream(
            kind="iterator",
            iterator=_iter_s3(),
            content_type=content_type,
            size=size,
            supports_range=True,
            headers={},
            temporary=False,
            content_range=content_range,
        )

    async def download(self, config: dict, item: ExternalItemRef) -> ExternalStream:
        """Return a complete byte stream for the object."""
        return await self.open_stream(config, item)

    async def compute_sha256(self, config: dict, item: ExternalItemRef) -> str:
        """Compute the item's audio hash, preferring a stored S3 checksum."""
        bucket = self._bucket(config)
        key = self._full_key(config, item.provider_key)

        async with self._get_client(config) as client:
            try:
                head = await client.head_object(Bucket=bucket, Key=key, ChecksumMode="ENABLED")
            except ClientError as exc:
                if self._is_missing_error(exc):
                    raise ExternalItemNotFound(
                        f"Object not found: {key}",
                        provider_key=item.provider_key,
                    ) from exc
                head = None

            checksum_b64 = (head or {}).get("ChecksumSHA256")
            if checksum_b64:
                try:
                    return binascii.hexlify(base64.b64decode(checksum_b64)).decode("ascii")
                except ValueError:
                    pass

            if config.get("fast_hash"):
                try:
                    response = await client.get_object(Bucket=bucket, Key=key)
                except ClientError as exc:
                    if self._is_missing_error(exc):
                        raise ExternalItemNotFound(
                            f"Object not found: {key}",
                            provider_key=item.provider_key,
                        ) from exc
                    raise
                hasher = hashlib.sha256()
                body = response["Body"]
                async with body:
                    async for chunk in body.iter_chunks(_CHUNK_SIZE):
                        hasher.update(chunk)
                return hasher.hexdigest()

            tmp_path, _ = await self._download_to_temp(client, bucket, key)

        try:
            from ..services.storage import audio_hash

            async with _FFMPEG_SEMAPHORE:
                return await audio_hash(tmp_path)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    async def write_metadata(
        self,
        config: dict,
        item: ExternalItemRef,
        metadata: ExternalTrackMetadata,
    ) -> ExternalMutationResult:
        """Rewrite the object's embedded tags and re-upload it in place."""
        if not self.capabilities().write_tags:
            raise UnsupportedExternalOperation("write_tags is not enabled for this S3 library")

        bucket = self._bucket(config)
        key = self._full_key(config, item.provider_key)

        async with self._get_client(config) as client:
            tmp_path, response = await self._download_to_temp(client, bucket, key)

            try:
                from ..services.metadata import AudioMetadataWrite, write_metadata

                write_obj = AudioMetadataWrite(
                    title=metadata.title,
                    artist=metadata.artist,
                    album=metadata.album,
                    track_number=metadata.track_number,
                    disc_number=metadata.disc_number,
                    genre=metadata.genre,
                    year=metadata.release_year,
                    cover_art=metadata.cover_art,
                    cover_art_mime=metadata.cover_art_mime,
                )

                def _write() -> bytes:
                    write_metadata(tmp_path, write_obj)
                    return tmp_path.read_bytes()

                data = await asyncio.to_thread(_write)
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

            put_response = await client.put_object(
                Bucket=bucket,
                Key=key,
                Body=data,
                ContentType=response.get("ContentType") or item.mime_type or "application/octet-stream",
            )

        etag = put_response.get("ETag")
        if isinstance(etag, str):
            etag = etag.strip('"')

        return ExternalMutationResult(
            provider_key=item.provider_key,
            etag=etag,
            mtime=datetime.now(timezone.utc),
        )

    async def rename_source(
        self,
        config: dict,
        item: ExternalItemRef,
        new_name: str,
    ) -> ExternalItemRef:
        """Rename the object via a server-side copy followed by a delete."""
        if not self.capabilities().rename_source:
            raise UnsupportedExternalOperation("rename_source is not enabled for this S3 library")

        bucket = self._bucket(config)
        old_key = self._full_key(config, item.provider_key)

        old_path = PurePosixPath(item.provider_key)
        new_basename = PurePosixPath(new_name.replace("\\", "/")).name
        if not new_basename or new_basename in (".", ".."):
            raise ExternalPermissionDenied(
                f"Invalid new name: {new_name!r}",
                operation="rename_source",
            )

        new_provider_key = (
            new_basename if old_path.name == item.provider_key else (old_path.parent / new_basename).as_posix()
        )
        if ".." in PurePosixPath(new_provider_key).parts:
            raise ExternalPermissionDenied(
                f"Invalid new path: {new_provider_key}",
                operation="rename_source",
            )
        if new_provider_key == item.provider_key:
            return item
        new_key = self._full_key(config, new_provider_key)

        async with self._get_client(config) as client:
            try:
                await client.head_object(Bucket=bucket, Key=new_key)
            except ClientError as exc:
                if not self._is_missing_error(exc):
                    raise
            else:
                raise ExternalPermissionDenied(
                    f"Target path already exists: {new_provider_key}",
                    operation="rename_source",
                )

            try:
                await client.copy_object(
                    Bucket=bucket,
                    Key=new_key,
                    CopySource={"Bucket": bucket, "Key": old_key},
                )
            except ClientError as exc:
                if self._is_missing_error(exc):
                    raise ExternalItemNotFound(
                        f"Object not found: {old_key}",
                        provider_key=item.provider_key,
                    ) from exc
                raise

            await client.delete_object(Bucket=bucket, Key=old_key)

            head = await client.head_object(Bucket=bucket, Key=new_key)

        etag = head.get("ETag")
        if isinstance(etag, str):
            etag = etag.strip('"')
        mtime = head.get("LastModified")
        if mtime is not None and mtime.tzinfo is None:
            mtime = mtime.replace(tzinfo=timezone.utc)

        return ExternalItemRef(
            provider_key=new_provider_key,
            display_path=new_provider_key,
            etag=etag,
            mtime=mtime,
            size=head.get("ContentLength", item.size),
            mime_type=head.get("ContentType") or self._mime_for_key(new_provider_key),
            checksum=item.checksum,
            sha256=item.sha256,
        )

    async def delete_source(self, config: dict, item: ExternalItemRef) -> ExternalMutationResult:
        """Delete the object from the bucket."""
        if not self.capabilities().delete_source:
            raise UnsupportedExternalOperation("delete_source is not enabled for this S3 library")

        bucket = self._bucket(config)
        key = self._full_key(config, item.provider_key)

        async with self._get_client(config) as client:
            try:
                await client.head_object(Bucket=bucket, Key=key)
            except ClientError as exc:
                if self._is_missing_error(exc):
                    raise ExternalItemNotFound(
                        f"Object not found: {key}",
                        provider_key=item.provider_key,
                    ) from exc
                raise
            await client.delete_object(Bucket=bucket, Key=key)

        return ExternalMutationResult(provider_key=item.provider_key)

    async def healthcheck(self, config: dict) -> ExternalHealth:
        """Check that the bucket is reachable and listable."""
        bucket = self._bucket(config)
        prefix = self._prefix(config)
        try:
            async with self._get_client(config) as client:
                await client.head_bucket(Bucket=bucket)
                await client.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1)
        except (ClientError, BotoCoreError) as exc:
            return ExternalHealth(ok=False, message=str(exc))
        except ExternalConfigError as exc:
            return ExternalHealth(ok=False, message=str(exc))
        return ExternalHealth(ok=True, message=f"Bucket {bucket} is accessible")
