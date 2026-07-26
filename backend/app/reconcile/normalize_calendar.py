"""Calendar source adapter → `NormalizedEvent`.

Same contract as the CRM normalizer (step 8): 22 records in, 22 out, every defect flagged
rather than raised. The differences are all in the source's shape — emails instead of names,
a real end time, and the one record in either file that states its own timezone.
"""

from app.models.normalized import (
    FlagCode,
    NormalizedEvent,
    Participant,
    Source,
)
from app.reconcile.parse import Parsed, normalize_status, parse_datetime, repair_email


def normalize_calendar_records(records: list[dict]) -> list[NormalizedEvent]:
    """Normalize every calendar record, preserving input order."""
    return [normalize_calendar_record(record) for record in records]


def normalize_calendar_record(record: dict) -> NormalizedEvent:
    """One raw calendar dict → one event."""
    event = NormalizedEvent(
        source=Source.CALENDAR,
        source_ids=[str(record.get("event_id", "")) or "CAL-UNKNOWN"],
        raw=record,
    )

    start = parse_datetime(record.get("start_time"))
    end = parse_datetime(record.get("end_time"))
    event.start = start.value
    event.end = end.value
    if event.start is not None:
        event.event_date = event.start.date()

    _flag_timestamps(event, start, end)

    event.title = _text(record.get("title"))
    event.text = _text(record.get("description"))
    event.location = _text(record.get("location"))
    event.organizer = _text(record.get("organizer"))
    event.is_recurring = bool(record.get("is_recurring", False))

    status = normalize_status(record.get("status"))
    if status.code is not None:
        event.add_flag(status.code, field="status", raw_value=record.get("status"))
    event.status = status.value
    event.status_raw = _text(record.get("status"))

    event.created_at = parse_datetime(record.get("created_at")).value

    event.participants = _participants(event, record)

    # client_name, client_company, owner_name and meeting_type stay None: the calendar has
    # no such fields, and guessing them from a domain would put invented values where step
    # 13's precedence rules expect a genuine absence.
    return event


def _flag_timestamps(event: NormalizedEvent, start: Parsed, end: Parsed) -> None:
    """One timezone flag per record, not per timestamp.

    Most records have both a start and an end, and flagging each would report 42 timezone
    assumptions across 21 records — a stats page overstating the problem by 2x. Shape
    problems (`MALFORMED_DATETIME`) *are* per-field, since they identify a specific value.
    """
    assumed = FlagCode.TIMEZONE_ASSUMED
    for parsed, field in ((start, "start_time"), (end, "end_time")):
        if parsed.code is None or parsed.code is assumed:
            continue
        event.add_flag(parsed.code, field=field, raw_value=event.raw.get(field))

    if assumed in (start.code, end.code):
        event.add_flag(assumed, field="start_time", raw_value=event.raw.get("start_time"))


def _participants(event: NormalizedEvent, record: dict) -> list[Participant]:
    """Build from `attendees`, marking the organizer — who is also always in that list.

    Appending the organizer unconditionally would duplicate them on all 21 records that
    have attendees; never appending would lose them on CAL-A11, which has an organizer and
    no attendees at all.
    """
    organizer_email = (event.organizer or "").strip().lower()
    participants: list[Participant] = []
    seen: set[str] = set()

    for attendee in record.get("attendees") or []:
        parsed = repair_email(attendee)
        raw = str(attendee)

        if parsed.code is not None:
            event.add_flag(parsed.code, field="attendees", raw_value=raw)

        email = parsed.value
        key = email or raw.strip().lower()
        if key in seen:
            continue
        seen.add(key)

        participants.append(
            Participant(
                email=email,
                display=email or raw,
                raw=raw,
                is_organizer=bool(email) and email == organizer_email,
            )
        )

    if organizer_email and organizer_email not in seen:
        participants.append(
            Participant(
                email=organizer_email,
                display=organizer_email,
                raw=event.organizer or organizer_email,
                is_organizer=True,
            )
        )

    return participants


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
