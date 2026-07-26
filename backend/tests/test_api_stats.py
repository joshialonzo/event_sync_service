"""Tests for the stats and re-sync endpoints (step 19).

`/api/stats` is the endpoint a reviewer hits to check the reconciliation without reading
code, so its numbers are asserted against the fixture rather than against itself.
"""

import time

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_repository, sync_now
from app.jobs.sync import run_sync


# --- stats ---


def test_stats_reports_the_headline_numbers(client: TestClient) -> None:
    """Doc 03's five-second check: 42 records in, 24 meetings out, 17 matched, 4 conflicts."""
    payload = client.get("/api/stats").json()

    assert payload["records_in"] == 42
    assert payload["meetings_out"] == 24
    assert payload["matched_pairs"] == 17
    assert sum(payload["conflicts_by_field"].values()) == 4


def test_stats_reports_the_source_split(client: TestClient) -> None:
    payload = client.get("/api/stats").json()

    assert payload["crm_records_in"] == 20
    assert payload["calendar_records_in"] == 22
    assert payload["crm_only"] == 3
    assert payload["calendar_only"] == 4
    assert payload["duplicates_collapsed"] == 1


def test_stats_reports_no_low_confidence_matches(client: TestClient) -> None:
    """A zero is an answer: nothing in this dataset was merged on a hunch."""
    assert client.get("/api/stats").json()["low_confidence_matches"] == 0


def test_stats_reports_the_conflict_breakdown(client: TestClient) -> None:
    payload = client.get("/api/stats").json()

    assert payload["conflicts_by_kind"] == {
        "contradiction": 4,
        "granularity": 8,
        "absence": 3,
    }
    assert payload["conflicts_by_field"] == {
        "start_time": 2,
        "location": 1,
        "status": 1,
    }


def test_stats_reports_the_data_quality_census(client: TestClient) -> None:
    payload = client.get("/api/stats").json()

    assert payload["flags_by_code"]["TIMEZONE_ASSUMED"] == 40
    assert payload["flags_by_severity"] == {"info": 47, "warning": 3, "error": 1}


def test_stats_does_not_recount_the_meetings(client: TestClient) -> None:
    """The endpoint returns the summary the pipeline built. If it re-derived the numbers it
    could disagree with the run that produced the data it is describing."""
    payload = client.get("/api/stats").json()
    expected = run_sync().summary

    assert payload["meetings_out"] == expected.meetings_out
    assert payload["flags_by_code"] == expected.flags_by_code
    assert payload["conflicts_by_kind"] == expected.conflicts_by_kind


def test_stats_agrees_with_the_meetings_endpoint(client: TestClient) -> None:
    """The cross-check a reviewer would actually do."""
    stats = client.get("/api/stats").json()
    meetings = client.get("/api/meetings").json()
    conflicted = client.get("/api/meetings", params={"has_conflicts": "true"}).json()

    assert stats["meetings_out"] == len(meetings)
    assert sum(stats["conflicts_by_field"].values()) == len(conflicted)


def test_stats_includes_a_generated_at_timestamp(client: TestClient) -> None:
    payload = client.get("/api/stats").json()

    assert payload["generated_at"].startswith("20")


# --- re-sync ---


def test_resync_returns_the_new_summary(client: TestClient) -> None:
    response = client.post("/api/sync")

    assert response.status_code == 200, "the work is done; 202 would imply a job to poll"
    assert response.json()["meetings_out"] == 24
    assert response.json()["records_in"] == 42


def test_resync_is_idempotent(client: TestClient) -> None:
    """Pressing the button twice must leave 24 meetings, not 48."""
    before = client.get("/api/meetings").json()

    client.post("/api/sync")
    client.post("/api/sync")

    after = client.get("/api/meetings").json()

    assert [m["id"] for m in after] == [m["id"] for m in before]


def test_resync_changes_only_the_timestamp(client: TestClient) -> None:
    first = client.get("/api/stats").json()
    time.sleep(0.01)
    second = client.post("/api/sync").json()

    assert second["generated_at"] > first["generated_at"]
    assert {k: v for k, v in second.items() if k != "generated_at"} == {
        k: v for k, v in first.items() if k != "generated_at"
    }


def test_meetings_remain_retrievable_after_a_resync(client: TestClient) -> None:
    """Ids are derived from source ids, so a bookmark survives a re-sync."""
    client.post("/api/sync")

    assert client.get("/api/meetings/crm-1002-cal-a2").status_code == 200


def test_a_failed_resync_leaves_the_previous_dataset_serving(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure mode the obvious implementation gets wrong.

    `run_sync` builds the entire result before `replace_all` is called, so a run that raises
    cannot empty the store. A "clear, then repopulate" store would serve zero meetings after
    a transient disk error.
    """
    from app.config import get_settings

    monkeypatch.setenv("DATA_DIR", "/tmp/event-sync-vanished")
    get_settings.cache_clear()

    try:
        with pytest.raises(FileNotFoundError):
            client.post("/api/sync")
    finally:
        monkeypatch.delenv("DATA_DIR", raising=False)
        get_settings.cache_clear()

    assert len(get_repository().list_meetings()) == 24
    assert len(client.get("/api/meetings").json()) == 24


def test_concurrent_resyncs_leave_a_consistent_store(client: TestClient) -> None:
    """No lock is taken: one run wins and the loser's work is discarded, but a reader never
    sees a mixture because publishing is a single reference swap."""
    import threading

    errors: list[BaseException] = []

    def run() -> None:
        try:
            sync_now()
        except BaseException as exc:  # pragma: no cover - only on failure
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert len(get_repository().list_meetings()) == 24


# --- documentation ---


def test_both_endpoints_are_documented(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert "get" in paths["/api/stats"]
    assert "post" in paths["/api/sync"]


def test_the_stats_schema_advertises_records_in(client: TestClient) -> None:
    """`records_in` is derived, not stored. `computed_field` is what puts it in the payload
    and the schema — a plain `@property` would appear in neither."""
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert "records_in" in schemas["SyncRunSummary"]["properties"]
