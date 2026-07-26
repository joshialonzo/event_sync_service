"""Tests for the calendar normalizer (step 9).

Against the real 22 records, plus synthetic ones for the two rules the real data cannot
distinguish (organizer de-duplication and the empty-attendee branch) — the gap mutation
testing exposed in step 8.
"""

from collections import Counter
from datetime import date

import pytest

from app.ingest.calendar import load_calendar
from app.models.normalized import FlagCode, MeetingStatus, Source
from app.reconcile.normalize_calendar import (
    normalize_calendar_record,
    normalize_calendar_records,
)


@pytest.fixture(scope="module")
def events():
    return normalize_calendar_records(load_calendar())


@pytest.fixture(scope="module")
def by_id(events):
    return {event.primary_id: event for event in events}


# --- nothing is dropped ---


def test_every_record_becomes_an_event(events) -> None:
    assert len(events) == 22
    assert all(event.source is Source.CALENDAR for event in events)


def test_input_order_is_preserved(events) -> None:
    assert [e.primary_id for e in events] == [r["event_id"] for r in load_calendar()]


def test_the_batch_normalizer_never_filters() -> None:
    """Real records all have attendees and timestamps, so a filter on either would be
    invisible here. Synthetic records make it visible."""
    records = [
        {"event_id": "CAL-X", "start_time": "2025-03-10T14:00:00"},
        {"event_id": "CAL-Y"},
        {},
    ]

    assert len(normalize_calendar_records(records)) == 3


def test_normalizing_a_hostile_record_does_not_raise() -> None:
    event = normalize_calendar_record({"event_id": "CAL-Z", "attendees": None, "status": 42})

    assert event.primary_id == "CAL-Z"
    assert event.status is MeetingStatus.UNKNOWN
    assert event.participants == []


def test_raw_record_is_carried_untouched(by_id) -> None:
    assert "raj.patel[at]atlasvc.com" in by_id["CAL-A16"].raw["attendees"]


# --- field mapping ---


def test_clean_record_maps_every_field(by_id) -> None:
    event = by_id["CAL-A1"]

    assert event.title == "Q1 Portfolio Review - Meridian Capital"
    assert event.organizer == "sarah.chen@firma.com"
    assert event.location == "Conference Room B"
    assert event.event_date == date(2025, 3, 10)
    assert event.start.hour == 14
    assert event.end.hour == 15
    assert event.status is MeetingStatus.CONFIRMED
    assert event.status_raw == "confirmed"
    assert event.is_recurring is False


def test_calendar_supplies_no_crm_shaped_fields(events) -> None:
    """Guessing a client from an attendee domain would put invented values where step 13's
    precedence rules expect a genuine absence."""
    assert all(event.client_name is None for event in events)
    assert all(event.client_company is None for event in events)
    assert all(event.owner_name is None for event in events)
    assert all(event.meeting_type is None for event in events)


def test_recurring_events_are_marked(events) -> None:
    """The carve-out that stops step 10 deleting one instance of a series."""
    recurring = [event.primary_id for event in events if event.is_recurring]

    assert recurring == ["CAL-A3", "CAL-A7", "CAL-A18"]


# --- the timezone case the whole inference rests on (doc 01, section D) ---


def test_utc_record_converts_and_is_not_flagged(by_id) -> None:
    """CAL-A4 states its own offset, so nothing is assumed. 19:00Z is 15:00 EDT — see the
    correction in doc 01 section D."""
    event = by_id["CAL-A4"]

    assert event.start.hour == 15
    assert event.start.utcoffset().total_seconds() == -4 * 3600
    assert event.event_date == date(2025, 3, 13)
    assert FlagCode.TIMEZONE_ASSUMED not in event.flag_codes


def test_naive_records_are_flagged_exactly_once(events) -> None:
    """Most records have two naive timestamps. Flagging both would report 42 assumptions
    across 21 records and overstate the problem by 2x on the stats page."""
    for event in events:
        assumed = [f for f in event.flags if f.code is FlagCode.TIMEZONE_ASSUMED]
        expected = 0 if event.primary_id == "CAL-A4" else 1
        assert len(assumed) == expected, event.primary_id


def test_the_timezone_flag_names_the_start_field(by_id) -> None:
    flag = next(f for f in by_id["CAL-A1"].flags if f.code is FlagCode.TIMEZONE_ASSUMED)

    assert flag.field == "start_time"
    assert flag.raw_value == "2025-03-10T14:00:00"


# --- the malformed records ---


