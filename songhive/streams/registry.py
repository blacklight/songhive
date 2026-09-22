"""
Provider-keyed registry for audio output providers.
"""

from .base import AudioOutput

_REGISTRY: dict[str, type[AudioOutput]] = {}


def register_output(provider_type: str, output_cls: type[AudioOutput]) -> None:
    """Register an output class under the given provider type."""
    if not isinstance(provider_type, str) or not provider_type:
        raise ValueError("provider_type must be a non-empty string")

    existing = _REGISTRY.get(provider_type)
    if existing is not None and existing is not output_cls:
        raise ValueError(f"Provider type {provider_type!r} is already registered to {existing.__name__!r}")

    _REGISTRY[provider_type] = output_cls


def get_output(provider_type: str) -> type[AudioOutput]:
    """Return the output class registered for the given provider type."""
    try:
        return _REGISTRY[provider_type]
    except KeyError as exc:
        raise KeyError(f"No output provider registered for provider type {provider_type!r}") from exc


def list_output_types() -> list[str]:
    """Return all registered provider type keys."""
    return sorted(_REGISTRY.keys())


def is_user_configurable(provider_type: str) -> bool:
    """Return True when the provider for the provider type is user-configurable."""
    try:
        return get_output(provider_type).user_configurable
    except KeyError:
        return False
