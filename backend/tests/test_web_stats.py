"""Tests for the sync overview page (step 24).

Numbers are checked against `/api/stats` rather than hard-coded, and the links are followed —
a count nobody can check is a claim, which is the thing this page exists not to be.
"""

import re

from fastapi.testclient import TestClient

from app.models.unified import SyncResult, SyncRunSummary
from app.repository.memory import EMPTY, InMemoryRepository


def _section(html: str, heading: str) -> str:
    start = html.index(f">{heading}<")
    rest = html[start:]
    end = rest.find("<h2", 1)
    return rest if end == -1 else rest[:end]


def _text(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _links(html: str) -> list[str]:
    return re.findall(r'href="/meetings/([^"]+)"', html)


# --- the numbers ---


def test_the_page_renders(client: TestClient) -> None:
    response = client.get("/stats")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_the_headline_numbers_match_the_api(client: TestClient) -> None:
    """Doc 03's five-second check, on a page instead of in a curl."""
    stats = client.get("/api/stats").json()
    text = _text(client.get("/stats").text)

    assert f"{stats['records_in']} records in" in text
    assert f"{stats['meetings_out']} meetings out" in text
    assert f"{stats['matched_pairs']} matched across both sources" in text


def test_the_source_split_is_shown(client: TestClient) -> None:
    text = _text(client.get("/stats").text)

    assert "20 CRM + 22 calendar" in text
    assert "3 CRM only" in text
    assert "4 calendar only" in text


def test_the_collapsed_duplicate_is_reported(client: TestClient) -> None:
    assert "1 duplicates collapsed" in _text(client.get("/stats").text)


def test_the_last_run_time_and_data_dir_are_shown(client: TestClient) -> None:
    """Where the data came from is the first thing to check when the numbers look wrong."""
    text = _text(client.get("/stats").text)

    assert "Last run 20" in text
    assert "/data" in text


# --- conflicts ---


def test_the_conflict_kinds_are_explained_not_just_counted(client: TestClient) -> None:
    """15 fields differ; 4 are disagreements. Showing only the 4 hides the decision, and
    showing all 15 as conflicts is the badge inflation doc 02 warns about."""
    text = _text(_section(client.get("/stats").text, "Conflicts"))

    assert "4 genuine contradictions" in text
    assert "8 differences of specificity" in text
    assert "3 absences" in text


def test_every_conflicting_meeting_is_linked(client: TestClient) -> None:
    section = _section(client.get("/stats").text, "Conflicts")
    linked = set(_links(section))
    expected = {
        m["id"] for m in client.get("/api/meetings", params={"has_conflicts": "true"}).json()
    }

    assert linked == expected
    assert len(linked) == 4


def test_conflicts_are_grouped_by_field(client: TestClient) -> None:
    text = _text(_section(client.get("/stats").text, "Conflicts"))

    assert "start time" in text
    assert "location" in text
    assert "status" in text


def test_the_conflict_links_open_meetings_that_agree(client: TestClient) -> None:
    """Following a link must land on a page that actually shows the conflict."""
    for meeting_id in set(_links(_section(client.get("/stats").text, "Conflicts"))):
        page = client.get(f"/meetings/{meeting_id}")

        assert page.status_code == 200
        assert "Sources disagree on" in _text(page.text), meeting_id


# --- data quality ---


def test_every_flag_code_has_a_row(client: TestClient) -> None:
    codes = set(client.get("/api/stats").json()["flags_by_code"])
    text = _text(_section(client.get("/stats").text, "Data quality"))

    for code in codes:
        assert code in text, code


def test_flag_counts_match_the_api(client: TestClient) -> None:
    counts = client.get("/api/stats").json()["flags_by_code"]
    section = _section(client.get("/stats").text, "Data quality")
    rows = section.split("<tr>")

    for code, count in counts.items():
        row = next(r for r in rows if code in r)
        assert f"<td>{count}</td>" in row, code


def test_the_error_sorts_above_the_info_rows(client: TestClient) -> None:
    """One corrupt date must not sit below 40 timezone assumptions."""
    section = _section(client.get("/stats").text, "Data quality")

    assert section.index("MALFORMED_DATE") < section.index("TIMEZONE_ASSUMED")
    assert section.index("badge-error") < section.index("badge-info")


def test_flag_links_reach_meetings_that_carry_the_flag(client: TestClient) -> None:
    """The links are the point: a count is a claim until you can check it."""
    section = _section(client.get("/stats").text, "Data quality")
    row = next(r for r in section.split("<tr>") if "MALFORMED_DATE" in r)
    linked = _links(row)

    assert linked == ["crm-1008-cal-a9"]

    detail = client.get(f"/meetings/{linked[0]}")
    assert "MALFORMED_DATE" in detail.text


def test_a_widespread_flag_truncates_its_links(client: TestClient) -> None:
    """TIMEZONE_ASSUMED fires on all 24 meetings. A row of 24 links buries the eight codes
    below it, each affecting one or two records — which is the interesting part."""
    section = _section(client.get("/stats").text, "Data quality")
    row = next(r for r in section.split("<tr>") if "TIMEZONE_ASSUMED" in r)

    assert len(_links(row)) == 6
    assert "+18 more" in row


def test_each_flag_row_explains_itself(client: TestClient) -> None:
    """The code is a token; the message is what a reader can act on."""
    text = _text(_section(client.get("/stats").text, "Data quality"))

    assert "Naive timestamp assumed to be Eastern" in text
    assert "Date required a fallback pattern to parse" in text


def test_the_absence_of_low_confidence_matches_is_stated(client: TestClient) -> None:
    """A zero worth saying out loud: nothing was merged on a hunch."""
    text = _text(client.get("/stats").text)

    assert "nothing here was merged on a hunch" in text


# --- robustness ---


def test_the_page_survives_an_empty_store(client: TestClient) -> None:
    """Before the first sync, or after one that produced nothing, the page must render a
    zero rather than divide by it."""
    from app.dependencies import get_repository
    from app.main import app

    app.dependency_overrides[get_repository] = lambda: InMemoryRepository(EMPTY)
    try:
        response = client.get("/stats")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "No data-quality flags" in response.text
    assert "The sources agree everywhere" in response.text


def test_the_page_reflects_a_replaced_dataset(client: TestClient) -> None:
    """The page reads the store, not a cached render."""
    from app.dependencies import get_repository
    from app.main import app

    trimmed = SyncResult(
        meetings={},
        by_date=[],
        summary=SyncRunSummary(
            generated_at=client.get("/api/stats").json()["generated_at"],
            crm_records_in=7,
            calendar_records_in=3,
        ),
    )
    app.dependency_overrides[get_repository] = lambda: InMemoryRepository(trimmed)
    try:
        text = _text(client.get("/stats").text)
    finally:
        app.dependency_overrides.clear()

    assert "10 records in" in text
    assert "7 CRM + 3 calendar" in text


def test_the_nav_links_here_from_every_page(client: TestClient) -> None:
    for path in ("/", "/stats", "/meetings/crm-1001-cal-a1"):
        assert 'href="/stats"' in client.get(path).text, path
