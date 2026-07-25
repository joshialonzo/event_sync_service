"""Unit tests for Settings (step 2).

These construct `Settings()` directly rather than calling `get_settings()`, so they test
the class rather than the cache.
"""

from pathlib import Path

import pytest

from app.config import Settings, get_settings


def test_data_dir_defaults_to_the_repo_data_directory() -> None:
    settings = Settings()

    assert settings.data_dir.is_absolute()
    assert settings.data_dir.is_dir()
    assert settings.data_dir.name == "data"


def test_default_data_dir_contains_both_source_files() -> None:
    """The default is only useful if it points at the real files — asserting the path
    shape alone would pass against an empty directory."""
    settings = Settings()

    names = {path.name for path in settings.data_dir.iterdir()}

    assert {"crm_events.json", "calendar_events.json"} <= names


def test_timezone_defaults_to_eastern() -> None:
    assert Settings().timezone == "America/New_York"


@pytest.mark.usefixtures("clean_settings")
def test_data_dir_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", "/tmp")

    # Compared against the resolved path: on macOS /tmp is a symlink to /private/tmp, so
    # asserting the literal string would pass on Linux and fail here.
    assert Settings().data_dir == Path("/tmp").resolve()


@pytest.mark.usefixtures("clean_settings")
def test_relative_data_dir_is_resolved_absolute(monkeypatch: pytest.MonkeyPatch) -> None:
    """A relative path would otherwise resolve against each process's cwd, which differs
    between uvicorn, pytest, and the container."""
    monkeypatch.setenv("DATA_DIR", "../data")

    resolved = Settings().data_dir

    assert resolved.is_absolute()
    assert resolved.is_dir()
    assert resolved.name == "data"


@pytest.mark.usefixtures("clean_settings")
def test_timezone_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TIMEZONE", "UTC")

    assert Settings().timezone == "UTC"


@pytest.mark.usefixtures("clean_settings")
def test_get_settings_returns_the_same_instance() -> None:
    """The cache is the reason config cannot drift mid-process."""
    assert get_settings() is get_settings()
