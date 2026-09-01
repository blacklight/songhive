"""
Tests for the external library adapter registry.
"""

import pytest

from songhive.external.base import ExternalLibraryAdapter
from songhive.external.errors import UnsupportedExternalOperation
from songhive.external.registry import (
    get_external_adapter,
    is_user_configurable,
    list_external_provider_types,
    register_external_adapter,
)
from songhive.external.types import ExternalItemRef, ExternalLibraryCapabilities


class _DummyAdapter(ExternalLibraryAdapter):
    """Minimal concrete adapter for registry tests."""

    provider_type = "dummy"
    user_configurable = True

    async def validate_config(self, config: dict) -> ExternalLibraryCapabilities:
        return ExternalLibraryCapabilities(validate_config=True)

    async def iter_items(self, config: dict, since=None):
        if False:
            yield ExternalItemRef(provider_key="", display_path="")


@pytest.fixture(autouse=True)
def _register_dummy():
    register_external_adapter("dummy", _DummyAdapter)


def test_register_and_get_adapter():
    assert get_external_adapter("dummy") is _DummyAdapter


def test_re_register_same_class_is_noop():
    register_external_adapter("dummy", _DummyAdapter)
    assert get_external_adapter("dummy") is _DummyAdapter


def test_register_different_class_raises():
    class _OtherAdapter(_DummyAdapter):
        """Another adapter claiming the same provider_type."""

    with pytest.raises(ValueError, match="already registered"):
        register_external_adapter("dummy", _OtherAdapter)


def test_get_unregistered_raises_keyerror():
    with pytest.raises(KeyError, match="No external adapter registered"):
        get_external_adapter("not-registered")


def test_list_external_provider_types():
    types = list_external_provider_types()
    assert "dummy" in types


def test_is_user_configurable():
    assert is_user_configurable("dummy") is True
    assert is_user_configurable("unknown") is False


async def test_default_method_raises_unsupported():
    adapter = _DummyAdapter()
    item = ExternalItemRef(provider_key="x", display_path="x")
    with pytest.raises(UnsupportedExternalOperation):
        await adapter.read_metadata({}, item)
