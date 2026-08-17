"""
Configuration loading tests.
"""

from songhive.config.loader import _deep_merge, load_config
from songhive.config.schema import SonghiveConfig


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
    """Test that CORS origins default to all origins."""
    config = SonghiveConfig()
    assert config.server.cors_origins == ["*"]


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
