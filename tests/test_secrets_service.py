"""
Tests for the secret-at-rest helper service.
"""

from songhive.services import secrets


def test_encrypt_secret_round_trip():
    """encrypt_secret and decrypt_secret round-trip a plaintext value."""
    plaintext = "hello"
    token = secrets.encrypt_secret(plaintext)
    assert token != plaintext
    assert secrets.decrypt_secret(token) == plaintext


def test_encrypt_json_round_trip():
    """encrypt_json and decrypt_json round-trip a dict."""
    obj = {"provider": "s3", "bucket": "music", "secret_key": "abc"}
    token = secrets.encrypt_json(obj)
    assert secrets.decrypt_json(token) == obj


def test_redact_config():
    """redact_config replaces secret-like keys with a redaction marker."""
    config = {"host": "x", "secret_key": "y", "password": "z"}
    redacted = secrets.redact_config(config)
    assert redacted == {"host": "x", "secret_key": "<redacted>", "password": "<redacted>"}


def test_redact_config_heuristic_covers_token_and_credential():
    """The redaction heuristic also matches token and credential key names."""
    config = {
        "host": "x",
        "api_token": "t",
        "credential_id": "c",
        "my_key": "k",
        "top_secret": "s",
    }
    redacted = secrets.redact_config(config)
    assert redacted["host"] == "x"
    assert redacted["api_token"] == "<redacted>"
    assert redacted["credential_id"] == "<redacted>"
    assert redacted["my_key"] == "<redacted>"
    assert redacted["top_secret"] == "<redacted>"
