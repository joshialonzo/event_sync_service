"""The shape both sources normalize into.

Doc 02, Decision 1: normalization is non-destructive. Every raw record becomes a
`NormalizedEvent` no matter how malformed, with parse failures recorded as flags rather than
raised. That is why almost everything here is optional — the model has to be able to hold the
worst record in the dataset, not just the well-formed ones.
"""

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator


class Source(str, Enum):
    """Which upstream system a record came from."""

    CRM = "crm"
    CALENDAR = "calendar"


class Severity(str, Enum):
    """How much a data-quality flag should worry the reader.

    Without these, an internal meeting with no client (legitimate) looks as broken as a
    corrupt date, and the data-quality count on the stats page stops meaning anything.
    """

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class MeetingStatus(str, Enum):
    """Both source vocabularies mapped onto one enum (doc 02, Decision 1).

    `UNKNOWN` exists so an unrecognised status degrades instead of raising. It should never
    fire on this dataset — step 4's tests pin both vocabularies — but normalization must
    never be the thing that crashes a sync.
    """

    SCHEDULED = "scheduled"
    CONFIRMED = "confirmed"
    TENTATIVE = "tentative"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class FlagCode(str, Enum):
    """The defects the pipeline can report, one code per kind."""

    MALFORMED_DATE = "MALFORMED_DATE"
    MALFORMED_DATETIME = "MALFORMED_DATETIME"
    UNPARSABLE_DATE = "UNPARSABLE_DATE"
    TIME_MISSING = "TIME_MISSING"
    TIMEZONE_ASSUMED = "TIMEZONE_ASSUMED"
    MALFORMED_EMAIL = "MALFORMED_EMAIL"
    NON_EMAIL_ATTENDEE = "NON_EMAIL_ATTENDEE"
    INTERNAL_NO_CLIENT = "INTERNAL_NO_CLIENT"
    PLACEHOLDER_CLIENT = "PLACEHOLDER_CLIENT"
    DUPLICATE_COLLAPSED = "DUPLICATE_COLLAPSED"
    UNKNOWN_STATUS = "UNKNOWN_STATUS"

    @property
    def severity(self) -> Severity:
        """Severity is a property of the code, not of the call site.

        If each normalizer chose its own, the same defect would be reported at two
        severities by the two sources and the stats page would contradict itself.
        """
        return _SEVERITY_BY_CODE[self]

    @property
    def description(self) -> str:
        return _DESCRIPTION_BY_CODE[self]


_SEVERITY_BY_CODE: dict[FlagCode, Severity] = {
    FlagCode.MALFORMED_DATE: Severity.ERROR,
    FlagCode.MALFORMED_DATETIME: Severity.WARNING,
    FlagCode.UNPARSABLE_DATE: Severity.ERROR,
    FlagCode.TIME_MISSING: Severity.WARNING,
    FlagCode.TIMEZONE_ASSUMED: Severity.INFO,
    FlagCode.MALFORMED_EMAIL: Severity.WARNING,
    FlagCode.NON_EMAIL_ATTENDEE: Severity.INFO,
    FlagCode.INTERNAL_NO_CLIENT: Severity.INFO,
    FlagCode.PLACEHOLDER_CLIENT: Severity.INFO,
    FlagCode.DUPLICATE_COLLAPSED: Severity.INFO,
    FlagCode.UNKNOWN_STATUS: Severity.WARNING,
}

_DESCRIPTION_BY_CODE: dict[FlagCode, str] = {
    FlagCode.MALFORMED_DATE: "Date required a fallback pattern to parse",
    FlagCode.MALFORMED_DATETIME: "Timestamp was missing a component",
    FlagCode.UNPARSABLE_DATE: "Date could not be parsed by any known pattern",
    FlagCode.TIME_MISSING: "No time of day was supplied",
    FlagCode.TIMEZONE_ASSUMED: "Naive timestamp assumed to be Eastern",
    FlagCode.MALFORMED_EMAIL: "Email address required repair",
    FlagCode.NON_EMAIL_ATTENDEE: "Attendee is not an email address",
    FlagCode.INTERNAL_NO_CLIENT: "Internal meeting with no client (expected)",
    FlagCode.PLACEHOLDER_CLIENT: "Client field holds a placeholder, not a single client",
    FlagCode.DUPLICATE_COLLAPSED: "Entered twice in the source; records were merged",
    FlagCode.UNKNOWN_STATUS: "Status is outside both source vocabularies",
}


