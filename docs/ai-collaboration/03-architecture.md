# 03 — Architecture

## Stack

| Layer | Choice |
|---|---|
| API | FastAPI (Python 3.12), Pydantic models |
| Frontend | Next.js (App Router, TypeScript) |
| Storage | In-process store, rebuilt from `data/` on each sync run |
| Runtime | Docker Compose — Uvicorn + Next.js, nothing else |

**Why this stack.** The reconciliation logic is the substance of the assessment, and Python is the
right language for it. FastAPI gives typed request/response models and a free OpenAPI page at `/docs`
— useful when the deliverable includes "expose the reconciled data through a REST API," because the
reviewer gets an interactive contract without reading the code. Next.js keeps the frontend typed
against the same shapes.

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
docker compose up
```

Two containers: the FastAPI app (via Uvicorn, hot-reload) and Next.js. The API runs the ingest and
reconciliation pipeline on startup, so the dataset is populated the moment the service is reachable —
there is no seeding step to forget. No account, no credentials, no network needed to evaluate this
project.

**The design constraint that follows:** the pipeline must be able to run at import time without
blocking on anything external, and the business logic must not know where its output is stored.
A repository interface satisfies the second half; the first is why the store is in-process. The
single command is the only path, so there is no second path to drift out of sync with it.

---

## Layout

```
event_sync_service/
├── data/                          # provided source files (unmodified)
├── backend/
│   ├── app/
│   │   ├── main.py                # FastAPI app + startup sync
│   │   ├── config.py              # Settings (env-driven)
│   │   ├── api/routes.py          # HTTP layer only — no logic
│   │   ├── models/                # Pydantic: NormalizedEvent, UnifiedMeeting, ProvenanceField…
│   │   ├── ingest/                # source adapters: crm.py, calendar.py
│   │   ├── reconcile/
│   │   │   ├── normalize.py       # parsing + quality flags
│   │   │   ├── dedupe.py          # intra-source
│   │   │   ├── matcher.py         # scoring + assignment
│   │   │   └── merge.py           # precedence + provenance
│   │   ├── repository/            # Repository protocol + InMemoryRepository
│   │   └── jobs/sync.py           # the pipeline entrypoint
│   └── tests/
├── frontend/                      # Next.js
├── docs/ai-collaboration/         # this folder
├── docker-compose.yml
└── README.md
```

**Why `reconcile/` is four files.** They are four independently testable decisions from
[02-reconciliation-design.md](02-reconciliation-design.md). Collapsing them into one `reconcile.py`
would make the interesting part of this project a single 400-line function, and the pipeline stages
are exactly the seams a reviewer will want to inspect.

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

**Why store raw records at all.** The frontend must show the user what each source said. Keeping the
raw payload means the merge can be re-run with different precedence rules without re-ingesting, and
the API can always answer "what did the CRM actually say?" — which is the whole provenance feature.

**Consistency.** `POST /api/sync` builds a complete new `SyncResult` and then rebinds a single
reference, so a reader either sees the entire previous dataset or the entire new one, never a
half-written mix. At 24 items the atomic swap costs nothing, and it is the reason a re-sync while the
UI is open cannot produce a torn read.

**The repository interface.** Routes depend on a `Repository` protocol (`list_meetings`, `get_meeting`,
`get_stats`, `replace_all`), not on the dictionaries. `InMemoryRepository` is the only implementation.
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
| `POST /api/sync` | Re-run the pipeline (idempotent). |
| `GET /api/health` | Liveness. |
| `GET /docs` | OpenAPI UI. |

`GET /api/stats` exists because it is how a reviewer verifies the reconciliation in five seconds
without reading a line of code: 42 records in, 24 meetings out, 17 matched, 4 conflicts.

---

## Frontend

Three views:

1. **Meeting list** — date-ordered, each row badged `Both` / `CRM only` / `Calendar only`, with a
   conflict indicator and a data-quality indicator. Filter controls map 1:1 to the API's query params.
2. **Meeting detail** — merged record on top; below it, a side-by-side of the raw CRM and Calendar
   records with conflicting fields highlighted and the match evidence (per-signal score breakdown)
   shown. This is the view that answers "where did this come from and why do you think these are the
   same meeting?"
3. **Sync overview** — the `/api/stats` numbers, plus the data-quality flag list linking to the
   affected records.

Visual design is explicitly not being evaluated, so styling stays minimal and the effort goes into
making provenance and conflicts legible.

---

## What is deliberately out of scope

Auth, pagination, real upstream API clients with retry/backoff, durable storage, cloud deployment,
CI/CD. Each is a paragraph in the README explaining what would change, which is more useful to a
reviewer than a half-built version of any of them.

Deployment is the largest of these omissions, so it gets the explicit note: this service is built to
be run locally by a reviewer, and a hosted environment would need a persistent repository
implementation, real ingest scheduling, and secrets handling before it meant anything. Sketching that
in infrastructure code without those pieces would document an intention, not a system.
