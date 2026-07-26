"""Tests for the CRM normalizer (step 8).

These run against the real 20 records rather than fixtures: the point of the step is that
this specific dataset survives normalization intact, and a hand-built record would only
prove that the code handles the cases its author remembered.
"""

from collections import Counter
from datetime import date

import pytest

from app.ingest.crm import load_crm
from app.models.normalized import FlagCode, MeetingStatus, Severity, Source
from app.reconcile.normalize_crm import normalize_crm_record, normalize_crm_records


@pytest.fixture(scope="module")
def events():
    return normalize_crm_records(load_crm())


@pytest.fixture(scope="module")
def by_id(events):
    return {event.primary_id: event for event in events}


# --- nothing is dropped (doc 02, Decision 1) ---


def test_every_record_becomes_an_event(events) -> None:
    assert len(events) == 20
    assert all(event.source is Source.CRM for event in events)


def test_input_order_is_preserved(events) -> None:
    """Sorting happens once, in the sync job. Reordering here would make every later
    assertion positional and fragile."""
    assert [event.primary_id for event in events] == [record["crm_id"] for record in load_crm()]


def test_raw_record_is_carried_untouched(by_id) -> None:
    """The UI's "what did the CRM actually say?" panel reads this."""
    raw = by_id["CRM-1008"].raw

    assert raw["meeting_date"] == "03-15/2025"
    assert raw["crm_id"] == "CRM-1008"


def test_normalizing_a_hostile_record_does_not_raise() -> None:
    """The promise the whole pipeline rests on: a record this broken still produces an
    event, because a dropped record cannot be reported."""
    event = normalize_crm_record({"crm_id": "CRM-9999"})

    assert event.primary_id == "CRM-9999"
    assert event.start is None
    assert event.status is MeetingStatus.UNKNOWN


def test_a_record_without_an_id_still_normalizes() -> None:
    event = normalize_crm_record({})

    assert event.source_ids == ["CRM-UNKNOWN"]


def test_the_batch_normalizer_never_filters() -> None:
    """`len(events) == 20` cannot catch a filter, because every real record happens to have
    the fields one might filter on. This feeds in records that would not survive a filter
    and asserts the count anyway."""
    records = [
        {"crm_id": "CRM-A", "meeting_date": "2025-03-10", "meeting_time": "14:00"},
        {"crm_id": "CRM-B"},  # no date, no time, no status
        {},  # no id at all
    ]

    assert len(normalize_crm_records(records)) == 3


def test_a_null_client_outside_an_internal_meeting_is_not_the_internal_flag() -> None:
    """Every null client in the real file happens to be internal, so the real data cannot
    distinguish "flag internal meetings" from "flag every null client". A synthetic record
    can: an external meeting missing its client is a different fact, and mislabelling it
    `info` would hide a genuine gap behind an "expected" badge."""
    event = normalize_crm_record(
        {"crm_id": "CRM-X", "meeting_type": "Virtual", "client_name": None}
    )

    assert FlagCode.INTERNAL_NO_CLIENT not in event.flag_codes


# --- field mapping ---


def test_clean_record_maps_every_field(by_id) -> None:
    event = by_id["CRM-1001"]

    assert event.title == "Q1 Portfolio Review"
    assert event.client_name == "David Park"
    assert event.client_company == "Meridian Capital"
    assert event.owner_name == "Sarah Chen"
    assert event.organizer == "Sarah Chen"
    assert event.location == "HQ - Conference Room B"
    assert event.meeting_type == "In-Person"
    assert event.event_date == date(2025, 3, 10)
    assert event.start.hour == 14
    assert event.status is MeetingStatus.COMPLETED
    assert event.status_raw == "Completed"


def test_crm_has_no_end_time(by_id) -> None:
    """The source simply has no such field — inferring a duration would be invention."""
    assert by_id["CRM-1001"].end is None


def test_created_at_is_converted_from_utc(by_id) -> None:
    """created_at is Z-suffixed in all 42 records; February converts at EST."""
    created = by_id["CRM-1001"].created_at

    assert created.hour == 4
    assert created.utcoffset().total_seconds() == -5 * 3600


def test_empty_notes_become_none(by_id) -> None:
    """CRM-1010's notes are "" — the only empty string in either file. It means the same as
    null and should not reach the UI as an empty box."""
    assert by_id["CRM-1010"].text is None


# --- the anomalies (doc 01) ---


def test_malformed_date_parses_and_is_flagged(by_id) -> None:
    """CRM-1008: the date is recovered *and* the defect is reported. Doing only the first
    would hide it; only the second would drop a real meeting."""
    event = by_id["CRM-1008"]

    assert event.event_date == date(2025, 3, 15)
    assert FlagCode.MALFORMED_DATE in event.flag_codes

    flag = next(f for f in event.flags if f.code is FlagCode.MALFORMED_DATE)
    assert flag.raw_value == "03-15/2025"
    assert flag.severity is Severity.ERROR


