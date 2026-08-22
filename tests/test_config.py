"""
Configuration loading tests.
"""

import json

import pytest
from pydantic import ValidationError

from songhive.config.loader import _deep_merge, load_config
from songhive.config.schema import RegistrationMode, SonghiveConfig, effective_bitrate


def test_default_config():
    """Test that default config loads without errors."""
    config = SonghiveConfig()
    assert config.server.port == 8000
    assert config.server.host == "0.0.0.0"
    assert config.federation.enabled is True


def test_config_from_dict():
    """Test creating config from a dictionary."""
    config = SonghiveConfig(
        server={"port": 9000, "debug": True},
        federation={"instance_domain": "music.example.com"},
    )
    assert config.server.port == 9000
    assert config.server.debug is True
    assert config.federation.instance_domain == "music.example.com"


def test_deep_merge():
    """Test deep merge utility."""
    base = {"a": {"b": 1, "c": 2}, "d": 3}
    override = {"a": {"b": 10}, "e": 5}
    result = _deep_merge(base, override)
    assert result == {"a": {"b": 10, "c": 2}, "d": 3, "e": 5}


def test_cli_overrides():
    """Test that CLI args override config values."""
    config = load_config(["--port", "9999", "--host", "127.0.0.1"])
    assert config.server.port == 9999
    assert config.server.host == "127.0.0.1"


def test_cors_origins_default():
    """Test that CORS origins default to an empty allow-list."""
    config = SonghiveConfig()
    assert config.server.cors_origins == []


def test_cors_origins_from_list():
    """Test that CORS origins can be set from a list."""
    config = SonghiveConfig(server={"cors_origins": ["http://localhost:8080", "http://app.local"]})
    assert config.server.cors_origins == ["http://localhost:8080", "http://app.local"]


def test_cors_origins_from_comma_string():
    """Test that CORS origins can be parsed from a comma-separated string."""
    config = SonghiveConfig(server={"cors_origins": "http://a, http://b"})
    assert config.server.cors_origins == ["http://a", "http://b"]


def test_cors_origins_from_json_string():
    """Test that CORS origins can be parsed from a JSON list string."""
    config = SonghiveConfig(server={"cors_origins": '["http://a", "http://b"]'})
    assert config.server.cors_origins == ["http://a", "http://b"]


def test_cors_origins_from_env(monkeypatch):
    """Test that CORS origins can be set via an environment variable."""
    monkeypatch.setenv("SONGHIVE_SERVER__CORS_ORIGINS", "http://localhost:8080,http://app.local")
    config = SonghiveConfig()
    assert config.server.cors_origins == ["http://localhost:8080", "http://app.local"]


def test_cli_cors_origins():
    """Test that CORS origins can be set via CLI args."""
    config = load_config(["--cors-origins", "http://a", "http://b"])
    assert config.server.cors_origins == ["http://a", "http://b"]


def test_auth_config_defaults():
    """Test that auth configuration has the expected defaults."""
    config = SonghiveConfig()
    assert config.auth.registration_mode == "open"
    assert config.auth.require_email_verification is False
    assert config.auth.access_token_expiry_minutes == 15
    assert config.auth.refresh_token_expiry_days == 30
    assert config.auth.password_reset_token_expiry_minutes == 30
    assert config.auth.rate_limit_enabled is True
    assert config.auth.rate_limit_requests == 10
    assert config.auth.rate_limit_window_seconds == 60
    assert config.auth.secret_key
    assert config.auth.secret_key != "change-me-in-production"
    assert len(config.auth.secret_key.encode("utf-8")) >= 32


@pytest.mark.parametrize(
    "mode",
    ["open", "invite-only", "approval-required", "closed"],
)
def test_registration_modes(mode):
    """Test that all supported registration modes can be parsed."""
    config = SonghiveConfig(auth={"registration_mode": mode})
    assert config.auth.registration_mode == mode


def test_invalid_registration_mode():
    """Test that an unknown registration mode is rejected."""
    with pytest.raises(ValidationError):
        SonghiveConfig(auth={"registration_mode": "disabled"})


def test_auth_config_rejects_placeholder_secret(monkeypatch):
    """Test that a placeholder JWT secret is rejected."""
    monkeypatch.delenv("SONGHIVE_AUTH__SECRET_KEY", raising=False)
    with pytest.raises(ValidationError):
        SonghiveConfig(auth={"secret_key": "change-me-in-production"})


