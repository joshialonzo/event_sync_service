"""Tests for intra-source dedupe (step 10).

Over-collapsing deletes a real meeting with no error anywhere, so most of this file asserts
that pairs are *not* merged. The real data contains exactly one duplicate pair and cannot
exercise the recurrence guard at all, so the negative cases are synthetic by necessity.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.ingest.calendar import load_calendar
from app.ingest.crm import load_crm
from app.models.normalized import FlagCode, NormalizedEvent, Participant, Source
from app.reconcile.dedupe import dedupe_events
from app.reconcile.normalize_calendar import normalize_calendar_records
from app.reconcile.normalize_crm import normalize_crm_records

EASTERN = ZoneInfo("America/New_York")
BASE = datetime(2025, 3, 17, 11, 0, tzinfo=EASTERN)


def _event(
    event_id: str,
    *,
    start: datetime = BASE,
    organizer: str = "james.wu@firma.com",
    clients: tuple[str, ...] = ("kevin.obrien@pinnaclegp.com",),
    internal: tuple[str, ...] = (),
    is_recurring: bool = False,
    created_at: datetime | None = None,
) -> NormalizedEvent:
    participants = [
        Participant(email=organizer, display=organizer, raw=organizer, is_organizer=True)
    ]
    for email in (*clients, *internal):
        participants.append(Participant(email=email, display=email, raw=email))

    return NormalizedEvent(
        source=Source.CALENDAR,
        source_ids=[event_id],
        start=start,
        organizer=organizer,
        participants=participants,
        is_recurring=is_recurring,
        created_at=created_at or datetime(2025, 3, 1, tzinfo=EASTERN),
        raw={"event_id": event_id},
    )


@pytest.fixture(scope="module")
def calendar_events():
    return normalize_calendar_records(load_calendar())


@pytest.fixture(scope="module")
def deduped(calendar_events):
    return dedupe_events(calendar_events)


# --- the planted duplicate ---


def test_calendar_collapses_by_exactly_one(calendar_events, deduped) -> None:
    assert len(calendar_events) == 22
    assert len(deduped) == 21


def test_crm_has_nothing_to_collapse() -> None:
    """No two CRM records share a day and an owner, so the rule must not fire once."""
    events = normalize_crm_records(load_crm())

    assert len(dedupe_events(events)) == 20


def test_the_duplicate_pair_becomes_one_event(deduped) -> None:
    merged = next(e for e in deduped if "CAL-A5" in e.source_ids)

    assert merged.source_ids == ["CAL-A5", "CAL-A6"]
    assert not any("CAL-A6" == e.primary_id for e in deduped)


def test_the_earlier_record_stays_canonical(deduped) -> None:
    """CAL-A5 was created 8 days earlier. Doc 02 rejects last-write-wins here because A6 is
    newer *and worse* — a re-created invite with a vaguer location."""
    merged = next(e for e in deduped if "CAL-A5" in e.source_ids)

    assert merged.location == "Boston Office - Room 301"
    assert merged.title == "Investor Update - Pinnacle"
    assert merged.start.hour == 11 and merged.start.minute == 0


def test_the_added_attendee_survives(deduped) -> None:
    """Sandra Mills exists only on CAL-A6. Losing her is the data-loss this step risks."""
    merged = next(e for e in deduped if "CAL-A5" in e.source_ids)
    emails = {p.email for p in merged.participants}

    assert "sandra.mills@pinnaclegp.com" in emails
    assert emails == {
        "james.wu@firma.com",
        "kevin.obrien@pinnaclegp.com",
        "sandra.mills@pinnaclegp.com",
    }


def test_the_loser_raw_record_is_retained(deduped) -> None:
    """The detail view must be able to show every record the source held."""
    merged = next(e for e in deduped if "CAL-A5" in e.source_ids)

    assert [r["event_id"] for r in merged.duplicates] == ["CAL-A6"]
    assert [r["event_id"] for r in merged.raw_records] == ["CAL-A5", "CAL-A6"]


def test_the_collapse_is_flagged(deduped) -> None:
    merged = next(e for e in deduped if "CAL-A5" in e.source_ids)

    assert FlagCode.DUPLICATE_COLLAPSED in merged.flag_codes


def test_distinct_flags_from_both_records_survive(deduped) -> None:
    """A5 and A6 each carry TIMEZONE_ASSUMED for a *different* raw timestamp — two real
    assumptions. De-duplicating by code rather than by value would lose one."""
    merged = next(e for e in deduped if "CAL-A5" in e.source_ids)
    assumed = [f for f in merged.flags if f.code is FlagCode.TIMEZONE_ASSUMED]

    assert {f.raw_value for f in assumed} == {"2025-03-17T11:00:00", "2025-03-17T11:30:00"}


def test_the_file_wide_flag_census_is_unchanged_by_dedupe(calendar_events, deduped) -> None:
    """21 timezone assumptions were made across 22 records; collapsing two of them must not
    quietly retire one."""
    before = sum(1 for e in calendar_events for f in e.flags if f.code is FlagCode.TIMEZONE_ASSUMED)
    after = sum(1 for e in deduped for f in e.flags if f.code is FlagCode.TIMEZONE_ASSUMED)

    assert before == after == 21


# --- what must NOT collapse (the expensive failure) ---


def test_the_recurring_series_is_untouched(deduped) -> None:
    """CAL-A3 and CAL-A18 are one week apart — they fail the same-day check first."""
    ids = {e.primary_id for e in deduped}

    assert {"CAL-A3", "CAL-A18"}.issubset(ids)


def test_two_recurring_instances_at_the_same_time_do_not_collapse() -> None:
    """The carve-out the real data cannot reach: A3/A18 never get past the date check, so
    only a synthetic pair proves the guard exists. Deleting one instance of a series is the
    silent data-loss bug this guard is for."""
    events = [
        _event("REC-1", is_recurring=True),
        _event("REC-2", start=BASE + timedelta(minutes=5), is_recurring=True),
    ]

    assert len(dedupe_events(events)) == 2


def test_one_recurring_side_is_enough_to_block_the_merge() -> None:
    events = [_event("REC-1", is_recurring=True), _event("ONE-OFF")]

    assert len(dedupe_events(events)) == 2


def test_records_more_than_an_hour_apart_do_not_collapse() -> None:
    events = [_event("A"), _event("B", start=BASE + timedelta(minutes=90))]

    assert len(dedupe_events(events)) == 2


def test_the_window_boundary_is_inclusive() -> None:
    exactly = dedupe_events([_event("A"), _event("B", start=BASE + timedelta(minutes=60))])
    just_over = dedupe_events([_event("A"), _event("B", start=BASE + timedelta(minutes=61))])

    assert len(exactly) == 1
    assert len(just_over) == 2


def test_different_organizers_do_not_collapse() -> None:
    events = [_event("A"), _event("B", organizer="sarah.chen@firma.com")]

    assert len(dedupe_events(events)) == 2


def test_records_missing_an_organizer_never_collapse() -> None:
    """Otherwise every anonymous record would be a duplicate of every other."""
    a, b = _event("A"), _event("B")
    a.organizer = b.organizer = None

    assert len(dedupe_events([a, b])) == 2


def test_sharing_only_internal_attendees_does_not_collapse() -> None:
    """Two internal meetings on one day share the same colleagues. Counting them as party
    overlap would merge unrelated meetings — this is what protects the team syncs before
    the recurrence guard is even consulted."""
    events = [
        _event("A", clients=(), internal=("priya.sharma@firma.com",)),
        _event(
            "B",
            start=BASE + timedelta(minutes=15),
            clients=(),
            internal=("priya.sharma@firma.com", "michael.ross@firma.com"),
        ),
    ]

    assert len(dedupe_events(events)) == 2


def test_different_clients_do_not_collapse() -> None:
    events = [_event("A"), _event("B", clients=("someone@other.com",))]

    assert len(dedupe_events(events)) == 2


def test_different_days_do_not_collapse() -> None:
    events = [_event("A"), _event("B", start=BASE + timedelta(days=1))]

    assert len(dedupe_events(events)) == 2


def test_events_from_different_sources_never_collapse() -> None:
    """Cross-source pairing is step 12's job and follows entirely different rules."""
    calendar = _event("CAL-X")
    crm = _event("CRM-X")
    crm.source = Source.CRM

    assert len(dedupe_events([calendar, crm])) == 2


