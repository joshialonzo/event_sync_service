"""Tests for the merge (step 13).

Doc 02: *the service reconciles; it does not adjudicate*. Most of this file therefore checks
that the losing value is still there, and that the conflict badge is raised for exactly the
four cases where the sources genuinely contradict each other — no more, or the badge stops
meaning anything.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.ingest.calendar import load_calendar
from app.ingest.crm import load_crm
from app.models.normalized import MeetingStatus, NormalizedEvent, Participant, Source
from app.models.unified import ConflictKind, Origin
from app.reconcile.dedupe import dedupe_events
from app.reconcile.matcher import MatchedPair, match_events, score_pair
from app.reconcile.merge import merge_all, merge_pair, merge_single
from app.reconcile.normalize_calendar import normalize_calendar_records
from app.reconcile.normalize_crm import normalize_crm_records

EASTERN = ZoneInfo("America/New_York")
BASE = datetime(2025, 3, 10, 14, 0, tzinfo=EASTERN)

EXPECTED_CONFLICTS = {
    "crm-1002-cal-a2": ["location"],
    "crm-1004-cal-a4": ["start_time"],
    "crm-1009-cal-a10": ["status"],
    "crm-1016-cal-a17": ["start_time"],
}


@pytest.fixture(scope="module")
def meetings():
    crm = normalize_crm_records(load_crm())
    calendar = dedupe_events(normalize_calendar_records(load_calendar()))
    return merge_all(match_events(crm, calendar))


@pytest.fixture(scope="module")
def by_id(meetings):
    return {meeting.id: meeting for meeting in meetings}


def _pair(crm_over: dict | None = None, cal_over: dict | None = None) -> MatchedPair:
    crm_fields = {
        "source": Source.CRM,
        "source_ids": ["CRM-X"],
        "start": BASE,
        "event_date": BASE.date(),
        "owner_name": "Sarah Chen",
        "client_name": "David Park",
        "title": "Q1 Portfolio Review",
        "location": "HQ - Conference Room B",
        "status": MeetingStatus.CONFIRMED,
        "raw": {},
    }
    crm_fields.update(crm_over or {})

    calendar_fields = {
        "source": Source.CALENDAR,
        "source_ids": ["CAL-X"],
        "start": BASE,
        "event_date": BASE.date(),
        "organizer": "sarah.chen@firma.com",
        "participants": [
            Participant(
                email="sarah.chen@firma.com",
                display="sarah.chen@firma.com",
                raw="sarah.chen@firma.com",
                is_organizer=True,
            )
        ],
        "title": "Q1 Portfolio Review - Meridian",
        "location": "Conference Room B",
        "status": MeetingStatus.CONFIRMED,
        "raw": {},
    }
    calendar_fields.update(cal_over or {})

    crm = NormalizedEvent(**crm_fields)
    calendar = NormalizedEvent(**calendar_fields)
    return MatchedPair(crm=crm, calendar=calendar, evidence=score_pair(crm, calendar))


# =========================== shape ===========================


def test_every_pair_and_orphan_becomes_one_meeting(meetings) -> None:
    origins = [m.origin for m in meetings]

    assert len(meetings) == 24
    assert origins.count(Origin.BOTH) == 17
    assert origins.count(Origin.CRM_ONLY) == 3
    assert origins.count(Origin.CALENDAR_ONLY) == 4


def test_meeting_ids_are_stable_and_readable(by_id) -> None:
    """Derived from the source ids, so a URL survives a re-sync."""
    assert "crm-1001-cal-a1" in by_id
    assert "crm-1005-cal-a5-cal-a6" in by_id


def test_ids_are_unique(meetings) -> None:
    ids = [m.id for m in meetings]

    assert len(ids) == len(set(ids))


def test_raw_records_from_both_sources_are_carried(by_id) -> None:
    """The detail view's "what did each source say?" panel is a dict lookup, not a join."""
    meeting = by_id["crm-1001-cal-a1"]

    assert [r["crm_id"] for r in meeting.raw_crm] == ["CRM-1001"]
    assert [r["event_id"] for r in meeting.raw_calendar] == ["CAL-A1"]


def test_the_collapsed_duplicate_carries_two_calendar_records(by_id) -> None:
    """CAL-A5 and CAL-A6 both reach the UI through one meeting."""
    meeting = by_id["crm-1005-cal-a5-cal-a6"]

    assert [r["event_id"] for r in meeting.raw_calendar] == ["CAL-A5", "CAL-A6"]
    assert meeting.calendar_ids == ["CAL-A5", "CAL-A6"]


def test_flags_from_both_sources_are_unioned(by_id) -> None:
    meeting = by_id["crm-1008-cal-a9"]
    codes = {flag.code.value for flag in meeting.flags}

    assert "MALFORMED_DATE" in codes  # from the CRM side
    assert "TIMEZONE_ASSUMED" in codes  # from the calendar side


def test_matched_meetings_carry_their_evidence(by_id) -> None:
    meeting = by_id["crm-1001-cal-a1"]

    assert meeting.match_evidence is not None
    assert len(meeting.match_evidence.signals) == 4