def test_auth_config_rejects_short_secret(monkeypatch):
    """Test that a JWT secret shorter than 32 bytes is rejected."""
    monkeypatch.delenv("SONGHIVE_AUTH__SECRET_KEY", raising=False)
    with pytest.raises(ValidationError):
        SonghiveConfig(auth={"secret_key": "short"})


def test_auth_config_from_env(monkeypatch):
    """Test that auth settings can be set via environment variables."""
    monkeypatch.setenv("SONGHIVE_AUTH__REGISTRATION_MODE", "invite-only")
    monkeypatch.setenv("SONGHIVE_AUTH__ACCESS_TOKEN_EXPIRY_MINUTES", "5")
    monkeypatch.setenv("SONGHIVE_AUTH__RATE_LIMIT_ENABLED", "false")
    config = SonghiveConfig()
    assert config.auth.registration_mode == "invite-only"
    assert config.auth.access_token_expiry_minutes == 5
    assert config.auth.rate_limit_enabled is False


def test_email_config_defaults():
    """Test that email configuration has the expected defaults."""
    config = SonghiveConfig()
    assert config.email.smtp_host is None
    assert config.email.smtp_port == 587
    assert config.email.smtp_username is None
    assert config.email.smtp_password is None
    assert config.email.smtp_tls is True
    assert config.email.from_address is None


def test_email_config_from_env(monkeypatch):
    """Test that email settings can be set via environment variables."""
    monkeypatch.setenv("SONGHIVE_EMAIL__SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SONGHIVE_EMAIL__SMTP_PORT", "465")
    monkeypatch.setenv("SONGHIVE_EMAIL__SMTP_USERNAME", "user")
    monkeypatch.setenv("SONGHIVE_EMAIL__FROM_ADDRESS", "songhive@example.com")
    config = SonghiveConfig()
    assert config.email.smtp_host == "smtp.example.com"
    assert config.email.smtp_port == 465
    assert config.email.smtp_username == "user"
    assert config.email.from_address == "songhive@example.com"


def test_load_config_from_toml(tmp_path):
    """Test that auth and email settings are loaded from a TOML file."""
    toml_file = tmp_path / "config.toml"
    toml_file.write_text(
        "\n".join(
            [
                "[auth]",
                'registration_mode = "closed"',
                "access_token_expiry_minutes = 5",
                "",
                "[email]",
                'smtp_host = "smtp.test"',
                "smtp_port = 465",
                'from_address = "test@example.com"',
            ]
        )
    )
    config = load_config(["--config", str(toml_file)])
    assert config.auth.registration_mode == "closed"
    assert config.auth.access_token_expiry_minutes == 5
    assert config.email.smtp_host == "smtp.test"
    assert config.email.smtp_port == 465
    assert config.email.from_address == "test@example.com"


def test_registration_mode_serializes_as_string():
    """Test that the registration mode enum behaves as a plain string when serialized."""
    config = SonghiveConfig(auth={"registration_mode": "open"})
    assert config.auth.registration_mode == "open"
    assert config.auth.registration_mode == RegistrationMode.OPEN
    assert json.dumps(config.auth.registration_mode) == '"open"'


def test_storage_config_cdn_prefix():
    """Test that the storage CDN prefix is optional and defaults to None."""
    config = SonghiveConfig()
    assert config.storage.cdn_prefix is None

    config = SonghiveConfig(storage={"cdn_prefix": "https://cdn.example.com"})
    assert config.storage.cdn_prefix == "https://cdn.example.com"


def test_storage_config_max_upload_size():
    """Test that max_upload_size defaults to 500 MiB and can be overridden."""
    config = SonghiveConfig()
    assert config.storage.max_upload_size == 500 * 1024 * 1024

    config = SonghiveConfig(storage={"max_upload_size": 104857600})
    assert config.storage.max_upload_size == 104857600


