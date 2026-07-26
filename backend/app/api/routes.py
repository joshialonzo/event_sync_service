"""Meeting endpoints."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field

from app.dependencies import get_repository
from app.models.filters import MeetingFilters, apply_filters
from app.models.unified import Origin, UnifiedMeeting
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
def list_meetings(
    origin: Origin | None = Query(default=None, description="both | crm_only | calendar_only"),
    has_conflicts: bool | None = Query(
        default=None, description="Only meetings where the sources contradict each other"
    ),
    date_from: date | None = Query(default=None, description="Inclusive lower bound"),
    date_to: date | None = Query(default=None, description="Inclusive upper bound"),
    owner: str | None = Query(
        default=None, description="Relationship owner or organizer; matches names and emails"
    ),
    repository: Repository = Depends(get_repository),
) -> list[UnifiedMeeting]:
    """Reconciled meetings in date order, optionally filtered.

    The ordering is the store's, decided once in the sync job, and the filtering is
    `apply_filters` — the same function the HTML list uses, so the two views cannot disagree
    about what a parameter means.
    """
    filters = MeetingFilters(
        origin=origin,
        has_conflicts=has_conflicts,
        date_from=date_from,
        date_to=date_to,
        owner=owner,
    )
    return apply_filters(repository.list_meetings(), filters)


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
