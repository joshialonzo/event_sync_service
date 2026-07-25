# Step 01 — FastAPI app boots

**Goal.** A running FastAPI process with a liveness endpoint. No data, no logic.

**Files**
- `backend/app/__init__.py` (empty)
- `backend/app/main.py`

## What to build

`main.py`:

- `app = FastAPI(title="Event Sync Service", version="0.1.0")`
- `GET /api/health` → `{"status": "ok"}`

Nothing else. Do not import ingest, models, or config — they don't exist yet, and this step exists to
prove the process starts.

## Manual test

```bash
cd backend
source venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

In a second terminal:

```bash
curl -s localhost:8000/api/health
```

→ `{"status":"ok"}`

Then open <http://localhost:8000/docs> in a browser — the OpenAPI page lists `/api/health`.

## Done when

- [x] The server starts with no traceback and no warnings about missing modules.
- [x] `curl` returns the JSON above with HTTP 200.
- [x] `/docs` renders and shows the endpoint.
- [x] `--reload` picks up an edit to `main.py` without a manual restart.

## Notes

- Run `uvicorn` from `backend/`, not the repo root — `app` must be importable as a top-level package.
- If you get `ModuleNotFoundError: No module named 'app'`, you are in the wrong directory or the venv
  is not active.
