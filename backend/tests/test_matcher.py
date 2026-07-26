"""Tests for the matcher (step 12) — the project's correctness gate.

The fixture below was derived by hand in doc 01 *before* any of this code existed. It is the
one test in the suite that would still matter if every other were deleted: if these pairings
are wrong, every page the service renders is confidently wrong.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.ingest.calendar import load_calendar
from app.ingest.crm import load_crm
from app.models.normalized import NormalizedEvent, Participant, Source
from app.models.unified import MatchConfidence
from app.reconcile.dedupe import dedupe_events
from app.reconcile.matcher import (
    AUTO_MATCH_THRESHOLD,
    LOW_CONFIDENCE_THRESHOLD,
    match_events,
    score_pair,
)
from app.reconcile.normalize_calendar import normalize_calendar_records
from app.reconcile.normalize_crm import normalize_crm_records

EASTERN = ZoneInfo("America/New_York")
BASE = datetime(2025, 3, 10, 14, 0, tzinfo=EASTERN)

# doc 01, "Expected reconciliation outcome" — derived by hand from the raw files.
EXPECTED_PAIRS = {
    ("CRM-1001", "CAL-A1"),
    ("CRM-1002", "CAL-A2"),
    ("CRM-1004", "CAL-A4"),
    ("CRM-1005", "CAL-A5"),
    ("CRM-1006", "CAL-A7"),
    ("CRM-1007", "CAL-A8"),
    ("CRM-1008", "CAL-A9"),
    ("CRM-1009", "CAL-A10"),
    ("CRM-1011", "CAL-A12"),
    ("CRM-1012", "CAL-A13"),
    ("CRM-1013", "CAL-A14"),
    ("CRM-1014", "CAL-A15"),
    ("CRM-1015", "CAL-A16"),
    ("CRM-1016", "CAL-A17"),
    ("CRM-1017", "CAL-A20"),
    ("CRM-1018", "CAL-A21"),
    ("CRM-1019", "CAL-A22"),
}
EXPECTED_CRM_ONLY = {"CRM-1003", "CRM-1010", "CRM-1020"}
EXPECTED_CALENDAR_ONLY = {"CAL-A3", "CAL-A11", "CAL-A18", "CAL-A19"}


@pytest.fixture(scope="module")
def crm_events():
    return normalize_crm_records(load_crm())


@pytest.fixture(scope="module")
def calendar_events():
    return dedupe_events(normalize_calendar_records(load_calendar()))


@pytest.fixture(scope="module")
def result(crm_events, calendar_events):
    return match_events(crm_events, calendar_events)


def _crm(crm_id="CRM-X", *, start=BASE, **overrides) -> NormalizedEvent:
    kwargs = {
        "source": Source.CRM,
        "source_ids": [crm_id],
        "start": start,
        "owner_name": "Sarah Chen",
        "client_name": "David Park",
        "client_company": "Meridian Capital",
        "title": "Q1 Portfolio Review",
        "location": "HQ - Conference Room B",
        "meeting_type": "In-Person",
        "raw": {},
    }
    kwargs.update(overrides)
    return NormalizedEvent(**kwargs)


def _calendar(event_id="CAL-X", *, start=BASE, **overrides) -> NormalizedEvent:
    emails = ("sarah.chen@firma.com", "david.park@meridiancap.com")
    kwargs = {
        "source": Source.CALENDAR,
        "source_ids": [event_id],
        "start": start,
        "organizer": emails[0],
        "participants": [
            Participant(email=e, display=e, raw=e, is_organizer=(i == 0))
            for i, e in enumerate(emails)
        ],
        "title": "Q1 Portfolio Review - Meridian Capital",
        "location": "Conference Room B",
        "raw": {},
    }
    kwargs.update(overrides)
    return NormalizedEvent(**kwargs)


# =========================== THE FIXTURE ===========================


def test_the_seventeen_documented_pairs(result) -> None:
    """Doc 01's hand-derived pairings, exactly — no extras, no omissions."""
    produced = {(p.crm.primary_id, p.calendar.primary_id) for p in result.pairs}

    assert produced == EXPECTED_PAIRS


def test_the_three_crm_only_records(result) -> None:
    """Both tentative Northwind records plus the Lakeshore intro. Doc 02, Decision 5: a CRM
    meeting with no calendar entry is a business signal — it was never actually booked."""
    assert {e.primary_id for e in result.unmatched_crm} == EXPECTED_CRM_ONLY


def test_the_four_calendar_only_records(result) -> None:
    """The two recurring team syncs, the thin reception, and the roadshow prep — client time
    that never reached the CRM."""
    assert {e.primary_id for e in result.unmatched_calendar} == EXPECTED_CALENDAR_ONLY


def test_twenty_four_meetings(result) -> None:
    """The number the whole project is judged on."""
    assert result.meeting_count == 24
    assert len(result.pairs) == 17


