# Step 27 — Reconcile the docs with the built repo

**Goal.** Doc 03 was written before a line of code existed. That was the right order — the
architecture is a decision, not a description — but it means every concrete claim in it has been a
prediction until now. This step makes it a description, and adds the tests that keep it one.

**Files**
- `docs/ai-collaboration/03-architecture.md` (edit)
- `docs/ai-collaboration/README.md` (edit)
- (tests) `backend/tests/test_docs.py`

## What had drifted

| Claim | Reality |
|---|---|
| `reconcile/normalize.py` | `parse.py`, `normalize_crm.py`, `normalize_calendar.py` |
| "`reconcile/` is four files" | seven |
| Templates: base, meetings, detail, stats | plus `not_found.html` |
| `models/` as one line | `normalized.py`, `unified.py`, `filters.py` |
| no `dependencies.py` | the process-wide store lives there |
| no `steps/`, no `Dockerfile`, no `requirements.txt` | all three exist |
| "Uvicorn with hot-reload" in the container | no `--reload` in the image |
| `GET /api/health` — "Liveness" | also reports data dir, timezone, meeting count, last sync |
| Pages absent from the API surface | `/`, `/meetings/{id}`, `/stats`, `POST /sync`, `/static/app.css` |
| "`POST /api/sync` rebinds a reference" | two entry points, one `sync_now()` |

None of these were wrong when written. They are what a document looks like after 26 steps of building
against it — which is the argument for checking it mechanically rather than by re-reading.

## The three splits that were not in the plan

Doc 03 sketched `reconcile/` as four files. It is seven, and the three extra ones each earned it
while being built, so the document now says why rather than just how many:

- **`parse.py` apart from the normalizers** — every malformed value is a parsing question, and
  parsing is the one part that must never raise.
- **One normalizer per source** — the two sources disagree about what a record *is*; a single
  function with `if source == …` is two functions sharing a name.
- **`signals.py` apart from `matcher.py`** — the four scorers are what a reviewer will argue with,
  and they are pure functions over two events. Blocking and assignment is a different question.

## Testing prose

`test_docs.py` reads the markdown and checks it against the running app:

- Every path in the layout tree exists. The tree is parsed by indentation, so `routes.py` is checked
  as `backend/app/api/routes.py` — a name on its own proves nothing.
- Every documented route exists **and** every route is documented. The second direction is the one
  that rots: endpoints get added and tables do not.
- The numbers quoted in prose — "42 records in, 24 meetings out, 17 matched, 4 conflicts", "a 24-row
  list", "the input is 42 records" — are compared against `/api/stats`, not restated. A test that
  hard-codes 24 only proves it agrees with itself.
- The `Repository` methods doc 03 names are exactly the protocol's members. The document claims that
  seam is what makes the store swappable; a protocol that grew a method behind its back would make
  that claim false in the only way that matters.
- Every relative link in every document resolves.
- The word in "Why `reconcile/` is seven files" is parsed and counted. Numbers spelled as words are
  the ones that go stale silently.

## Manual test

Re-read [03-architecture.md](../docs/ai-collaboration/03-architecture.md) against the repo: every
path, route, and number in it is true. Then:

```bash
cd backend && source venv/bin/activate && pytest tests/test_docs.py
```

## Done when

- [x] The layout tree names every file that exists in `reconcile/`, `models/` and `templates/`.
- [x] The tree includes `dependencies.py`, `Dockerfile`, `requirements.txt` and `steps/`.
- [x] The API section lists the HTML routes as well as the JSON ones.
- [x] The container description matches step 26 — read-only mount, non-root, healthcheck, no reload.
- [x] Every number in doc 03 matches `/api/stats`.
- [x] The collaboration log records how the implementation phase actually ran.
- [x] Every relative link in `docs/ai-collaboration/` resolves.

*19 tests; 557 total.*

### Mutation run

Twelve deliberate edits to the document, each caught by exactly the test that should catch it:
a renamed module in the tree, a dropped template, a stale file count, a route removed from the table,
a route present in the app but absent from the table, wrong headline numbers, a wrong row count, a
protocol method dropped from the seam description, a renamed setting, a changed start command, a
broken cross-reference, and a stale link cap.

### The honest note in the collaboration log

The log now records the two occasions the documents were wrong about the data and the code was right
— the DST arithmetic in doc 01, and the missing `CRM-1010` empty string — because both were caught by
tests asserting the document's own numbers. That is the same mechanism as this step, applied earlier
and by accident. Writing it down is cheaper than the alternative reading, which is that the documents
were right all along.
