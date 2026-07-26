"""Tests for the meeting filters (step 18).

Counts are asserted against the reconciliation fixture rather than against whatever the
filter happens to return, so a filter that quietly stops working fails here.
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.jobs.sync import run_sync
from app.models.filters import MeetingFilters, apply_filters
from app.models.unified import Origin


@pytest.fixture(scope="module")
def meetings():
    return run_sync().ordered_meetings


def _ids(response) -> list[str]:
    return [meeting["id"] for meeting in response.json()]


# --- no filter ---


def test_no_parameters_returns_everything(client: TestClient) -> None:
    assert len(client.get("/api/meetings").json()) == 24


def test_empty_filters_are_a_no_op(meetings) -> None:
    assert apply_filters(meetings, MeetingFilters()) == meetings
    assert MeetingFilters().is_empty is True


# --- origin ---


@pytest.mark.parametrize(
    ("origin", "expected"), [("both", 17), ("crm_only", 3), ("calendar_only", 4)]
)
def test_origin_filter(client: TestClient, origin: str, expected: int) -> None:
    response = client.get("/api/meetings", params={"origin": origin})

    assert len(response.json()) == expected
    assert all(m["origin"] == origin for m in response.json())


def test_crm_only_returns_the_documented_records(client: TestClient) -> None:
    """The never-booked meetings — doc 02 Decision 5 calls these the most valuable output."""
    assert set(_ids(client.get("/api/meetings", params={"origin": "crm_only"}))) == {
        "crm-1003",
        "crm-1010",
        "crm-1020",
    }


def test_calendar_only_returns_the_documented_records(client: TestClient) -> None:
    assert set(_ids(client.get("/api/meetings", params={"origin": "calendar_only"}))) == {
        "cal-a3",
        "cal-a11",
        "cal-a18",
        "cal-a19",
    }


def test_an_unknown_origin_is_rejected(client: TestClient) -> None:
    """422 rather than an empty list: a typo in a filter should not look like "no results"."""
    assert client.get("/api/meetings", params={"origin": "nonsense"}).status_code == 422


# --- conflicts ---


def test_conflicts_filter_returns_the_four(client: TestClient) -> None:
    assert set(_ids(client.get("/api/meetings", params={"has_conflicts": "true"}))) == {
        "crm-1002-cal-a2",
        "crm-1004-cal-a4",
        "crm-1009-cal-a10",
        "crm-1016-cal-a17",
    }


def test_conflicts_false_returns_the_complement(client: TestClient) -> None:
    """`false` must mean "the other 20", not "no filter" — the classic tri-state bug."""
    response = client.get("/api/meetings", params={"has_conflicts": "false"})

    assert len(response.json()) == 20


# --- dates ---


def test_date_from_is_inclusive(client: TestClient) -> None:
    """The earliest meeting is on 2025-03-10 and must survive a bound equal to its date."""
    response = client.get("/api/meetings", params={"date_from": "2025-03-10"})

    assert len(response.json()) == 24


def test_date_to_is_inclusive(client: TestClient) -> None:
    response = client.get("/api/meetings", params={"date_to": "2025-04-02"})

    assert len(response.json()) == 24


def test_a_date_window_narrows_the_list(client: TestClient) -> None:
    response = client.get(
        "/api/meetings", params={"date_from": "2025-03-17", "date_to": "2025-03-19"}
    )
    dates = [m["event_date"] for m in response.json()]

    assert dates
    assert all("2025-03-17" <= d <= "2025-03-19" for d in dates)


def test_a_single_day_window_works(client: TestClient) -> None:
    response = client.get(
        "/api/meetings", params={"date_from": "2025-03-10", "date_to": "2025-03-10"}
    )

    assert all(m["event_date"] == "2025-03-10" for m in response.json())
    assert len(response.json()) >= 1


def test_a_window_with_no_meetings_returns_an_empty_list(client: TestClient) -> None:
    response = client.get("/api/meetings", params={"date_from": "2030-01-01"})

    assert response.status_code == 200
    assert response.json() == []


def test_a_malformed_date_is_rejected(client: TestClient) -> None:
    assert client.get("/api/meetings", params={"date_from": "March"}).status_code == 422


# --- owner: the filter the data makes interesting ---


def test_owner_matches_names_and_emails_alike(client: TestClient) -> None:
    """11 meetings carry "Sarah Chen" from the CRM; 3 calendar-only ones carry
    "sarah.chen@firma.com" because there is no CRM record to take a name from. A plain
    substring test would return 11 and silently hide exactly the meetings worth surfacing."""
    response = client.get("/api/meetings", params={"owner": "sarah chen"})

    assert len(response.json()) == 14


def test_owner_match_is_case_and_punctuation_insensitive(client: TestClient) -> None:
    variants = ["sarah chen", "Sarah Chen", "SARAH.CHEN", "sarahchen"]
    counts = {
        len(client.get("/api/meetings", params={"owner": v}).json()) for v in variants
    }

    assert counts == {14}


def test_a_partial_owner_matches(client: TestClient) -> None:
    response = client.get("/api/meetings", params={"owner": "sarah"})

    assert len(response.json()) == 14


def test_the_owners_between_them_cover_every_meeting(client: TestClient) -> None:
    """Discrimination check: each owner returns a distinct subset, and the three together
    account for all 24."""
    sarah = set(_ids(client.get("/api/meetings", params={"owner": "sarah"})))
    james = set(_ids(client.get("/api/meetings", params={"owner": "james wu"})))
    priya = set(_ids(client.get("/api/meetings", params={"owner": "priya"})))

    assert (len(sarah), len(james)) == (14, 9)
    assert not sarah & james, "the two relationship owners never share a meeting"
    assert len(sarah | james | priya) == 24


def test_the_owner_filter_also_searches_the_alternative_source(client: TestClient) -> None:
    """The subtlety the data forced into the open.

    `crm-1013-cal-a14` has Sarah Chen as the CRM relationship owner, but Priya Sharma
    created the calendar entry — she is the *alternative* on the merged field. Searching
    either name finds it, which is why owner results overlap rather than partitioning.

    That is the documented behaviour ("relationship owner or organizer"): a filter that
    ignored the alternative would answer "no" to "what did Priya convene?" for a meeting she
    demonstrably convened.
    """
    priya = set(_ids(client.get("/api/meetings", params={"owner": "priya"})))
    sarah = set(_ids(client.get("/api/meetings", params={"owner": "sarah"})))

    assert priya == {"cal-a11", "crm-1013-cal-a14"}
    assert "crm-1013-cal-a14" in sarah, "it is Sarah's relationship, and Priya's invite"


def test_an_unknown_owner_returns_nothing(client: TestClient) -> None:
    response = client.get("/api/meetings", params={"owner": "nobody"})

    assert response.status_code == 200
    assert response.json() == []


def test_an_email_query_finds_the_name_form(client: TestClient) -> None:
    """Someone pasting an address out of the calendar should find the CRM meetings too."""
    response = client.get("/api/meetings", params={"owner": "sarah.chen@firma.com"})

    assert len(response.json()) == 14


# --- combinations ---


def test_filters_and_together(client: TestClient) -> None:
    response = client.get(
        "/api/meetings", params={"owner": "sarah", "origin": "calendar_only"}
    )

    assert len(response.json()) == 3
    assert all(m["origin"] == "calendar_only" for m in response.json())


def test_an_impossible_combination_is_empty_not_an_error(client: TestClient) -> None:
    response = client.get(
        "/api/meetings", params={"origin": "crm_only", "has_conflicts": "true"}
    )

    assert response.status_code == 200
    assert response.json() == []


def test_filtering_preserves_date_order(client: TestClient) -> None:
    dates = [m["event_date"] for m in client.get(
        "/api/meetings", params={"owner": "sarah"}
    ).json()]

    assert dates == sorted(dates)


def test_filtering_does_not_mutate_the_store(client: TestClient) -> None:
    client.get("/api/meetings", params={"origin": "crm_only"})

    assert len(client.get("/api/meetings").json()) == 24


# --- the pure function, directly ---


def test_apply_filters_is_a_pure_function(meetings) -> None:
    before = list(meetings)

    apply_filters(meetings, MeetingFilters(origin=Origin.BOTH))

    assert meetings == before


def test_apply_filters_handles_a_meeting_without_a_date(meetings) -> None:
    """Nothing in this dataset lacks one, so only a direct call can cover the branch."""
    filters = MeetingFilters(date_from=date(2025, 1, 1))
    dateless = meetings[0].model_copy(update={"event_date": None})

    assert apply_filters([dateless], filters) == []


# --- documentation ---


def test_every_filter_is_documented(client: TestClient) -> None:
    """Step 22's HTML form mirrors these one for one."""
    spec = client.get("/openapi.json").json()
    params = {p["name"] for p in spec["paths"]["/api/meetings"]["get"]["parameters"]}

    assert params == {"origin", "has_conflicts", "date_from", "date_to", "owner"}