def test_every_input_record_is_accounted_for(result, crm_events, calendar_events) -> None:
    """Doc 02, Decision 1 end to end: nothing is dropped anywhere in the pipeline."""
    matched_crm = {p.crm.primary_id for p in result.pairs}
    matched_calendar = {p.calendar.primary_id for p in result.pairs}
    unmatched_crm = {e.primary_id for e in result.unmatched_crm}
    unmatched_calendar = {e.primary_id for e in result.unmatched_calendar}

    assert matched_crm | unmatched_crm == {e.primary_id for e in crm_events}
    assert matched_calendar | unmatched_calendar == {e.primary_id for e in calendar_events}
    assert not matched_crm & unmatched_crm
    assert not matched_calendar & unmatched_calendar


def test_every_source_id_including_the_collapsed_duplicate(result) -> None:
    """All 42 raw ids survive to here — CAL-A6 inside its merged event."""
    ids = set()
    for pair in result.pairs:
        ids |= set(pair.crm.source_ids) | set(pair.calendar.source_ids)
    for event in (*result.unmatched_crm, *result.unmatched_calendar):
        ids |= set(event.source_ids)

    assert len(ids) == 42
    assert "CAL-A6" in ids


def test_no_record_is_used_twice(result) -> None:
    crm_ids = [p.crm.primary_id for p in result.pairs]
    calendar_ids = [p.calendar.primary_id for p in result.pairs]

    assert len(crm_ids) == len(set(crm_ids))
    assert len(calendar_ids) == len(set(calendar_ids))


# =========================== evidence ===========================


def test_every_pair_carries_a_four_signal_breakdown(result) -> None:
    for pair in result.pairs:
        names = [signal.name for signal in pair.evidence.signals]

        assert names == ["participants", "time", "title", "structure"]
        assert [s.weight for s in pair.evidence.signals] == [0.40, 0.30, 0.20, 0.10]


def test_scores_equal_their_breakdowns(result) -> None:
    """MatchEvidence enforces this, but only if the matcher builds it honestly — this
    asserts the guard is actually reached for real pairs."""
    for pair in result.pairs:
        total = sum(s.contribution for s in pair.evidence.signals)

        assert pair.evidence.score == pytest.approx(total)


def test_all_real_pairs_clear_the_auto_threshold(result) -> None:
    """Observed range 0.763 - 1.000. No pair in this dataset relies on the low-confidence
    band, so a regression that pushes one into it is visible here."""
    scores = [p.evidence.score for p in result.pairs]

    assert min(scores) > AUTO_MATCH_THRESHOLD
    assert all(p.evidence.confidence is MatchConfidence.HIGH for p in result.pairs)


def test_evidence_explains_the_timezone_pair(result) -> None:
    """CRM-1004/CAL-A4 is an hour apart after the DST correction (doc 01, section D), and
    the evidence panel has to say so rather than claiming an exact match."""
    pair = next(p for p in result.pairs if p.crm.primary_id == "CRM-1004")
    time_signal = next(s for s in pair.evidence.signals if s.name == "time")

    assert 0 < time_signal.score < 1.0
    assert "60 min apart" in time_signal.detail


def test_evidence_explains_the_modality_conflict(result) -> None:
    """CRM-1002 In-Person vs CAL-A2 Zoom — matched anyway, with the disagreement visible."""
    pair = next(p for p in result.pairs if p.crm.primary_id == "CRM-1002")
    structure = next(s for s in pair.evidence.signals if s.name == "structure")

    assert structure.score == 0.0
    assert pair.evidence.score > AUTO_MATCH_THRESHOLD


def test_every_signal_detail_is_populated(result) -> None:
    """The evidence panel renders these strings; a blank one is a blank row in the UI."""
    for pair in result.pairs:
        assert all(signal.detail for signal in pair.evidence.signals)


# =========================== behaviour ===========================


def test_pairings_are_independent_of_input_order(crm_events, calendar_events) -> None:
    """A greedy matcher whose output depends on dict iteration order is untestable, and its
    results would change with the order records were read from disk."""
    forward = match_events(crm_events, calendar_events)
    reversed_inputs = match_events(list(reversed(crm_events)), list(reversed(calendar_events)))

    assert {(p.crm.primary_id, p.calendar.primary_id) for p in forward.pairs} == {
        (p.crm.primary_id, p.calendar.primary_id) for p in reversed_inputs.pairs
    }


def test_ties_are_broken_deterministically() -> None:
    """Two identical calendar records cannot both win; which one does must not depend on
    luck."""
    crm = [_crm("CRM-1")]
    calendar = [_calendar("CAL-2"), _calendar("CAL-1")]

    first = match_events(crm, calendar)
    second = match_events(crm, list(reversed(calendar)))

    assert first.pairs[0].calendar.primary_id == second.pairs[0].calendar.primary_id == "CAL-1"


def test_blocking_rejects_records_more_than_a_day_apart() -> None:
    """Otherwise the two Atlas Ventures meetings 11 days apart would compete on their
    identical client and company."""
    crm = [_crm("CRM-1")]
    calendar = [_calendar("CAL-1", start=BASE + timedelta(days=2))]

    assert match_events(crm, calendar).pairs == []


