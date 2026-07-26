"""Tests for the sync job (step 15).

The end-to-end assertion for the whole pipeline: 42 records in, 24 meetings out, and every
number the stats page will report. If this file is green, the service is correct and only the
HTTP layer remains.
"""

import json
from datetime import date
from pathlib import Path

import pytest

from app.jobs.sync import run_sync
from app.models.unified import Origin, SyncResult


@pytest.fixture(scope="module")
def result() -> SyncResult:
    return run_sync()


@pytest.fixture(scope="module")
def summary(result):
    return result.summary


# --- shape ---


def test_produces_twenty_four_meetings(result) -> None:
    assert len(result.meetings) == 24
    assert len(result.by_date) == 24


def test_by_date_covers_every_meeting(result) -> None:
    """SyncResult's validator enforces this; the assertion documents that the job satisfies
    it with real data rather than by accident."""
    assert sorted(result.by_date) == sorted(result.meetings)


def test_origins_split_as_documented(result) -> None:
    origins = [m.origin for m in result.ordered_meetings]

    assert origins.count(Origin.BOTH) == 17
    assert origins.count(Origin.CRM_ONLY) == 3
    assert origins.count(Origin.CALENDAR_ONLY) == 4


def test_every_source_record_survives_to_the_output(result) -> None:
    """The end-to-end form of doc 02 Decision 1: 42 in, 42 accounted for."""
    ids = {
        source_id
        for meeting in result.meetings.values()
        for source_id in (*meeting.crm_ids, *meeting.calendar_ids)
    }

    assert len(ids) == 42
    assert "CAL-A6" in ids, "the collapsed duplicate is still reachable"


def test_raw_records_reach_the_output(result) -> None:
    raw_count = sum(
        len(m.raw_crm) + len(m.raw_calendar) for m in result.meetings.values()
    )

    assert raw_count == 42


# --- ordering ---


def test_meetings_are_in_chronological_order(result) -> None:
    dates = [m.event_date for m in result.ordered_meetings]

    assert dates == sorted(dates)
    assert dates[0] == date(2025, 3, 10)


def test_same_day_meetings_are_ordered_by_start(result) -> None:
    ordered = result.ordered_meetings

    for earlier, later in zip(ordered, ordered[1:]):
        if earlier.event_date == later.event_date and earlier.start and later.start:
            assert earlier.start <= later.start


def test_ordering_tolerates_a_meeting_without_a_time(tmp_path: Path) -> None:
    """Nothing in this dataset needs the fallback, which is exactly why it needs a test: a
    date-only meeting must sort to the start of its day rather than fail the sync."""
    crm = [
        {
            "crm_id": "CRM-1",
            "subject": "Date only",
            "client_name": "A B",
            "client_company": "C",
            "relationship_owner": "D E",
            "meeting_date": "2025-03-10",
            "meeting_time": None,
            "meeting_type": "Virtual",
            "location": None,
            "notes": "",
            "status": "Scheduled",
            "created_at": "2025-03-01T10:00:00Z",
        }
    ]
    (tmp_path / "crm_events.json").write_text(json.dumps(crm), encoding="utf-8")
    (tmp_path / "calendar_events.json").write_text("[]", encoding="utf-8")

    outcome = run_sync(data_dir=tmp_path)

    assert len(outcome.meetings) == 1
    assert outcome.ordered_meetings[0].start is None


# --- the summary: every number on the stats page ---


def test_records_in(summary) -> None:
    assert summary.crm_records_in == 20
    assert summary.calendar_records_in == 22
    assert summary.records_in == 42


def test_duplicates_collapsed(summary) -> None:
    """CAL-A5/CAL-A6, and nothing else in either source."""
    assert summary.duplicates_collapsed == 1


