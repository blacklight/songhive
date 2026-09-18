"""
Name-keyed registry for third-party API adapters.

Mirrors the external-library adapter registry (``external/registry.py``):
adapters self-register at import time, and ``mount_adapters`` /
``adapter_tornado_routes`` mount every adapter whose ``is_enabled`` gate
accepts the running configuration.
"""

import logging
from typing import TYPE_CHECKING, Dict, List, Tuple, Type

from fastapi import FastAPI

from ..config.schema import SonghiveConfig
from .base import APIAdapter

if TYPE_CHECKING:
    import tornado.web

logger = logging.getLogger(__name__)

_REGISTRY: Dict[str, Type[APIAdapter]] = {}


def register_adapter(name: str, adapter_cls: Type[APIAdapter]) -> None:
    """Register an adapter class under ``name`` (idempotent for the same class)."""
    if not isinstance(name, str) or not name:
        raise ValueError("name must be a non-empty string")

    existing = _REGISTRY.get(name)
    if existing is not None and existing is not adapter_cls:
        raise ValueError(f"API adapter {name!r} is already registered to {existing.__name__!r}")

    _REGISTRY[name] = adapter_cls


def get_adapter(name: str) -> Type[APIAdapter]:
    """Return the adapter class registered under ``name``."""
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise KeyError(f"No API adapter registered under {name!r}") from exc


def list_adapters() -> List[str]:
    """Return all registered adapter names."""
    return sorted(_REGISTRY)


def _enabled_adapters(config: SonghiveConfig) -> List[APIAdapter]:
    """Instantiate every registered adapter whose config gate is enabled."""
    enabled: List[APIAdapter] = []
    for name in list_adapters():
        adapter = get_adapter(name)()
        try:
            if adapter.is_enabled(config):
                enabled.append(adapter)
        except Exception:
            logger.exception("Failed to evaluate API adapter %r; skipping", name)
    return enabled


def mount_adapters(app: FastAPI, config: SonghiveConfig) -> None:
    """Mount every enabled API adapter's FastAPI routes on ``app``."""
    for adapter in _enabled_adapters(config):
        try:
            adapter.include(app, config)
        except Exception:
            logger.exception("Failed to mount API adapter %r", adapter.name)


def adapter_tornado_routes(config: SonghiveConfig) -> List[Tuple[str, Type["tornado.web.RequestHandler"]]]:
    """Collect Tornado-native routes from every enabled API adapter."""
    routes: List[Tuple[str, Type["tornado.web.RequestHandler"]]] = []
    for adapter in _enabled_adapters(config):
        try:
            routes.extend(adapter.tornado_routes())
        except Exception:
            logger.exception("Failed to collect Tornado routes for API adapter %r", adapter.name)
    return routes
