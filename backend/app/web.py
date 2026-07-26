"""The server-rendered pages.

Renders from the same repository the JSON API reads (`get_repository`), which is the whole
argument for templates over a separate frontend: `/` and `/api/meetings` cannot disagree
about a record because there is one code path to the data.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.dependencies import get_repository
from app.repository import Repository

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

# Package-relative, not cwd-relative: uvicorn runs from backend/, pytest from wherever it
# was invoked, and the container from /app. A bare "templates" string works in one of those.
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["pages"], include_in_schema=False)


@router.get("/", response_class=HTMLResponse)
def meetings_page(
    request: Request, repository: Repository = Depends(get_repository)
) -> HTMLResponse:
    """The meeting list, in the store's date order.

    The template receives the meetings themselves rather than a flattened view model: every
    display decision it makes — the origin badge, the conflict badge, the quality severity —
    is already a property of the data, and pre-chewing it here would be a second place for
    those answers to differ from the JSON API's.
    """
    meetings = repository.list_meetings()

    return templates.TemplateResponse(
        request=request,
        name="meetings.html",
        context={"meetings": meetings, "meeting_count": len(meetings)},
    )
