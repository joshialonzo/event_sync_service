"""Tests for the unified models (step 6).

The provenance shape is the contract both frontend requirements rest on, so the first test
pins it against the JSON block in doc 02, Decision 4 literally. The rest are the invariants
that stop step 13 from producing a structurally valid but misleading merge.
"""

from datetime import date, datetime

import pytest
from pydantic import ValidationError

from app.models.normalized import DataQualityFlag, FlagCode, Source
from app.models.unified import (
    ConflictKind,
    MatchConfidence,
    MatchEvidence,
    MatchSignal,
    Origin,
    ProvenanceField,
    SourceValue,
    SyncResult,
    SyncRunSummary,
    UnifiedMeeting,
)


def _meeting(**overrides: object) -> UnifiedMeeting:
    kwargs: dict = {
        "id": "m-1",
        "origin": Origin.BOTH,
        "crm_ids": ["CRM-1001"],
        "calendar_ids": ["CAL-A1"],
    }
    kwargs.update(overrides)
    return UnifiedMeeting(**kwargs)


def _summary(**overrides: object) -> SyncRunSummary:
    kwargs: dict = {"generated_at": datetime(2025, 3, 1, 12, 0)}
    kwargs.update(overrides)
    return SyncRunSummary(**kwargs)


# --- the documented contract ---


def test_provenance_field_matches_the_documented_shape() -> None:
    """Doc 02, Decision 4's example, field for field. This block is what the UI was
    designed against, so a rename here is a breaking change and should fail loudly."""
    field = ProvenanceField.resolved(
        value="Zoom - https://zoom.us/j/98765432100",
        source=Source.CALENDAR,
        other_source=Source.CRM,
        other_value="NYC Office - 30th Floor",
        kind=ConflictKind.CONTRADICTION,
    )

    assert field.model_dump(mode="json") == {
        "value": "Zoom - https://zoom.us/j/98765432100",
        "source": "calendar",
        "alternatives": [{"source": "crm", "value": "NYC Office - 30th Floor"}],
        "conflict": True,
        "conflict_kind": "contradiction",
    }


def test_single_source_field_has_provenance_but_no_conflict() -> None:
    field = ProvenanceField.single("HQ - Conference Room B", Source.CRM)

    assert field.source is Source.CRM
    assert field.alternatives == []
    assert field.conflict is False
    assert field.conflict_kind is None


def test_empty_field_is_representable() -> None:
    """Neither source had it — distinct from "one source said null", which is an absence."""
    field = ProvenanceField.empty()

    assert field.value is None
    assert field.source is None
    assert field.conflict is False


# --- only contradictions are conflicts (doc 02, Decision 4) ---


def test_absence_keeps_the_alternative_without_raising_a_conflict() -> None:
    """CRM-1018 has no location, CAL-A21 says Zoom. The present value wins and the badge
    stays down — flagging this would put a conflict on nearly every record."""
    field = ProvenanceField.resolved(
        value="Zoom",
        source=Source.CALENDAR,
        other_source=Source.CRM,
        other_value=None,
        kind=ConflictKind.ABSENCE,
    )

    assert field.conflict is False
    assert field.conflict_kind is ConflictKind.ABSENCE
    assert field.alternatives == [SourceValue(source=Source.CRM, value=None)]


def test_granularity_keeps_the_less_specific_value_as_an_alternative() -> None:
    """CRM-1001 "HQ - Conference Room B" vs CAL-A1 "Conference Room B"."""
    field = ProvenanceField.resolved(
        value="HQ - Conference Room B",
        source=Source.CRM,
        other_source=Source.CALENDAR,
        other_value="Conference Room B",
        kind=ConflictKind.GRANULARITY,
    )

    assert field.conflict is False
    assert field.alternatives[0].value == "Conference Room B"


def test_conflict_without_a_contradiction_is_rejected() -> None:
    with pytest.raises(ValidationError, match="requires conflict_kind=contradiction"):
        ProvenanceField(value="x", source=Source.CRM, conflict=True, conflict_kind=ConflictKind.ABSENCE)


