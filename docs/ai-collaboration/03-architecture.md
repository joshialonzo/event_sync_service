# 03 — Architecture

## Stack

| Layer | Choice |
|---|---|
| API | FastAPI (Python 3.12), Pydantic models |
| UI | Server-rendered Jinja2 templates, served by the same FastAPI app |
| Storage | In-process store, rebuilt from `data/` on each sync run |
| Runtime | Docker Compose — one Uvicorn container, nothing else |
| Tests | pytest + `TestClient`, run against a checkout rather than inside the image |

The image is Python 3.12; local development is 3.11, which is why the code avoids 3.12-only syntax.

**Why this stack.** The reconciliation logic is the substance of the assessment, and Python is the
right language for it. FastAPI gives typed request/response models and a free OpenAPI page at `/docs`
— useful when the deliverable includes "expose the reconciled data through a REST API," because the
reviewer gets an interactive contract without reading the code.

**Why server-rendered templates.** The UI's job is to display a 24-row list and a detail page; it has
no client-side state, no optimistic updates, and no interaction beyond links and a filter form. A
separate single-page frontend would add a second language, a second container, a build step, and a
CORS story to render read-only pages — infrastructure whose entire output is HTML that Jinja2 emits
directly. Templates read through the repository in-process, so the pages and the JSON API cannot
drift apart or disagree about a record. The tradeoff accepted: no rich interactivity. Nothing in the
requirements asks for any.

**Why there is no database.** The input is 42 records in two static JSON files, and the sync job
rebuilds the entire dataset from those files in milliseconds. A database would add a container, a
schema, a migration story, and a failure mode, and would buy durability that nothing here needs — the
source of truth is the files, not the store. Adding one to look production-ready would be
infrastructure as decoration, and it would put a moving part between the reviewer and the single
command that has to work. The repository stays behind an interface (below), so a persistent
implementation is a swap rather than a rewrite if the data ever stops being static.

---

## The single-command requirement

The statement requires: *"The service should start with a single command (document it)."* That command
is the whole deployment story:

```bash
docker compose up --build
```

One container: the FastAPI app under Uvicorn, serving both the JSON API and the HTML pages. It runs
the ingest and reconciliation pipeline during startup — before the port accepts traffic — so the
dataset is populated the moment the service is reachable and there is no seeding step to forget. No
account, no credentials, no network needed to evaluate this project.

`data/` is mounted at `/data` **read-only** rather than copied into the image: the pipeline never
writes to its inputs, and `:ro` makes that the kernel's rule instead of the code's promise. The
process runs as a non-root user, and the container's healthcheck asks `/api/health`, which reports
the meeting count — a service that started but reconciled nothing is not reported healthy.

No `--reload` in the image; the source is baked in and cannot change under it. Reload is a
development-loop concern, and the development loop is `uvicorn app.main:app --reload` from
`backend/`.

**The design constraint that follows:** the pipeline must be able to run at import time without
blocking on anything external, and the business logic must not know where its output is stored.
A repository interface satisfies the second half; the first is why the store is in-process. The
single command is the only path, so there is no second path to drift out of sync with it.

---

## Layout

