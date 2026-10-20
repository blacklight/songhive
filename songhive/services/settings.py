"""
Instance settings service.

Provides runtime-editable key/value settings with Redis caching and config
overlay helpers.
"""

import json
import logging
from typing import Any, Optional

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config.schema import RegistrationMode, SonghiveConfig
from ..models.setting import Setting

logger = logging.getLogger(__name__)

SETTINGS_CACHE_TTL = 300

ALLOWED_SETTINGS: dict[str, dict[str, Any]] = {
    "instance_name": {"type": "str", "default": "Songhive"},
    "instance_description": {"type": "str", "default": "A federated music sharing service"},
    "instance_contact_name": {"type": "str", "default": ""},
    "instance_contact_email": {"type": "str", "default": ""},
    "instance_contact_url": {"type": "str", "default": ""},
    "registration_mode": {
        "type": "enum",
        "choices": ["open", "invite-only", "approval-required", "paid", "closed"],
        "default": "open",
    },
    "membership_price_minor": {
        "type": "number",
        "default": 500,
        "min": 0,
        "max": 10_000_000,
    },
    "membership_currency": {"type": "str", "default": "USD"},
    "membership_interval": {
        "type": "enum",
        "choices": ["week", "month", "year"],
        "default": "month",
    },
    "federation_enabled": {"type": "bool", "default": True},
    "remote_search_access": {
        "type": "enum",
        "choices": ["disabled", "authenticated", "public"],
        "default": "authenticated",
    },
    "fetch_timeout_seconds": {
        "type": "number",
        "default": 20.0,
        "min": 1,
        "max": 300,
    },
    "preview_cards_enabled": {
        "type": "bool",
        "default": True,
    },
    # Username whose profile becomes the landing page for anonymous
    # visitors; an empty string disables single-user mode.
    "single_user_username": {"type": "str", "default": ""},
    # Expose visibility-filtered content counts on GET /api/v1/instance/stats.
    "public_stats_enabled": {"type": "bool", "default": False},
}


