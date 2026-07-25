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

| # | Step | Phase |
|---|---|---|
| [01](01-fastapi-app-boots.md) | FastAPI app boots | Skeleton |
| [02](02-settings.md) | Settings | Skeleton |
| [03](03-raw-loaders.md) | Raw JSON loaders | Skeleton |
| [04](04-normalized-models.md) | Normalized models | Models |
| [05](05-unified-models.md) | Unified models | Models |
| [06](06-parsing-primitives.md) | Parsing primitives | Normalize |
| [07](07-crm-normalizer.md) | CRM normalizer | Normalize |
| [08](08-calendar-normalizer.md) | Calendar normalizer | Normalize |
| [09](09-dedupe.md) | Intra-source dedupe | Reconcile |
| [10](10-match-signals.md) | Match signals | Reconcile |
| [11](11-matcher.md) | Matcher — correctness fixture | Reconcile |
| [12](12-merge.md) | Merge with provenance | Reconcile |
| [13](13-repository.md) | Repository | Store |
| [14](14-sync-job.md) | Sync job | Store |
| [15](15-sync-on-startup.md) | Sync on startup | Store |
| [16](16-api-list-detail.md) | API list and detail | JSON API |
| [17](17-api-filters.md) | API filters | JSON API |
| [18](18-api-stats-sync.md) | Stats and re-sync endpoints | JSON API |
| [19](19-template-plumbing.md) | Template plumbing | UI |
| [20](20-meeting-list-page.md) | Meeting list page | UI |
| [21](21-filter-controls.md) | Filter controls | UI |
| [22](22-detail-page.md) | Detail page | UI |
| [23](23-stats-page.md) | Sync overview page | UI |
| [24](24-resync-from-ui.md) | Re-sync from the UI | UI |
| [25](25-container.md) | Container and single command | Ship |
| [26](26-reconcile-docs.md) | Reconcile the docs | Ship |
| [27](27-readme.md) | Project README | Ship |

## Invariants — re-check after every step

1. `pytest` is green; the 17-pair fixture (step 11) never regresses.
2. 42 records in, 24 meetings out. Nothing is ever dropped.
3. Templates read through the repository, never over HTTP.