class DataQualityFlag(BaseModel):
    """A defect found while normalizing, carried on the record it came from."""

    code: FlagCode
    field: str
    raw_value: str | None = None
    severity: Severity
    message: str

    @classmethod
    def of(
        cls,
        code: FlagCode,
        field: str,
        raw_value: object = None,
        message: str | None = None,
    ) -> "DataQualityFlag":
        """Build a flag with the code's own severity and default message."""
        return cls(
            code=code,
            field=field,
            raw_value=None if raw_value is None else str(raw_value),
            severity=code.severity,
            message=message or code.description,
        )


class Participant(BaseModel):
    """A person on a meeting, or an unresolvable label like "external-guests".

    `email` is None for labels the system cannot resolve to a person. That is information
    ("outsiders attended"), not an error, so it is kept rather than dropped.
    """

    email: str | None = None
    display: str
    domain: str | None = None
    is_organizer: bool = False
    raw: str

    @model_validator(mode="after")
    def _derive_from_email(self) -> "Participant":
        """`domain` is derived rather than passed in, so it cannot disagree with `email`."""
        if self.domain is None and self.email and "@" in self.email:
            self.domain = self.email.rsplit("@", 1)[1].lower()
        return self


class NormalizedEvent(BaseModel):
    """One meeting as a single source describes it.

    Only `source`, `source_ids`, and `raw` are required: a record that failed every parse
    still has to be representable, or the pipeline could not report it.
    """

    source: Source
    source_ids: list[str] = Field(min_length=1)
    """A list from the start — step 10 collapses CAL-A5 and CAL-A6 into one event that
    carries both ids."""

    start: datetime | None = None
    end: datetime | None = None
    event_date: date | None = None
    """Separate from `start` because CRM-1007 has a date and no time. Named `event_date`
    to avoid shadowing `datetime.date` in this module."""

    title: str | None = None
    text: str | None = None
    location: str | None = None

    participants: list[Participant] = Field(default_factory=list)
    organizer: str | None = None
    owner_name: str | None = None
    client_name: str | None = None
    client_company: str | None = None
    meeting_type: str | None = None

    status: MeetingStatus = MeetingStatus.UNKNOWN
    status_raw: str | None = None
    is_recurring: bool = False
    created_at: datetime | None = None

    flags: list[DataQualityFlag] = Field(default_factory=list)
    raw: dict

    duplicates: list[dict] = Field(default_factory=list)
    """Raw records of same-source duplicates collapsed into this one (step 10).

    Kept so the detail view can show every record the source actually held. `raw` remains
    the canonical survivor's own record.
    """

    @model_validator(mode="after")
    def _date_agrees_with_start(self) -> "NormalizedEvent":
        """Keep `event_date` and `start` from disagreeing.

        Blocking in step 12 reads `event_date` while scoring reads `start`; if a normalizer
        set one and forgot the other, an event would land on two different days depending on
        which field the consumer happened to use.
        """
        if self.start is not None:
            derived = self.start.date()
            if self.event_date is None:
                self.event_date = derived
            elif self.event_date != derived:
                raise ValueError(
                    f"event_date {self.event_date} disagrees with start {self.start.isoformat()}"
                )
        return self

    @property
    def has_time(self) -> bool:
        """Derived, not stored, so it cannot drift from `start`."""
        return self.start is not None

    @property
    def primary_id(self) -> str:
        """The canonical id — the survivor's own id after a dedupe merge."""
        return self.source_ids[0]

    @property
    def flag_codes(self) -> set[FlagCode]:
        return {flag.code for flag in self.flags}

    @property
    def raw_records(self) -> list[dict]:
        """Every source record behind this event — the survivor's, then any duplicates'."""
        return [self.raw, *self.duplicates]

    def add_flag(
        self,
        code: FlagCode,
        field: str,
        raw_value: object = None,
        message: str | None = None,
    ) -> None:
        """Convenience for the normalizers in steps 8-9."""
        self.flags.append(DataQualityFlag.of(code, field, raw_value, message))
