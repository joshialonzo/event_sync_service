# Step 20 — Template plumbing

**Goal.** Jinja2 and static files wired into the same app, a base layout every page extends, and one
placeholder route proving it renders. No meeting data yet.

**Files**
- `backend/app/web.py`
- `backend/app/templates/base.html`
- `backend/app/static/app.css`
- `backend/app/main.py` (edit — mount static, include the web router)
- (tests) `backend/tests/test_web_plumbing.py`

## What this step establishes

**One process, one store.** `web.py` depends on `get_repository` exactly as `api/routes.py` does, so
the pages and the JSON cannot disagree about a record. That is the entire argument for
server-rendered templates over a separate frontend (doc 03), and it is set up here.

**Templates resolve from a package-relative path**, not the working directory:

```python
TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")
```

`uvicorn` runs from `backend/`, pytest from wherever, and the container from `/app`. A relative
`"templates"` string works in exactly one of those.

**Static files are mounted, not routed.** `/static` is served by Starlette's `StaticFiles`, which
handles conditional requests and content types without a route handler.

## The base layout

`base.html` holds: the doctype, a `<title>` block, the stylesheet link, a nav (Meetings / Sync
overview), and a `{% block content %}`. Everything else in phase 7 extends it.

Two things it must get right, because fixing them later means touching every page:

- **`url_for` for the stylesheet**, not a hard-coded `/static/app.css`. If the mount ever moves, one
  line changes.
- **Autoescaping on** (Jinja2Templates' default). The data being rendered includes raw source strings
  — a location field containing a `<` would otherwise break the page or worse.

## Styling

Visual design is explicitly not evaluated, so the CSS is one hand-written file: a readable measure,
a monospace-ish table, and three badge colours (conflict, origin, severity). The effort in phase 7
goes into making provenance legible, not into a design system.

## Manual test

```bash
cd backend && source venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

Open <http://localhost:8000/>:

- The page renders with the nav and a placeholder heading.
- The stylesheet loads — no 404 in the network tab, and the page is visibly styled.
- `/api/meetings` still returns JSON, and `/docs` still works: adding HTML must not disturb the API.

```bash
curl -s -o /dev/null -w '%{http_code} %{content_type}\n' localhost:8000/
curl -s -o /dev/null -w '%{http_code} %{content_type}\n' localhost:8000/static/app.css
```

→ `200 text/html; charset=utf-8` and `200 text/css; charset=utf-8`.

## Done when

- [x] `GET /` returns HTML with status 200 and the nav present.
- [x] `/static/app.css` returns 200 with a CSS content type.
- [x] The rendered page references the stylesheet via the mounted path.
- [x] Templates resolve regardless of the process's working directory.
- [x] The JSON API and `/docs` are unaffected.
- [x] Autoescaping is on — a template rendering `<script>` as data emits escaped text.
- [x] The web router uses the same `get_repository` dependency as the API.

*12 tests; 415 total.*

```
/               -> 200 text/html; charset=utf-8
/static/app.css -> 200 text/css; charset=utf-8
/api/meetings   -> 200
/docs           -> 200
```

The placeholder page reports **24 reconciled meetings**, read from the repository — so the wiring
from store to template is proven before any table markup exists.

### Two notes for later steps

- `url_for` renders **absolute** URLs derived from the incoming request
  (`http://localhost:8014/static/app.css`). That is Starlette's behaviour and it is correct here —
  the host reflects however the client reached the service, so it survives the container's port
  mapping rather than being baked in at build time.
- HTML routes are registered with `include_in_schema=False`. The OpenAPI contract a reviewer reads
  stays the JSON API, not a mix of both — asserted by `test_pages_are_absent_from_the_api_schema`.

### The test worth keeping

`test_the_app_starts_from_an_unrelated_working_directory` boots the app in a subprocess from a
`tmp_path` with only `PYTHONPATH` set, and fetches both a page and the stylesheet. A cwd-relative
template path passes every other test in this file and fails only in the container — this is the one
that would catch it before step 26.