```
event_sync_service/
├── data/                              # provided source files (unmodified; mounted read-only)
├── backend/
│   ├── app/
│   │   ├── main.py                    # FastAPI app: lifespan sync, routers, /static mount
│   │   ├── config.py                  # Settings — DATA_DIR, TIMEZONE, INTERNAL_DOMAIN
│   │   ├── dependencies.py            # the process-wide store, and sync_now()
│   │   ├── api/routes.py              # JSON layer only — no logic
│   │   ├── web.py                     # HTML routes — render only, same repository
│   │   ├── templates/                 # Jinja2: base, meetings, detail, stats, not_found
│   │   ├── static/app.css             # one stylesheet
│   │   ├── models/
│   │   │   ├── normalized.py          # NormalizedEvent, Participant, DataQualityFlag
│   │   │   ├── unified.py             # UnifiedMeeting, ProvenanceField, SyncResult
│   │   │   └── filters.py             # MeetingFilters + apply_filters
│   │   ├── ingest/                    # source adapters: crm.py, calendar.py
│   │   ├── reconcile/
│   │   │   ├── parse.py               # dates, times, emails — returns flags, never raises
│   │   │   ├── normalize_crm.py       # CRM record → NormalizedEvent + quality flags
│   │   │   ├── normalize_calendar.py  # calendar record → the same shape
│   │   │   ├── dedupe.py              # intra-source
│   │   │   ├── signals.py             # the four scorers, each returning its evidence
│   │   │   ├── matcher.py             # weighting + assignment
│   │   │   └── merge.py               # precedence + provenance
│   │   ├── repository/                # Repository protocol + InMemoryRepository
│   │   └── jobs/sync.py               # the pipeline entrypoint
│   ├── tests/                         # pytest suite
│   ├── Dockerfile
│   └── requirements.txt
├── docs/ai-collaboration/             # this folder
├── steps/                             # one note per build step: the decision and the check
├── docker-compose.yml
└── README.md
```

**Why `reconcile/` is seven files.** They are the independently testable decisions from
[02-reconciliation-design.md](02-reconciliation-design.md). Collapsing them into one `reconcile.py`
would make the interesting part of this project a single 400-line function, and the pipeline stages
are exactly the seams a reviewer will want to inspect.

Three of the splits were not in the original sketch and earned their place while being built:

- **`parse.py` apart from the normalizers.** Every malformed value in the sources — `"03-15/2025"`,
  a missing time, `"john.smith@@techcorp.com"` — is a parsing question, and parsing is the one part
  that must never raise. Isolating it means the normalizers are only about which flag to attach.
- **One normalizer per source, not one with a branch.** The two sources disagree about what a record
  even is: the CRM has a date and a time in separate fields, the calendar has one timestamp and an
  attendee list. A single function with `if source == …` is two functions sharing a name.
- **`signals.py` apart from `matcher.py`.** The four scorers are the part a reviewer will argue with,
  and they are pure functions over two events. The matcher's job — blocking, weighting, resolving
  competing pairs — is a different question and has different tests.

The pure functions in `reconcile/` take and return plain models — no I/O, no framework. That
is what lets the correctness fixture run in milliseconds with no containers.

---

## Data model

A sync run produces one immutable `SyncResult`, and that object *is* the store:

| Field | Shape | Serves |
|---|---|---|
| `meetings` | `dict[str, UnifiedMeeting]` | detail view by id |
| `by_date` | `list[str]`, meeting ids in `(date, start_time)` order | the list view, already sorted |
| `summary` | `SyncRunSummary` — counts in/out, matched, conflicts by kind, quality flags by code | `GET /api/stats` |

Each `UnifiedMeeting` carries its own raw source records inline, so the detail view is a single
dictionary lookup rather than a join across collections.

**Why store raw records at all.** The UI must show the user what each source said. Keeping the
raw payload means the merge can be re-run with different precedence rules without re-ingesting, and
the API can always answer "what did the CRM actually say?" — which is the whole provenance feature.

**Consistency.** A sync builds a complete new `SyncResult` and then rebinds a single reference, so a
reader either sees the entire previous dataset or the entire new one, never a half-written mix. At 24
items the atomic swap costs nothing, and it is the reason a re-sync while the UI is open cannot
produce a torn read. Both entry points — `POST /api/sync` and the overview page's `POST /sync` form —
go through the same `sync_now()`, so there is one publish path rather than two.

It is also why a *failed* sync is safe: the whole result is built before anything is replaced, so a
run that raises leaves the previous dataset published rather than emptying the store.

