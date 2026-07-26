"""Tests for the meeting endpoints (step 17).

The route layer holds no logic, so these check the two things it *is* responsible for: what
gets serialized, and which status code comes back. The provenance shape is asserted against
doc 02's documented JSON, because that block is the frontend's contract.
"""

from fastapi.testclient import TestClient


# --- list ---


def test_list_returns_every_meeting(client: TestClient) -> None:
    response = client.get("/api/meetings")

    assert response.status_code == 200
    assert len(response.json()) == 24


def test_list_is_in_date_order(client: TestClient) -> None:
    """The order is the store's, decided once in the sync job — sorting in the route would
    give the API and the HTML list two chances to disagree."""
    dates = [meeting["event_date"] for meeting in client.get("/api/meetings").json()]

    assert dates == sorted(dates)
    assert dates[0] == "2025-03-10"


def test_list_omits_the_raw_source_records(client: TestClient) -> None:
    """~90 KB of duplicated source JSON that only the detail view reads."""
    first = client.get("/api/meetings").json()[0]

    assert "raw_crm" not in first
    assert "raw_calendar" not in first


def test_list_keeps_everything_the_list_view_needs(client: TestClient) -> None:
    first = client.get("/api/meetings").json()[0]

    assert {"id", "origin", "event_date", "title", "status", "flags"} <= set(first)
    assert first["title"]["value"]
    assert first["origin"] in {"both", "crm_only", "calendar_only"}


def test_list_carries_conflict_information(client: TestClient) -> None:
    """The badge on the list page reads this, so it cannot be detail-only."""
    meetings = client.get("/api/meetings").json()
    conflicted = [m for m in meetings if any(
        isinstance(v, dict) and v.get("conflict") for v in m.values()
    )]

    assert len(conflicted) == 4


def test_origins_are_all_represented(client: TestClient) -> None:
    origins = [m["origin"] for m in client.get("/api/meetings").json()]

    assert origins.count("both") == 17
    assert origins.count("crm_only") == 3
    assert origins.count("calendar_only") == 4


# --- detail ---


def test_detail_returns_one_meeting(client: TestClient) -> None:
    response = client.get("/api/meetings/crm-1001-cal-a1")

    assert response.status_code == 200
    assert response.json()["id"] == "crm-1001-cal-a1"


def test_detail_includes_both_raw_sides(client: TestClient) -> None:
    """"What did the CRM actually say?" — the whole provenance feature."""
    payload = client.get("/api/meetings/crm-1001-cal-a1").json()

    assert [r["crm_id"] for r in payload["raw_crm"]] == ["CRM-1001"]
    assert [r["event_id"] for r in payload["raw_calendar"]] == ["CAL-A1"]


def test_detail_of_the_collapsed_duplicate_shows_both_calendar_records(
    client: TestClient,
) -> None:
    """CAL-A5 and CAL-A6 both reach the UI through one meeting."""
    payload = client.get("/api/meetings/crm-1005-cal-a5-cal-a6").json()

    assert [r["event_id"] for r in payload["raw_calendar"]] == ["CAL-A5", "CAL-A6"]


def test_detail_matches_the_documented_provenance_shape(client: TestClient) -> None:
    """Doc 02, Decision 4, field for field. This block is the frontend's contract, so a
    rename here should fail loudly rather than quietly break a template."""
    payload = client.get("/api/meetings/crm-1002-cal-a2").json()

    assert payload["location"] == {
        "value": "Zoom - https://zoom.us/j/98765432100",
        "source": "calendar",
        "alternatives": [{"source": "crm", "value": "NYC Office - 30th Floor"}],
        "conflict": True,
        "conflict_kind": "contradiction",
    }


def test_detail_includes_match_evidence(client: TestClient) -> None:
    """"Why do you think these are the same meeting?" is answered per signal."""
    evidence = client.get("/api/meetings/crm-1001-cal-a1").json()["match_evidence"]

    assert [s["name"] for s in evidence["signals"]] == [
        "participants",
        "time",
        "title",
        "structure",
    ]
    assert evidence["confidence"] == "high"
    assert all(s["detail"] for s in evidence["signals"])


def test_single_source_meeting_has_no_evidence(client: TestClient) -> None:
    payload = client.get("/api/meetings/crm-1010").json()

    assert payload["match_evidence"] is None
    assert payload["raw_calendar"] == []
    assert payload["origin"] == "crm_only"


def test_detail_carries_data_quality_flags(client: TestClient) -> None:
    payload = client.get("/api/meetings/crm-1008-cal-a9").json()
    codes = {flag["code"] for flag in payload["flags"]}

    assert "MALFORMED_DATE" in codes
    assert any(flag["severity"] == "error" for flag in payload["flags"])


def test_unknown_id_returns_404_naming_the_id(client: TestClient) -> None:
    """What a stale bookmark looks like after a re-sync — ordinary, not exceptional."""
    response = client.get("/api/meetings/does-not-exist")

    assert response.status_code == 404
    assert "does-not-exist" in response.json()["detail"]


def test_every_listed_meeting_is_retrievable(client: TestClient) -> None:
    """The end-to-end version of the store's consistency guarantee: nothing appears in the
    list that 404s when clicked."""
    for meeting in client.get("/api/meetings").json():
        assert client.get(f"/api/meetings/{meeting['id']}").status_code == 200


# --- documentation ---


def test_both_endpoints_are_documented(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert "/api/meetings" in paths
    assert "/api/meetings/{meeting_id}" in paths


def test_the_list_schema_does_not_advertise_raw_records(client: TestClient) -> None:
    """A schema promising fields the endpoint never sends is worse than no schema."""
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    list_item = schemas["MeetingListItem"]["properties"]

    assert "raw_crm" not in list_item
    assert "raw_calendar" not in list_item
    assert "raw_crm" in schemas["UnifiedMeeting"]["properties"]
