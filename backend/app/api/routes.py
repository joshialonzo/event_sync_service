"""Meeting endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from app.dependencies import get_repository
from app.models.unified import UnifiedMeeting
from app.repository import Repository

router = APIRouter(prefix="/api", tags=["meetings"])


class MeetingListItem(UnifiedMeeting):
    """A meeting as it appears in the list: everything except the raw source records.

    The full 24 meetings serialize to ~90 KB, most of it duplicated source JSON that only
    the detail view reads. Subclassing rather than hand-writing a summary model means the
    19 shared fields cannot drift — this *is* a `UnifiedMeeting`, with two fields silenced.

    (`response_model_exclude` was tried first and does not work for list responses: the
    exclusion applies to the top-level list, not to each item.)
    """

    raw_crm: list[dict] = Field(default_factory=list, exclude=True)
    raw_calendar: list[dict] = Field(default_factory=list, exclude=True)


@router.get("/meetings", response_model=list[MeetingListItem])
def list_meetings(repository: Repository = Depends(get_repository)) -> list[UnifiedMeeting]:
    """Every reconciled meeting, in date order.

    The ordering is the store's, decided once in the sync job — sorting here would give the
    API and the HTML list two chances to disagree.
    """
    return repository.list_meetings()


@router.get("/meetings/{meeting_id}", response_model=UnifiedMeeting)
def get_meeting(
    meeting_id: str, repository: Repository = Depends(get_repository)
) -> UnifiedMeeting:
    """One meeting with full provenance, match evidence, and both sides' raw records.

    404 rather than an error: an unknown id is what a stale bookmark looks like after a
    re-sync, which is ordinary rather than exceptional.
    """
    meeting = repository.get_meeting(meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail=f"No meeting with id {meeting_id!r}")

    return meeting
