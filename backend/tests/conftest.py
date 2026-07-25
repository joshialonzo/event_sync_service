"""Shared fixtures.

Every later step's tests build on these, so the contracts here matter more than the two
test modules that currently use them.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


@pytest.fixture(scope="session")
def client() -> Iterator[TestClient]:
    """Exercises real routing and serialization, not the route functions directly."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def data_dir() -> Path:
    """The real source directory. Steps 4+ read the fixture files through this rather
    than rebuilding the path, so a moved data/ breaks one fixture instead of ten tests."""
    return get_settings().data_dir


@pytest.fixture
def clean_settings() -> Iterator[None]:
    """Drop the settings cache around a test that manipulates the environment.

    `get_settings` is lru_cached, so without this the first caller in the session pins
    the configuration for every test that follows — and an env-override test would then
    assert against stale values while still passing.
    """
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
