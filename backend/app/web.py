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
