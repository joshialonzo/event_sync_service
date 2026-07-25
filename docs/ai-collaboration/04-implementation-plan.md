# 04 — Implementation Plan

*Ordered build plan. One row = one commit-sized step touching 1–3 files, each with a manual check
that must pass before moving on.*

## What this plan builds

The service defined in [03-architecture.md](03-architecture.md): one FastAPI process serving the
JSON API and the HTML pages, with **server-rendered Jinja2 templates** for the UI. One container, one
language, one dependency file. The templates render from the same repository the API routes use, so
`/api/meetings` and `/` can never disagree about a record.

Practical consequences for the steps below: `docker-compose.yml` has a single service; there is no
separate UI build step and no CORS configuration; and "see the provenance" is a page render rather
than a client-side fetch.

## Local setup (once, before Step 1)

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Local Python is 3.11 (the container is 3.12), so **avoid 3.12-only syntax** — no PEP 695 `type`
statements or generic-class syntax.

Every step below is verified from `backend/` with the venv active. The server command, used
throughout, is:

```bash
uvicorn app.main:app --reload --port 8000
```

## On tests

Step 3 stands up the pytest harness. **From step 4 on, every step adds its own test module**, and the
`Files:` lists below name only the production files — the test module is assumed, not an extra. Each
step's `Test:` line describes the manual check; the automated check is the suite staying green.

Test coverage percentage is explicitly not evaluated by the brief, so the suite targets the decisions
in [02-reconciliation-design.md](02-reconciliation-design.md) rather than lines of code.

---

## Phase 1 — Skeleton

### Step 1 — FastAPI app boots ✅
**Files:** `backend/app/__init__.py`, `backend/app/main.py`
Minimal app, `GET /api/health` returning `{"status": "ok"}`.
**Test:** run the server, then `curl localhost:8000/api/health`; open `localhost:8000/docs`.

### Step 2 — Settings ✅
**Files:** `backend/app/config.py`, `backend/app/main.py`
`Settings` (pydantic-settings): `data_dir` (default repo `data/`, env-overridable), `timezone`
(`America/New_York`), both resolved absolute. Health response includes them.
**Test:** `curl localhost:8000/api/health` shows an absolute path that exists;
`DATA_DIR=/tmp uvicorn ...` shows `/tmp`.

### Step 3 — Test harness
**Files:** `backend/requirements.txt` (add `httpx`), `backend/pytest.ini`, `backend/tests/conftest.py`,
`backend/tests/test_config.py`, `backend/tests/test_health.py`
*The one step that exceeds three files — see [steps/03](../../steps/03-test-harness.md) for why.*
Unit tests for `Settings` (defaults, env override, relative-path resolution) and an integration test
for `/api/health` through `TestClient`, plus the shared fixtures every later step reuses.
**Test:** `pytest -v` — green, and `pytest --collect-only` shows both modules discovered.

### Step 4 — Raw loaders
**Files:** `backend/app/ingest/__init__.py`, `backend/app/ingest/crm.py`, `backend/app/ingest/calendar.py`
Each returns `list[dict]` read from the JSON file. No parsing, no models.
**Test:** `python -c "from app.ingest.crm import load_crm; from app.ingest.calendar import load_calendar; print(len(load_crm()), len(load_calendar()))"` → `20 22`.

---

## Phase 2 — Models

### Step 5 — Normalized models
**Files:** `backend/app/models/__init__.py`, `backend/app/models/normalized.py`
`DataQualityFlag` (code, field, raw_value, severity), `Severity`, `MeetingStatus`, `Participant`,
`NormalizedEvent` (incl. `raw: dict`, `source_ids: list[str]`).
**Test:** construct a maximally sparse `NormalizedEvent` — it must not raise.

### Step 6 — Unified models
**Files:** `backend/app/models/unified.py`
`ProvenanceField`, `ConflictKind`, `MatchEvidence`, `UnifiedMeeting`, `SyncRunSummary`, `SyncResult`.
**Test:** construct one `UnifiedMeeting` and `model_dump_json()` it — the shape must match the JSON
block in doc 02, Decision 4.

---

## Phase 3 — Normalize (doc 02, Decision 1)

### Step 7 — Parsing primitives
**Files:** `backend/app/reconcile/__init__.py`, `backend/app/reconcile/parse.py`
Lenient date parse (ISO, then `%m-%d/%Y`), lenient ISO datetime (missing seconds), timezone coercion
to Eastern, email repair (`[at]` → `@`), status vocabulary mapping.
**Test:** `pytest tests/test_parse.py` — cases: `"03-15/2025"`, `"2025-03-14T20:00"`,
`"2025-03-13T19:00:00Z"` → 14:00 local, `"raj.patel[at]atlasvc.com"`.

