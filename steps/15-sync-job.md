# Step 15 — Sync job

**Goal.** One function that runs the whole pipeline and returns a complete `SyncResult` — the object
the repository publishes and the API reads.

**Files**
- `backend/app/jobs/__init__.py`
- `backend/app/jobs/sync.py`
- (tests) `backend/tests/test_sync.py`

This is the first module that touches I/O *and* logic. It stays a thin assembly: every decision was
made in steps 7–13, and anything resembling a rule appearing here would mean a stage left something
undone.

## The pipeline

```
load ──► normalize ──► dedupe ──► match ──► merge ──► order ──► summarise
```

Dedupe runs on **both** sources, not just the calendar. It is a no-op on the CRM (no two records
share a day and an owner, verified in step 10), but running it on one source only would make the
pipeline asymmetric for a reason that is a property of this dataset rather than of the design.

## Ordering

`by_date` sorts on `(event_date, start)`. Every meeting in this dataset has both, but the sort must
tolerate neither: a date-only meeting sorts to the start of its day rather than crashing the sync.

Sorting happens **once, here**. The normalizers deliberately preserve input order so that every
earlier test can be positional; the store is where a canonical order gets decided.

## The summary — every number the stats page reports

Computed from the pipeline's own intermediate results, not recounted from the output:

| Field | Value on this dataset | Source |
|---|---|---|
| `crm_records_in` / `calendar_records_in` | 20 / 22 | length of the raw loads |
| `duplicates_collapsed` | 1 | records lost to dedupe, both sources |
| `meetings_out` | 24 | |
| `matched_pairs` | 17 | |
| `crm_only` / `calendar_only` | 3 / 4 | |
| `low_confidence_matches` | 0 | pairs in the 0.45–0.70 band |
| `conflicts_by_kind` | contradiction 4, granularity 8, absence 3 | |
| `conflicts_by_field` | start_time 2, location 1, status 1 | |
| `flags_by_code` | `TIMEZONE_ASSUMED` 40, then eight others at 1–4 | |
| `flags_by_severity` | info 47, warning 3, error 1 | |

`records_in` (42) is a property on the summary rather than a stored field — a total that can
disagree with its parts is a bug waiting to happen.

**`TIMEZONE_ASSUMED` is 40, not 21.** The count spans both sources: 19 CRM records with a time plus
21 calendar records. It is the loudest number on the stats page and it should be — every one of those
timestamps is a guess the service made and is owning up to.

**`low_confidence_matches` is 0.** No pair in this dataset relies on the badged band. The field exists
because a reviewer needs to know that, and a zero is an answer.

## Manual test

```bash
cd backend && source venv/bin/activate
python -c "
from app.jobs.sync import run_sync
r = run_sync()
s = r.summary
print(len(r.meetings), 'meetings |', s.records_in, 'records in |', s.matched_pairs, 'matched')
print('origins:', s.crm_only, 'crm-only,', s.calendar_only, 'calendar-only')
print('conflicts:', s.conflicts_by_kind)
print('flags:', s.flags_by_severity)
print('first:', r.ordered_meetings[0].id, r.ordered_meetings[0].event_date)
print('last :', r.ordered_meetings[-1].id, r.ordered_meetings[-1].event_date)
"
```

→ `24 meetings | 42 records in | 17 matched`.

## Done when

- [x] `run_sync()` returns a `SyncResult` with 24 meetings and a `by_date` covering all of them.
- [x] 42 records in, nothing dropped anywhere in the chain.
- [x] Meetings are ordered by date, earliest first (`crm-1001-cal-a1` on 2025-03-10 through
      `crm-1020` on 2025-04-02).
- [x] Every summary number matches the table above.
- [x] Two consecutive runs produce identical ids, order, and counts.
- [x] `run_sync(data_dir=...)` reads from an alternate directory.
- [x] A sync over a missing directory raises rather than publishing zero meetings.

*25 tests; 329 total. Four mutations: ordering (2 failures), conflict-kind counting (1), flag census
(3) — and one that survived, below.*

## The mutation that survived

Replacing `dedupe_events(crm_events)` with a pass-through broke **nothing**: dedupe is a no-op on the
real CRM file, so "runs on both sources" was an untested claim. `test_dedupe_runs_on_the_crm_too`
now syncs a synthetic CRM file containing an actual duplicate pair, and fails under that mutation.

Third time this pattern has appeared (steps 8, 13, 15): a rule the real data cannot exercise needs a
synthetic test, or it is documentation rather than behaviour.