def test_single_source_meetings_have_no_evidence(meetings) -> None:
    """There was no pairing decision to explain."""
    for meeting in meetings:
        if meeting.origin is not Origin.BOTH:
            assert meeting.match_evidence is None


# =========================== the four conflicts ===========================


def test_exactly_four_meetings_have_conflicts(meetings) -> None:
    """Doc 02's summary line predicts four. More would mean the badge is inflated; fewer
    would mean a real disagreement is being hidden."""
    conflicted = {m.id: m.conflicting_fields for m in meetings if m.has_conflicts}

    assert conflicted == EXPECTED_CONFLICTS


def test_the_modality_conflict_shows_both_values(by_id) -> None:
    """CRM-1002: the case the brief calls out by name."""
    location = by_id["crm-1002-cal-a2"].location

    assert location.conflict is True
    assert location.conflict_kind is ConflictKind.CONTRADICTION
    assert location.value == "Zoom - https://zoom.us/j/98765432100"
    assert location.source is Source.CALENDAR
    assert location.alternatives[0].value == "NYC Office - 30th Floor"
    assert location.alternatives[0].source is Source.CRM


def test_the_status_conflict_defaults_to_cancelled(by_id) -> None:
    """A cancelled meeting shown as confirmed sends someone to an empty room; the reverse
    means someone misses a client. Both values are shown and a human decides — the default
    is conservative for filtering only."""
    status = by_id["crm-1009-cal-a10"].status

    assert status.conflict is True
    assert status.value == "cancelled"
    assert status.alternatives[0].value == "confirmed"


def test_both_time_conflicts_are_reported(by_id) -> None:
    """CRM-1016 is doc 01's 2-hour gap. CRM-1004 is the 1-hour residue of the DST
    correction from step 7 — doc 01 originally called it an exact match."""
    two_hours = by_id["crm-1016-cal-a17"].start_time
    one_hour = by_id["crm-1004-cal-a4"].start_time

    for field in (two_hours, one_hour):
        assert field.conflict is True
        assert field.source is Source.CALENDAR
        assert field.alternatives[0].source is Source.CRM

    assert two_hours.value.hour == 15 and two_hours.alternatives[0].value.hour == 13
    assert one_hour.value.hour == 15 and one_hour.alternatives[0].value.hour == 14


# =========================== what must NOT be a conflict ===========================


def test_location_granularity_keeps_the_more_specific_value(by_id) -> None:
    """CRM-1001: "HQ - Conference Room B" contains "Conference Room B". Specificity beats
    the calendar's normal precedence for logistics — the fuller value is more useful."""
    location = by_id["crm-1001-cal-a1"].location

    assert location.conflict is False
    assert location.conflict_kind is ConflictKind.GRANULARITY
    assert location.value == "HQ - Conference Room B"
    assert location.source is Source.CRM
    assert location.alternatives[0].value == "Conference Room B"


def test_the_palm_is_granularity_not_contradiction(by_id) -> None:
    """CRM-1017: "The Palm - DC" vs "The Palm Restaurant". Neither contains the other, so a
    containment-only rule reports a contradiction — doc 01 says they are compatible. The
    shared significant token is what distinguishes them."""
    location = by_id["crm-1017-cal-a20"].location

    assert location.conflict is False
    assert location.conflict_kind is ConflictKind.GRANULARITY
    assert location.value == "The Palm Restaurant"
    assert location.alternatives[0].value == "The Palm - DC"


@pytest.mark.parametrize(
    ("crm_location", "calendar_location"),
    [
        ("Room 4A", "HQ - Room 4A"),  # calendar is the specific one
        ("HQ - Room 4A", "Room 4A"),  # CRM is the specific one
    ],
)
def test_containment_is_recognised_without_a_shared_significant_token(
    crm_location: str, calendar_location: str
) -> None:
    """Synthetic, and it closes a real gap — in both directions.

    On the actual data the containment rule is redundant: every contained pair also shares a
    long token, so deleting containment broke no test. It stops being redundant when the
    distinguishing words are short or stopworded — "Room 4A" tokenises to nothing
    significant ("room" is a stopword, "4a" too short), so only containment can tell that it
    is compatible with "HQ - Room 4A" rather than contradicting it.

    Both directions are parametrized because the rule has a branch per side, and a
    single-direction test leaves the other free to rot.
    """
    merged = merge_pair(_pair({"location": crm_location}, {"location": calendar_location}))

    assert merged.location.conflict is False
    assert merged.location.conflict_kind is ConflictKind.GRANULARITY
    assert merged.location.value == "HQ - Room 4A", "the more specific value wins"


def test_absent_location_is_not_a_conflict(by_id) -> None:
    """CRM-1018 has no location, CAL-A21 says Zoom. One source had nothing to say."""
    location = by_id["crm-1018-cal-a21"].location

    assert location.conflict is False
    assert location.conflict_kind is ConflictKind.ABSENCE
    assert location.value == "Zoom"
    assert location.alternatives[0].value is None