class SettingError(ValueError):
    """Raised when a setting value cannot be stored."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _redis_key(key: str) -> str:
    """Build the Redis cache key for a setting."""
    return f"setting:{key}"


def _default_for(key: str) -> Any:
    """Return the default value for an allowed setting key."""
    meta = ALLOWED_SETTINGS.get(key)
    if meta is None:
        return None
    return meta["default"]


def _validate_value(key: str, value: Any) -> None:
    """Validate ``value`` against the expected type/choices for ``key``."""
    meta = ALLOWED_SETTINGS.get(key)
    if meta is None:
        raise SettingError(f"Unknown setting: {key}", status_code=404)

    value_type = meta["type"]
    if value_type == "str" and not isinstance(value, str):
        raise SettingError(f"Setting {key!r} must be a string")
    if value_type == "bool" and not isinstance(value, bool):
        raise SettingError(f"Setting {key!r} must be a boolean")
    if value_type == "enum" and value not in meta["choices"]:
        raise SettingError(f"Setting {key!r} must be one of {meta['choices']!r}")
    if value_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingError(f"Setting {key!r} must be a number")
        if value < meta["min"] or value > meta["max"]:
            raise SettingError(f"Setting {key!r} must be between {meta['min']} and {meta['max']}")


async def get_setting(
    session: AsyncSession,
    redis: Optional[Redis],
    key: str,
) -> Any:
    """Return the current value of a setting, falling back to the default."""
    if key not in ALLOWED_SETTINGS:
        return None

    if redis is not None:
        cached = await redis.get(_redis_key(key))
        if cached is not None:
            try:
                return json.loads(cached)
            except json.JSONDecodeError:
                pass

    result = await session.execute(select(Setting).where(Setting.key == key))
    row = result.scalar_one_or_none()
    if row is not None:
        try:
            value = json.loads(row.value)
        except json.JSONDecodeError:
            value = row.value
        if redis is not None:
            await redis.set(_redis_key(key), json.dumps(value), ex=SETTINGS_CACHE_TTL)
        return value

    return _default_for(key)


async def set_setting(
    session: AsyncSession,
    redis: Optional[Redis],
    key: str,
    value: Any,
    updated_by: Optional[str] = None,
) -> Setting:
    """Create or update a setting, invalidating the Redis cache."""
    _validate_value(key, value)

    result = await session.execute(select(Setting).where(Setting.key == key))
    row = result.scalar_one_or_none()
    if row is None:
        row = Setting(key=key, value=json.dumps(value), updated_by=updated_by)
        session.add(row)
    else:
        row.value = json.dumps(value)
        row.updated_by = updated_by

    await session.flush()

    if redis is not None:
        await redis.delete(_redis_key(key))

    return row


async def list_settings(
    session: AsyncSession,
    redis: Optional[Redis],
) -> list[dict]:
    """Return all allowed settings with their current values and metadata."""
    result = await session.execute(select(Setting))
    rows = {row.key: row for row in result.scalars().all()}

    settings = []
    for key, meta in ALLOWED_SETTINGS.items():
        value = await get_setting(session, redis, key)
        row = rows.get(key)
        settings.append(
            {
                "key": key,
                "value": value,
                "type": meta["type"],
                "updated_at": row.updated_at if row is not None else None,
            }
        )
    return settings


async def _get_db_settings(session: AsyncSession) -> dict[str, Any]:
    """Return a mapping of decoded setting values for rows that exist in the DB."""
    result = await session.execute(select(Setting))
    settings: dict[str, Any] = {}
    for row in result.scalars().all():
        try:
            settings[row.key] = json.loads(row.value)
        except json.JSONDecodeError:
            settings[row.key] = row.value
    return settings


def _bounded_number(settings: dict, key: str, low: float, high: float) -> Optional[float]:
    raw = settings.get(key)
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and low <= raw <= high:
        return float(raw)
    logger.warning("Ignoring invalid %s setting: %r", key, raw)
    return None


def _enum_value(settings: dict, key: str, allowed) -> Optional[Any]:
    raw = settings.get(key)
    if raw in allowed:
        return raw
    logger.warning("Ignoring invalid %s setting: %r", key, raw)
    return None


def _federation_overrides(settings: dict) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    direct = {
        "instance_name": "instance_name",
        "instance_description": "instance_description",
        "instance_contact_name": "contact_name",
        "instance_contact_email": "contact_email",
        "instance_contact_url": "contact_url",
        "federation_enabled": "enabled",
    }
    for setting_key, override_key in direct.items():
        if setting_key in settings:
            overrides[override_key] = settings[setting_key]
    if "remote_search_access" in settings:
        value = _enum_value(settings, "remote_search_access", ("disabled", "authenticated", "public"))
        if value is not None:
            overrides["remote_search_access"] = value
    if "fetch_timeout_seconds" in settings:
        value = _bounded_number(settings, "fetch_timeout_seconds", 1, 300)
        if value is not None:
            overrides["fetch_timeout_seconds"] = value
    return overrides


def _auth_overrides(settings: dict) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    if "registration_mode" in settings:
        raw = settings["registration_mode"]
        try:
            overrides["registration_mode"] = RegistrationMode(raw)
        except ValueError:
            logger.warning("Ignoring invalid registration_mode setting: %r", raw)
    return overrides


def _payments_overrides(settings: dict) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    if "membership_price_minor" in settings:
        value = _bounded_number(settings, "membership_price_minor", 0, 10_000_000)
        if value is not None:
            overrides["default_membership_amount_minor"] = int(value)
    if "membership_currency" in settings:
        raw_currency = settings["membership_currency"]
        if isinstance(raw_currency, str) and len(raw_currency.strip()) == 3:
            overrides["membership_currency"] = raw_currency.strip().upper()
        else:
            logger.warning("Ignoring invalid membership_currency setting: %r", raw_currency)
    if "membership_interval" in settings:
        value = _enum_value(settings, "membership_interval", ("week", "month", "year"))
        if value is not None:
            overrides["membership_interval"] = value
    return overrides


async def apply_settings_overrides(
    session: AsyncSession,
    config: SonghiveConfig,
) -> SonghiveConfig:
    """Return a copy of ``config`` with DB settings overlaid."""
    try:
        settings = await _get_db_settings(session)
    except Exception:
        logger.exception("Failed to load settings overrides; using config file/defaults")
        return config

    fed_overrides = _federation_overrides(settings)
    auth_overrides = _auth_overrides(settings)
    payments_overrides = _payments_overrides(settings)

    if not fed_overrides and not auth_overrides and not payments_overrides:
        return config

    new_federation = config.federation.model_copy(update=fed_overrides)
    new_auth = config.auth.model_copy(update=auth_overrides)
    new_payments = config.payments.model_copy(update=payments_overrides)
    return config.model_copy(
        update={
            "federation": new_federation,
            "auth": new_auth,
            "payments": new_payments,
        },
        deep=True,
    )
