"""Tests for the normalized models (step 5).

The models carry no logic beyond their invariants, so these tests are about exactly that:
what the model refuses to represent, and what it derives so a normalizer cannot get it wrong.
"""

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from app.models.normalized import (
    DataQualityFlag,
    FlagCode,
    MeetingStatus,
    NormalizedEvent,
    Participant,
    Severity,
    Source,
)


def _sparse(**overrides: object) -> NormalizedEvent:
    """The minimum viable event — a record where every parse failed."""
    kwargs: dict = {
        "source": Source.CRM,
        "source_ids": ["CRM-1001"],
        "raw": {"crm_id": "CRM-1001"},
    }
    kwargs.update(overrides)
    return NormalizedEvent(**kwargs)


# --- the model must hold the worst record in the dataset ---


def test_a_maximally_sparse_event_constructs() -> None:
    """Doc 02 Decision 1: nothing is dropped, so a record that failed every parse still has
    to be representable. If this needs more arguments, the defaults are too strict."""
    event = _sparse()

    assert event.status is MeetingStatus.UNKNOWN
    assert event.has_time is False
    assert event.flags == []
    assert event.participants == []
    assert event.start is None
    assert event.event_date is None


def test_primary_id_is_the_first_source_id() -> None:
    assert _sparse(source_ids=["CAL-A5", "CAL-A6"]).primary_id == "CAL-A5"


def test_raw_is_required() -> None:
    """The UI's "what did the CRM actually say?" panel is impossible without it."""
    with pytest.raises(ValidationError):
        NormalizedEvent(source=Source.CRM, source_ids=["CRM-1001"])


def test_source_ids_cannot_be_empty() -> None:
    """An event with no id cannot be matched, merged, or linked to from the UI."""
    with pytest.raises(ValidationError):
        NormalizedEvent(source=Source.CRM, source_ids=[], raw={})


def test_raw_round_trips_through_json_unchanged() -> None:
    raw = {"crm_id": "CRM-1008", "meeting_date": "03-15/2025", "notes": None}

    restored = NormalizedEvent.model_validate_json(_sparse(raw=raw).model_dump_json())

    assert restored.raw == raw


# --- the event_date / start invariant ---


def test_event_date_is_derived_from_start() -> None:
    event = _sparse(start=datetime(2025, 3, 10, 14, 0))

    assert event.event_date == date(2025, 3, 10)
    assert event.has_time is True


def test_event_date_survives_without_a_start() -> None:
    """CRM-1007: a date and no time. It still participates in matching, on the date alone."""
    event = _sparse(event_date=date(2025, 3, 19))

    assert event.event_date == date(2025, 3, 19)
    assert event.has_time is False


def test_event_date_disagreeing_with_start_is_rejected() -> None:
    """Step 12 blocks on event_date but scores on start. A normalizer that set one and
    forgot the other would put the event on two different days at once."""
    with pytest.raises(ValidationError, match="disagrees with start"):
        _sparse(start=datetime(2025, 3, 10, 14, 0), event_date=date(2025, 3, 11))


def test_a_tz_aware_start_derives_its_own_local_date() -> None:
    event = _sparse(start=datetime(2025, 3, 13, 19, 0, tzinfo=timezone.utc))

    assert event.event_date == date(2025, 3, 13)


# --- flags ---


def test_flag_severity_comes_from_the_code() -> None:
    """Severity belongs to the code so the two normalizers cannot report the same defect
    at different severities."""
    assert FlagCode.MALFORMED_DATE.severity is Severity.ERROR
    assert FlagCode.INTERNAL_NO_CLIENT.severity is Severity.INFO
    assert FlagCode.TIME_MISSING.severity is Severity.WARNING


def test_every_flag_code_has_a_severity_and_description() -> None:
    """Guards against a code being added later without either — it would otherwise raise
    only when that specific defect first occurs, which may be never in testing."""
    for code in FlagCode:
        assert isinstance(code.severity, Severity)
        assert code.description


def test_flag_of_fills_in_severity_and_message() -> None:
    flag = DataQualityFlag.of(
        FlagCode.MALFORMED_DATE, field="meeting_date", raw_value="03-15/2025"
    )

    assert flag.severity is Severity.ERROR
    assert flag.raw_value == "03-15/2025"
    assert flag.message


def test_flag_stringifies_a_non_string_raw_value() -> None:
    """Raw values arrive as whatever JSON produced — the flag stores them for display."""
    assert DataQualityFlag.of(FlagCode.TIME_MISSING, field="meeting_time", raw_value=None).raw_value is None
    assert DataQualityFlag.of(FlagCode.MALFORMED_DATETIME, field="end_time", raw_value=42).raw_value == "42"


def test_add_flag_accumulates_on_the_event() -> None:
    event = _sparse()

    event.add_flag(FlagCode.TIME_MISSING, field="meeting_time")
    event.add_flag(FlagCode.INTERNAL_NO_CLIENT, field="client_name")

    assert event.flag_codes == {FlagCode.TIME_MISSING, FlagCode.INTERNAL_NO_CLIENT}
    assert [flag.severity for flag in event.flags] == [Severity.WARNING, Severity.INFO]


# --- participants ---


def test_participant_derives_its_domain() -> None:
    """The domain is the company signal step 11 scores on, so it must track the email."""
    participant = Participant(email="david.park@meridiancap.com", display="David Park", raw="x")

    assert participant.domain == "meridiancap.com"


def test_participant_domain_is_lowercased() -> None:
    participant = Participant(email="A.B@MeridianCap.COM", display="A B", raw="A.B@MeridianCap.COM")

    assert participant.domain == "meridiancap.com"


def test_participant_accepts_an_unresolvable_label() -> None:
    """CAL-A20's "external-guests" — not a person, but the fact that outsiders attended is
    real information and must survive."""
    participant = Participant(email=None, display="external-guests", raw="external-guests")

    assert participant.email is None
    assert participant.domain is None
    assert participant.raw == "external-guests"


def test_participant_keeps_the_raw_string() -> None:
    """CAL-A16's obfuscated address: the repaired email and the original both matter — one
    for matching, the other for the data-quality panel."""
    participant = Participant(
        email="raj.patel@atlasvc.com",
        display="raj.patel@atlasvc.com",
        raw="raj.patel[at]atlasvc.com",
    )

    assert participant.domain == "atlasvc.com"
    assert participant.raw == "raj.patel[at]atlasvc.com"


# --- enums serialize readably ---


def test_enums_serialize_as_plain_strings() -> None:
    """So JSON responses and templates can compare against strings without a custom
    encoder."""
    event = _sparse(status=MeetingStatus.CANCELLED)

    payload = event.model_dump(mode="json")

    assert payload["source"] == "crm"
    assert payload["status"] == "cancelled"


def test_status_vocabulary_covers_both_sources() -> None:
    """The five CRM values and two calendar values from step 4's pinned vocabularies."""
    values = {status.value for status in MeetingStatus}

    assert values == {
        "scheduled",
        "confirmed",
        "tentative",
        "completed",
        "cancelled",
        "unknown",
    }