def test_dedupe_runs_on_the_crm_too(tmp_path: Path) -> None:
    """Synthetic, and it closes a real gap.

    Dedupe is a no-op on the real CRM file — no two records share a day and an owner — so
    skipping the CRM entirely broke no test. The pipeline runs it on both sources anyway,
    because deduping one source only would make the design asymmetric for a reason that is
    a property of this dataset rather than of the problem. Only a CRM file that *does*
    contain a duplicate can prove the stage is wired up.
    """
    def record(crm_id: str, time_of_day: str, created: str) -> dict:
        return {
            "crm_id": crm_id,
            "subject": "Quarterly Review",
            "client_name": "David Park",
            "client_company": "Meridian Capital",
            "relationship_owner": "Sarah Chen",
            "meeting_date": "2025-03-10",
            "meeting_time": time_of_day,
            "meeting_type": "In-Person",
            "location": "HQ",
            "notes": "",
            "status": "Confirmed",
            "created_at": created,
        }

    crm = [
        record("CRM-1", "14:00", "2025-03-01T10:00:00Z"),
        record("CRM-2", "14:30", "2025-03-05T10:00:00Z"),
    ]
    (tmp_path / "crm_events.json").write_text(json.dumps(crm), encoding="utf-8")
    (tmp_path / "calendar_events.json").write_text("[]", encoding="utf-8")

    outcome = run_sync(data_dir=tmp_path)

    assert outcome.summary.crm_records_in == 2
    assert outcome.summary.duplicates_collapsed == 1
    assert len(outcome.meetings) == 1
    assert outcome.ordered_meetings[0].crm_ids == ["CRM-1", "CRM-2"]


def test_match_counts(summary) -> None:
    assert summary.meetings_out == 24
    assert summary.matched_pairs == 17
    assert summary.crm_only == 3
    assert summary.calendar_only == 4


def test_no_match_relies_on_the_low_confidence_band(summary) -> None:
    """A zero is an answer here: the reviewer needs to know nothing was merged on a hunch."""
    assert summary.low_confidence_matches == 0


def test_conflicts_by_kind(summary) -> None:
    assert summary.conflicts_by_kind == {
        "contradiction": 4,
        "granularity": 8,
        "absence": 3,
    }


def test_conflicts_by_field(summary) -> None:
    assert summary.conflicts_by_field == {"start_time": 2, "location": 1, "status": 1}


def test_flags_by_code(summary) -> None:
    assert summary.flags_by_code == {
        "TIMEZONE_ASSUMED": 40,
        "INTERNAL_NO_CLIENT": 4,
        "DUPLICATE_COLLAPSED": 1,
        "MALFORMED_DATE": 1,
        "MALFORMED_DATETIME": 1,
        "MALFORMED_EMAIL": 1,
        "NON_EMAIL_ATTENDEE": 1,
        "PLACEHOLDER_CLIENT": 1,
        "TIME_MISSING": 1,
    }


def test_timezone_assumptions_span_both_sources(summary) -> None:
    """40 = 19 CRM records with a time + 21 naive calendar records. The loudest number on
    the stats page, and it should be: each one is a guess the service is owning up to."""
    assert summary.flags_by_code["TIMEZONE_ASSUMED"] == 40


def test_flags_by_severity(summary) -> None:
    assert summary.flags_by_severity == {"info": 47, "warning": 3, "error": 1}


def test_only_one_genuine_error_in_the_dataset(summary) -> None:
    """CRM-1008's corrupt date. Severity is what stops the four internal meetings from
    looking equally broken."""
    assert summary.flags_by_severity["error"] == 1


def test_summary_totals_agree_with_the_meetings(result, summary) -> None:
    """A summary derived from the pipeline could drift from the output it describes."""
    assert summary.meetings_out == len(result.meetings)
    assert summary.matched_pairs + summary.crm_only + summary.calendar_only == 24

    conflicted = sum(1 for m in result.meetings.values() if m.has_conflicts)
    assert sum(summary.conflicts_by_field.values()) == conflicted


# --- properties ---


def test_two_runs_are_identical(result) -> None:
    """Step 25's re-sync button must be idempotent — a second press cannot renumber or
    reorder anything."""
    again = run_sync()

    assert again.by_date == result.by_date
    assert set(again.meetings) == set(result.meetings)
    assert again.summary.model_dump(exclude={"generated_at"}) == result.summary.model_dump(
        exclude={"generated_at"}
    )


def test_generated_at_is_timezone_aware(summary) -> None:
    assert summary.generated_at.tzinfo is not None


def test_sync_reads_an_alternate_directory(tmp_path: Path) -> None:
    """So tests never depend on the repo's data/ being pristine."""
    (tmp_path / "crm_events.json").write_text("[]", encoding="utf-8")
    (tmp_path / "calendar_events.json").write_text("[]", encoding="utf-8")

    outcome = run_sync(data_dir=tmp_path)

    assert outcome.meetings == {}
    assert outcome.summary.records_in == 0


def test_a_missing_data_directory_fails_loudly(tmp_path: Path) -> None:
    """Publishing zero meetings from a bad path would look like a working service with no
    data, which is worse than a startup crash."""
    with pytest.raises(FileNotFoundError):
        run_sync(data_dir=tmp_path / "nowhere")
