"""
Tests for the /data/secret_key fallback in CLI entry points.

The fallback is used when no explicit auth secret is configured.
"""

import os
from pathlib import Path

from songhive.cli import admin as cli_admin
from songhive.cli import watch as cli_watch

_VALID_CONFIG_SECRET = "nEh4UNkFQ4IhQTKt2_OaCK2KpQO9K0TOTt2C58AMzT_sPNUlBbSKW8K2S4CpMA3KRRSbNA1gH_OQlRIjx4DwkA"
_FALLBACK_FILE_SECRET = "fallback-secret-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"


def _write_config(path: Path, include_secret: bool = True) -> None:
    lines = [
        "[server]",
        'host = "0.0.0.0"',
        "port = 8000",
        "",
        "[database]",
        'url = "sqlite+aiosqlite:///./volumes/db/songhive.db"',
        "",
        "[auth]",
        'registration_mode = "open"',
    ]
    if include_secret:
        lines.append(f'secret_key = "{_VALID_CONFIG_SECRET}"')
    path.write_text("\n".join(lines) + "\n")


def _close_coro(coro) -> None:
    """Close a coroutine without running it to avoid 'never awaited' warnings."""
    if hasattr(coro, "close"):
        coro.close()


class TestWatchSecretFallback:
    """Tests for songhive.cli.watch.watch_main secret fallback."""

    def test_secret_file_used_when_config_has_no_secret(self, monkeypatch, tmp_path) -> None:
        """If config.toml has no auth secret, the fallback secret file is used."""
        monkeypatch.chdir(tmp_path)
        _write_config(tmp_path / "config.toml", include_secret=False)

        secret_file = tmp_path / "secret_key"
        secret_file.write_text(_FALLBACK_FILE_SECRET)

        monkeypatch.delenv("SONGHIVE_AUTH__SECRET_KEY", raising=False)
        monkeypatch.setenv("SONGHIVE_SECRET_FILE", str(secret_file))
        monkeypatch.setattr(cli_watch.logging, "basicConfig", lambda **kwargs: None)
        monkeypatch.setattr(cli_watch.asyncio, "run", _close_coro)

        cli_watch.watch_main([])

        assert os.environ.get("SONGHIVE_AUTH__SECRET_KEY") == _FALLBACK_FILE_SECRET


class TestAdminSecretFallback:
    """Tests for songhive.cli.admin.admin_main secret fallback."""

    def test_secret_file_used_when_config_has_no_secret(self, monkeypatch, tmp_path) -> None:
        """If config.toml has no auth secret, the fallback secret file is used."""
        monkeypatch.chdir(tmp_path)
        _write_config(tmp_path / "config.toml", include_secret=False)

        secret_file = tmp_path / "secret_key"
        secret_file.write_text(_FALLBACK_FILE_SECRET)

        monkeypatch.delenv("SONGHIVE_AUTH__SECRET_KEY", raising=False)
        monkeypatch.setenv("SONGHIVE_SECRET_FILE", str(secret_file))
        monkeypatch.setattr(cli_admin, "init_db", lambda *args, **kwargs: None)
        monkeypatch.setattr(cli_admin.asyncio, "run", _close_coro)

        cli_admin.admin_main(["init-db"])

        assert os.environ.get("SONGHIVE_AUTH__SECRET_KEY") == _FALLBACK_FILE_SECRET
