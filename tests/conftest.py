"""
Shared test fixtures.
"""

import pytest
from fastapi.testclient import TestClient

from songhive.api.app import create_app
from songhive.config.schema import SonghiveConfig


@pytest.fixture
def config():
    """Create a test configuration."""
    return SonghiveConfig(
        server={"host": "127.0.0.1", "port": 8000, "debug": True},
        database={"url": "sqlite+aiosqlite:///test.db"},
        federation={"enabled": False},
    )


@pytest.fixture
def app(config):
    """Create a test FastAPI application."""
    return create_app(config)


@pytest.fixture
def client(app):
    """Create a test client."""
    return TestClient(app)