def test_env_overrides_toml(monkeypatch, tmp_path):
    """Test that environment variables override TOML config values."""
    toml_file = tmp_path / "config.toml"
    toml_file.write_text(
        "\n".join(
            [
                "[server]",
                'host = "toml.host"',
                "port = 1111",
                "",
                "[email]",
                'smtp_host = "smtp.toml"',
            ]
        )
    )
    monkeypatch.setenv("SONGHIVE_SERVER__PORT", "2222")
    monkeypatch.setenv("SONGHIVE_EMAIL__SMTP_HOST", "smtp.env")
    config = load_config(["--config", str(toml_file)])
    assert config.server.port == 2222
    assert config.server.host == "toml.host"
    assert config.email.smtp_host == "smtp.env"


def test_env_overrides_cli_and_toml(monkeypatch, tmp_path):
    """Test that environment variables override both CLI arguments and TOML."""
    toml_file = tmp_path / "config.toml"
    toml_file.write_text(
        "\n".join(
            [
                "[server]",
                "port = 1111",
            ]
        )
    )
    monkeypatch.setenv("SONGHIVE_SERVER__PORT", "2222")
    config = load_config(["--config", str(toml_file), "--port", "3333"])
    assert config.server.port == 2222


def test_cli_overrides_toml(tmp_path):
    """Test that CLI arguments override TOML config when no env var is set."""
    toml_file = tmp_path / "config.toml"
    toml_file.write_text(
        "\n".join(
            [
                "[server]",
                "port = 1111",
            ]
        )
    )
    config = load_config(["--config", str(toml_file), "--port", "3333"])
    assert config.server.port == 3333


def test_config_repr_masks_secrets():
    """Test that repr does not expose raw secrets or the database URL password."""
    config = SonghiveConfig(
        auth={"secret_key": "super-secret-key-that-is-long-enough-to-pass"},
        storage={"s3_secret_key": "s3-secret"},
        email={"smtp_password": "email-secret"},
        database={"url": "postgresql://user:db-secret@localhost:5432/songhive"},
    )
    text = repr(config)
    assert "super-secret-key" not in text
    assert "s3-secret" not in text
    assert "email-secret" not in text
    assert ":db-secret@" not in text
    assert "postgresql://user:***@localhost:5432/songhive" in text


def test_config_dump_masks_secrets():
    """Test that model_dump and model_dump_json redact secrets."""
    config = SonghiveConfig(
        auth={"secret_key": "super-secret-key-that-is-long-enough-to-pass"},
        storage={"s3_secret_key": "s3-secret"},
        email={"smtp_password": "email-secret"},
        database={"url": "postgresql://user:db-secret@localhost:5432/songhive"},
    )
    data = config.model_dump()
    json_text = config.model_dump_json()
    for export in (data, json_text):
        assert "super-secret-key" not in str(export)
        assert "s3-secret" not in str(export)
        assert "email-secret" not in str(export)
        assert ":db-secret@" not in str(export)
    assert data["database"]["url"] == "postgresql://user:***@localhost:5432/songhive"


def test_streaming_config_defaults():
    """Streaming configuration has the expected defaults."""
    config = SonghiveConfig(auth={"secret_key": "a" * 64})
    assert config.streaming.max_bitrate == "320k"
    assert config.streaming.default_bitrate == "192k"
    assert config.streaming.chunk_size == 64 * 1024
    assert config.streaming.transcode_cache_enabled is True
    assert config.streaming.max_bitrate_by_role == {
        "user": "192k",
        "moderator": "256k",
        "admin": "320k",
    }


def test_streaming_config_from_env(monkeypatch):
    """SONGHIVE_STREAMING__* environment variables override streaming config."""
    monkeypatch.setenv("SONGHIVE_STREAMING__MAX_BITRATE", "128k")
    config = SonghiveConfig(auth={"secret_key": "a" * 64})
    assert config.streaming.max_bitrate == "128k"


def test_effective_bitrate():
    """effective_bitrate returns the lowest valid ceiling and falls back safely."""
    config = SonghiveConfig(auth={"secret_key": "a" * 64})
    assert effective_bitrate(config.streaming, "user", None) == "192k"
    assert effective_bitrate(config.streaming, "user", "500k") == "192k"
    assert effective_bitrate(config.streaming, "admin", "256k") == "256k"
    assert effective_bitrate(config.streaming, "admin", "500k") == "320k"
    assert effective_bitrate(config.streaming, "unknown", "96k") == "96k"
