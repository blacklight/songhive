"""
Provider-keyed registry for external library adapters.
"""

from .base import ExternalLibraryAdapter

_REGISTRY: dict[str, type[ExternalLibraryAdapter]] = {}


def register_external_adapter(provider_type: str, adapter_cls: type[ExternalLibraryAdapter]) -> None:
    """Register an adapter class under the given provider type."""
    if not isinstance(provider_type, str) or not provider_type:
        raise ValueError("provider_type must be a non-empty string")

    existing = _REGISTRY.get(provider_type)
    if existing is not None and existing is not adapter_cls:
        raise ValueError(f"Provider type {provider_type!r} is already registered to {existing.__name__!r}")

    _REGISTRY[provider_type] = adapter_cls


def get_external_adapter(provider_type: str) -> type[ExternalLibraryAdapter]:
    """Return the adapter class registered for the given provider type."""
    try:
        return _REGISTRY[provider_type]
    except KeyError as exc:
        raise KeyError(f"No external adapter registered for provider type {provider_type!r}") from exc


def list_external_provider_types() -> list[str]:
    """Return all registered provider type keys."""
    return sorted(_REGISTRY.keys())


def is_user_configurable(provider_type: str) -> bool:
    """Return True when the adapter for the provider type is user-configurable."""
    try:
        return get_external_adapter(provider_type).user_configurable
    except KeyError:
        return False