**The repository interface.** Routes depend on a `Repository` protocol (`list_meetings`, `get_meeting`,
`get_stats`, `replace_all`), not on the dictionaries. `InMemoryRepository` is the only implementation.
It adds one thing beyond the protocol — a `result` property returning the whole snapshot — which the
overview page uses to read the meetings and the summary without two calls that could straddle a swap.
The seam exists because the store is the one component whose choice is driven by the fixture-sized
dataset rather than by the problem — if the sources became live APIs, that is the file that changes,
and nothing in `reconcile/` would notice.

---

## API surface

| Endpoint | Purpose |
|---|---|
| `GET /api/meetings` | Unified list. Filters: `origin` (`both`/`crm_only`/`calendar_only`), `has_conflicts`, `date_from`/`date_to`, `owner`. |
| `GET /api/meetings/{id}` | One meeting with full provenance, match evidence, and both raw source records. |
| `GET /api/stats` | Counts: total, matched, source-only, conflicts by kind, data-quality flags by code, records in vs. out. |
| `POST /api/sync` | Re-run the pipeline (idempotent) and return the new summary. |
| `GET /api/health` | Liveness — plus the resolved `data_dir`, the timezone, the meeting count, and the last sync time. |
| `GET /docs` | OpenAPI UI. |

`GET /api/stats` exists because it is how a reviewer verifies the reconciliation in five seconds
without reading a line of code: 42 records in, 24 meetings out, 17 matched, 4 conflicts.

`/api/health` reports `data_dir` for a reason that turned out to matter during the container work: it
is what distinguishes this process from another one answering on the same port.

The pages are routes too, and they are deliberately not under `/api`:

| Endpoint | Purpose |
|---|---|
| `GET /` | Meeting list, with the filter form's parameters. |
| `GET /meetings/{id}` | Detail page. An unknown id renders an HTML 404, not a JSON error body. |
| `GET /stats` | Sync overview. |
| `POST /sync` | The overview's Re-sync button; redirects `303` back to `/stats`. |
| `GET /static/app.css` | The stylesheet. |

---

## UI

Three server-rendered pages, each backed by a `GET` route that pulls from the repository and renders
a template:

1. **Meeting list** (`/`) — date-ordered, each row badged `Both` / `CRM only` / `Calendar only`, with
   a conflict indicator and a data-quality indicator. Filters are a plain `GET` form whose inputs are
   the same query params the JSON endpoint accepts.
2. **Meeting detail** (`/meetings/{id}`) — merged record on top; below it, a side-by-side of the raw CRM and Calendar
   records with conflicting fields highlighted and the match evidence (per-signal score breakdown)
   shown. This is the view that answers "where did this come from and why do you think these are the
   same meeting?"
3. **Sync overview** (`/stats`) — the `/api/stats` numbers, plus the data-quality flag list linking
   to the affected records, and a form that re-runs the pipeline.

Plus a fourth template that is not a page in its own right: `not_found.html`, so a stale link gets
something with a way back rather than `{"detail": …}`.

The flag list links to the records it counts, capped at six links per code with a "+N more". That cap
exists because `TIMEZONE_ASSUMED` fires on all 24 meetings, and a row of 24 links buries the eight
codes below it — each of which affects one or two records and is the part worth looking at. Rows sort
by severity rather than count for the same reason: the single corrupt date must not sit under 40
timezone assumptions.

Visual design is explicitly not being evaluated, so styling stays minimal — one hand-written
stylesheet, no framework — and the effort goes into making provenance and conflicts legible.

---

## What is deliberately out of scope

Auth, pagination, real upstream API clients with retry/backoff, durable storage, cloud deployment,
CI/CD. Each is a paragraph in the README explaining what would change, which is more useful to a
reviewer than a half-built version of any of them.

Deployment is the largest of these omissions, so it gets the explicit note: this service is built to
be run locally by a reviewer, and a hosted environment would need a persistent repository
implementation, real ingest scheduling, and secrets handling before it meant anything. Sketching that
in infrastructure code without those pieces would document an intention, not a system.
