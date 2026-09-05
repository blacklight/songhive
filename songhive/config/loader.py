"""
Configuration loader: merges TOML file, environment variables, and CLI args.
"""

import argparse
import os
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic_settings import TomlConfigSettingsSource

from .schema import SonghiveConfig


def _xdg_config_home() -> Path:
    """Return the XDG config home, falling back to ~/.config."""
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(os.path.expanduser(xdg_config_home))
    return Path.home() / ".config"


def _config_search_paths() -> list[Path]:
    """Return the default config file search paths in priority order."""
    paths = [
        Path("./config.toml"),
        _xdg_config_home() / "songhive" / "config.toml",
        Path("/etc/songhive/config.toml"),
    ]

    seen = set()
    unique: list[Path] = []
    for path in paths:
        # Use the path as-is for relative paths; resolve absolute ones to avoid
        # duplicates when XDG_CONFIG_HOME points at ~/.config.
        key = path.resolve() if path.is_absolute() else path
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _find_config_file(explicit_path: Optional[str] = None) -> Optional[Path]:
    """Find the config file from an explicit path or default search paths."""
    if explicit_path:
        p = Path(explicit_path)
        if p.exists():
            return p
        raise FileNotFoundError(f"Config file not found: {p}")

    for path in _config_search_paths():
        if path.exists():
            return path
    return None


def _build_cli_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="songhive",
        description="Songhive - A federated music sharing service",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=str,
        default=os.environ.get("SONGHIVE_CONFIG"),
        help="Path to config.toml file (also read from SONGHIVE_CONFIG env var)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="Server bind address",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Server listen port",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=None,
        help="Enable debug mode",
    )
    parser.add_argument(
        "--db-url",
        type=str,
        default=None,
        help="Database URL",
    )
    parser.add_argument(
        "--redis-url",
        type=str,
        default=None,
        help="Redis URL",
    )
    parser.add_argument(
        "--domain",
        type=str,
        default=None,
        help="Public domain for federation",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Number of worker processes",
    )
    parser.add_argument(
        "--cors-origins",
        type=str,
        nargs="+",
        default=None,
        help="Allowed CORS origins (can be given multiple times or as comma-separated values)",
    )
    return parser


def _cli_overrides(args: argparse.Namespace) -> Dict[str, Any]:
    """Convert parsed CLI args to a nested config dict (only non-None values)."""
    overrides: Dict[str, Any] = {}

    if args.host is not None:
        overrides.setdefault("server", {})["host"] = args.host
    if args.port is not None:
        overrides.setdefault("server", {})["port"] = args.port
    if args.debug is not None:
        overrides.setdefault("server", {})["debug"] = args.debug
    if args.workers is not None:
        overrides.setdefault("server", {})["num_workers"] = args.workers
    if args.db_url is not None:
        overrides.setdefault("database", {})["url"] = args.db_url
    if args.redis_url is not None:
        overrides.setdefault("redis", {})["url"] = args.redis_url
    if args.domain is not None:
        overrides.setdefault("federation", {})["instance_domain"] = args.domain
    if args.cors_origins is not None:
        overrides.setdefault("server", {})["cors_origins"] = args.cors_origins

    return overrides


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Deep-merge override into base, returning a new dict."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(argv: Optional[list] = None) -> SonghiveConfig:
    """
    Load configuration by merging (in order of decreasing priority):
    1. Environment variables (SONGHIVE_ prefix)
    2. CLI arguments
    3. TOML config file
    4. Defaults (from schema)

    The TOML file is discovered from ``--config``/``SONGHIVE_CONFIG``,
    ``./config.toml``, ``$XDG_CONFIG_HOME/songhive/config.toml``
    (or ``~/.config/songhive/config.toml``), or ``/etc/songhive/config.toml``.

    :param argv: Optional list of CLI arguments (defaults to sys.argv[1:]).
    :returns: A fully resolved SonghiveConfig instance.
    """
    parser = _build_cli_parser()
    args = parser.parse_args(argv if argv is not None else None)
    config_file = _find_config_file(args.config)
    cli_data = _cli_overrides(args)

    class _SonghiveConfig(SonghiveConfig):
        """
        SonghiveConfig variant that loads the discovered TOML file and orders
        sources so that environment variables > CLI > TOML > defaults.
        """

        model_config = SonghiveConfig.model_config.copy()
        model_config["toml_file"] = str(config_file) if config_file else None

        @classmethod
        def settings_customise_sources(
            cls,
            settings_cls,
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
        ):
            """Order sources with env first, then CLI, then TOML, then defaults."""
            toml_file = settings_cls.model_config.get("toml_file")
            toml_settings = TomlConfigSettingsSource(
                settings_cls,
                toml_file=toml_file,
                deep_merge=True,
            )
            return (
                env_settings,
                init_settings,
                toml_settings,
                dotenv_settings,
                file_secret_settings,
            )

    return _SonghiveConfig(**cli_data)
