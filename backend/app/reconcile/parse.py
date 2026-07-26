"""Parsing primitives — one raw string in, one typed value plus the story of how it parsed.

Doc 02, Decision 1: normalization is non-destructive. Nothing here raises and nothing guesses
silently; a parser returns what it managed to produce and the code describing how. The
normalizers in steps 8-9 attach the field name and raw value, because this module has no idea
whether a string came from `meeting_date` or `start_time`.
"""

from datetime import date, datetime, time
from typing import Any, NamedTuple
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.models.normalized import FlagCode, MeetingStatus


class Parsed(NamedTuple):
    """A parsed value and the flag code earned along the way (None when clean)."""

    value: Any | None
    code: FlagCode | None = None


# Fallbacks tried only after `date.fromisoformat` fails, so a clean ISO date never earns a
# flag. Deliberately short and all month-first: adding a day-first pattern would make
# "03-15/2025" ambiguous, and doc 01 resolves it as 2025-03-15 from its counterpart CAL-A9.
_DATE_PATTERNS = ("%m-%d/%Y", "%m/%d/%Y")

_TIME_PATTERNS = ("%H:%M:%S", "%H:%M")

# Tried in order after `fromisoformat`. The first catches CAL-A11's missing seconds; Python
# 3.11's fromisoformat already handles both that and the Z suffix, so these are a safety net
# for shapes it rejects rather than the primary path.
_DATETIME_PATTERNS = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M")

_STATUS_BY_RAW = {
    "scheduled": MeetingStatus.SCHEDULED,
    "confirmed": MeetingStatus.CONFIRMED,
    "tentative": MeetingStatus.TENTATIVE,
    "completed": MeetingStatus.COMPLETED,
    "cancelled": MeetingStatus.CANCELLED,
    "canceled": MeetingStatus.CANCELLED,
}


def local_timezone() -> ZoneInfo:
    """The one timezone everything is coerced to. Read from settings, not hard-coded: the
    dataset straddles the 2025-03-09 DST change, so a fixed offset would be right for the
    March events and wrong for the January/February `created_at` values."""
    return ZoneInfo(get_settings().timezone)


def parse_date(raw: object) -> Parsed:
    """ISO first, then a small ordered list of tolerated patterns.

    Never invents a date: input no pattern matches comes back as None with UNPARSABLE_DATE,
    and the record continues without one.
    """
    text = _clean(raw)
    if text is None:
        return Parsed(None, FlagCode.UNPARSABLE_DATE)

    try:
        return Parsed(date.fromisoformat(text))
    except ValueError:
        pass

    for pattern in _DATE_PATTERNS:
        try:
            return Parsed(datetime.strptime(text, pattern).date(), FlagCode.MALFORMED_DATE)
        except ValueError:
            continue

    return Parsed(None, FlagCode.UNPARSABLE_DATE)


def parse_time(raw: object) -> Parsed:
    """A missing time is a gap, not corruption — CRM-1007 still matches on its date alone."""
    text = _clean(raw)
    if text is None:
        return Parsed(None, FlagCode.TIME_MISSING)

    for pattern in _TIME_PATTERNS:
        try:
            return Parsed(datetime.strptime(text, pattern).time())
        except ValueError:
            continue

    return Parsed(None, FlagCode.TIME_MISSING)


def parse_datetime(raw: object) -> Parsed:
    """Lenient ISO parse, always returning an Eastern-aware datetime.

    Emits TIMEZONE_ASSUMED when the input carried no offset (the assumption made visible),
    or MALFORMED_DATETIME when a component was missing.
    """
    text = _clean(raw)
    if text is None:
        return Parsed(None, FlagCode.MALFORMED_DATETIME)

    malformed = False
    parsed: datetime | None = None

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        # Everything else in the files carries seconds; CAL-A11 does not.
        malformed = _is_missing_seconds(text)
    except ValueError:
        for pattern in _DATETIME_PATTERNS:
            try:
                parsed = datetime.strptime(text, pattern)
                malformed = True
                break
            except ValueError:
                continue

    if parsed is None:
        return Parsed(None, FlagCode.MALFORMED_DATETIME)

    localized = to_eastern(parsed)
    if malformed:
        # A malformed timestamp that was also naive earns the more serious of the two codes;
        # the timezone assumption still applies but the shape problem is what a reader needs.
        return Parsed(localized.value, FlagCode.MALFORMED_DATETIME)
    return Parsed(localized.value, localized.code)


def to_eastern(value: datetime | None) -> Parsed:
    """Naive timestamps are *assumed* Eastern and flagged; aware ones are converted silently.

    Doc 01, section D: CAL-A4 is the only Z-suffixed event timestamp in either file, and
    taking it literally would place it five hours from CRM-1004 and break the match.
    """
    if value is None:
        return Parsed(None)

    tz = local_timezone()
    if value.tzinfo is None:
        return Parsed(value.replace(tzinfo=tz), FlagCode.TIMEZONE_ASSUMED)
    return Parsed(value.astimezone(tz))


def combine(day: date | None, moment: time | None) -> Parsed:
    """Build an Eastern-aware datetime from a separately-parsed date and time.

    Returns None when either half is missing — a date-only event keeps its `event_date` and
    simply has no `start`, which is how CRM-1007 stays matchable on the date signal.
    """
    if day is None or moment is None:
        return Parsed(None)

    return Parsed(datetime.combine(day, moment, tzinfo=local_timezone()))


def repair_email(raw: object) -> Parsed:
    """Repair obfuscated addresses; keep unresolvable labels as labels.

    `"external-guests"` (CAL-A20) is not an error — "outsiders attended" is real information.
    It comes back as None with NON_EMAIL_ATTENDEE so the caller keeps the raw string.
    """
    text = _clean(raw)
    if text is None:
        return Parsed(None, FlagCode.NON_EMAIL_ATTENDEE)

    repaired = text.replace("[at]", "@").replace("[dot]", ".")
    was_repaired = repaired != text

    if "@" not in repaired:
        return Parsed(None, FlagCode.NON_EMAIL_ATTENDEE)

    return Parsed(repaired.lower(), FlagCode.MALFORMED_EMAIL if was_repaired else None)


def normalize_status(raw: object) -> Parsed:
    """Map both source vocabularies onto one enum (doc 02, Decision 1).

    An unrecognised value degrades to UNKNOWN with a flag rather than raising — normalization
    must never be the thing that fails a sync.
    """
    text = _clean(raw)
    if text is None:
        return Parsed(MeetingStatus.UNKNOWN, FlagCode.UNKNOWN_STATUS)

    status = _STATUS_BY_RAW.get(text.lower())
    if status is None:
        return Parsed(MeetingStatus.UNKNOWN, FlagCode.UNKNOWN_STATUS)

    return Parsed(status)


def _clean(raw: object) -> str | None:
    """Normalize the many spellings of "absent" into one. Empty strings count: CRM-1010's
    notes are "" rather than null, and the two mean the same thing to a parser."""
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _is_missing_seconds(text: str) -> bool:
    """`2025-03-14T20:00` against every other record's `...T20:00:00`."""
    time_part = text.split("T")[-1] if "T" in text else ""
    time_part = time_part.split("+")[0].split("Z")[0]
    return time_part.count(":") == 1
