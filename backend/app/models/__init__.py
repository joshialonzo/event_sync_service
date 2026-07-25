"""Domain models.

The bottom of the dependency graph: nothing here imports from `ingest` or `reconcile`, so
tests further down the pipeline can build events by hand instead of loading JSON.
"""

from app.models.normalized import (
    DataQualityFlag,
    FlagCode,
    MeetingStatus,
    NormalizedEvent,
    Participant,
    Severity,
    Source,
)
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

__all__ = [
    "ConflictKind",
    "DataQualityFlag",
    "FlagCode",
    "MatchConfidence",
    "MatchEvidence",
    "MatchSignal",
    "MeetingStatus",
    "NormalizedEvent",
    "Origin",
    "Participant",
    "ProvenanceField",
    "Severity",
    "Source",
    "SourceValue",
    "SyncResult",
    "SyncRunSummary",
    "UnifiedMeeting",
]
