"""
Configuration loader: merges TOML file, environment variables, and CLI args.
"""

import argparse
import os
from pathlib import Path
from typing import Any, Dict, Optional

from .schema import SonghiveConfig

_CONFIG_SEARCH_PATHS = [
    Path("./config.toml"),
    Path(os.path.expanduser("~/.config/songhive/config.toml")),
    Path("/etc/songhive/config.toml"),
]


def _find_config_file(explicit_path: Optional[str] = None) -> Optional[Path]:
    """Find the config file from an explicit path or default search paths."""
    if explicit_path:
        p = Path(explicit_path)
        if p.exists():
            return p
        return None

    for path in _CONFIG_SEARCH_PATHS:
        if path.exists():
            return path
    return None


def _load_toml(path: Path) -> Dict[str, Any]:
    """Load a TOML file and return it as a dict."""
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

    with open(path, "rb") as f:
        data: Dict[str, Any] = tomllib.load(f)
        return data


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
        default=None,
        help="Path to config.toml file",
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
    Load configuration by merging (in order of increasing priority):
    1. Defaults (from schema)
    2. TOML config file
    3. Environment variables (handled by pydantic-settings)
    4. CLI arguments

    :param argv: Optional list of CLI arguments (defaults to sys.argv[1:]).
    :returns: A fully resolved SonghiveConfig instance.
    """
    parser = _build_cli_parser()
    args = parser.parse_args(argv if argv is not None else None)

    # Load TOML
    config_file = _find_config_file(args.config)
    toml_data: Dict[str, Any] = {}
    if config_file:
        toml_data = _load_toml(config_file)

    # Apply CLI overrides on top of TOML
    cli_data = _cli_overrides(args)
    merged = _deep_merge(toml_data, cli_data)

    # Pydantic-settings will layer env vars on top
    return SonghiveConfig(**merged)
