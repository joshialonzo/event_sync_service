"""The pipeline's output shape.

Doc 02, Decision 4: every field a user reads carries its own provenance, so "where did this
come from?" and "where do the sources disagree?" are properties of the data rather than of
the UI. Doc 03: one immutable `SyncResult` per run *is* the store.

No merge logic lives here. Step 13 decides which source wins; this module defines what a
decision looks like once it has been made.
"""

from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, computed_field, model_validator

from app.models.normalized import DataQualityFlag, Severity, Source

# Signal weights are only compared against this when checking the arithmetic; floating
# point sums of four products do not land exactly.
_SCORE_TOLERANCE = 1e-6


class ConflictKind(str, Enum):
    """The three kinds of disagreement (doc 02, Decision 4).

    Collapsing them would be the easy mistake: if absence and granularity also raised the
    conflict badge, nearly every record would show one and the badge would carry no
    information.
    """

    CONTRADICTION = "contradiction"
    """Both sources have values and they are incompatible. The only kind that is a conflict."""

    ABSENCE = "absence"
    """One source has a value, the other is null."""

    GRANULARITY = "granularity"
    """Compatible values at different specificity: "Conference Room B" in "HQ - Conference Room B"."""


class Origin(str, Enum):
    """Which sources contributed (doc 02, Decision 5 — unmatched records are first-class)."""

    BOTH = "both"
    CRM_ONLY = "crm_only"
    CALENDAR_ONLY = "calendar_only"


class MatchConfidence(str, Enum):
    HIGH = "high"
    """>= 0.70 — auto-matched."""

    LOW = "low"
    """0.45-0.70 — merged, but badged in the UI."""


class SourceValue(BaseModel):
    """What one source said, kept as an alternative when another source won."""

    source: Source
    value: Any = None


class ProvenanceField(BaseModel):
    """One field of a unified meeting, with its origin and any disagreement preserved."""

    value: Any = None
    source: Source | None = None
    alternatives: list[SourceValue] = Field(default_factory=list)
    conflict: bool = False
    conflict_kind: ConflictKind | None = None

    @model_validator(mode="after")
    def _only_contradictions_are_conflicts(self) -> "ProvenanceField":
        """Doc 02 is explicit that only a contradiction raises the badge.

        Enforced here rather than trusted to step 13, which sets these flags in several
        branches — getting it right in one and wrong in another is exactly the bug that
        would make the badge untrustworthy without failing any obvious test.
        """
        if self.conflict and self.conflict_kind is not ConflictKind.CONTRADICTION:
            raise ValueError(
                f"conflict=True requires conflict_kind=contradiction, got {self.conflict_kind}"
            )
        if not self.conflict and self.conflict_kind is ConflictKind.CONTRADICTION:
            raise ValueError("conflict_kind=contradiction requires conflict=True")
        return self

    @classmethod
    def empty(cls) -> "ProvenanceField":
        """Neither source supplied this field."""
        return cls()

    @classmethod
    def single(cls, value: Any, source: Source) -> "ProvenanceField":
        """Only one source supplied this field — provenance, but nothing to disagree with."""
        return cls(value=value, source=source)

    @classmethod
    def resolved(
        cls,
        value: Any,
        source: Source,
        other_source: Source,
        other_value: Any,
        kind: ConflictKind,
    ) -> "ProvenanceField":
        """Both sources spoke; `kind` decides whether that is a conflict."""
        return cls(
            value=value,
            source=source,
            alternatives=[SourceValue(source=other_source, value=other_value)],
            conflict=kind is ConflictKind.CONTRADICTION,
            conflict_kind=kind,
        )


class MatchSignal(BaseModel):
    """One scoring signal's contribution to a match (doc 02, Decision 3)."""

    name: str
    weight: float
    score: float = Field(ge=0.0, le=1.0)
    detail: str | None = None

    @property
    def contribution(self) -> float:
        return self.weight * self.score


class MatchEvidence(BaseModel):
    """Why the matcher believed two records describe the same meeting.

    Stored and displayed, not just logged: doc 02 rejects a black-box matcher precisely
    because a reviewer will check the pairs by hand.
    """

    score: float = Field(ge=0.0, le=1.0)
    signals: list[MatchSignal]
    confidence: MatchConfidence

    @model_validator(mode="after")
    def _score_is_the_sum_of_contributions(self) -> "MatchEvidence":
        """The evidence is only meaningful if the arithmetic checks out — a score that
        doesn't add up would make the per-signal breakdown decoration."""
        total = sum(signal.contribution for signal in self.signals)
        if abs(total - self.score) > _SCORE_TOLERANCE:
            raise ValueError(f"score {self.score} != sum of contributions {total}")
        return self

    @property
    def is_low_confidence(self) -> bool:
        return self.confidence is MatchConfidence.LOW


