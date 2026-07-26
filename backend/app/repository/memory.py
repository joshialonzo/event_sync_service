"""The in-process store (doc 03).

One `SyncResult` behind one reference. The entire consistency argument for this service rests
on `replace_all` doing exactly one assignment, so this file is deliberately tiny — there is
nowhere for a partial update to hide.
"""

from datetime import datetime, timezone

from app.models.unified import SyncResult, SyncRunSummary, UnifiedMeeting

EMPTY = SyncResult(
    meetings={},
    by_date=[],
    summary=SyncRunSummary(generated_at=datetime.fromtimestamp(0, tz=timezone.utc)),
)
"""The pre-sync state.

An empty result rather than None: step 16 syncs during startup so this is never observed in
practice, but modelling "not loaded yet" as a null reference would push an `if store is None`
branch into every route and every template.
"""


class InMemoryRepository:
    """Satisfies `Repository` structurally — it does not import or inherit the protocol."""

    def __init__(self, result: SyncResult = EMPTY) -> None:
        self._result = result

    def list_meetings(self) -> list[UnifiedMeeting]:
        """Date-ordered, because `by_date` was built that way by the sync job.

        Reads `self._result` once. Reading it twice — once for the ids and once for the
        lookup — would reintroduce exactly the torn read the single reference prevents.
        """
        return self._result.ordered_meetings

    def get_meeting(self, meeting_id: str) -> UnifiedMeeting | None:
        return self._result.meetings.get(meeting_id)

    def get_stats(self) -> SyncRunSummary:
        return self._result.summary

    def replace_all(self, result: SyncResult) -> None:
        """Publish a new dataset by rebinding a single reference.

        This must stay one statement. Clearing a dict and refilling it, or updating
        `meetings` before `by_date`, would open a window in which a concurrent request sees
        a meeting in the list that 404s when clicked. `SyncResult` is frozen and validates
        that `by_date` permutes `meetings`, so anything published here is already internally
        consistent; the only remaining job is to never publish half of it.
        """
        self._result = result

    @property
    def result(self) -> SyncResult:
        """The current snapshot, for callers that need meetings and stats together without
        risking two reads across a swap."""
        return self._result
