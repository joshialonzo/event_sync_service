# Event Sync Service

Reconciles meeting records from two upstream systems — a CRM and a calendar — that share no common
identifier, and serves a unified view showing **where each field came from** and **where the sources
disagree**.

**42 records in → 24 meetings out.** 17 matched across both sources, 3 CRM-only, 4 calendar-only, 1
duplicate collapsed, 4 genuine conflicts surfaced.

See [problem_statement.md](problem_statement.md) for the assessment brief.

---

## Quick start

```bash
docker compose up --build
```

That is the whole deployment story. Open <http://localhost:8000>.

The pipeline runs during startup, before the port accepts traffic, so the data is there the moment
the service is reachable — no seeding step. `data/` is mounted read-only at `/data`; nothing is
copied into the image and nothing writes back.

| | |
|---|---|
| <http://localhost:8000/> | Meeting list — origin, conflicts, data quality, filters |
| <http://localhost:8000/stats> | Sync overview — every count, linked to the records behind it |
| <http://localhost:8000/docs> | OpenAPI, interactive |
| <http://localhost:8000/api/health> | Which data directory this process is actually reading |

### Running it without Docker

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Python 3.11+ (the image is 3.12). Configuration is environment-driven and every setting has a working
default — `DATA_DIR`, `TIMEZONE`, `INTERNAL_DOMAIN`.

### Running the tests

```bash
cd backend && source venv/bin/activate
pytest            # 582 tests, ~2 seconds
```

---

## Where to look first

If you have five minutes, this is the order that shows the most:

1. **<http://localhost:8000/stats>** — 42 in, 24 out, and every number links to the records it
   counts. A count nobody can check is a claim.
2. **<http://localhost:8000/meetings/crm-1002-cal-a2>** — the CRM says "NYC Office - 30th Floor", the
   calendar says a Zoom link. Both are shown, marked as a conflict, with the raw records side by side
   and the match evidence that says *why these two records are the same meeting*.
3. **<http://localhost:8000/meetings/crm-1005-cal-a5-cal-a6>** — the duplicate pair, collapsed to one
   meeting that keeps both calendar records.
4. **<http://localhost:8000/meetings/crm-1010>** — a CRM record that was never booked. It is in the
   list, not dropped.

---

## The approach

Five stages, each a separate module and each independently testable:

```
ingest → normalize → dedupe → match → merge
```

**Ingest** reads the two JSON files and hands back exactly what was in them. Loaders never clean
data; a loader that quietly fixes a record is a loader that hides one.

**Normalize** turns each raw record into the same shape and *never raises*. Every malformed value —
`"03-15/2025"`, a missing time, `"john.smith[at]techcorp.com"` — becomes a parsed value plus a
data-quality flag, or a null plus a flag. 42 records in, 42 normalized events out: nothing is dropped
for being ugly. ([Decision 1](docs/ai-collaboration/02-reconciliation-design.md))

**Dedupe** collapses near-duplicates *within* a source before comparing across sources, so the
matcher never sees two copies of the same meeting competing for one partner. Recurring meetings are
carved out explicitly — a weekly sync is not a duplicate of last week's.
([Decision 2](docs/ai-collaboration/02-reconciliation-design.md))

**Match** blocks candidates by date (±1 day), then scores each pair on four weighted signals:

| Signal | Weight | What it looks at |
|---|---|---|
| Participant overlap | 0.40 | `"David Park"` → `david.park@…`, `"Meridian Capital"` → `@meridiancap.com` |
| Time proximity | 0.30 | Exact = 1.0, decaying to 0 at ±4 hours |
| Title similarity | 0.20 | Token overlap, plus acronyms (`LPAC` ↔ `LP Advisory Committee`) |
| Structural agreement | 0.10 | Location compatibility, in-person vs. virtual |

Above 0.70 the pair is matched; 0.45–0.70 is matched but marked low-confidence; below that they stay
separate. **Every score is kept and shown on the detail page**, per signal, with the arithmetic
visible. That was a constraint, not a feature: a reviewer checks these pairs by hand, and a number
they cannot argue with is a number they cannot check.
([Decision 3](docs/ai-collaboration/02-reconciliation-design.md))

**Merge** produces a unified meeting in which every field is an object, not a scalar: the chosen
value, which source it came from, what the other source said, and whether that difference is a
conflict. ([Decision 4](docs/ai-collaboration/02-reconciliation-design.md))

---

## Key decisions

### Conflicts are classified, not just counted

15 fields differ between the sources. Only **4** are marked as conflicts, because a difference is not
automatically a disagreement:

