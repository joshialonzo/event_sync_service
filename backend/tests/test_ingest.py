"""Tests for the source adapters (step 4).

Two things are being verified: that both files load, and — more importantly — that the
loaders leave the data alone. Every anomaly asserted here is one the pipeline must be able
to *report* later, which is only possible if it survives ingest untouched.
"""

from pathlib import Path

import pytest

from app.ingest import read_json_array
from app.ingest.calendar import load_calendar
from app.ingest.crm import load_crm


def _by_id(records: list[dict], key: str, value: str) -> dict:
    return next(record for record in records if record[key] == value)


def test_crm_loads_twenty_records() -> None:
    records = load_crm()

    assert len(records) == 20
    assert all(isinstance(record, dict) for record in records)


def test_calendar_loads_twentytwo_records() -> None:
    records = load_calendar()

    assert len(records) == 22
    assert all(isinstance(record, dict) for record in records)


def test_source_ids_are_unique_within_each_file() -> None:
    """The planted duplicate (CAL-A5/A6) is a duplicate *meeting*, not a duplicate id —
    if this ever fails, dedupe in step 10 is solving the wrong problem."""
    crm_ids = [record["crm_id"] for record in load_crm()]
    calendar_ids = [record["event_id"] for record in load_calendar()]

    assert len(set(crm_ids)) == 20
    assert len(set(calendar_ids)) == 22


def test_crm_records_keep_their_original_field_names() -> None:
    record = _by_id(load_crm(), "crm_id", "CRM-1001")

    assert record["subject"] == "Q1 Portfolio Review"
    assert record["client_name"] == "David Park"
    assert record["relationship_owner"] == "Sarah Chen"
    assert record["meeting_date"] == "2025-03-10"
    assert record["meeting_time"] == "14:00"


def test_calendar_records_keep_their_original_field_names() -> None:
    record = _by_id(load_calendar(), "event_id", "CAL-A1")

    assert record["title"] == "Q1 Portfolio Review - Meridian Capital"
    assert record["organizer"] == "sarah.chen@firma.com"
    assert record["start_time"] == "2025-03-10T14:00:00"
    assert record["is_recurring"] is False


# --- the anomalies must survive ingest verbatim (doc 01, sections A and B) ---


def test_malformed_date_is_not_repaired() -> None:
    """CRM-1008's mixed separators. Step 8 flags it MALFORMED_DATE; it can only do that
    if the raw string arrives intact."""
    assert _by_id(load_crm(), "crm_id", "CRM-1008")["meeting_date"] == "03-15/2025"


def test_obfuscated_email_is_not_repaired() -> None:
    """CAL-A16's `[at]`. Repairing it here would make MALFORMED_EMAIL unreportable."""
    record = _by_id(load_calendar(), "event_id", "CAL-A16")

    assert "raj.patel[at]atlasvc.com" in record["attendees"]


def test_non_email_attendee_is_not_dropped() -> None:
    """CAL-A20's "external-guests" is real information — that outsiders attended."""
    record = _by_id(load_calendar(), "event_id", "CAL-A20")

    assert "external-guests" in record["attendees"]


def test_truncated_timestamp_is_not_normalized() -> None:
    """CAL-A11 is missing the seconds component every other record has."""
    assert _by_id(load_calendar(), "event_id", "CAL-A11")["end_time"] == "2025-03-14T20:00"


def test_utc_suffix_is_preserved() -> None:
    """CAL-A4 is the only Z-suffixed timestamp in either file, and the whole timezone
    inference in doc 01 section D depends on that distinction being visible."""
    assert _by_id(load_calendar(), "event_id", "CAL-A4")["start_time"] == "2025-03-13T19:00:00Z"


def test_missing_meeting_time_stays_null() -> None:
    """CRM-1007. Not coerced to "" or midnight — step 8 needs the difference between
    absent and blank to raise TIME_MISSING, and step 11 scores date-only events apart."""
    assert _by_id(load_crm(), "crm_id", "CRM-1007")["meeting_time"] is None


def test_all_internal_meetings_keep_their_null_client() -> None:
    """All four internal CRM records. Doc 02 treats this as valid rather than corrupt, so
    the whole set is pinned — if one ever arrives as "" the INTERNAL_NO_CLIENT flag would
    silently stop firing for it."""
    crm = load_crm()
    internal = ["CRM-1006", "CRM-1009", "CRM-1013", "CRM-1019"]

    for crm_id in internal:
        record = _by_id(crm, "crm_id", crm_id)
        assert record["client_name"] is None, crm_id
        assert record["client_company"] is None, crm_id
        assert record["meeting_type"] == "Internal", crm_id

    assert [r["crm_id"] for r in crm if r["client_name"] is None] == internal


def test_null_crm_locations_are_preserved() -> None:
    """CRM-1018's null location against CAL-A21's "Zoom" is the *absence* case in doc 02,
    Decision 4 — distinct from a contradiction, and only if the null survives."""
    crm = load_crm()

    assert [r["crm_id"] for r in crm if r["location"] is None] == [
        "CRM-1003",
        "CRM-1007",
        "CRM-1014",
        "CRM-1018",
    ]


def test_null_calendar_locations_are_preserved() -> None:
    calendar = load_calendar()

    assert [r["event_id"] for r in calendar if r["location"] is None] == ["CAL-A11", "CAL-A15"]


def test_calendar_thin_record_keeps_every_gap() -> None:
    """CAL-A11 is the thinnest record in either file: no attendees, no location, no
    description. It still has to reach the output as a calendar-only meeting."""
    record = _by_id(load_calendar(), "event_id", "CAL-A11")

    assert record["attendees"] == []
    assert record["location"] is None
    assert record["description"] is None