# --- properties ---


def test_dedupe_is_order_independent(calendar_events) -> None:
    """A greedy single pass could in principle depend on input order; with this data it
    must not."""
    forward = {tuple(sorted(e.source_ids)) for e in dedupe_events(list(calendar_events))}
    backward = {
        tuple(sorted(e.source_ids)) for e in dedupe_events(list(reversed(calendar_events)))
    }

    assert forward == backward


def test_dedupe_does_not_mutate_its_input() -> None:
    """Doc 03 requires the reconcile stages to be pure.

    This caught a real bug: the first implementation absorbed into the caller's own objects,
    so a before/after flag census measured the same mutated list twice and silently agreed
    with itself.
    """
    events = [_event("A"), _event("B", start=BASE + timedelta(minutes=20))]

    merged = dedupe_events(events)

    assert len(merged) == 1
    assert [e.source_ids for e in events] == [["A"], ["B"]]
    assert all(e.duplicates == [] for e in events)
    assert all(FlagCode.DUPLICATE_COLLAPSED not in e.flag_codes for e in events)
    assert merged[0] is not events[0]


def test_no_source_id_is_lost(calendar_events, deduped) -> None:
    """The strongest guard against over-collapsing: every input id must still be reachable."""
    before = {i for e in calendar_events for i in e.source_ids}
    after = {i for e in deduped for i in e.source_ids}

    assert before == after
    assert len(before) == 22


def test_three_way_duplicates_fold_into_one() -> None:
    events = [
        _event("A", created_at=datetime(2025, 3, 1, tzinfo=EASTERN)),
        _event("B", start=BASE + timedelta(minutes=20), created_at=datetime(2025, 3, 2, tzinfo=EASTERN)),
        _event("C", start=BASE + timedelta(minutes=40), created_at=datetime(2025, 3, 3, tzinfo=EASTERN)),
    ]

    merged = dedupe_events(events)

    assert len(merged) == 1
    assert merged[0].source_ids == ["A", "B", "C"]
    assert len(merged[0].duplicates) == 2


def test_the_earliest_of_three_stays_canonical() -> None:
    """Input order must not decide the survivor — created_at does."""
    events = [
        _event("LATE", created_at=datetime(2025, 3, 9, tzinfo=EASTERN)),
        _event("EARLY", start=BASE + timedelta(minutes=10), created_at=datetime(2025, 3, 1, tzinfo=EASTERN)),
    ]

    merged = dedupe_events(events)[0]

    assert merged.raw["event_id"] == "EARLY"
    assert merged.start == BASE + timedelta(minutes=10)
    assert sorted(merged.source_ids) == ["EARLY", "LATE"]