def test_missing_time_keeps_the_date(by_id) -> None:
    """CRM-1007 still participates in matching, on the date signal alone (step 11)."""
    event = by_id["CRM-1007"]

    assert event.event_date == date(2025, 3, 18)
    assert event.start is None
    assert event.has_time is False
    assert FlagCode.TIME_MISSING in event.flag_codes


def test_date_only_event_is_not_flagged_as_a_timezone_assumption(by_id) -> None:
    """There is no timestamp to assume anything about."""
    assert FlagCode.TIMEZONE_ASSUMED not in by_id["CRM-1007"].flag_codes


def test_internal_meetings_are_flagged_at_info(by_id) -> None:
    """Doc 02: a null client on an internal meeting is valid, not corrupt. At `error` it
    would put four false problems on the stats page."""
    for crm_id in ("CRM-1006", "CRM-1009", "CRM-1013", "CRM-1019"):
        event = by_id[crm_id]
        flag = next(f for f in event.flags if f.code is FlagCode.INTERNAL_NO_CLIENT)

        assert event.client_name is None, crm_id
        assert event.meeting_type == "Internal", crm_id
        assert flag.severity is Severity.INFO, crm_id


def test_placeholder_client_is_flagged(by_id) -> None:
    """CRM-1017's client is literally "Multiple". Unmarked, step 11 would derive a
    `multiple` email local-part and score it as though it were a person."""
    event = by_id["CRM-1017"]

    assert event.client_name == "Multiple"
    assert FlagCode.PLACEHOLDER_CLIENT in event.flag_codes
    assert next(f for f in event.flags if f.code is FlagCode.PLACEHOLDER_CLIENT).severity is Severity.INFO


def test_a_real_client_is_not_flagged_as_a_placeholder(by_id) -> None:
    assert FlagCode.PLACEHOLDER_CLIENT not in by_id["CRM-1001"].flag_codes


# --- timezone ---


def test_naive_crm_times_are_flagged_as_assumed(by_id) -> None:
    """The CRM carries no timezone at all, so every timestamp built here is an assumption —
    flagged identically to the naive calendar records, or the stats page undercounts it."""
    event = by_id["CRM-1001"]

    assert event.start.tzinfo is not None
    assert FlagCode.TIMEZONE_ASSUMED in event.flag_codes


def test_every_timed_record_is_eastern(events) -> None:
    offsets = {event.start.utcoffset().total_seconds() for event in events if event.start}

    assert offsets == {-4 * 3600}, "all events fall after the 2025-03-09 DST change"


# --- participants ---


def test_owner_becomes_the_organizer_participant(by_id) -> None:
    event = by_id["CRM-1001"]
    organizer = next(p for p in event.participants if p.is_organizer)

    assert organizer.display == "Sarah Chen"
    assert organizer.email is None, "the CRM has no addresses; inventing one would fabricate evidence"


def test_client_becomes_a_participant(by_id) -> None:
    displays = [p.display for p in by_id["CRM-1001"].participants]

    assert displays == ["Sarah Chen", "David Park"]


def test_internal_meeting_has_only_its_organizer(by_id) -> None:
    participants = by_id["CRM-1006"].participants

    assert [p.display for p in participants] == ["Sarah Chen"]
    assert participants[0].is_organizer is True


# --- the whole-file flag census ---


def test_flag_counts_across_the_source(events) -> None:
    """Pins the entire flag output rather than sampling it, so a normalizer change that
    quietly starts over- or under-reporting fails here."""
    counts = Counter(flag.code for event in events for flag in event.flags)

    assert counts == {
        FlagCode.TIMEZONE_ASSUMED: 19,
        FlagCode.INTERNAL_NO_CLIENT: 4,
        FlagCode.MALFORMED_DATE: 1,
        FlagCode.TIME_MISSING: 1,
        FlagCode.PLACEHOLDER_CLIENT: 1,
    }


def test_no_record_has_an_unknown_status(events) -> None:
    """Step 4 pinned the five CRM status values; this proves the mapping covers them."""
    assert all(event.status is not MeetingStatus.UNKNOWN for event in events)
    assert {event.status for event in events} == {
        MeetingStatus.SCHEDULED,
        MeetingStatus.CONFIRMED,
        MeetingStatus.TENTATIVE,
        MeetingStatus.COMPLETED,
        MeetingStatus.CANCELLED,
    }


def test_every_event_keeps_a_usable_date(events) -> None:
    """20 of 20 — nothing in this file is so broken that it loses its day."""
    assert all(event.event_date is not None for event in events)
