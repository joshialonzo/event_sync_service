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
| [03](03-test-harness.md) | Test harness — pytest, unit + integration | Skeleton | next |
| [04](04-raw-loaders.md) | Raw JSON loaders | Skeleton | |
| 05 | Normalized models | Models | |
| 06 | Unified models | Models | |
| 07 | Parsing primitives | Normalize | |
| 08 | CRM normalizer | Normalize | |
| 09 | Calendar normalizer | Normalize | |
| 10 | Intra-source dedupe | Reconcile | |
| 11 | Match signals | Reconcile | |
| 12 | Matcher — correctness fixture | Reconcile | |
| 13 | Merge with provenance | Reconcile | |
| 14 | Repository | Store | |
| 15 | Sync job | Store | |
| 16 | Sync on startup | Store | |
| 17 | API list and detail | JSON API | |
| 18 | API filters | JSON API | |
| 19 | Stats and re-sync endpoints | JSON API | |
| 20 | Template plumbing | UI | |
| 21 | Meeting list page | UI | |
| 22 | Filter controls | UI | |
| 23 | Detail page | UI | |
| 24 | Sync overview page | UI | |
| 25 | Re-sync from the UI | UI | |
| 26 | Container and single command | Ship | |
| 27 | Reconcile the docs | Ship | |
| 28 | Project README | Ship | |

Only written-up steps are linked. The rest are written just before they're built, so each one can
describe what the code actually turned out to be — see
[04-implementation-plan.md](../docs/ai-collaboration/04-implementation-plan.md) for their scope.

## Running the tests

From `backend/`, once step 03 is in place:

```bash
pytest -q          # the suite
pytest -v          # with test names
```

From step 04 on, every step adds its own test module and the suite must stay green.

## Invariants — re-check after every step

1. `pytest` is green; the 17-pair fixture (step 12) never regresses.
2. 42 records in, 24 meetings out. Nothing is ever dropped.
3. Templates read through the repository, never over HTTP.
