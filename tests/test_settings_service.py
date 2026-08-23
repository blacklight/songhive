"""
Tests for the runtime settings service.
"""

import json
import logging

import pytest

from songhive.config.schema import RegistrationMode, SonghiveConfig
from songhive.models.setting import Setting
from songhive.services import settings as settings_service


@pytest.mark.asyncio
async def test_setting_error_has_status_code():
    """SettingError carries a status code."""
    err = settings_service.SettingError("boom", status_code=418)
    assert str(err) == "boom"
    assert err.status_code == 418


@pytest.mark.asyncio
async def test_default_for_unknown():
    """_default_for returns None for unknown keys."""
    assert settings_service._default_for("nope") is None


@pytest.mark.asyncio
async def test_validate_value_unknown():
    """_validate_value raises 404 for unknown settings."""
    with pytest.raises(settings_service.SettingError) as exc_info:
        settings_service._validate_value("nope", "x")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_validate_value_type_errors():
    """_validate_value rejects invalid types."""
    with pytest.raises(settings_service.SettingError):
        settings_service._validate_value("instance_name", 123)
    with pytest.raises(settings_service.SettingError):
        settings_service._validate_value("federation_enabled", "yes")
    with pytest.raises(settings_service.SettingError):
        settings_service._validate_value("registration_mode", "bad")


@pytest.mark.asyncio
async def test_validate_value_valid():
    """_validate_value accepts well-typed values."""
    settings_service._validate_value("instance_name", "Songhive")
    settings_service._validate_value("federation_enabled", True)
    settings_service._validate_value("registration_mode", "open")


@pytest.mark.asyncio
async def test_get_setting_unknown_key(db_session, fake_redis):
    """get_setting returns None for a key that is not allowed."""
    assert await settings_service.get_setting(db_session, fake_redis, "unknown") is None


@pytest.mark.asyncio
async def test_get_setting_from_redis_cache(db_session, fake_redis):
    """get_setting returns a value cached in Redis."""
    await fake_redis.set("setting:instance_name", json.dumps("Cached"))
    assert await settings_service.get_setting(db_session, fake_redis, "instance_name") == "Cached"


@pytest.mark.asyncio
async def test_get_setting_redis_cache_decode_error(db_session, fake_redis):
    """A malformed cached value falls back to the database."""
    await fake_redis.set("setting:instance_name", "not-json")
    db_session.add(Setting(key="instance_name", value=json.dumps("From DB")))
    await db_session.flush()

    assert await settings_service.get_setting(db_session, fake_redis, "instance_name") == "From DB"


@pytest.mark.asyncio
async def test_get_setting_db_value(db_session, fake_redis):
    """get_setting reads from the database when Redis is empty."""
    db_session.add(Setting(key="instance_name", value=json.dumps("DB Value")))
    await db_session.flush()
    assert await settings_service.get_setting(db_session, fake_redis, "instance_name") == "DB Value"


@pytest.mark.asyncio
async def test_get_setting_db_decode_error(db_session, fake_redis):
    """A malformed DB value is returned as-is."""
    db_session.add(Setting(key="instance_name", value="not-json"))
    await db_session.flush()
    assert await settings_service.get_setting(db_session, fake_redis, "instance_name") == "not-json"


@pytest.mark.asyncio
async def test_get_setting_default_when_missing(db_session, fake_redis):
    """get_setting returns the default when no DB or Redis value exists."""
    assert await settings_service.get_setting(db_session, fake_redis, "instance_name") == "Songhive"


@pytest.mark.asyncio
async def test_set_setting_create_and_update(db_session, fake_redis, regular_user):
    """set_setting inserts, updates and invalidates Redis."""
    row = await settings_service.set_setting(
        db_session,
        fake_redis,
        "instance_name",
        "New Name",
        updated_by=regular_user.id,
    )
    assert row.key == "instance_name"
    assert json.loads(row.value) == "New Name"
    assert row.updated_by == regular_user.id
    assert await fake_redis.get("setting:instance_name") is None

    updated = await settings_service.set_setting(
        db_session,
        fake_redis,
        "instance_name",
        "Updated Name",
        updated_by=regular_user.id,
    )
    assert updated.id == row.id
    assert json.loads(updated.value) == "Updated Name"


