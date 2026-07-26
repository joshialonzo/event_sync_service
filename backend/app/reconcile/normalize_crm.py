"""CRM source adapter → `NormalizedEvent`.

Doc 02, Decision 1: every raw record becomes an event no matter how malformed. Parse
failures attach flags; nothing raises and nothing is dropped, so 20 records in is always 20
records out.
"""

from app.models.normalized import (
    FlagCode,
    NormalizedEvent,
    Participant,
    Source,
)
from app.reconcile.parse import (
    Parsed,
    combine,
    normalize_status,
    parse_date,
    parse_datetime,
    parse_time,
)

INTERNAL_MEETING_TYPE = "Internal"

# `CRM-1017`'s client is literally "Multiple" — a dinner with several clients, which the CRM
# had no field for. Marked so step 11 does not derive an email local-part from it and score a
# fabricated participant signal.
_PLACEHOLDER_CLIENTS = {"multiple", "various", "n/a", "tbd", "unknown"}


def normalize_crm_records(records: list[dict]) -> list[NormalizedEvent]:
    """Normalize every CRM record, preserving input order."""
    return [normalize_crm_record(record) for record in records]


def normalize_crm_record(record: dict) -> NormalizedEvent:
    """One raw CRM dict → one event, with every defect recorded rather than raised."""
    event = NormalizedEvent(
        source=Source.CRM,
        source_ids=[str(record.get("crm_id", "")) or "CRM-UNKNOWN"],
        raw=record,
    )

    day = parse_date(record.get("meeting_date"))
    _apply_when(event, "meeting_date", day)
    event.event_date = day.value

    moment = parse_time(record.get("meeting_time"))
    _apply_when(event, "meeting_time", moment)

    start = combine(day.value, moment.value)
    if start.value is not None:
        event.start = start.value
        # The CRM carries no timezone at all, so building an Eastern timestamp is the same
        # assumption made for naive calendar records — and is flagged identically, or the
        # stats page would undercount it.
        event.add_flag(
            FlagCode.TIMEZONE_ASSUMED,
            field="meeting_time",
            raw_value=record.get("meeting_time"),
        )

    event.title = _text(record.get("subject"))
    event.text = _text(record.get("notes"))
    event.location = _text(record.get("location"))
    event.meeting_type = _text(record.get("meeting_type"))

    event.client_name = _text(record.get("client_name"))
    event.client_company = _text(record.get("client_company"))
    event.owner_name = _text(record.get("relationship_owner"))
    event.organizer = event.owner_name

    status = normalize_status(record.get("status"))
    _apply_when(event, "status", status)
    event.status = status.value
    event.status_raw = _text(record.get("status"))

    created = parse_datetime(record.get("created_at"))
    event.created_at = created.value

    event.participants = _participants(event)
    _flag_client_gaps(event, record)

    return event


def _participants(event: NormalizedEvent) -> list[Participant]:
    """The CRM has names, not addresses.

    `email` stays None for both participants: inventing `david.park@meridiancap.com` here
    would fabricate data that then looks like evidence when step 11 scores it. Bridging
    names to addresses is the matcher's job, and it does it explicitly.
    """
    participants: list[Participant] = []

    if event.owner_name:
        participants.append(
            Participant(display=event.owner_name, raw=event.owner_name, is_organizer=True)
        )

    if event.client_name:
        participants.append(Participant(display=event.client_name, raw=event.client_name))

    return participants


def _flag_client_gaps(event: NormalizedEvent, record: dict) -> None:
    """A null client is only newsworthy when the meeting is not internal.

    Doc 02: an internal meeting legitimately has no client, so it is `info`. Treating it as
    a defect would put four false problems on the stats page and make CRM-1008's genuinely
    corrupt date look routine.
    """
    if event.client_name is None:
        if event.meeting_type == INTERNAL_MEETING_TYPE:
            event.add_flag(FlagCode.INTERNAL_NO_CLIENT, field="client_name")
        return

    if event.client_name.strip().lower() in _PLACEHOLDER_CLIENTS:
        event.add_flag(
            FlagCode.PLACEHOLDER_CLIENT,
            field="client_name",
            raw_value=record.get("client_name"),
        )


def _apply_when(event: NormalizedEvent, field: str, parsed: Parsed) -> None:
    """Attach the parser's code, if it earned one. `parse.py` knows how a value parsed;
    only the normalizer knows which field it came from."""
    if parsed.code is not None:
        event.add_flag(parsed.code, field=field, raw_value=event.raw.get(field))


def _text(value: object) -> str | None:
    """Collapse the spellings of "absent" into one. CRM-1010's notes are `""`, which means
    the same thing as null and should not reach the UI as an empty box."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None
