"""Tests for the meeting list page (step 21).

Asserted against the API rather than against hard-coded markup wherever possible: the point
of server-rendering from the same store is that the page and the JSON cannot disagree, and a
test that checks the page in isolation would not notice if they did.
"""

import re

from fastapi.testclient import TestClient


def _rows(html: str) -> list[str]:
    """The table body's rows, crudely but adequately for a page this size."""
    body = html.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    return [row for row in body.split("<tr>") if row.strip()]


# --- the table ---


def test_every_meeting_has_a_row(client: TestClient) -> None:
    html = client.get("/").text

    assert len(_rows(html)) == 24


def test_the_page_and_the_api_agree_on_every_id(client: TestClient) -> None:
    """The whole argument for rendering from the same store."""
    html = client.get("/").text
    ids = [meeting["id"] for meeting in client.get("/api/meetings").json()]

    for meeting_id in ids:
        assert f"/meetings/{meeting_id}" in html, meeting_id


def test_rows_are_in_the_stores_order(client: TestClient) -> None:
    html = client.get("/").text
    api_ids = [m["id"] for m in client.get("/api/meetings").json()]
    positions = [html.index(f"/meetings/{i}") for i in api_ids]

    assert positions == sorted(positions), "the page reorders what the store already sorted"


def test_the_first_row_is_the_earliest_meeting(client: TestClient) -> None:
    first = _rows(client.get("/").text)[0]

    assert "2025-03-10" in first
    assert "Q1 Portfolio Review" in first


# --- origin badges ---


def test_origin_badges_match_the_api_counts(client: TestClient) -> None:
    html = client.get("/").text

    assert html.count("badge-both") == 17
    assert html.count("badge-crm") == 3
    assert html.count("badge-calendar") == 4


def test_origin_labels_are_readable(client: TestClient) -> None:
    """"crm_only" is a wire format, not something to show a salesperson.

    Scoped to the table body: the filter form (step 22) legitimately carries the raw value
    in its `<option value="crm_only">`, because that is what goes into the URL.
    """
    html = client.get("/").text
    table = html.split("<tbody>", 1)[1].split("</tbody>", 1)[0]

    assert "CRM only" in table
    assert "Calendar only" in table
    assert "crm_only" not in table


# --- conflict badge ---


def test_the_conflict_badge_appears_exactly_four_times(client: TestClient) -> None:
    """Doc 02's anti-inflation rule, enforced in the rendering: only contradictions."""
    html = client.get("/").text

    assert html.count("badge-conflict") == 4


def test_the_conflicted_rows_are_the_ones_the_api_names(client: TestClient) -> None:
    html = client.get("/").text
    conflicted = [
        m["id"] for m in client.get("/api/meetings", params={"has_conflicts": "true"}).json()
    ]

    assert len(conflicted) == 4
    for meeting_id in conflicted:
        row = next(r for r in _rows(html) if f"/meetings/{meeting_id}" in r)
        assert "badge-conflict" in row, meeting_id


def test_granularity_differences_do_not_raise_the_badge(client: TestClient) -> None:
    """crm-1001-cal-a1 has a location granularity difference and a status lifecycle drift.
    Badging either would put a conflict marker on nearly every row."""
    row = next(
        r for r in _rows(client.get("/").text) if "/meetings/crm-1001-cal-a1" in r
    )

    assert "badge-conflict" not in row


def test_the_conflict_badge_names_the_fields(client: TestClient) -> None:
    row = next(
        r for r in _rows(client.get("/").text) if "/meetings/crm-1002-cal-a2" in r
    )

    assert "Sources disagree on: location" in row


# --- quality badge ---


def test_exactly_one_row_shows_an_error(client: TestClient) -> None:
    """CRM-1008's corrupt date is the only genuine error in 42 records — and it stands out
    only because the four internal meetings are `info`."""
    html = client.get("/").text

    assert html.count("badge-error") == 1
    row = next(r for r in _rows(html) if "/meetings/crm-1008-cal-a9" in r)
    assert "badge-error" in row


def test_the_quality_badges_split_as_the_data_does(client: TestClient) -> None:
    html = client.get("/").text

    assert html.count("badge-error") == 1
    assert html.count("badge-warning") == 3
    assert html.count("badge-info") == 20


def test_the_quality_badge_shows_severity_not_a_count(client: TestClient) -> None:
    """Every meeting carries at least one flag, so a count would be noise on all 24 rows.

    crm-1008-cal-a9 has three flags and must display "error", not "3".
    """
    html = client.get("/").text
    row = next(r for r in _rows(html) if "/meetings/crm-1008-cal-a9" in r)
    badge = re.search(r'class="badge badge-error"[^>]*>\s*([a-z]+)\s*<', row)

    assert badge is not None, "no severity badge rendered"
    assert badge.group(1) == "error"
    assert "3 data-quality flag(s)" in row, "the count is a tooltip, not the label"


# --- the awkward records ---


def test_a_meeting_without_a_client_renders_a_dash(client: TestClient) -> None:
    """Not "None" — the internal meetings legitimately have no client."""
    html = client.get("/").text

    assert ">None<" not in html
    assert "—" in html


def test_the_thin_calendar_record_renders(client: TestClient) -> None:
    """CAL-A11: no attendees, no location, no description, no CRM counterpart."""
    row = next(r for r in _rows(client.get("/").text) if "/meetings/cal-a11" in r)

    assert "Calendar only" in row
    assert "priya.sharma@firma.com" in row, "the organizer stands in for the owner"


def test_a_time_supplied_by_the_other_source_is_shown(client: TestClient) -> None:
    """CRM-1007 has no meeting_time; CAL-A8 supplies 15:00."""
    row = next(r for r in _rows(client.get("/").text) if "/meetings/crm-1007-cal-a8" in r)

    assert "15:00" in row


def test_every_row_has_a_date(client: TestClient) -> None:
    for row in _rows(client.get("/").text):
        assert re.search(r"20\d\d-\d\d-\d\d", row)


# --- safety ---


def test_titles_are_escaped(client: TestClient) -> None:
    """Titles are raw source strings; one containing markup must not become markup."""
    html = client.get("/").text
    body = html.split("<tbody>", 1)[1]

    assert "<script" not in body.lower()


def test_the_page_still_serves_alongside_the_api(client: TestClient) -> None:
    assert client.get("/").status_code == 200
    assert client.get("/api/meetings").status_code == 200
    assert client.get("/docs").status_code == 200