### Step 8 — CRM normalizer
**Files:** `backend/app/reconcile/normalize_crm.py`
20 records in → 20 `NormalizedEvent` out, never raising.
**Test:** `pytest` asserts `CRM-1008` carries `MALFORMED_DATE` and still has a parsed date,
`CRM-1007` carries `TIME_MISSING`, `CRM-1006` carries `INTERNAL_NO_CLIENT` at `info` severity.

### Step 9 — Calendar normalizer
**Files:** `backend/app/reconcile/normalize_calendar.py`
22 in → 22 out, with `TIMEZONE_ASSUMED` on naive records.
**Test:** `pytest` asserts `CAL-A4` normalizes to 14:00 Eastern, `CAL-A16` repairs the email with a
flag, `CAL-A20` keeps `"external-guests"` as an opaque participant with `NON_EMAIL_ATTENDEE`.

---

## Phase 4 — Reconcile

### Step 10 — Intra-source dedupe (doc 02, Decision 2)
**Files:** `backend/app/reconcile/dedupe.py`
Same day + same organizer + overlapping client participants + starts within 60 min + neither
recurring. Survivor = earlier `created_at`, attendees unioned, both IDs retained.
**Test:** `pytest` asserts `CAL-A5`/`CAL-A6` collapse into one event holding both IDs and Sandra
Mills, and that `CAL-A3`/`CAL-A18` do **not** collapse. Calendar: 22 → 21.

### Step 11 — Match signals (doc 02, Decision 3)
**Files:** `backend/app/reconcile/signals.py`
Four scorers, each `(NormalizedEvent, NormalizedEvent) -> float` in `[0, 1]`: participant overlap,
time proximity, title similarity, structural agreement. Pure, no weights applied here.
**Test:** `pytest` — `CRM-1007` (no time) scores 0.5 on time; `"Conference Room B"` vs `"HQ -
Conference Room B"` scores as agreement; `LPAC` ↔ `LP Advisory Committee` scores > 0 on title.

### Step 12 — Matcher
**Files:** `backend/app/reconcile/matcher.py`
±1-day blocking, weighted sum (0.40/0.30/0.20/0.10), greedy descending assignment, thresholds
0.70 / 0.45, `MatchEvidence` per pair.
**Test:** `pytest` asserts the **exact 17 pairs** from doc 01, plus 3 CRM-only and 4 Calendar-only.
This is the correctness fixture — it must be green before anything renders.

### Step 13 — Merge (doc 02, Decision 4)
**Files:** `backend/app/reconcile/merge.py`
Field-level `ProvenanceField` with precedence (calendar: logistics; CRM: relationship; status: both
surfaced, conservative default), and the contradiction / absence / granularity split.
**Test:** `pytest` asserts conflicts on `CRM-1002` (modality), `CRM-1009` (status), `CRM-1016`
(time); asserts `CRM-1018` location is `absence`, not a conflict; asserts `CRM-1001` location is
`granularity` and the specific value wins.

---

## Phase 5 — Store and pipeline

### Step 14 — Repository
**Files:** `backend/app/repository/__init__.py`, `backend/app/repository/memory.py`
`Repository` protocol (`list_meetings`, `get_meeting`, `get_stats`, `replace_all`) and
`InMemoryRepository` holding one `SyncResult` behind a single rebindable reference.
**Test:** `pytest` — `replace_all` twice, confirm `list_meetings` reflects only the latest.

### Step 15 — Sync job
**Files:** `backend/app/jobs/__init__.py`, `backend/app/jobs/sync.py`
Wires ingest → normalize → dedupe → match → merge → `SyncResult` with `by_date` and summary.
**Test:** `python -c "from app.jobs.sync import run_sync; r = run_sync(); print(len(r.meetings), r.summary)"`
→ 24 meetings, 42 records in, 17 matched.

### Step 16 — Sync on startup
**Files:** `backend/app/main.py`, `backend/app/dependencies.py`
Lifespan handler runs the pipeline into a module-level repository; a `get_repository` dependency
exposes it.
**Test:** start the server, confirm the log line reports 24 meetings before the first request.

---

## Phase 6 — JSON API

### Step 17 — List and detail
**Files:** `backend/app/api/__init__.py`, `backend/app/api/routes.py`, `backend/app/main.py`
`GET /api/meetings` (unfiltered, date-ordered), `GET /api/meetings/{id}` (provenance + evidence +
both raw records).
**Test:** `curl -s localhost:8000/api/meetings | jq length` → 24;
`curl -s localhost:8000/api/meetings/<id> | jq .location` shows value/source/alternatives/conflict;
unknown id → 404.

