# Step 03 — Test harness

**Goal.** A working pytest setup with one unit test and one integration test, so every step after
this has somewhere to put its assertions and a single command that says "still correct".

This is deliberately placed before the pipeline work. The reconciliation logic in steps 7–13 is
verified against a hand-derived fixture (24 meetings, 17 exact pairs), and writing that fixture is
only cheap if the harness already exists. Retrofitting tests after the matcher is written is how you
end up asserting whatever the code happens to do.

**Files** — five, the only step that exceeds three:
- `backend/requirements.txt` (edit — add `httpx`)
- `backend/pytest.ini`
- `backend/tests/conftest.py`
- `backend/tests/test_config.py` — unit
- `backend/tests/test_health.py` — integration

*Why the exception:* a harness has an irreducible minimum — dependency, config, fixtures, and one
test of each kind to prove both paths work. Splitting it would produce a step whose only check is
"pytest collected 0 tests", which verifies nothing.

## What to build

### `requirements.txt` — add `httpx`

`fastapi.testclient.TestClient` is a thin wrapper over `httpx`, which is **not** currently installed
(pytest and starlette are). Add it pinned, consistent with the rest of the file:

```
httpx==0.28.1
```

Then `pip install -r requirements.txt` again.

### `pytest.ini`

```ini
[pytest]
testpaths = tests
pythonpath = .
addopts = -q --strict-markers
```

`pythonpath = .` is the important line: it puts `backend/` on `sys.path` so `from app.config import
...` resolves when pytest is run from `backend/`, without needing an installed package or a
`conftest.py` path hack.

### `tests/conftest.py`

Fixtures the later steps reuse:

| Fixture | Scope | Purpose |
|---|---|---|
| `client` | session | `TestClient(app)` for integration tests |
| `data_dir` | session | The real `data/` path from settings — steps 4+ read the fixture files through this |
| `clean_settings` | function | `get_settings.cache_clear()` before *and* after, so a test that sets `DATA_DIR` cannot leak into the next |

`clean_settings` matters because `get_settings` is `lru_cache`d (step 2). Without a reset, the first
test to call it pins the config for the whole session and any later env-override test silently
asserts nothing.

Use `monkeypatch.setenv` for environment manipulation — it undoes itself at test teardown, unlike
`os.environ[...] = ...`.

### `tests/test_config.py` — unit

Cover the three behaviours step 2 actually promises:

1. **Defaults** — `data_dir` points at the repo `data/`, is absolute, exists, and contains both JSON
   files; `timezone` is `America/New_York`.
2. **Env override** — `DATA_DIR=/tmp` changes the value. Assert on `Path("/tmp").resolve()`, **not**
   the literal string `/tmp`: on macOS it resolves to `/private/tmp`, and a string comparison makes
   this test pass on Linux and fail on your machine.
3. **Relative resolution** — a relative `DATA_DIR` comes back absolute (the `field_validator`).

Construct `Settings()` directly in these tests rather than calling `get_settings()`, so you are
testing the class and not the cache.

### `tests/test_health.py` — integration

Through `TestClient`, so it exercises real routing and serialization rather than calling the function:

1. `GET /api/health` → 200, `status == "ok"`, and the payload has `data_dir` and `timezone` keys.
2. The reported `data_dir` exists on disk — this is the check that catches a broken container mount.
3. `GET /openapi.json` → 200 and lists `/api/health`, proving the app object is wired.
4. An unknown path → 404, proving routing is real and not a catch-all.

## Manual test

```bash
cd backend && source venv/bin/activate
pip install -r requirements.txt      # picks up httpx

pytest -v
```

Expect every test to pass, with names that read as sentences. Then confirm the harness itself is
sound rather than vacuously green:

```bash
pytest --collect-only     # both modules discovered, no import errors
pytest -q                 # the quiet form used by later steps
```

Prove the tests can actually fail — temporarily change `"status": "ok"` to `"status": "fine"` in
`app/main.py`, run `pytest -q`, and confirm `test_health` fails. **Revert it.** A test suite that has
never failed is not evidence of anything.

## Done when

- [x] `pytest -v` is green and reports at least 6 tests. *(12)*
- [x] `pytest --collect-only` shows both modules with no import errors.
- [x] Breaking the health payload makes exactly the health test fail, and reverting makes it pass.
- [x] The `DATA_DIR` override test passes on macOS (i.e. it compares resolved paths).
- [x] `httpx` is pinned in `requirements.txt`.

## Notes

- Run `pytest` from `backend/`, not the repo root — `pytest.ini` and `pythonpath` are anchored there.
- VS Code's Test Explorer is already configured for this (`.vscode/settings.json` sets
  `python.testing.pytestArgs` to `["tests"]` with `cwd` at `backend/`), so tests should appear in the
  flask icon after a window reload.
- From step 4 on, each step adds its own test module and the suite is expected to stay green — that
  is invariant #1 in the plan.
