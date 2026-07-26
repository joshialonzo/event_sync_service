"""Tests for the re-sync button (step 25).

The redirect and the banner are easy; the test that carries the weight is the idempotence
one — it is the only one that fails if `sync_now` starts appending instead of replacing.
"""

import re

import pytest
from fastapi.testclient import TestClient

import app.web as web


def _text(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _links(html: str) -> list[str]:
    return re.findall(r'href="/meetings/([^"]+)"', html)


# --- the form ---


def test_the_overview_carries_a_resync_form(client: TestClient) -> None:
    html = client.get("/stats").text

    assert 'method="post"' in html
    assert 'action="/sync"' in html
    assert "Re-sync now" in _text(html)


def test_sync_is_not_reachable_by_link(client: TestClient) -> None:
    """A GET must not run the pipeline: crawlers, prefetchers and the back button all issue
    GETs, and none of them mean "re-sync"."""
    assert client.get("/sync").status_code == 405


# --- post-redirect-get ---


def test_posting_redirects_to_the_overview(client: TestClient) -> None:
    response = client.post("/sync", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/stats?synced=ok"


def test_the_redirect_is_303_not_302(client: TestClient) -> None:
    """302 lets some clients preserve the method and re-POST to the target; 303 is defined as
    "GET the other resource", which is the whole point of the pattern."""
    assert client.post("/sync", follow_redirects=False).status_code == 303


def test_following_the_redirect_renders_the_overview(client: TestClient) -> None:
    response = client.post("/sync")

    assert response.status_code == 200
    assert str(response.url).endswith("/stats?synced=ok")
    assert "Sync overview" in _text(response.text)


def test_the_banner_quotes_the_run(client: TestClient) -> None:
    """"Done" is not checkable; this page exists precisely because its numbers are."""
    stats = client.get("/api/stats").json()
    text = _text(client.post("/sync").text)

    assert (
        f"Re-synced — {stats['meetings_out']} meetings from {stats['records_in']} records"
        in text
    )


# --- the banner is opt-in ---


def test_a_plain_visit_shows_no_banner(client: TestClient) -> None:
    assert "Re-synced" not in _text(client.get("/stats").text)


def test_an_unknown_synced_value_shows_no_banner(client: TestClient) -> None:
    """Query strings arrive from bookmarks and hand-editing; neither deserves an error page,
    and neither should be able to claim a sync happened."""
    for value in ("", "yes", "1", "ok'; drop"):
        text = _text(client.get("/stats", params={"synced": value}).text)

        assert "Re-synced" not in text, value
        assert "Re-sync failed" not in text, value
        assert "Sync overview" in text, value


def test_reloading_the_redirect_target_does_not_sync_again(client: TestClient) -> None:
    """The GET renders the banner; it must not be the thing that runs the pipeline."""
    client.post("/sync")
    first = client.get("/stats?synced=ok")
    second = client.get("/stats?synced=ok")

    generated = [
        _text(page.text).split("Last run ")[1][:16] for page in (first, second)
    ]
    assert generated[0] == generated[1]


# --- idempotence ---


def test_a_second_sync_leaves_the_dataset_identical(client: TestClient) -> None:
    """Two presses give 24 meetings, not 48. Every run rebuilds from the JSON files, and
    publishing swaps one reference — so this is a property of the pipeline, not a lock."""
    before = client.get("/api/meetings").json()
    before_stats = client.get("/api/stats").json()

    client.post("/sync")
    client.post("/sync")

    after = client.get("/api/meetings").json()
    after_stats = client.get("/api/stats").json()

    assert [m["id"] for m in after] == [m["id"] for m in before]
    assert len(after) == len(before)
    assert {k: v for k, v in after_stats.items() if k != "generated_at"} == {
        k: v for k, v in before_stats.items() if k != "generated_at"
    }


def test_the_rendered_page_is_unchanged_apart_from_the_run_time(client: TestClient) -> None:
    """The tiles, the conflict links and the flag rows must all survive a re-sync."""

    def body(html: str) -> str:
        """Everything from the first tile down — the run's timestamp is the one line that
        legitimately differs between two renders."""
        return _text(html).split("Sync overview", 1)[1].split("records in", 1)[1]

    before = client.get("/stats").text
    after = client.post("/sync").text

    assert body(after) == body(before)
    assert _links(after) == _links(before)


def test_the_last_run_time_advances(client: TestClient) -> None:
    before = client.get("/api/stats").json()["generated_at"]
    client.post("/sync")
    after = client.get("/api/stats").json()["generated_at"]

    assert after > before


def test_meetings_are_reachable_after_a_resync(client: TestClient) -> None:
    """A stale id after a re-sync would mean the ids are not derived from the data."""
    ids = _links(client.get("/stats").text)
    client.post("/sync")

    for meeting_id in ids:
        assert client.get(f"/meetings/{meeting_id}").status_code == 200, meeting_id


# --- failure ---


def test_a_failing_sync_redirects_instead_of_500ing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom() -> None:
        raise RuntimeError("data files moved")

    monkeypatch.setattr(web, "sync_now", boom)
    response = client.post("/sync", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/stats?synced=failed"


def test_a_failing_sync_says_the_previous_data_is_still_served(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(web, "sync_now", lambda: (_ for _ in ()).throw(RuntimeError("nope")))
    text = _text(client.post("/sync").text)

    assert "Re-sync failed" in text
    assert "previous dataset is still being served" in text


def test_a_failing_sync_leaves_the_published_dataset_intact(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guarantee the banner claims: `run_sync` builds the whole result before anything is
    replaced, so a raise cannot empty the store."""
    before = client.get("/api/stats").json()

    monkeypatch.setattr(web, "sync_now", lambda: (_ for _ in ()).throw(RuntimeError("nope")))
    client.post("/sync")
    monkeypatch.undo()

    after = client.get("/api/stats").json()
    assert after == before
    assert len(client.get("/api/meetings").json()) == before["meetings_out"]


def test_a_failing_sync_is_logged(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The banner is deliberately vague; the traceback still has to land somewhere."""
    monkeypatch.setattr(web, "sync_now", lambda: (_ for _ in ()).throw(RuntimeError("nope")))

    with caplog.at_level("ERROR", logger="event-sync"):
        client.post("/sync", follow_redirects=False)

    assert "re-sync from the UI failed" in caplog.text
    assert "RuntimeError" in caplog.text