def test_blocking_admits_the_adjacent_day() -> None:
    """The +/-1 day window: a timezone mistake should produce a badged low-confidence match,
    not a silent miss."""
    crm = [_crm("CRM-1")]
    calendar = [_calendar("CAL-1", start=BASE + timedelta(days=1))]

    assert len(match_events(crm, calendar).pairs) == 1


def test_blocking_loses_no_true_pair(crm_events, calendar_events, result) -> None:
    """Measured: 420 combinations reduce to 61 candidates. This asserts the reduction costs
    nothing."""
    within_block = sum(
        1
        for c in crm_events
        for k in calendar_events
        if c.event_date and k.event_date and abs((c.event_date - k.event_date).days) <= 1
    )

    assert within_block < len(crm_events) * len(calendar_events)
    assert {(p.crm.primary_id, p.calendar.primary_id) for p in result.pairs} == EXPECTED_PAIRS


def test_a_record_without_a_date_is_never_a_candidate() -> None:
    """Comparing it against every calendar entry would be scoring on no evidence."""
    crm = [_crm("CRM-1", start=None, event_date=None)]
    calendar = [_calendar("CAL-1")]

    outcome = match_events(crm, calendar)

    assert outcome.pairs == []
    assert len(outcome.unmatched_crm) == 1


def test_a_weak_pair_is_rejected_entirely() -> None:
    crm = [_crm("CRM-1", client_name="Nobody", client_company=None, owner_name=None, title="Fire Drill")]
    calendar = [_calendar("CAL-1", title="Board Offsite", location="Elsewhere")]

    outcome = match_events(crm, calendar)

    assert outcome.pairs == []
    assert len(outcome.unmatched_crm) == len(outcome.unmatched_calendar) == 1


def test_a_middling_pair_matches_but_is_badged_low() -> None:
    """The 0.45-0.70 band: merged so the data is not lost, badged so nobody trusts it
    silently."""
    crm = _crm(
        "CRM-1",
        client_name=None,
        client_company=None,
        title="Fire Drill",
        location=None,
        meeting_type=None,
    )
    # The owner attends but did not organise (0.7), the start is two hours off (0.5), the
    # titles share nothing, and neither side states a location.
    others = ("priya.sharma@firma.com", "sarah.chen@firma.com")
    calendar = _calendar(
        "CAL-1",
        start=BASE + timedelta(hours=2),
        title="Board Offsite",
        location=None,
        organizer=others[0],
        participants=[
            Participant(email=e, display=e, raw=e, is_organizer=(i == 0))
            for i, e in enumerate(others)
        ],
    )

    evidence = score_pair(crm, calendar)

    assert LOW_CONFIDENCE_THRESHOLD <= evidence.score < AUTO_MATCH_THRESHOLD
    assert evidence.confidence is MatchConfidence.LOW
    assert len(match_events([crm], [calendar]).pairs) == 1, "merged despite low confidence"


def test_matching_does_not_mutate_its_inputs(crm_events, calendar_events) -> None:
    """Doc 03: the reconcile stages are pure — the bug step 10 actually hit."""
    before = [e.model_dump_json() for e in crm_events]

    match_events(crm_events, calendar_events)

    assert [e.model_dump_json() for e in crm_events] == before


def test_empty_inputs_are_handled() -> None:
    assert match_events([], []).meeting_count == 0
    assert len(match_events([_crm()], []).unmatched_crm) == 1
    assert len(match_events([], [_calendar()]).unmatched_calendar) == 1


def test_the_atlas_ventures_pair_is_not_confused() -> None:
    """CRM-1008 (lunch, 3/15) and CRM-1015 (pitch, 3/26) share a client 11 days apart. Doc
    02 names this as the case a title-only or client-only matcher would fuse."""
    crm = normalize_crm_records(load_crm())
    calendar = dedupe_events(normalize_calendar_records(load_calendar()))
    pairs = {p.crm.primary_id: p.calendar.primary_id for p in match_events(crm, calendar).pairs}

    assert pairs["CRM-1008"] == "CAL-A9"
    assert pairs["CRM-1015"] == "CAL-A16"


def test_the_recurring_series_stays_two_meetings(result) -> None:
    """CAL-A3 and CAL-A18 both survive to the output — deleting either would be silent data
    loss of a real meeting."""
    calendar_only = {e.primary_id for e in result.unmatched_calendar}

    assert {"CAL-A3", "CAL-A18"} <= calendar_only


def test_dates_are_compared_not_datetimes() -> None:
    """23:30 and 00:30 the next morning are one calendar day apart, so blocking admits
    them — the score then decides."""
    late = _crm("CRM-1", start=BASE.replace(hour=23, minute=30))
    early = _calendar("CAL-1", start=(BASE + timedelta(days=1)).replace(hour=0, minute=30))

    assert len(match_events([late], [early]).pairs) == 1
