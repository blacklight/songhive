"""
Output stream CRUD and policy for server-side audio outputs.

Output configurations are Fernet-encrypted at rest and redacted on the way out.
The service mirrors the external-library pattern: secret-bearing keys are
replaced with ``"<redacted>"`` in API responses, and PATCH requests that leave
the sentinel in place do not clobber the stored secret.
"""

import dataclasses
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config.schema import SonghiveConfig
from ..models.output_stream import OutputStream
from ..models.user import User
from ..services.secrets import decrypt_json, encrypt_json, redact_config
from ..streams.registry import get_output, is_user_configurable

logger = logging.getLogger(__name__)


def _sanitize_error(exc: Any) -> str:
    """Return a short, config-free string for an exception or message."""
    if isinstance(exc, str):
        return exc[:512]
    return str(exc)[:512]


def _decrypt_output_config(raw: Any) -> dict:
    """Decrypt an output config when it is stored as a Fernet token."""
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            return decrypt_json(raw)
        except Exception:
            logger.warning("Failed to decrypt output config")
            return {}
    if isinstance(raw, dict):
        return raw
    return {}


def redacted_config(raw: Any, provider_type: str) -> dict:
    """Return the redacted, decrypted form of an output config."""
    decrypted = _decrypt_output_config(raw)
    try:
        provider_cls = get_output(provider_type)
    except KeyError:
        return redact_config(decrypted)
    provider = provider_cls()
    return provider.sanitize_config_for_response(decrypted)


def _merge_config_preserving_redacted(new_config: dict, decrypted_old: dict) -> dict:
    """Restore stored values for fields the client left at the redaction sentinel."""
    merged = dict(new_config)
    for key, value in new_config.items():
        if value == "<redacted>" and key in decrypted_old:
            merged[key] = decrypted_old[key]
    return merged


def _to_capabilities_dict(caps: Optional[Any]) -> Optional[dict]:
    """Convert a capabilities object/dict to a plain dict."""
    if caps is None:
        return None
    if isinstance(caps, dict):
        return caps
    try:
        return dataclasses.asdict(caps)
    except Exception:
        logger.warning("Failed to convert capabilities to dict: %s", caps)
        return None


def _provider_allowed_for_create(provider_type: str, user: User, config: SonghiveConfig) -> bool:
    """Return whether the user may create an output of this type."""
    if not is_user_configurable(provider_type):
        return False
    if user.is_admin:
        return True
    if not config.streams.allow_user_created_outputs:
        return False
    allowed = config.streams.allowed_user_providers
    if allowed and provider_type not in allowed:
        return False
    if provider_type in config.streams.denied_user_providers:
        return False
    return True


def _output_host_allowed(provider_type: str, cfg: dict, user: User, config: SonghiveConfig) -> bool:
    """Enforce the Icecast host allowlist for non-admin users."""
    if user.is_admin:
        return True
    if provider_type != "icecast":
        return True
    allowed_hosts = config.streams.allowed_output_hosts
    if not allowed_hosts:
        return True
    host = cfg.get("host", "")
    if not host:
        return False
    return host in allowed_hosts


async def find_http_stream_output(db: AsyncSession, mount: str) -> Optional[tuple[OutputStream, dict]]:
    """Return the enabled native-HTTP output owning ``mount`` and its decrypted config.

    Used by the Tornado mountpoint handler; configs are decrypted on read and
    never leave the server side.
    """
    from ..streams.http import normalize_mount

    slug = normalize_mount(mount)
    if not slug:
        return None

    result = await db.execute(
        select(OutputStream).where(
            OutputStream.provider_type == "http",
            OutputStream.enabled.is_(True),
        )
    )

    for output in result.scalars().all():
        cfg = _decrypt_output_config(output.config)
        if normalize_mount(cfg.get("mount")) == slug:
            return output, cfg
    return None


async def _check_http_mount_available(db: AsyncSession, cfg: dict, exclude_id: Optional[str] = None) -> None:
    """Reject a native-HTTP config whose mount slug is already in use."""
    from ..streams.http import normalize_mount

    slug = normalize_mount(cfg.get("mount"))
    if not slug:
        return

    result = await db.execute(select(OutputStream).where(OutputStream.provider_type == "http"))
    for output in result.scalars().all():
        if exclude_id is not None and str(output.id) == exclude_id:
            continue
        existing = _decrypt_output_config(output.config)
        if normalize_mount(existing.get("mount")) == slug:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Mount point '{slug}' is already in use",
            )