### Step 18 — Filters
**Files:** `backend/app/api/routes.py`
`origin`, `has_conflicts`, `date_from`, `date_to`, `owner`.
**Test:** `?origin=crm_only` → 3, `?origin=calendar_only` → 4, `?has_conflicts=true` → the conflict
set from Step 13, a date range narrows the list.

### Step 19 — Stats and re-sync
**Files:** `backend/app/api/routes.py`
`GET /api/stats`, `POST /api/sync`.
**Test:** `curl -s localhost:8000/api/stats | jq` shows 42 in / 24 out / 17 matched / conflicts by
kind / flags by code; `curl -X POST localhost:8000/api/sync` then re-check stats — identical
(idempotent).

---

## Phase 7 — Server-rendered UI

### Step 20 — Template plumbing
**Files:** `backend/app/main.py`, `backend/app/templates/base.html`, `backend/app/static/app.css`
`Jinja2Templates` + `StaticFiles`, a base layout with a nav (Meetings / Sync overview), minimal CSS.
Route `GET /` renders a placeholder extending the base.
**Test:** open `localhost:8000/` — layout renders, stylesheet loads (no 404 in the network tab).

### Step 21 — Meeting list page
**Files:** `backend/app/web.py`, `backend/app/templates/meetings.html`
`GET /` renders the date-ordered list: date, time, title, client, owner, `Both` / `CRM only` /
`Calendar only` badge, conflict indicator, data-quality indicator, link to detail.
**Test:** open `localhost:8000/` — 24 rows, spot-check three rows against `/api/meetings`.

### Step 22 — Filter controls
**Files:** `backend/app/web.py`, `backend/app/templates/meetings.html`
A plain `GET` form whose inputs are the Step 18 query params; selections persist in the rendered
form.
**Test:** filter to `CRM only` → 3 rows and the URL carries `?origin=crm_only`; reload keeps the
selection; "conflicts only" matches the API count.

### Step 23 — Detail page
**Files:** `backend/app/web.py`, `backend/app/templates/detail.html`
Merged record with a source label per field; below it, side-by-side raw CRM and Calendar records with
conflicting fields highlighted; match evidence as the per-signal breakdown.
**Test:** open the `CRM-1002`/`CAL-A2` meeting — location shows both values marked as a conflict;
open the `CRM-1005` meeting — both `CAL-A5` and `CAL-A6` are listed as sources; open a CRM-only
meeting — the calendar column reads "no calendar record", not an error.

### Step 24 — Sync overview page
**Files:** `backend/app/web.py`, `backend/app/templates/stats.html`
The `/api/stats` numbers, plus the data-quality flag list with links to the affected meetings.
**Test:** open `localhost:8000/stats` — numbers equal the JSON endpoint's; a flag link lands on the
right detail page.

### Step 25 — Re-sync from the UI
**Files:** `backend/app/web.py`, `backend/app/templates/stats.html`
A `POST /sync` form that re-runs the pipeline and redirects back (303) to `/stats`.
**Test:** click Re-sync — the page reloads with identical counts and no duplicated meetings; the
back button does not re-post.

---

## Phase 8 — Ship

### Step 26 — Container and single command
**Files:** `backend/Dockerfile`, `docker-compose.yml`, `.dockerignore`
One service; `data/` mounted read-only; port 8000.
**Test:** `docker compose up` from a clean clone → `localhost:8000/` serves the list and
`localhost:8000/docs` serves OpenAPI. Confirm nothing but that one command was needed.

### Step 27 — Reconcile the docs with the built repo
**Files:** `docs/ai-collaboration/03-architecture.md`, `docs/ai-collaboration/README.md`
Doc 03 was written ahead of the code, so this is the pass that makes it describe what exists: correct
the layout tree against the actual files, the API table against the actual routes, and the counts
against the actual sync output. Add the collaboration-log entry for the implementation phase.
**Test:** re-read doc 03 against the repo — every path, route, and number in it is true.

### Step 28 — README
**Files:** `README.md`
Setup guide (Docker path and venv path), approach walkthrough, key decisions with links into
`docs/ai-collaboration/`, honest time spent.
**Test:** follow the README top to bottom in a fresh clone, copy-pasting only what it says.

---

## Invariants to re-check at every step

1. `pytest` stays green; the 17-pair fixture (step 12) never regresses.
2. 42 records in, 24 meetings out, nothing dropped.
3. The templates read through the repository, never through HTTP — one code path to the data.