| Kind | Count | Example | Badged? |
|---|---|---|---|
| **Contradiction** | 4 | CRM "NYC Office - 30th Floor" vs. calendar "Zoom - https://…" | **Yes** |
| **Granularity** | 8 | "Portfolio Walkthrough" vs. "Summit Advisors - Portfolio Discussion" | No |
| **Absence** | 3 | The CRM has no end time; the calendar does | No |

Badging all 15 would put a conflict marker on nearly every record, and a badge that fires everywhere
stops meaning anything. All three kinds are recorded and all three are visible on the overview page —
only contradictions raise the flag. The four are `crm-1002-cal-a2` (location), `crm-1004-cal-a4` and
`crm-1016-cal-a17` (start time), and `crm-1009-cal-a10` (status).

### Nothing is dropped, and nothing is silently repaired

Every anomaly in the source files survives into the output as a flag with the original value attached:

| Severity | Code | Count | What it is |
|---|---|---|---|
| error | `MALFORMED_DATE` | 1 | `CRM-1008`'s `"03-15/2025"` — parsed by a fallback pattern, flagged loudly |
| warning | `MALFORMED_DATETIME` | 1 | `CAL-A11`'s timestamp is missing a component |
| warning | `MALFORMED_EMAIL` | 1 | `"john.smith[at]techcorp.com"` — repaired, and said so |
| warning | `TIME_MISSING` | 1 | `CRM-1007` has a date and no time; it still matches, on the date signal |
| info | `TIMEZONE_ASSUMED` | 40 | A naive timestamp was assumed Eastern |
| info | `INTERNAL_NO_CLIENT` | 4 | An internal meeting with no client — valid, not a defect |
| info | `NON_EMAIL_ATTENDEE` | 1 | `"external-guests"` kept as an opaque label, not discarded |
| info | `PLACEHOLDER_CLIENT` | 1 | `"TBD"` recorded as a placeholder rather than a name |
| info | `DUPLICATE_COLLAPSED` | 1 | Two calendar records became one meeting |

One error, three warnings, and the rest are disclosures. The severity split matters: `TIMEZONE_ASSUMED`
outnumbers the genuinely corrupt record 40 to 1, so the overview sorts by severity rather than count.

### Timezones are DST-aware, and the residue is a real conflict

The dataset straddles the 2025-03-09 DST change, so a fixed −5 offset is wrong for half of it.
Everything is coerced to `America/New_York` via `zoneinfo`. This is what brings `CAL-A4` within an
hour of `CRM-1004` instead of five — and the remaining hour is a **genuine disagreement between the
sources**, not a timezone artifact, so it is shown as one.

The original planning document got this arithmetic wrong (it had `19:00Z` as 14:00 Eastern; it is
15:00). A test asserting the document's number failed, the code was right, and
[the document was corrected](docs/ai-collaboration/01-data-analysis.md).

### Unmatched records are first-class

3 CRM records were never booked; 4 calendar entries were never logged. They appear in the list badged
`CRM only` / `Calendar only`, filterable, with a detail page that says which side is missing and why
that is interesting. "The relationship owner logged a meeting that never made it into anyone's
calendar" is a finding, not a gap in the data.

### Server-rendered pages, not a separate frontend

The UI displays a 24-row list and a detail page. It has no client-side state, no optimistic updates,
and no interaction beyond links and a filter form. A single-page frontend would add a second
language, a second container, a build step and a CORS story to render read-only HTML. The templates
read through the same repository the JSON API uses, in-process — so `/` and `/api/meetings` cannot
disagree about a record. The tradeoff: no rich interactivity. Nothing here asks for any.

### No database

The input is 42 records in two static files, rebuilt in milliseconds. A database would add a
container, a schema, a migration story and a failure mode, to buy durability that nothing here needs
— the files are the source of truth. The store sits behind a `Repository` protocol, so a persistent
implementation is a swap rather than a rewrite.

A sync builds a complete new result and then rebinds **one reference**. A reader sees the entire old
dataset or the entire new one, never a mixture — and a sync that *fails* leaves the previous dataset
serving, because nothing is replaced until everything is built.