class UnifiedMeeting(BaseModel):
    """One real-world meeting, however many source records describe it."""

    id: str
    origin: Origin

    # Denormalized for sorting and date filtering. The authoritative, provenance-carrying
    # values are start_time/end_time below.
    event_date: date | None = None
    start: datetime | None = None

    crm_ids: list[str] = Field(default_factory=list)
    calendar_ids: list[str] = Field(default_factory=list)

    # Provenance-carrying fields — everything the user reads.
    title: ProvenanceField = Field(default_factory=ProvenanceField.empty)
    start_time: ProvenanceField = Field(default_factory=ProvenanceField.empty)
    end_time: ProvenanceField = Field(default_factory=ProvenanceField.empty)
    location: ProvenanceField = Field(default_factory=ProvenanceField.empty)
    participants: ProvenanceField = Field(default_factory=ProvenanceField.empty)
    client_name: ProvenanceField = Field(default_factory=ProvenanceField.empty)
    client_company: ProvenanceField = Field(default_factory=ProvenanceField.empty)
    owner_name: ProvenanceField = Field(default_factory=ProvenanceField.empty)
    meeting_type: ProvenanceField = Field(default_factory=ProvenanceField.empty)
    notes: ProvenanceField = Field(default_factory=ProvenanceField.empty)
    status: ProvenanceField = Field(default_factory=ProvenanceField.empty)

    match_evidence: MatchEvidence | None = None
    flags: list[DataQualityFlag] = Field(default_factory=list)

    # The untouched source records, carried inline so the detail view is one lookup rather
    # than a join. Lists because dedupe can fold two calendar records into one meeting.
    raw_crm: list[dict] = Field(default_factory=list)
    raw_calendar: list[dict] = Field(default_factory=list)

    @model_validator(mode="after")
    def _origin_agrees_with_the_ids(self) -> "UnifiedMeeting":
        """A `both` meeting missing one side's ids is a merge bug that would otherwise show
        up as a blank column in the UI instead of as an error."""
        has_crm = bool(self.crm_ids)
        has_calendar = bool(self.calendar_ids)
        expected = {
            Origin.BOTH: (True, True),
            Origin.CRM_ONLY: (True, False),
            Origin.CALENDAR_ONLY: (False, True),
        }[self.origin]

        if (has_crm, has_calendar) != expected:
            raise ValueError(
                f"origin={self.origin.value} disagrees with "
                f"crm_ids={self.crm_ids} calendar_ids={self.calendar_ids}"
            )
        return self

    @property
    def provenance_fields(self) -> dict[str, ProvenanceField]:
        """Name → field, so a template can iterate instead of hard-coding the list."""
        return {
            name: value
            for name, value in ((n, getattr(self, n)) for n in self.__class__.model_fields)
            if isinstance(value, ProvenanceField)
        }

    @property
    def conflicting_fields(self) -> list[str]:
        return [name for name, field in self.provenance_fields.items() if field.conflict]

    @property
    def has_conflicts(self) -> bool:
        return bool(self.conflicting_fields)

    @property
    def source_ids(self) -> list[str]:
        return [*self.crm_ids, *self.calendar_ids]

    @property
    def worst_flag_severity(self) -> Severity | None:
        """The most serious data-quality flag on this meeting, or None if it is clean.

        A property rather than template logic: "which badge does this row get?" is a
        question about the data, and every page that shows a quality indicator should
        answer it the same way.
        """
        if not self.flags:
            return None

        order = (Severity.ERROR, Severity.WARNING, Severity.INFO)
        present = {flag.severity for flag in self.flags}
        return next(severity for severity in order if severity in present)


class SyncRunSummary(BaseModel):
    """What `GET /api/stats` reports — the five-second verification of a sync run."""

    generated_at: datetime

    crm_records_in: int = 0
    calendar_records_in: int = 0
    duplicates_collapsed: int = 0

    meetings_out: int = 0
    matched_pairs: int = 0
    crm_only: int = 0
    calendar_only: int = 0
    low_confidence_matches: int = 0

    conflicts_by_kind: dict[str, int] = Field(default_factory=dict)
    conflicts_by_field: dict[str, int] = Field(default_factory=dict)
    flags_by_code: dict[str, int] = Field(default_factory=dict)
    flags_by_severity: dict[str, int] = Field(default_factory=dict)

    @computed_field
    @property
    def records_in(self) -> int:
        """Derived rather than stored — a total that can disagree with its parts is a bug
        waiting to happen. `computed_field` is what puts it in the JSON payload and the
        OpenAPI schema, so the API needs no separate response model to expose it."""
        return self.crm_records_in + self.calendar_records_in


class SyncResult(BaseModel):
    """One sync run's complete output. This object *is* the store (doc 03).

    Frozen: `POST /api/sync` builds a whole new result and rebinds a single reference, so a
    reader sees either the entire previous dataset or the entire new one, never a mix.
    """

    model_config = {"frozen": True}

    meetings: dict[str, UnifiedMeeting]
    by_date: list[str]
    summary: SyncRunSummary

    @model_validator(mode="after")
    def _by_date_covers_every_meeting(self) -> "SyncResult":
        """A missing id here is a meeting that the API can return but the list view never
        shows — a bug that reads as a UI problem for an hour before anyone suspects the store."""
        if sorted(self.by_date) != sorted(self.meetings):
            missing = sorted(set(self.meetings) - set(self.by_date))
            unknown = sorted(set(self.by_date) - set(self.meetings))
            raise ValueError(f"by_date must permute meetings (missing={missing}, unknown={unknown})")
        if len(set(self.by_date)) != len(self.by_date):
            raise ValueError("by_date contains duplicate ids")
        return self

    @property
    def ordered_meetings(self) -> list[UnifiedMeeting]:
        """The list view, already sorted."""
        return [self.meetings[meeting_id] for meeting_id in self.by_date]