def test_lifecycle_status_drift_is_not_a_conflict(by_id) -> None:
    """CRM-1001 is Completed while its calendar entry still says confirmed — the calendar
    simply never updates after the fact. Badging this would mark 3 of 17 meetings for
    vocabulary drift and teach the reader to ignore the badge."""
    status = by_id["crm-1001-cal-a1"].status

    assert status.conflict is False
    assert status.value == "completed"
    assert status.alternatives[0].value == "confirmed"


def test_synonymous_statuses_are_not_a_conflict(by_id) -> None:
    """CRM-1007 Scheduled vs CAL-A8 confirmed — two vocabularies for "it's on"."""
    assert by_id["crm-1007-cal-a8"].status.conflict is False


def test_differing_titles_are_never_a_conflict(meetings) -> None:
    """Titles differ on essentially every pair by convention: the calendar prefixes the
    company name. That is not a disagreement about anything."""
    for meeting in meetings:
        assert meeting.title.conflict is False


def test_ordinary_precedence_records_no_conflict_kind(by_id) -> None:
    """A kind records a *judgement* about compatibility. Precedence alone makes none, so
    the field keeps the alternative without a label — otherwise 76 fields carry a "kind"
    and the word means nothing."""
    title = by_id["crm-1001-cal-a1"].title

    assert title.conflict_kind is None
    assert title.source is Source.CRM
    assert title.alternatives[0].source is Source.CALENDAR


def test_cancelled_against_tentative_is_still_a_contradiction() -> None:
    """Synthetic: the rule must not be special-cased to `confirmed`."""
    merged = merge_pair(
        _pair({"status": MeetingStatus.CANCELLED}, {"status": MeetingStatus.TENTATIVE})
    )

    assert merged.status.conflict is True
    assert merged.status.value == "cancelled"


def test_tentative_against_confirmed_is_a_contradiction() -> None:
    """Booked or not is a real question. It does not occur in this data, but the rule should
    not be silent about it."""
    merged = merge_pair(
        _pair({"status": MeetingStatus.TENTATIVE}, {"status": MeetingStatus.CONFIRMED})
    )

    assert merged.status.conflict is True


# =========================== precedence ===========================


def test_calendar_wins_logistics(by_id) -> None:
    meeting = by_id["crm-1012-cal-a13"]

    assert meeting.start_time.source is Source.CALENDAR
    assert meeting.participants.source is Source.CALENDAR


def test_crm_wins_the_relationship_fields(by_id) -> None:
    meeting = by_id["crm-1001-cal-a1"]

    assert meeting.client_name.value == "David Park"
    assert meeting.client_name.source is Source.CRM
    assert meeting.owner_name.value == "Sarah Chen"
    assert meeting.owner_name.source is Source.CRM
    assert meeting.notes.source is Source.CRM


def test_identical_values_record_no_alternative(by_id) -> None:
    """CRM-1006 and CAL-A7 agree on the location exactly — nothing to disagree about."""
    location = by_id["crm-1006-cal-a7"].location

    assert location.alternatives == []
    assert location.conflict_kind is None


# =========================== single-source meetings ===========================


def test_crm_only_meeting_is_first_class(by_id) -> None:
    """CRM-1010: tentative, never booked. Doc 02, Decision 5 — arguably the most valuable
    output of the exercise, not an error."""
    meeting = next(m for m in by_id.values() if m.id == "crm-1010")

    assert meeting.origin is Origin.CRM_ONLY
    assert meeting.calendar_ids == []
    assert meeting.raw_calendar == []
    assert meeting.has_conflicts is False
    assert meeting.client_name.source is Source.CRM


def test_calendar_only_meeting_is_first_class(by_id) -> None:
    """CAL-A19: client time that never reached the CRM."""
    meeting = by_id["cal-a19"]

    assert meeting.origin is Origin.CALENDAR_ONLY
    assert meeting.crm_ids == []
    assert meeting.title.source is Source.CALENDAR


def test_a_single_source_meeting_never_claims_a_conflict(meetings) -> None:
    """With one source there is nothing to disagree with — a conflict here would be a bug
    that the UI would render as a real disagreement."""
    for meeting in meetings:
        if meeting.origin is not Origin.BOTH:
            assert meeting.has_conflicts is False
            assert all(f.alternatives == [] for f in meeting.provenance_fields.values())


def test_the_thin_calendar_record_survives_the_merge(by_id) -> None:
    """CAL-A11 has no attendees, location, or description and must still be a meeting."""
    meeting = by_id["cal-a11"]

    assert meeting.location.value is None
    assert meeting.notes.value is None
    assert meeting.event_date is not None


def test_merging_does_not_mutate_its_inputs() -> None:
    """Doc 03: the reconcile stages are pure."""
    pair = _pair()
    before = (pair.crm.model_dump_json(), pair.calendar.model_dump_json())

    merge_pair(pair)

    assert (pair.crm.model_dump_json(), pair.calendar.model_dump_json()) == before


def test_merge_single_handles_a_bare_event() -> None:
    event = NormalizedEvent(source=Source.CRM, source_ids=["CRM-Z"], raw={})

    meeting = merge_single(event)

    assert meeting.id == "crm-z"
    assert meeting.origin is Origin.CRM_ONLY
    assert meeting.has_conflicts is False
