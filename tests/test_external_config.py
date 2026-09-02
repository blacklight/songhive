"""
Tests for the external libraries configuration schema.
"""

from songhive.config.schema import SonghiveConfig


def test_external_libraries_defaults():
    """ExternalLibrariesConfig uses the safe defaults from the contract."""
    config = SonghiveConfig()
    assert config.external_libraries.allow_user_created_libraries is False
    assert config.external_libraries.allowed_user_providers == []
    assert config.external_libraries.denied_user_providers == []
    assert config.external_libraries.allow_admin_library_index_inclusion is True
    assert config.external_libraries.allow_destructive_delete is False
    assert config.external_libraries.minimum_sync_interval_seconds == 300
    assert config.external_libraries.max_concurrent_syncs == 4
    assert config.external_libraries.stream_temp_dir is None
    assert config.external_libraries.stream_max_proxy_bytes == 256 * 1024 * 1024
    assert config.external_libraries.stream_proxy_timeout_seconds == 60


def test_external_libraries_env_override(monkeypatch):
    """Environment variables can toggle external library settings."""
    monkeypatch.setenv("SONGHIVE_EXTERNAL_LIBRARIES__ALLOW_USER_CREATED_LIBRARIES", "true")
    config = SonghiveConfig()
    assert config.external_libraries.allow_user_created_libraries is True


def test_external_libraries_allowed_providers_from_comma_string(monkeypatch):
    """allowed_user_providers can be parsed from a comma-separated env var."""
    monkeypatch.setenv("SONGHIVE_EXTERNAL_LIBRARIES__ALLOWED_USER_PROVIDERS", "s3, webdav, nextcloud")
    config = SonghiveConfig()
    assert config.external_libraries.allowed_user_providers == ["s3", "webdav", "nextcloud"]


def test_external_libraries_denied_providers_from_json_string(monkeypatch):
    """denied_user_providers can be parsed from a JSON list env var."""
    monkeypatch.setenv("SONGHIVE_EXTERNAL_LIBRARIES__DENIED_USER_PROVIDERS", '["ftp", "sftp"]')
    config = SonghiveConfig()
    assert config.external_libraries.denied_user_providers == ["ftp", "sftp"]


def test_external_libraries_local_roots_default():
    """local_roots is empty by default."""
    config = SonghiveConfig()
    assert config.external_libraries.local_roots == []


def test_external_libraries_local_roots_from_comma_string(monkeypatch):
    """local_roots can be parsed from a comma-separated env var."""
    monkeypatch.setenv("SONGHIVE_EXTERNAL_LIBRARIES__LOCAL_ROOTS", "/a, /b")
    config = SonghiveConfig()
    assert config.external_libraries.local_roots == ["/a", "/b"]


def test_external_libraries_local_roots_from_json_string(monkeypatch):
    """local_roots can be parsed from a JSON list env var."""
    monkeypatch.setenv("SONGHIVE_EXTERNAL_LIBRARIES__LOCAL_ROOTS", '["/a","/b"]')
    config = SonghiveConfig()
    assert config.external_libraries.local_roots == ["/a", "/b"]
