"""Tests for the meeting detail page (step 23).

This is the page that answers both frontend requirements from the brief, so the assertions
are about what a reader can actually see: the source behind each value, the value the other
source gave, and the arithmetic behind the match.
"""

import re

from fastapi.testclient import TestClient


def _section(html: str, heading: str) -> str:
    """Everything between one <h2> and the next."""
    start = html.index(f">{heading}<")
    rest = html[start:]
    end = rest.find("<h2", 1)
    return rest if end == -1 else rest[:end]


def _text(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


# --- every meeting renders ---


def test_every_meeting_has_a_page(client: TestClient) -> None:
    for meeting in client.get("/api/meetings").json():
        response = client.get(f"/meetings/{meeting['id']}")

        assert response.status_code == 200, meeting["id"]


def test_the_list_links_reach_real_pages(client: TestClient) -> None:
    """Step 21's rows have pointed here since they were written; this is where they start
    working."""
    listing = client.get("/").text
    hrefs = set(re.findall(r'href="/meetings/([^"]+)"', listing))

    assert len(hrefs) == 24
    for meeting_id in hrefs:
        assert client.get(f"/meetings/{meeting_id}").status_code == 200


# --- merged record ---


def test_every_field_shows_its_source(client: TestClient) -> None:
    section = _section(client.get("/meetings/crm-1001-cal-a1").text, "Merged record")

    assert "calendar" in section
    assert "crm" in section
    assert _text(section).count("crm") >= 4


def test_a_conflicting_field_shows_both_values(client: TestClient) -> None:
    """CRM-1002: the In-Person/Zoom case the brief names. Both values, marked."""
    section = _text(_section(client.get("/meetings/crm-1002-cal-a2").text, "Merged record"))

    assert "Zoom - https://zoom.us/j/98765432100" in section
    assert "NYC Office - 30th Floor" in section


def test_the_conflicting_value_is_marked_in_place(client: TestClient) -> None:
    """Not only badged at the top — the disagreement should be visible where the reader is
    looking."""
    html = client.get("/meetings/crm-1002-cal-a2").text

    assert "conflict-value" in html
    assert "Sources disagree on location" in _text(html)


def test_a_non_conflicting_alternative_is_shown_without_alarm(client: TestClient) -> None:
    """crm-1001-cal-a1's location differs only in specificity. The other value is visible,
    the conflict styling is not."""
    section = _section(client.get("/meetings/crm-1001-cal-a1").text, "Merged record")
    text = _text(section)

    assert "HQ - Conference Room B" in text
    assert "Conference Room B" in text
    assert "granularity" in text
    assert "conflict-value" not in section


def test_most_fields_carry_an_alternative_and_almost_none_conflict(client: TestClient) -> None:
    """Doc 02's badge-inflation argument, shown rather than argued: a matched meeting has
    alternatives nearly everywhere and at most one conflict."""
    html = client.get("/meetings/crm-1002-cal-a2").text
    section = _section(html, "Merged record")

    assert section.count("alternative") >= 8
    assert section.count("conflict-value") == 1


def test_participants_render_as_people_not_as_python(client: TestClient) -> None:
    """The field holds a list of dicts. Printed raw it puts `[{'email': ..., 'domain': ...}]`
    in the middle of the page — technically the data, practically unreadable."""
    section = _text(_section(client.get("/meetings/crm-1002-cal-a2").text, "Merged record"))

    for email in (
        "sarah.chen@firma.com",
        "mark.johnson@summitadv.com",
        "anna.lee@summitadv.com",
    ):
        assert email in section

    assert "(organizer)" in section
    assert "'is_organizer'" not in section, "the dict repr leaked into the page"
    assert "{" not in section


def test_an_empty_field_renders_a_dash(client: TestClient) -> None:
    section = _text(_section(client.get("/meetings/cal-a11").text, "Merged record"))

    assert "None" not in section
    assert "—" in section


# --- match evidence ---


def test_the_evidence_shows_the_full_breakdown(client: TestClient) -> None:
    """Doc 02 rejected a black-box matcher because a reviewer checks pairs by hand. This is
    where that promise is kept."""
    section = _text(_section(client.get("/meetings/crm-1002-cal-a2").text, "Why these records were matched"))

    for name in ("participants", "time", "title", "structure"):
        assert name in section
    assert "0.860" in section
    assert "high confidence" in section


def test_the_evidence_contributions_sum_to_the_total(client: TestClient) -> None:
    """The arithmetic is on the page precisely so it can be checked — so the test checks it."""
    section = _section(client.get("/meetings/crm-1001-cal-a1").text, "Why these records were matched")
    contributions = [float(n) for n in re.findall(r"(\d\.\d{3})", section)]

    assert len(contributions) == 5, "four signals and a total"
    # The displayed figures are rounded to three places, so the visible sum can differ from
    # the visible total by a hair. Anything larger means the page is showing arithmetic that
    # does not hold.
    assert abs(sum(contributions[:4]) - contributions[4]) < 0.002


def test_the_evidence_explains_each_signal(client: TestClient) -> None:
    section = _text(_section(client.get("/meetings/crm-1002-cal-a2").text, "Why these records were matched"))

    assert "owner organised it" in section
    assert "same start time" in section
    assert "In-Person vs virtual location" in section


def test_a_single_source_meeting_has_no_evidence_section(client: TestClient) -> None:
    """There was no pairing decision to explain."""
    html = client.get("/meetings/crm-1010").text

    assert "Why these records were matched" not in html


# --- raw records, side by side ---


def test_both_raw_records_are_shown(client: TestClient) -> None:
    section = _text(_section(client.get("/meetings/crm-1001-cal-a1").text, "What each source said"))

    assert "CRM-1001" in section
    assert "CAL-A1" in section
    assert "relationship_owner" in section, "the CRM's own field names"
    assert "organizer" in section, "and the calendar's"


def test_the_conflicting_raw_keys_are_highlighted(client: TestClient) -> None:
    """The two sources spell the same fact differently, so the map from merged field to raw
    keys is what makes this possible."""
    section = _section(client.get("/meetings/crm-1002-cal-a2").text, "What each source said")

    assert "conflict-value" in section


def test_a_time_conflict_highlights_both_crm_date_keys(client: TestClient) -> None:
    """CRM-1016's 13:00 against CAL-A17's 15:00 — the CRM spells it as two fields."""
    section = _section(client.get("/meetings/crm-1016-cal-a17").text, "What each source said")
    highlighted = re.findall(r'<th scope="row">(\w+)</th>\s*<td class="conflict-value"', section)

    assert "meeting_time" in highlighted
    assert "start_time" in highlighted


def test_the_collapsed_duplicate_shows_both_calendar_records(client: TestClient) -> None:
    """CAL-A5 and CAL-A6 — the whole reason dedupe keeps the loser's raw record."""
    section = _text(_section(client.get("/meetings/crm-1005-cal-a5-cal-a6").text, "What each source said"))

    assert "CAL-A5" in section
    assert "CAL-A6" in section
    assert "sandra.mills@pinnaclegp.com" in section, "the attendee only A6 had"


def test_a_missing_side_explains_itself(client: TestClient) -> None:
    """A blank column reads as a bug; a sentence reads as a finding."""
    crm_only = _text(_section(client.get("/meetings/crm-1010").text, "What each source said"))
    calendar_only = _text(_section(client.get("/meetings/cal-a19").text, "What each source said"))

    assert "never booked" in crm_only
    assert "never logged in the CRM" in calendar_only


# --- data quality ---


def test_flags_show_severity_field_and_raw_value(client: TestClient) -> None:
    """A code alone is not actionable — "03-15/2025" is."""
    section = _text(_section(client.get("/meetings/crm-1008-cal-a9").text, "Data quality"))

    assert "MALFORMED_DATE" in section
    assert "meeting_date" in section
    assert "03-15/2025" in section
    assert "error" in section


def test_the_collapsed_duplicate_is_disclosed(client: TestClient) -> None:
    section = _text(_section(client.get("/meetings/crm-1005-cal-a5-cal-a6").text, "Data quality"))

    assert "DUPLICATE_COLLAPSED" in section


def test_the_timezone_assumption_is_disclosed(client: TestClient) -> None:
    """40 of these across the dataset — the service owning up to its one inference."""
    section = _text(_section(client.get("/meetings/crm-1001-cal-a1").text, "Data quality"))

    assert "TIMEZONE_ASSUMED" in section


# --- 404 ---


def test_an_unknown_id_returns_an_html_page(client: TestClient) -> None:
    """Someone following a stale link deserves a page with a way back, not a JSON error."""
    response = client.get("/meetings/does-not-exist")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "No such meeting" in response.text
    assert "does-not-exist" in response.text
    assert "Back to all meetings" in response.text


def test_the_json_api_still_answers_404_as_json(client: TestClient) -> None:
    """The HTML page is for humans; the API contract is unchanged."""
    response = client.get("/api/meetings/does-not-exist")

    assert response.status_code == 404
    assert response.json()["detail"]


# --- safety ---


def test_raw_values_are_escaped(client: TestClient) -> None:
    html = client.get("/meetings/crm-1002-cal-a2").text

    assert "<script" not in html.lower().split("<body")[1]
