"""Integration tests for the app surface (steps 1-2), through the ASGI stack."""

from pathlib import Path

from fastapi.testclient import TestClient


def test_health_reports_ok(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_reports_the_resolved_configuration(client: TestClient) -> None:
    payload = client.get("/api/health").json()

    assert set(payload) == {"status", "data_dir", "timezone", "meetings", "last_sync"}
    assert payload["timezone"] == "America/New_York"


def test_health_reports_that_data_was_actually_loaded(client: TestClient) -> None:
    """A process that booted but reconciled nothing is not healthy, and this is the first
    endpoint a reviewer hits."""
    payload = client.get("/api/health").json()

    assert payload["meetings"] == 24
    assert payload["last_sync"].startswith("20")


def test_health_data_dir_exists_on_disk(client: TestClient) -> None:
    """The check that catches a broken container mount: the path is reported whether or
    not it is real, so the value is only meaningful if something asserts it resolves."""
    reported = Path(client.get("/api/health").json()["data_dir"])

    assert reported.is_dir()
    assert (reported / "crm_events.json").is_file()


def test_openapi_lists_the_health_route(client: TestClient) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert "/api/health" in response.json()["paths"]


def test_unknown_path_is_404(client: TestClient) -> None:
    """Proves routing is real rather than a catch-all that would make every other
    assertion here vacuous."""
    assert client.get("/api/nope").status_code == 404
