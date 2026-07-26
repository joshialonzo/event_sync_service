"""The server-rendered pages.

Renders from the same repository the JSON API reads (`get_repository`), which is the whole
argument for templates over a separate frontend: `/` and `/api/meetings` cannot disagree
about a record because there is one code path to the data.
"""

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.dependencies import get_repository
from app.models.filters import MeetingFilters, apply_filters
from app.models.unified import Origin
from app.repository import Repository

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

# Package-relative, not cwd-relative: uvicorn runs from backend/, pytest from wherever it
# was invoked, and the container from /app. A bare "templates" string works in one of those.
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["pages"], include_in_schema=False)

# Which raw keys back each merged field. The two sources spell the same fact differently —
# a CRM date plus a time against one calendar timestamp — so highlighting the records behind
# a conflict needs this map. Kept beside the route that uses it rather than in the models:
# it is a presentation concern, and nothing else needs to know it.
SOURCE_FIELDS: dict[str, dict[str, tuple[str, ...]]] = {
    "title": {"crm": ("subject",), "calendar": ("title",)},
    "start_time": {"crm": ("meeting_date", "meeting_time"), "calendar": ("start_time",)},
    "end_time": {"crm": (), "calendar": ("end_time",)},
    "location": {"crm": ("location",), "calendar": ("location",)},
    "participants": {"crm": ("client_name",), "calendar": ("attendees", "organizer")},
    "client_name": {"crm": ("client_name",), "calendar": ()},
    "client_company": {"crm": ("client_company",), "calendar": ()},
    "owner_name": {"crm": ("relationship_owner",), "calendar": ("organizer",)},
    "meeting_type": {"crm": ("meeting_type",), "calendar": ()},
    "notes": {"crm": ("notes",), "calendar": ("description",)},
    "status": {"crm": ("status",), "calendar": ("status",)},
}


@router.get("/", response_class=HTMLResponse)
def meetings_page(
    request: Request,
    origin: str | None = Query(default=None),
    has_conflicts: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    owner: str | None = Query(default=None),
    repository: Repository = Depends(get_repository),
) -> HTMLResponse:
    """The meeting list, filtered by the form's query parameters.

    The template receives the meetings themselves rather than a flattened view model: every
    display decision it makes — the origin badge, the conflict badge, the quality severity —
    is already a property of the data, and pre-chewing it here would be a second place for
    those answers to differ from the JSON API's.

    Parameters are taken as strings and parsed here rather than declared as typed query
    params like the API's. A submitted HTML form sends every field, including the untouched
    ones (`?origin=&date_from=`), and a typed parameter would 422 on the empty string —
    turning "I pressed Filter without choosing anything" into an error page.
    """
    filters = _filters_from_query(origin, has_conflicts, date_from, date_to, owner)
    meetings = apply_filters(repository.list_meetings(), filters)

    return templates.TemplateResponse(
        request=request,
        name="meetings.html",
        context={
            "meetings": meetings,
            "meeting_count": len(meetings),
            "total_count": len(repository.list_meetings()),
            "filters": filters,
            "is_filtered": not filters.is_empty,
        },
    )


@router.get("/meetings/{meeting_id}", response_class=HTMLResponse)
def meeting_detail_page(
    request: Request,
    meeting_id: str,
    repository: Repository = Depends(get_repository),
) -> HTMLResponse:
    """One meeting: the merged record, the evidence, both raw sides, and the flags.

    An unknown id renders an HTML page rather than FastAPI's JSON error body — someone
    following a stale link deserves a page with a way back, not `{"detail": ...}`.
    """
    meeting = repository.get_meeting(meeting_id)
    if meeting is None:
        return templates.TemplateResponse(
            request=request,
            name="not_found.html",
            context={"meeting_id": meeting_id},
            status_code=404,
        )

    return templates.TemplateResponse(
        request=request,
        name="detail.html",
        context={
            "meeting": meeting,
            "conflict_keys": _conflict_keys(meeting),
        },
    )