def test_contradiction_without_the_conflict_flag_is_rejected() -> None:
    """The inverse mistake: step 13 sets these in several branches, and getting it right in
    one and wrong in another is exactly what would make the badge untrustworthy."""
    with pytest.raises(ValidationError, match="requires conflict=True"):
        ProvenanceField(
            value="x", source=Source.CRM, conflict=False, conflict_kind=ConflictKind.CONTRADICTION
        )


# --- match evidence ---


def test_evidence_score_is_the_sum_of_contributions() -> None:
    evidence = MatchEvidence(
        score=0.83,
        confidence=MatchConfidence.HIGH,
        signals=[
            MatchSignal(name="participants", weight=0.40, score=1.0),
            MatchSignal(name="time", weight=0.30, score=1.0),
            MatchSignal(name="title", weight=0.20, score=0.55),
            MatchSignal(name="structure", weight=0.10, score=0.20),
        ],
    )

    assert evidence.confidence is MatchConfidence.HIGH
    assert evidence.is_low_confidence is False
    assert [round(signal.contribution, 3) for signal in evidence.signals] == [0.4, 0.3, 0.11, 0.02]


def test_evidence_rejects_arithmetic_that_does_not_add_up() -> None:
    """Doc 02 rejects a black-box matcher because a reviewer checks the pairs by hand. A
    score that doesn't equal its breakdown would make the display decoration."""
    with pytest.raises(ValidationError, match="!= sum of contributions"):
        MatchEvidence(
            score=0.95,
            confidence=MatchConfidence.HIGH,
            signals=[MatchSignal(name="participants", weight=0.40, score=1.0)],
        )


def test_signal_score_outside_zero_to_one_is_rejected() -> None:
    with pytest.raises(ValidationError):
        MatchSignal(name="time", weight=0.30, score=1.5)


def test_low_confidence_is_exposed_for_the_badge() -> None:
    evidence = MatchEvidence(
        score=0.5,
        confidence=MatchConfidence.LOW,
        signals=[MatchSignal(name="time", weight=0.50, score=1.0)],
    )

    assert evidence.is_low_confidence is True


# --- unified meeting ---


def test_origin_must_agree_with_the_populated_ids() -> None:
    with pytest.raises(ValidationError, match="disagrees with"):
        UnifiedMeeting(id="m-1", origin=Origin.BOTH, crm_ids=[], calendar_ids=["CAL-A1"])


def test_crm_only_meeting_rejects_calendar_ids() -> None:
    with pytest.raises(ValidationError, match="disagrees with"):
        UnifiedMeeting(
            id="m-1", origin=Origin.CRM_ONLY, crm_ids=["CRM-1003"], calendar_ids=["CAL-A1"]
        )


def test_calendar_only_meeting_is_first_class() -> None:
    """CAL-A19: client time not being logged. Doc 02, Decision 5 — these are the most
    valuable output of the exercise, not an error bucket."""
    meeting = UnifiedMeeting(id="m-19", origin=Origin.CALENDAR_ONLY, calendar_ids=["CAL-A19"])

    assert meeting.crm_ids == []
    assert meeting.match_evidence is None
    assert meeting.has_conflicts is False


def test_a_meeting_can_carry_two_calendar_records() -> None:
    """CAL-A5 + CAL-A6 collapse into one meeting that keeps both ids and both raw records."""
    meeting = _meeting(
        calendar_ids=["CAL-A5", "CAL-A6"],
        raw_calendar=[{"event_id": "CAL-A5"}, {"event_id": "CAL-A6"}],
        raw_crm=[{"crm_id": "CRM-1005"}],
    )

    assert meeting.source_ids == ["CRM-1001", "CAL-A5", "CAL-A6"]
    assert len(meeting.raw_calendar) == 2