def test_truncated_end_time_parses_with_a_flag(by_id) -> None:
    """CAL-A11's end_time is missing the seconds every other record carries."""
    event = by_id["CAL-A11"]

    assert event.end.hour == 20
    assert FlagCode.MALFORMED_DATETIME in event.flag_codes

    flag = next(f for f in event.flags if f.code is FlagCode.MALFORMED_DATETIME)
    assert flag.field == "end_time"
    assert flag.raw_value == "2025-03-14T20:00"


def test_obfuscated_attendee_is_repaired_and_flagged(by_id) -> None:
    """CAL-A16. The repaired address is what matches; the original is what the
    data-quality panel shows."""
    event = by_id["CAL-A16"]
    raj = next(p for p in event.participants if p.raw == "raj.patel[at]atlasvc.com")

    assert raj.email == "raj.patel@atlasvc.com"
    assert raj.domain == "atlasvc.com"
    assert FlagCode.MALFORMED_EMAIL in event.flag_codes


def test_non_email_attendee_survives_as_a_label(by_id) -> None:
    """CAL-A20. "External guests attended" is real information, so it is kept as an opaque
    participant rather than dropped."""
    event = by_id["CAL-A20"]
    guests = next(p for p in event.participants if p.raw == "external-guests")

    assert guests.email is None
    assert guests.domain is None
    assert guests.display == "external-guests"
    assert FlagCode.NON_EMAIL_ATTENDEE in event.flag_codes


def test_thin_record_keeps_its_gaps(by_id) -> None:
    """CAL-A11 is the thinnest record in either file, and still reaches the output as a
    calendar-only meeting."""
    event = by_id["CAL-A11"]

    assert event.location is None
    assert event.text is None
    assert event.raw["attendees"] == []


# --- participants ---


def test_organizer_is_marked_not_duplicated(by_id) -> None:
    """The organizer is always also an attendee in this file; appending unconditionally
    would double them on 21 of 22 records."""
    event = by_id["CAL-A1"]
    organizers = [p for p in event.participants if p.is_organizer]

    assert len(organizers) == 1
    assert organizers[0].email == "sarah.chen@firma.com"
    assert [p.email for p in event.participants].count("sarah.chen@firma.com") == 1


def test_every_record_has_exactly_one_organizer(events) -> None:
    for event in events:
        assert len([p for p in event.participants if p.is_organizer]) == 1, event.primary_id


def test_organizer_is_added_when_there_are_no_attendees(by_id) -> None:
    """CAL-A11 has an organizer and zero attendees — the append branch."""
    participants = by_id["CAL-A11"].participants

    assert len(participants) == 1
    assert participants[0].is_organizer is True


def test_an_organizer_outside_the_attendee_list_is_appended() -> None:
    """Synthetic: the real file never separates them, so only this can prove the branch."""
    event = normalize_calendar_record(
        {
            "event_id": "CAL-X",
            "organizer": "boss@firma.com",
            "attendees": ["someone@client.com"],
        }
    )

    assert [p.email for p in event.participants] == ["someone@client.com", "boss@firma.com"]
    assert event.participants[1].is_organizer is True


def test_duplicate_attendees_are_collapsed() -> None:
    """Synthetic: a repaired address can collide with a clean one already in the list."""
    event = normalize_calendar_record(
        {
            "event_id": "CAL-X",
            "organizer": "a@firma.com",
            "attendees": ["a@firma.com", "a[at]firma.com", "a@firma.com"],
        }
    )

    assert [p.email for p in event.participants] == ["a@firma.com"]


def test_participant_domains_are_derived(by_id) -> None:
    """The company signal step 11 scores on."""
    domains = {p.domain for p in by_id["CAL-A1"].participants}

    assert domains == {"firma.com", "meridiancap.com"}


# --- the whole-file flag census ---


def test_flag_counts_across_the_source(events) -> None:
    counts = Counter(flag.code for event in events for flag in event.flags)

    assert counts == {
        FlagCode.TIMEZONE_ASSUMED: 21,
        FlagCode.MALFORMED_DATETIME: 1,
        FlagCode.MALFORMED_EMAIL: 1,
        FlagCode.NON_EMAIL_ATTENDEE: 1,
    }


def test_no_record_has_an_unknown_status(events) -> None:
    assert {event.status for event in events} == {
        MeetingStatus.CONFIRMED,
        MeetingStatus.TENTATIVE,
    }


def test_every_event_has_a_start(events) -> None:
    """Unlike the CRM, the calendar always supplies a timestamp."""
    assert all(event.start is not None for event in events)
    assert all(event.has_time for event in events)