@router.get("/stats", response_class=HTMLResponse)
def stats_page(
    request: Request, repository: Repository = Depends(get_repository)
) -> HTMLResponse:
    """The sync overview: the summary's numbers, joined to the records behind them.

    Reads meetings and stats from one snapshot rather than two calls, so a re-sync landing
    mid-render cannot produce a page whose tiles and tables describe different runs.
    """
    snapshot = repository.result if hasattr(repository, "result") else None
    meetings = snapshot.ordered_meetings if snapshot else repository.list_meetings()
    summary = snapshot.summary if snapshot else repository.get_stats()

    return templates.TemplateResponse(
        request=request,
        name="stats.html",
        context={
            "summary": summary,
            "flag_rows": _flag_rows(meetings),
            "conflict_rows": _conflict_rows(meetings),
            "settings": get_settings(),
            "link_limit": FLAG_LINK_LIMIT,
        },
    )


FLAG_LINK_LIMIT = 6
"""How many affected meetings to link per flag code.

TIMEZONE_ASSUMED fires on all 24, and a row of 24 links buries the eight codes below it —
each of which affects one or two records and is the part worth looking at.
"""

_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def _flag_rows(meetings: list) -> list[dict]:
    """One row per flag code: severity, count, and the meetings carrying it.

    A count on its own is a claim; the links are what make it checkable. Joined here rather
    than in the summary because "which meetings" is a presentation concern — the store
    counts, the page points.
    """
    rows: dict[str, dict] = {}

    for meeting in meetings:
        for flag in meeting.flags:
            row = rows.setdefault(
                flag.code.value,
                {
                    "code": flag.code.value,
                    "severity": flag.severity.value,
                    "message": flag.message,
                    "count": 0,
                    "meeting_ids": [],
                },
            )
            row["count"] += 1
            if meeting.id not in row["meeting_ids"]:
                row["meeting_ids"].append(meeting.id)

    # Severity first: the single corrupt date must not sit below 40 timezone assumptions.
    return sorted(
        rows.values(),
        key=lambda row: (_SEVERITY_ORDER.get(row["severity"], 9), -row["count"]),
    )


def _conflict_rows(meetings: list) -> list[dict]:
    """Which meetings contradict each other, per field."""
    rows: dict[str, list[str]] = {}

    for meeting in meetings:
        for field_name in meeting.conflicting_fields:
            rows.setdefault(field_name, []).append(meeting.id)

    return [
        {"field": field_name, "meeting_ids": ids}
        for field_name, ids in sorted(rows.items())
    ]


def _conflict_keys(meeting) -> dict[str, set[str]]:
    """The raw keys, per source, that a conflict on this meeting is about.

    Lets the side-by-side panels highlight `location` on both sides for CRM-1002, and both
    of the CRM's date/time keys for a start-time disagreement.
    """
    keys: dict[str, set[str]] = {"crm": set(), "calendar": set()}

    for field_name in meeting.conflicting_fields:
        mapping = SOURCE_FIELDS.get(field_name, {})
        keys["crm"].update(mapping.get("crm", ()))
        keys["calendar"].update(mapping.get("calendar", ()))

    return keys


def _filters_from_query(
    origin: str | None,
    has_conflicts: str | None,
    date_from: str | None,
    date_to: str | None,
    owner: str | None,
) -> MeetingFilters:
    """Build filters from raw form input, treating blanks and nonsense as "unset".

    The list page must survive whatever arrives in the query string: a bad date or an
    unknown origin should show all the meetings, not a 422. The JSON API is stricter on
    purpose — there a typo is a programming error worth reporting.
    """
    return MeetingFilters(
        origin=_as_origin(origin),
        has_conflicts=_as_bool(has_conflicts),
        date_from=_as_date(date_from),
        date_to=_as_date(date_to),
        owner=(owner or "").strip() or None,
    )


def _as_origin(value: str | None) -> Origin | None:
    try:
        return Origin(value) if value else None
    except ValueError:
        return None


def _as_bool(value: str | None) -> bool | None:
    """Tri-state: unset, true, or false. An HTML checkbox cannot express the third, which
    is why the control is a select with an empty default — otherwise "meetings with no
    conflicts" would be unaskable."""
    if value in {"true", "false"}:
        return value == "true"
    return None


def _as_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None