async def _validate_and_encrypt_config(provider_type: str, config: dict) -> tuple[str, Optional[dict], Optional[str]]:
    """Validate config with the provider and return the encrypted token and capabilities."""
    try:
        provider_cls = get_output(provider_type)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown provider type: {provider_type}",
        ) from exc

    provider = provider_cls()
    try:
        capabilities = await provider.validate_config(config)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=_sanitize_error(exc),
        ) from exc

    encrypted = encrypt_json(config)
    return encrypted, _to_capabilities_dict(capabilities), None


async def create_output(
    db: AsyncSession,
    user: User,
    config: SonghiveConfig,
    *,
    provider_type: str,
    name: str,
    cfg: dict,
    enabled: bool = True,
) -> OutputStream:
    """Persist a new output after validating provider policy and config."""
    if not _provider_allowed_for_create(provider_type, user, config):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Output creation is not allowed",
        )

    if not _output_host_allowed(provider_type, cfg, user, config):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Icecast host is not in the allowlist",
        )

    if provider_type == "http":
        await _check_http_mount_available(db, cfg)

    encrypted, capabilities, _ = await _validate_and_encrypt_config(provider_type, cfg)

    output = OutputStream(
        user_id=str(user.id),
        provider_type=provider_type,
        name=name,
        config=encrypted,
        capabilities=capabilities,
        enabled=enabled,
    )
    db.add(output)
    await db.flush()
    return output


async def list_outputs(db: AsyncSession, user: User) -> list[OutputStream]:
    """List outputs owned by the user, newest first."""
    result = await db.execute(
        select(OutputStream).where(OutputStream.user_id == str(user.id)).order_by(OutputStream.created_at.desc())
    )
    return list(result.scalars().all())


async def get_output_stream(db: AsyncSession, output_id: str, user: User) -> OutputStream:
    """Load an output if it is owned by the user."""
    output = await db.get(OutputStream, output_id)
    if output is None or output.user_id != str(user.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Output not found",
        )
    return output


async def update_output(
    db: AsyncSession,
    output: OutputStream,
    user: User,
    config: SonghiveConfig,
    *,
    name: Optional[str] = None,
    cfg: Optional[dict] = None,
    enabled: Optional[bool] = None,
) -> OutputStream:
    """Update an output, preserving redacted sentinels in the config."""
    if output.user_id != str(user.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Output not found",
        )

    if cfg is not None:
        old = _decrypt_output_config(output.config)
        merged = _merge_config_preserving_redacted(cfg, old)

        if output.provider_type == "icecast" and not _output_host_allowed(output.provider_type, merged, user, config):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Icecast host is not in the allowlist",
            )

        if output.provider_type == "http":
            await _check_http_mount_available(db, merged, exclude_id=str(output.id))

        encrypted, capabilities, _ = await _validate_and_encrypt_config(output.provider_type, merged)
        output.config = encrypted
        output.capabilities = capabilities

    if name is not None:
        output.name = name
    if enabled is not None:
        output.enabled = enabled

    output.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return output


async def delete_output(db: AsyncSession, output: OutputStream, user: User) -> bool:
    """Delete an output if it is owned by the user."""
    if output.user_id != str(user.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Output not found",
        )
    await db.delete(output)
    await db.flush()
    return True


async def validate_output(db: AsyncSession, output: OutputStream, user: User) -> OutputStream:
    """Run the provider's validation and update stored capabilities/error."""
    if output.user_id != str(user.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Output not found",
        )

    decrypted = _decrypt_output_config(output.config)
    try:
        provider_cls = get_output(output.provider_type)
    except KeyError as exc:
        output.last_error = _sanitize_error(exc)
        await db.flush()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown provider type: {output.provider_type}",
        ) from exc

    provider = provider_cls()
    try:
        capabilities = await provider.validate_config(decrypted)
    except Exception as exc:
        output.last_error = _sanitize_error(exc)
        output.capabilities = _to_capabilities_dict(provider.capabilities()) if provider._capabilities else None
        await db.flush()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=_sanitize_error(exc),
        ) from exc

    output.capabilities = _to_capabilities_dict(capabilities)
    output.last_error = None
    output.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return output


def get_provider_fields(provider_type: str) -> list[dict]:
    """Return a schema hint for the provider's configuration form."""
    try:
        provider_cls = get_output(provider_type)
    except KeyError:
        return []
    return getattr(provider_cls, "FIELDS", [])
