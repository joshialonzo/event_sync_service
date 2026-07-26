"""Tests for startup wiring (step 16).

The claim under test is that the service is populated *before* it is reachable — so most of
these assert against a client that has just entered its context, and one asserts the failure
path, which matters more than the success path.
"""

import logging

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_repository, sync_now
from app.main import app
from app.repository import Repository
from app.repository.memory import EMPTY, InMemoryRepository


# --- the store is populated before the first request ---


def test_entering_the_client_context_runs_the_sync() -> None:
    """The lifespan handler, exercised the way the real server does it. This is also what
    makes step 17's route tests possible without seeding fixtures."""
    with TestClient(app) as client:
        payload = client.get("/api/health").json()

    assert payload["meetings"] == 24


def test_the_store_holds_the_reconciled_dataset(client: TestClient) -> None:
    repository = get_repository()

    assert len(repository.list_meetings()) == 24
    assert repository.get_stats().records_in == 42


def test_meetings_are_available_in_order_immediately(client: TestClient) -> None:
    meetings = get_repository().list_meetings()

    assert meetings[0].id == "crm-1001-cal-a1"
    assert [m.event_date for m in meetings] == sorted(m.event_date for m in meetings)


def test_the_sync_logs_what_it_produced(caplog: pytest.LogCaptureFixture) -> None:
    """The fastest way to know a container is healthy is this line in the log."""
    with caplog.at_level(logging.INFO, logger="event-sync"):
        sync_now()

    assert "24 meetings from 42 records" in caplog.text
    assert "17 matched" in caplog.text


# --- the dependency ---


def test_get_repository_returns_one_instance() -> None:
    """Two stores would mean a re-sync updated one of them and the UI read the other."""
    assert get_repository() is get_repository()


def test_get_repository_satisfies_the_protocol() -> None:
    assert isinstance(get_repository(), Repository)


def test_the_dependency_can_be_overridden_without_touching_module_state() -> None:
    """The seam that lets later steps test routes against a controlled dataset."""
    empty = InMemoryRepository(EMPTY)
    app.dependency_overrides[get_repository] = lambda: empty

    try:
        with TestClient(app) as client:
            assert client.get("/api/health").json()["meetings"] == 0
    finally:
        app.dependency_overrides.clear()

    assert len(get_repository().list_meetings()) == 24, "module state untouched"


# --- idempotence and failure ---


def test_syncing_twice_does_not_duplicate_anything(client: TestClient) -> None:
    """`run_sync` builds a whole new result and `replace_all` swaps one reference, so the
    re-sync button in step 25 is safe to press repeatedly."""
    first = sync_now()
    second = sync_now()

    assert first.meetings_out == second.meetings_out == 24
    assert len(get_repository().list_meetings()) == 24


def test_a_bad_data_dir_prevents_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """The important failure. An empty store would look like a working service with no
    meetings; a crash at boot is unambiguous.

    The check runs against `sync_now` rather than the lifespan handler because a failing
    startup inside TestClient surfaces as the same exception either way, and this form says
    plainly which call is expected to raise.
    """
    from app.config import get_settings

    monkeypatch.setenv("DATA_DIR", "/tmp/event-sync-does-not-exist")
    get_settings.cache_clear()

    try:
        with pytest.raises(FileNotFoundError):
            sync_now()
    finally:
        monkeypatch.delenv("DATA_DIR", raising=False)
        get_settings.cache_clear()

    # And the store still holds the previous dataset rather than a half-written one.
    sync_now()
    assert len(get_repository().list_meetings()) == 24


def test_startup_failure_propagates_through_the_lifespan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same failure as seen by uvicorn: the process does not come up."""
    from app.config import get_settings

    monkeypatch.setenv("DATA_DIR", "/tmp/event-sync-does-not-exist")
    get_settings.cache_clear()

    try:
        with pytest.raises(FileNotFoundError), TestClient(app):
            pass
    finally:
        monkeypatch.delenv("DATA_DIR", raising=False)
        get_settings.cache_clear()
        sync_now()
