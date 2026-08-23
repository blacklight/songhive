"""
Federation allow/block helper tests.
"""

import pytest

from songhive.config.schema import SonghiveConfig
from songhive.services.federation import (
    extract_domain,
    is_domain_allowed,
    is_domain_blocked,
    normalize_instance_domain,
)


@pytest.fixture
def config():
    return SonghiveConfig(
        auth={"secret_key": "a" * 64},
        federation={"enabled": True, "instance_domain": "music.example.com"},
    )


def test_normalize_instance_domain():
    assert normalize_instance_domain("Example.COM") == "example.com"
    assert normalize_instance_domain("https://Example.COM/path") == "example.com"
    assert normalize_instance_domain("http://a.example/users/bob") == "a.example"
    assert normalize_instance_domain("") == ""


def test_extract_domain():
    assert extract_domain("https://a.example/users/bob") == "a.example"
    assert extract_domain("a.example") == "a.example"
    assert extract_domain("") == ""


def test_empty_allow_and_block_lists_allow_all(config):
    assert is_domain_blocked("a.example", config) is False
    assert is_domain_allowed("a.example", config) is True


def test_blocked_domain_takes_precedence(config):
    config.federation.allowed_instances = ["a.example"]
    config.federation.blocked_instances = ["a.example"]
    assert is_domain_blocked("a.example", config) is True
    assert is_domain_allowed("a.example", config) is False


def test_allowed_list_blocks_non_allowed(config):
    config.federation.allowed_instances = ["a.example"]
    assert is_domain_blocked("b.example", config) is True
    assert is_domain_allowed("b.example", config) is False
    assert is_domain_blocked("a.example", config) is False


def test_blocked_list_without_allow_list(config):
    config.federation.blocked_instances = ["evil.example"]
    assert is_domain_blocked("evil.example", config) is True
    assert is_domain_blocked("good.example", config) is False


def test_case_insensitive_and_url_parsing(config):
    config.federation.blocked_instances = ["EVIL.EXAMPLE"]
    assert is_domain_blocked("https://evil.example/users/bob", config) is True