---

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/meetings` | The unified list. Filters: `origin`, `has_conflicts`, `date_from`, `date_to`, `owner`. |
| `GET /api/meetings/{id}` | One meeting: provenance, match evidence, both raw records. |
| `GET /api/stats` | Every count on the overview page, as JSON. |
| `POST /api/sync` | Re-run the pipeline. Idempotent. |
| `GET /api/health` | Liveness, plus the resolved data directory and the last sync time. |

```bash
curl -s localhost:8000/api/stats | jq '{records_in, meetings_out, matched_pairs}'
curl -s 'localhost:8000/api/meetings?has_conflicts=true' | jq 'map(.id)'
```

---

## Testing

**582 tests, about two seconds.** The suite is not there for a coverage number — the brief says
coverage is not being evaluated. It is there because this problem is all edge cases, and three
specific things were worth pinning:

**The 24-meeting outcome was derived by hand first.** Before any matching code existed, every
expected pair was worked out from the raw files and written down in
[01-data-analysis.md](docs/ai-collaboration/01-data-analysis.md). That list is the correctness
fixture. The matcher was written against a target it could not quietly redefine.

**Mutation testing at every step.** After each module, the implementation was deliberately broken to
confirm the right test failed. That is how three genuinely untested rules were found — a widened
internal-participant check, a silently dropped record, a skipped dedupe pass — all of which the 42
real records happen never to exercise. Each was closed with a synthetic record. *A rule the dataset
cannot exercise is documentation, not behaviour, until a test pins it.*

**The documents are tested too.** `backend/tests/test_docs.py` reads the architecture document and
fails if the layout tree names a file that does not exist, if a documented route is missing, if an
undocumented one appears, or if a number quoted in prose disagrees with what the pipeline produces.

---

## Layout

```
data/                       the two source files, unmodified
backend/app/ingest/         source adapters — read, never clean
backend/app/reconcile/      parse, normalize (one per source), dedupe, signals, matcher, merge
backend/app/models/         normalized, unified (provenance), filters
backend/app/repository/     Repository protocol + in-memory store
backend/app/jobs/sync.py    the pipeline entrypoint
backend/app/api/routes.py   JSON layer — no logic
backend/app/web.py          the HTML routes, reading the same repository
backend/app/templates/      Jinja2 — list, detail, overview, 404
backend/tests/              582 tests
docs/ai-collaboration/      planning documents, written before the code
steps/                      one note per build step: the decision, the check, the result
```

---

## Documentation and AI collaboration

The brief encourages AI assistance and transparency about it. The full record is in
[docs/ai-collaboration/](docs/ai-collaboration/):

- **[01 — Data Analysis](docs/ai-collaboration/01-data-analysis.md)** — every anomaly located by
  record ID, and the hand-derived 24-meeting expected outcome.
- **[02 — Reconciliation Design](docs/ai-collaboration/02-reconciliation-design.md)** — the matching
  algorithm, merge precedence, conflict handling, and the alternatives rejected with reasons.
- **[03 — Architecture](docs/ai-collaboration/03-architecture.md)** — stack, layout, data model, and
  how the single-command requirement is met.
- **[04 — Implementation Plan](docs/ai-collaboration/04-implementation-plan.md)** — 28 ordered steps
  of one to three files each.
- **[Collaboration log](docs/ai-collaboration/README.md)** — what AI did at each phase, what was not
  delegated, and how the build loop actually ran.
- **[steps/](steps/)** — one note per step as it was built, including the ones where the plan met the
  real data and lost.

The rules in doc 02 are decisions, not outputs. The brief deliberately withholds guidance on the
ambiguous cases, which is where the actual evaluation lives — so every rule there is one I chose and
can defend, including the ones where I chose *not* to resolve something automatically.

---

## Time spent

Roughly **14 hours** of elapsed working time across three days, from the commit history:

| Phase | Elapsed | What |
|---|---|---|
| Data analysis and design | ~2.5 h | Reading both files record by record, deriving the 24 expected meetings by hand, writing docs 01–02 |
| Architecture and planning | ~1 h | Doc 03, and breaking the build into 28 checkable steps |
| Implementation | ~9 h | Steps 1–25: pipeline, API, pages — each with its own tests and a mutation check |
| Container, docs, README | ~1.5 h | Steps 26–28 |

The largest single cost was not the matching algorithm. It was deciding what *should* happen to the
ambiguous records — the duplicate, the 1-hour discrepancy, the cancelled-vs-completed status — and
that work is in doc 02 rather than in the code.

---

## Deliberately out of scope

Auth, pagination, real upstream API clients with retry and backoff, durable storage, cloud
deployment, CI/CD.

Deployment is the largest omission, so it gets the explicit note: this is built to be run locally by
a reviewer. A hosted environment would need a persistent repository implementation, real ingest
scheduling, and secrets handling before it meant anything — and sketching that in infrastructure code
without those pieces would document an intention, not a system.