def test_provenance_fields_are_discoverable_without_a_hard_coded_list() -> None:
    """The templates iterate this, so a field added in step 13 shows up in the UI without
    touching the template."""
    meeting = _meeting()

    names = set(meeting.provenance_fields)

    assert names == {
        "title",
        "start_time",
        "end_time",
        "location",
        "participants",
        "client_name",
        "client_company",
        "owner_name",
        "meeting_type",
        "notes",
        "status",
    }


def test_conflicting_fields_lists_only_contradictions() -> None:
    meeting = _meeting(
        location=ProvenanceField.resolved(
            value="Zoom",
            source=Source.CALENDAR,
            other_source=Source.CRM,
            other_value="NYC Office - 30th Floor",
            kind=ConflictKind.CONTRADICTION,
        ),
        title=ProvenanceField.resolved(
            value="HQ - Conference Room B",
            source=Source.CRM,
            other_source=Source.CALENDAR,
            other_value="Conference Room B",
            kind=ConflictKind.GRANULARITY,
        ),
    )

    assert meeting.conflicting_fields == ["location"]
    assert meeting.has_conflicts is True


def test_meeting_carries_flags_from_both_sides() -> None:
    meeting = _meeting(
        flags=[
            DataQualityFlag.of(FlagCode.MALFORMED_DATE, field="meeting_date", raw_value="03-15/2025"),
            DataQualityFlag.of(FlagCode.TIMEZONE_ASSUMED, field="start_time"),
        ]
    )

    assert {flag.code for flag in meeting.flags} == {
        FlagCode.MALFORMED_DATE,
        FlagCode.TIMEZONE_ASSUMED,
    }


# --- sync result: the store ---


def test_sync_result_orders_meetings_by_its_index() -> None:
    first = _meeting(id="m-1", event_date=date(2025, 3, 10))
    second = _meeting(id="m-2", event_date=date(2025, 3, 11))

    result = SyncResult(
        meetings={"m-2": second, "m-1": first},
        by_date=["m-1", "m-2"],
        summary=_summary(meetings_out=2),
    )

    assert [meeting.id for meeting in result.ordered_meetings] == ["m-1", "m-2"]


def test_sync_result_rejects_a_meeting_missing_from_by_date() -> None:
    """Otherwise the meeting is reachable by URL but never appears in the list — a bug that
    reads as a UI problem for an hour."""
    with pytest.raises(ValidationError, match="must permute meetings"):
        SyncResult(
            meetings={"m-1": _meeting(id="m-1"), "m-2": _meeting(id="m-2")},
            by_date=["m-1"],
            summary=_summary(),
        )


def test_sync_result_rejects_an_unknown_id_in_by_date() -> None:
    with pytest.raises(ValidationError, match="must permute meetings"):
        SyncResult(meetings={"m-1": _meeting(id="m-1")}, by_date=["m-1", "ghost"], summary=_summary())


def test_sync_result_rejects_duplicate_ids_in_by_date() -> None:
    with pytest.raises(ValidationError):
        SyncResult(meetings={"m-1": _meeting(id="m-1")}, by_date=["m-1", "m-1"], summary=_summary())


def test_sync_result_is_frozen() -> None:
    """The atomic-swap guarantee: a re-sync rebinds one reference rather than mutating a
    result somebody is mid-read of."""
    result = SyncResult(meetings={}, by_date=[], summary=_summary())

    with pytest.raises(ValidationError):
        result.by_date = ["m-1"]


def test_summary_totals_records_in() -> None:
    summary = _summary(crm_records_in=20, calendar_records_in=22)

    assert summary.records_in == 42


def test_summary_holds_the_stats_page_breakdowns() -> None:
    summary = _summary(
        meetings_out=24,
        matched_pairs=17,
        crm_only=3,
        calendar_only=4,
        duplicates_collapsed=1,
        conflicts_by_kind={"contradiction": 3, "absence": 1},
        flags_by_code={"MALFORMED_DATE": 1},
        flags_by_severity={"error": 1},
    )

    assert summary.meetings_out == 24
    assert summary.conflicts_by_kind["contradiction"] == 3
    assert summary.flags_by_severity["error"] == 1
