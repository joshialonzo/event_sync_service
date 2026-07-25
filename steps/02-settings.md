# Step 02 — Settings

**Goal.** One env-driven settings object, so nothing downstream hard-codes a path or a timezone.

**Files**
- `backend/app/config.py`
- `backend/app/main.py` (edit)

## What to build

`config.py` — a `pydantic_settings.BaseSettings` subclass:

| Setting | Default | Notes |
|---|---|---|
| `data_dir` | `Path(__file__).resolve().parents[2] / "data"` | Repo `data/`, resolved absolute. Env: `DATA_DIR` |
| `timezone` | `"America/New_York"` | The one timezone every timestamp is coerced to (doc 02, Decision 1) |

Expose a module-level `settings = Settings()` — or a `get_settings()` with `functools.lru_cache` if
you prefer the injectable form. Either is fine; be consistent from here on.

`main.py` — extend the health payload:

```json
{"status": "ok", "data_dir": "/abs/path/to/data", "timezone": "America/New_York"}
```

## Manual test

Run the server, then:

```bash
curl -s localhost:8000/api/health | python -m json.tool
```

→ `data_dir` is an **absolute** path that exists. Confirm it:

```bash
ls "$(curl -s localhost:8000/api/health | python -c 'import json,sys; print(json.load(sys.stdin)["data_dir"])')"
```

→ lists `crm_events.json` and `calendar_events.json`.

Then prove the env override works — stop the server and restart it as:

```bash
DATA_DIR=/tmp uvicorn app.main:app --port 8000
```

→ health now reports `/tmp`. Stop it and restart normally.

## Done when

- [x] Health reports an absolute `data_dir` that contains both JSON files.
- [x] `DATA_DIR=/tmp` changes the reported value.
- [x] No other file contains a literal path to `data/`.

## Notes

- `parents[2]` from `backend/app/config.py` is the repo root: `app` → `backend` → root. Verify by
  printing it rather than trusting the count.
- Keeping `data_dir` in settings is what lets step 26 mount the directory at a different path inside
  the container without touching code.
