"""The store seam.

Routes and templates depend on this protocol, not on dictionaries. It exists because the
store is the one component chosen for the *size* of the dataset rather than for the problem:
if the sources became live APIs, `memory.py` is the file that changes and nothing in
`reconcile/` would notice.
"""

from typing import Protocol, runtime_checkable

from app.models.unified import SyncResult, SyncRunSummary, UnifiedMeeting


@runtime_checkable
class Repository(Protocol):
    """What a reader of reconciled data needs.

    A Protocol rather than an ABC: implementations satisfy it structurally, so nothing has
    to import this module to be usable, and a test double is a plain object with four
    methods rather than a subclass.
    """

    def list_meetings(self) -> list[UnifiedMeeting]:
        """Every meeting, in date order — the list view reads this directly."""
        ...

    def get_meeting(self, meeting_id: str) -> UnifiedMeeting | None:
        """One meeting, or None. Routes turn the None into a 404; the store does not raise
        for a missing id because "not found" is an ordinary answer here."""
        ...

    def get_stats(self) -> SyncRunSummary:
        """The counts behind `GET /api/stats` and the sync overview page."""
        ...

    def replace_all(self, result: SyncResult) -> None:
        """Publish a complete new dataset, atomically."""
        ...