def test_recurrence_flags_are_preserved() -> None:
    """CAL-A3 and CAL-A18 are one series a week apart, and the is_recurring flag is the
    carve-out that stops dedupe (step 10) from deleting one of them."""
    calendar = load_calendar()

    assert [r["event_id"] for r in calendar if r["is_recurring"]] == [
        "CAL-A3",
        "CAL-A7",
        "CAL-A18",
    ]


def test_the_duplicate_pair_arrives_as_two_records() -> None:
    """CAL-A5/CAL-A6 are the planted intra-source duplicate. Ingest must deliver both —
    collapsing here would hide the case step 10 exists to solve."""
    calendar = load_calendar()

    a5 = _by_id(calendar, "event_id", "CAL-A5")
    a6 = _by_id(calendar, "event_id", "CAL-A6")

    assert a5["start_time"] == "2025-03-17T11:00:00"
    assert a6["start_time"] == "2025-03-17T11:30:00"
    assert a5["organizer"] == a6["organizer"]
    # A6 adds an attendee A5 lacks — the union is what step 10 must preserve.
    assert set(a5["attendees"]) < set(a6["attendees"])


def test_each_source_keeps_its_own_status_vocabulary() -> None:
    """CRM is title case with five values, calendar is lower case with two. Step 7 maps
    both onto one enum; that mapping is only testable if ingest leaves them divergent."""
    assert {r["status"] for r in load_crm()} == {
        "Scheduled",
        "Confirmed",
        "Tentative",
        "Completed",
        "Cancelled",
    }
    assert {r["status"] for r in load_calendar()} == {"confirmed", "tentative"}


def test_empty_string_is_distinct_from_null() -> None:
    """CRM-1010's notes are "" rather than null — the only empty string in either file,
    and absent from doc 01's inventory. Kept distinct because "the user typed nothing"
    and "the field was never populated" are different facts, and collapsing them at
    ingest would decide that question for every downstream consumer."""
    assert _by_id(load_crm(), "crm_id", "CRM-1010")["notes"] == ""


def test_the_complete_gap_inventory_is_pinned() -> None:
    """A completeness guard rather than a case test.

    The tests above assert known anomalies one at a time, which can only ever prove that
    the ones somebody thought of survive. This pins the *entire* set of null/empty fields
    across both files, so a source record that gains a new kind of gap fails here instead
    of silently reaching the matcher.
    """
    gaps: dict[str, dict[str, list[str]]] = {}
    for source, key, records in (
        ("crm", "crm_id", load_crm()),
        ("calendar", "event_id", load_calendar()),
    ):
        by_field: dict[str, list[str]] = {}
        for record in records:
            for field, value in record.items():
                if value is None or value == [] or value == "":
                    by_field.setdefault(field, []).append(record[key])
        gaps[source] = by_field

    assert gaps == {
        "crm": {
            "location": ["CRM-1003", "CRM-1007", "CRM-1014", "CRM-1018"],
            "client_name": ["CRM-1006", "CRM-1009", "CRM-1013", "CRM-1019"],
            "client_company": ["CRM-1006", "CRM-1009", "CRM-1013", "CRM-1019"],
            "meeting_time": ["CRM-1007"],
            "notes": ["CRM-1010"],
        },
        "calendar": {
            "attendees": ["CAL-A11"],
            "location": ["CAL-A11", "CAL-A15"],
            "description": ["CAL-A11"],
        },
    }


def test_no_record_is_missing_a_field() -> None:
    """Every record carries every key — the gaps are null values, never absent keys. Step
    5's model can therefore rely on the shape and flag on the value."""
    crm = load_crm()
    calendar = load_calendar()

    assert {frozenset(record) for record in crm} == {
        frozenset(
            [
                "crm_id",
                "subject",
                "client_name",
                "client_company",
                "relationship_owner",
                "meeting_date",
                "meeting_time",
                "meeting_type",
                "location",
                "notes",
                "status",
                "created_at",
            ]
        )
    }
    assert {frozenset(record) for record in calendar} == {
        frozenset(
            [
                "event_id",
                "title",
                "organizer",
                "attendees",
                "start_time",
                "end_time",
                "location",
                "description",
                "is_recurring",
                "status",
                "created_at",
            ]
        )
    }


# --- failure modes ---


def test_missing_file_raises(tmp_path: Path) -> None:
    """A misconfigured DATA_DIR must fail loudly. Returning [] here would produce a
    service that starts cleanly and reconciles zero meetings."""
    with pytest.raises(FileNotFoundError):
        load_crm(data_dir=tmp_path)

    with pytest.raises(FileNotFoundError):
        load_calendar(data_dir=tmp_path)


def test_non_array_payload_raises(tmp_path: Path) -> None:
    (tmp_path / "crm_events.json").write_text('{"crm_id": "CRM-1001"}', encoding="utf-8")

    with pytest.raises(ValueError, match="does not contain a JSON array"):
        load_crm(data_dir=tmp_path)


def test_explicit_data_dir_overrides_settings(tmp_path: Path) -> None:
    """Steps 15+ pass a directory in; the default is only a convenience."""
    (tmp_path / "crm_events.json").write_text('[{"crm_id": "CRM-9999"}]', encoding="utf-8")

    assert load_crm(data_dir=tmp_path) == [{"crm_id": "CRM-9999"}]


def test_read_json_array_rejects_malformed_json(tmp_path: Path) -> None:
    import json

    path = tmp_path / "broken.json"
    path.write_text("[{", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        read_json_array(path)
