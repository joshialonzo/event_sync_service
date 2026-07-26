"""Tests for the parsing primitives (step 7).

Every case below is either a real value from `data/` or a degenerate input the functions
must survive. Doc 02 Decision 1 promises normalization never raises, so the last test
sweeps every function with every hostile input rather than trusting case-by-case coverage.
"""

from datetime import date, datetime, time, timezone

import pytest

from app.models.normalized import FlagCode, MeetingStatus
from app.reconcile.parse import (
    combine,
    local_timezone,
    normalize_status,
    parse_date,
    parse_datetime,
    parse_time,
    repair_email,
    to_eastern,
)

# --- dates ---


def test_iso_date_parses_without_a_flag() -> None:
    value, code = parse_date("2025-03-10")

    assert value == date(2025, 3, 10)
    assert code is None


def test_mixed_separator_date_parses_with_a_flag() -> None:
    """CRM-1008. Doc 01 resolves it as 2025-03-15 from its counterpart CAL-A9."""
    value, code = parse_date("03-15/2025")

    assert value == date(2025, 3, 15)
    assert code is FlagCode.MALFORMED_DATE


@pytest.mark.parametrize("raw", ["not a date", "2025-13-45", "", "   ", None, "03-15-2025-extra"])
def test_unparsable_dates_return_none_rather_than_guessing(raw: object) -> None:
    """Never invent a date. The record continues without one and says so."""
    value, code = parse_date(raw)

    assert value is None
    assert code is FlagCode.UNPARSABLE_DATE


# --- times ---


def test_time_parses() -> None:
    value, code = parse_time("14:00")

    assert value == time(14, 0)
    assert code is None


def test_missing_time_is_a_gap_not_corruption() -> None:
    """CRM-1007. It still participates in matching, on the date signal alone."""
    value, code = parse_time(None)

    assert value is None
    assert code is FlagCode.TIME_MISSING


# --- timezone: the one inference in the project (doc 01, section D) ---


def test_utc_timestamp_converts_to_eastern() -> None:
    """CAL-A4 — the only Z-suffixed event timestamp in either file.

    19:00Z on 2025-03-13 is 15:00 EDT, *not* 14:00: DST began 2025-03-09, so the offset is
    -4 rather than -5. Doc 01 originally asserted 14:00 and an exact match with CRM-1004;
    the correction is recorded there. Converting still beats reading the Z literally — 1
    hour apart instead of 5 — which is what the match depends on.
    """
    value, code = parse_datetime("2025-03-13T19:00:00Z")

    assert value.hour == 15
    assert value.utcoffset().total_seconds() == -4 * 3600  # EDT
    assert code is None, "nothing was assumed — the source stated its offset"


def test_naive_timestamp_is_assumed_eastern_and_says_so() -> None:
    value, code = parse_datetime("2025-03-10T14:00:00")

    assert value.hour == 14
    assert value.tzinfo is not None
    assert code is FlagCode.TIMEZONE_ASSUMED


def test_truncated_timestamp_parses_with_a_flag() -> None:
    """CAL-A11's end_time is missing the seconds every other record carries."""
    value, code = parse_datetime("2025-03-14T20:00")

    assert value.hour == 20
    assert code is FlagCode.MALFORMED_DATETIME


def test_dst_boundary_is_respected_across_the_dataset() -> None:
    """The events are all post-2025-03-09 (EDT, -4) but created_at values run back to
    January (EST, -5). A hard-coded offset would be right for one and wrong for the other."""
    march, _ = parse_datetime("2025-03-13T19:00:00Z")
    february, _ = parse_datetime("2025-02-28T09:15:00Z")

    assert march.utcoffset().total_seconds() == -4 * 3600
    assert march.hour == 15
    assert february.utcoffset().total_seconds() == -5 * 3600
    assert february.hour == 4


def test_to_eastern_flags_only_naive_input() -> None:
    naive = to_eastern(datetime(2025, 3, 10, 14, 0))
    aware = to_eastern(datetime(2025, 3, 13, 19, 0, tzinfo=timezone.utc))

    assert naive.code is FlagCode.TIMEZONE_ASSUMED
    assert aware.code is None
    assert aware.value.hour == 15


def test_to_eastern_passes_none_through() -> None:
    assert to_eastern(None) == (None, None)


# --- combining ---


def test_combine_builds_an_aware_datetime() -> None:
    value, code = combine(date(2025, 3, 10), time(14, 0))

    assert value == datetime(2025, 3, 10, 14, 0, tzinfo=local_timezone())
    assert code is None


def test_combine_without_a_time_yields_no_start() -> None:
    """CRM-1007 keeps its event_date and simply has no start."""
    assert combine(date(2025, 3, 19), None).value is None
    assert combine(None, time(14, 0)).value is None


# --- emails ---


def test_clean_email_needs_no_repair() -> None:
    value, code = repair_email("david.park@meridiancap.com")

    assert value == "david.park@meridiancap.com"
    assert code is None


def test_obfuscated_email_is_repaired_with_a_flag() -> None:
    """CAL-A16."""
    value, code = repair_email("raj.patel[at]atlasvc.com")

    assert value == "raj.patel@atlasvc.com"
    assert code is FlagCode.MALFORMED_EMAIL


def test_email_is_lowercased_for_comparison() -> None:
    """Step 11 matches CRM names against calendar addresses; case would break the join."""
    assert repair_email("David.Park@MeridianCap.com").value == "david.park@meridiancap.com"


def test_non_email_attendee_is_a_label_not_an_error() -> None:
    """CAL-A20. "Outsiders attended" is real information, so the caller keeps the raw
    string as an opaque participant rather than dropping it."""
    value, code = repair_email("external-guests")

    assert value is None
    assert code is FlagCode.NON_EMAIL_ATTENDEE


# --- status ---


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Scheduled", MeetingStatus.SCHEDULED),
        ("Confirmed", MeetingStatus.CONFIRMED),
        ("Tentative", MeetingStatus.TENTATIVE),
        ("Completed", MeetingStatus.COMPLETED),
        ("Cancelled", MeetingStatus.CANCELLED),
        ("confirmed", MeetingStatus.CONFIRMED),
        ("tentative", MeetingStatus.TENTATIVE),
    ],
)
def test_both_source_vocabularies_map_onto_one_enum(raw: str, expected: MeetingStatus) -> None:
    """The five title-case CRM values and two lower-case calendar values pinned in step 4."""
    value, code = normalize_status(raw)

    assert value is expected
    assert code is None


def test_unknown_status_degrades_rather_than_raising() -> None:
    value, code = normalize_status("postponed")

    assert value is MeetingStatus.UNKNOWN
    assert code is FlagCode.UNKNOWN_STATUS


# --- the promise that holds all of this up ---


@pytest.mark.parametrize(
    "hostile",
    [None, "", "   ", 0, 42, [], {}, "\n", "????", "2025-03-10T14:00:00+99:00"],
)
def test_no_parser_raises_on_any_input(hostile: object) -> None:
    """Doc 02, Decision 1: a pipeline that raises on a bad record cannot report it. This is
    the promise every normalizer downstream depends on, so it is tested exhaustively rather
    than sampled."""
    for parser in (parse_date, parse_time, parse_datetime, repair_email, normalize_status):
        result = parser(hostile)
        assert result.value is not None or result.code is not None
