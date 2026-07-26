# Build steps

One file per step of [04-implementation-plan.md](../docs/ai-collaboration/04-implementation-plan.md).
Each file is self-contained: what to build, how to verify it by hand, and what "done" means.

**Work them in order.** Every step assumes the previous one passed its check.

## One-time local setup

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Local Python is 3.11, the container is 3.12 — so **no 3.12-only syntax** (no PEP 695 `type` aliases
or `class Foo[T]` generics).

All commands in these files are run from `backend/` with the venv active. The server command,
referenced throughout as "run the server":

```bash
uvicorn app.main:app --reload --port 8000
```

## Index

| # | Step | Phase | Status |
|---|---|---|---|
| [01](01-fastapi-app-boots.md) | FastAPI app boots | Skeleton | ✅ done |
| [02](02-settings.md) | Settings | Skeleton | ✅ done |
| [03](03-test-harness.md) | Test harness — pytest, unit + integration | Skeleton | ✅ done |
| [04](04-raw-loaders.md) | Raw JSON loaders | Skeleton | ✅ done |
| [05](05-normalized-models.md) | Normalized models | Models | ✅ done |
| [06](06-unified-models.md) | Unified models | Models | ✅ done |
| [07](07-parsing-primitives.md) | Parsing primitives | Normalize | ✅ done |
| [08](08-crm-normalizer.md) | CRM normalizer | Normalize | ✅ done |
| [09](09-calendar-normalizer.md) | Calendar normalizer | Normalize | ✅ done |
| [10](10-dedupe.md) | Intra-source dedupe | Reconcile | ✅ done |
| [11](11-match-signals.md) | Match signals | Reconcile | ✅ done |
| [12](12-matcher.md) | Matcher — correctness fixture | Reconcile | ✅ done |
| [13](13-merge.md) | Merge with provenance | Reconcile | ✅ done |
| [14](14-repository.md) | Repository | Store | ✅ done |
| [15](15-sync-job.md) | Sync job | Store | ✅ done |
| [16](16-sync-on-startup.md) | Sync on startup | Store | ✅ done |
| [17](17-api-list-detail.md) | API list and detail | JSON API | ✅ done |
| [18](18-api-filters.md) | API filters | JSON API | ✅ done |
| [19](19-api-stats-sync.md) | Stats and re-sync endpoints | JSON API | ✅ done |
| [20](20-template-plumbing.md) | Template plumbing | UI | ✅ done |
| [21](21-meeting-list-page.md) | Meeting list page | UI | ✅ done |
| [22](22-filter-controls.md) | Filter controls | UI | ✅ done |
| [23](23-detail-page.md) | Detail page | UI | ✅ done |
| [24](24-stats-page.md) | Sync overview page | UI | ✅ done |
| [25](25-resync-from-ui.md) | Re-sync from the UI | UI | ✅ done |
| [26](26-container.md) | Container and single command | Ship | ✅ done |
| [27](27-reconcile-the-docs.md) | Reconcile the docs | Ship | ✅ done |
| [28](28-readme.md) | Project README | Ship | ✅ done |

All 28 are done. Each file was written just before its step was built, so it describes what the code
actually turned out to be — including the places where the plan met the real data and lost.

## Running the tests

From `backend/`:

```bash
pytest             # 582 tests, ~2 seconds
pytest -v          # with test names
```

Every step from 04 on added its own test module, and the suite stayed green throughout.

## Invariants — re-check after every step

1. `pytest` is green; the 17-pair fixture (step 12) never regresses.
2. 42 records in, 24 meetings out. Nothing is ever dropped.
3. Templates read through the repository, never over HTTP.
