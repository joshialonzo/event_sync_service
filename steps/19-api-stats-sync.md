# Step 19 — Stats and re-sync endpoints

**Goal.** Finish the JSON layer: the numbers that let a reviewer verify the reconciliation in five
seconds, and the endpoint that re-runs it.

**Files**
- `backend/app/api/routes.py` (edit)
- (tests) `backend/tests/test_api_stats.py`

## `GET /api/stats`

Doc 03: *"`GET /api/stats` exists because it is how a reviewer verifies the reconciliation in five
seconds without reading a line of code: 42 records in, 24 meetings out, 17 matched, 4 conflicts."*

It returns the `SyncRunSummary` the sync job already built (step 15) — the route recounts nothing.
A stats endpoint that re-derived its numbers from the meetings could disagree with the pipeline that
produced them, which is the one thing this endpoint must never do.

**`records_in` needed a fix in the model, not a wrapper in the route.** It is derived (a total that
can disagree with its parts is a bug waiting to happen), and a plain `@property` serializes to
nothing — it appears in neither the payload nor the schema. The first attempt here was a
`StatsResponse(SyncRunSummary)` subclass redeclaring it as a field; that worked but emitted a pydantic
warning about shadowing the parent attribute, which was the design telling on itself. Marking the
property `@computed_field` on `SyncRunSummary` puts it in both, and deletes the subclass.

## `POST /api/sync`

Re-runs the pipeline and republishes. Returns the new summary, so a caller gets the outcome without
a second request.

**Idempotent**, and that is a property of the pipeline rather than a promise here: `run_sync` reads
the same files and produces the same 24 meetings with the same ids, then `replace_all` swaps one
reference. Pressing it twice leaves 24 meetings, not 48. Only `generated_at` changes.

**Status code 200, not 202.** The work is done by the time the response is written — there is no
background job to poll, and 202 would imply one.

**No lock.** Two concurrent syncs would each build a complete result and one would win; the loser's
work is discarded and no reader ever sees a mixture (step 14's atomicity test). A lock would add a
failure mode to protect against an outcome that is already correct.

**What a failed re-sync must not do:** leave the store empty. If the data files vanish between
startup and a re-sync, the exception propagates as a 500 and **the previous dataset stays published**
— a service that keeps serving the last good data beats one that empties itself because a disk
hiccuped. This is a consequence of `run_sync` building the whole result before `replace_all` is
called, and it is worth a test because the obvious "clear then repopulate" implementation gets it
wrong.

## Manual test

```bash
curl -s localhost:8000/api/stats | jq
curl -s -X POST localhost:8000/api/sync | jq '{meetings_out, matched_pairs}'
curl -s localhost:8000/api/stats | jq '.meetings_out'      # still 24 after the re-sync
```

The stats payload must show `records_in: 42`, `meetings_out: 24`, `matched_pairs: 17`,
`conflicts_by_field` summing to 4, and `flags_by_code.TIMEZONE_ASSUMED: 40`.

## Done when

- [x] `GET /api/stats` returns every field from step 15's table, including `records_in: 42`.
- [x] The numbers match `run_sync()` exactly — the route recounts nothing.
- [x] `POST /api/sync` returns 200 and the new summary.
- [x] A re-sync leaves 24 meetings with identical ids and order.
- [x] `generated_at` advances across a re-sync; nothing else changes.
- [x] A re-sync that fails leaves the previous dataset intact and serving.
- [x] Both endpoints appear in `/docs`.

*16 tests; 403 total.*

Live output — the whole reconciliation in one payload:

```json
{"crm_records_in": 20, "calendar_records_in": 22, "duplicates_collapsed": 1,
 "meetings_out": 24, "matched_pairs": 17, "crm_only": 3, "calendar_only": 4,
 "low_confidence_matches": 0,
 "conflicts_by_kind": {"granularity": 8, "absence": 3, "contradiction": 4},
 "conflicts_by_field": {"status": 1, "start_time": 2, "location": 1},
 "flags_by_code": {"TIMEZONE_ASSUMED": 40, ...},
 "flags_by_severity": {"info": 47, "error": 1, "warning": 3},
 "records_in": 42}
```

`test_stats_agrees_with_the_meetings_endpoint` performs the cross-check a reviewer would do by hand:
`meetings_out` against `GET /api/meetings`, and the conflict total against
`?has_conflicts=true`.