@pytest.mark.asyncio
async def test_set_setting_validation_error(db_session, fake_redis):
    """set_setting rejects invalid values."""
    with pytest.raises(settings_service.SettingError):
        await settings_service.set_setting(db_session, fake_redis, "nope", "x")
    with pytest.raises(settings_service.SettingError):
        await settings_service.set_setting(db_session, fake_redis, "instance_name", 123)


@pytest.mark.asyncio
async def test_list_settings(db_session, fake_redis, regular_user):
    """list_settings returns all allowed settings with metadata."""
    await settings_service.set_setting(
        db_session,
        fake_redis,
        "instance_name",
        "Listed",
        updated_by=regular_user.id,
    )

    settings = await settings_service.list_settings(db_session, fake_redis)
    keys = {s["key"] for s in settings}
    assert keys == set(settings_service.ALLOWED_SETTINGS)
    by_key = {s["key"]: s for s in settings}
    assert by_key["instance_name"]["value"] == "Listed"
    assert by_key["instance_name"]["type"] == "str"
    assert by_key["instance_name"]["updated_at"] is not None
    assert by_key["federation_enabled"]["value"] is True


@pytest.mark.asyncio
async def test_get_db_settings_with_bad_json(db_session):
    """_get_db_settings returns malformed JSON values as-is."""
    db_session.add(Setting(key="instance_name", value="not-json"))
    await db_session.flush()

    settings = await settings_service._get_db_settings(db_session)
    assert settings["instance_name"] == "not-json"


@pytest.mark.asyncio
async def test_apply_settings_overrides(db_session):
    """apply_settings_overlays DB settings onto config."""
    db_session.add(Setting(key="instance_name", value=json.dumps("Override Name")))
    db_session.add(Setting(key="instance_description", value=json.dumps("Override Desc")))
    db_session.add(Setting(key="federation_enabled", value=json.dumps(False)))
    db_session.add(Setting(key="registration_mode", value=json.dumps("closed")))
    await db_session.flush()

    base = SonghiveConfig(
        auth={"secret_key": "a" * 32},
        federation={"enabled": True},
    )
    updated = await settings_service.apply_settings_overrides(db_session, base)
    assert updated.federation.instance_name == "Override Name"
    assert updated.federation.instance_description == "Override Desc"
    assert updated.federation.enabled is False
    assert updated.auth.registration_mode == RegistrationMode.CLOSED


@pytest.mark.asyncio
async def test_apply_settings_overrides_invalid_registration_mode(db_session, caplog):
    """An invalid registration_mode setting is ignored with a warning."""
    db_session.add(Setting(key="registration_mode", value=json.dumps("nope")))
    await db_session.flush()

    base = SonghiveConfig(auth={"secret_key": "a" * 32})
    with caplog.at_level(logging.WARNING, logger="songhive.services.settings"):
        updated = await settings_service.apply_settings_overrides(db_session, base)
    assert updated.auth.registration_mode == RegistrationMode.OPEN
    assert "invalid registration_mode" in caplog.text.lower()


@pytest.mark.asyncio
async def test_apply_settings_overrides_no_changes(db_session):
    """apply_settings_overrides returns the same config when no settings match."""
    base = SonghiveConfig(auth={"secret_key": "a" * 32})
    updated = await settings_service.apply_settings_overrides(db_session, base)
    assert updated is base


@pytest.mark.asyncio
async def test_apply_settings_overrides_db_error(db_session, monkeypatch):
    """A DB error during overlay leaves the original config untouched."""

    async def _boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(settings_service, "_get_db_settings", _boom)

    base = SonghiveConfig(auth={"secret_key": "a" * 32})
    updated = await settings_service.apply_settings_overrides(db_session, base)
    assert updated is base
