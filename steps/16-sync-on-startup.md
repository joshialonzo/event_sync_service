# Step 16 — Sync on startup

**Goal.** The service is populated the moment it is reachable. No seeding step, no first-request
latency spike, no "why is the list empty?".

**Files**
- `backend/app/dependencies.py`
- `backend/app/main.py` (edit)
- (tests) `backend/tests/test_startup.py`

## Why startup and not lazily

Doc 03 makes this a design constraint, not a convenience: *"The API runs the ingest and
reconciliation pipeline on startup, so the dataset is populated the moment the service is reachable —
there is no seeding step to forget."* The single `docker compose up` has to produce a working service,
and a lazily-populated store means the first request pays for the sync and a concurrent second
request either blocks or races it.

It also means a **broken `DATA_DIR` fails at boot**, loudly, instead of returning an empty list that
looks like a working service with no meetings.

## Implementation

`dependencies.py` owns the process-wide repository and the FastAPI dependency:

```python
_repository = InMemoryRepository()

def get_repository() -> Repository: ...
def sync_now() -> SyncRunSummary: ...   # run the pipeline and publish
```

`main.py` gets a `lifespan` handler that calls `sync_now()` before the app accepts traffic, and logs
the outcome:

```
INFO  event-sync: sync complete - 24 meetings from 42 records (17 matched, 4 conflicts)
```

That log line is the fastest way to know the service is healthy, and it is what the manual test
below reads.

**Why a module-level repository rather than `app.state`.** The templates in steps 20–25 render from
the same store, and reaching `request.app.state` from a template context is more indirection than a
module attribute. The `Repository` protocol is still the seam — `get_repository()` is the only way
routes obtain it, so a test can override the dependency without touching the module.

**Lifespan, not `@app.on_event("startup")`**, which is deprecated in current FastAPI.

## `/api/health` grows a sync status

Health currently answers "is the process up?". It should also answer "does it have data?":

```json
{"status": "ok", "data_dir": "...", "timezone": "America/New_York",
 "meetings": 24, "last_sync": "2025-07-25T12:00:00-04:00"}
```

A service that boots but reconciled nothing is not healthy, and this is the one endpoint a reviewer
hits first.

## Manual test

```bash
cd backend && source venv/bin/activate
uvicorn app.main:app --port 8000
```

The log must show the sync line **before** the "Application startup complete" banner. Then:

```bash
curl -s localhost:8000/api/health | python -m json.tool
```

→ `meetings: 24` and a `last_sync` timestamp.

And the failure path, which matters more:

```bash
DATA_DIR=/tmp/nope uvicorn app.main:app --port 8001
```

→ the process **exits** with `FileNotFoundError`. It must not serve an empty dataset.

## Done when

- [x] The sync log line appears during startup, before the app accepts requests.
- [x] `/api/health` reports 24 meetings and a `last_sync` timestamp.
- [x] A `TestClient` context manager triggers the sync.
- [x] `get_repository()` returns the same instance on every call.
- [x] A bad `DATA_DIR` prevents startup rather than yielding an empty store.
- [x] `sync_now()` is idempotent — calling it twice leaves 24 meetings, not 48.
- [x] Overriding `get_repository` in a test swaps the store without touching module state.

*12 tests; 340 total.*

### Verified on a real server, not just `TestClient`

Startup ordering — the sync completes **before** the app accepts traffic:

```
INFO  event-sync: sync complete - 24 meetings from 42 records (17 matched, 4 conflicts)
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8010
```

Health:

```json
{"status": "ok", "data_dir": ".../data", "timezone": "America/New_York",
 "meetings": 24, "last_sync": "2026-07-25T23:12:38.275330-04:00"}
```

The failure path, which matters more than the success path:

```
$ DATA_DIR=/tmp/nope uvicorn app.main:app --port 8011
FileNotFoundError: [Errno 2] No such file or directory: '/private/tmp/nope/crm_events.json'
ERROR:    Application startup failed. Exiting.
exit code: 3        # and the port refuses connections
```

### One change to an earlier step

`test_health_reports_the_resolved_configuration` (step 3) pinned health's exact key set, so adding
`meetings` and `last_sync` failed it — the test doing its job. Updated to the new contract, plus
`test_health_reports_that_data_was_actually_loaded`, since a process that booted but reconciled
nothing is not healthy.