# Step 10 — Intra-source dedupe

**Goal.** Collapse near-duplicates *within* a source before cross-source matching, so step 12 stays a
clean 1:1 problem.

**This is the step where a bug silently deletes a real meeting.** Over-collapsing loses data with no
error anywhere; under-collapsing leaves the planted duplicate in the output. The tests are weighted
accordingly.

**Files**
- `backend/app/reconcile/dedupe.py`
- `backend/app/config.py` (edit — `internal_domain`)
- `backend/app/models/normalized.py` (edit — `duplicates` field, `DUPLICATE_COLLAPSED` code)
- (tests) `backend/tests/test_dedupe.py`

## The rule (doc 02, Decision 2)

Two records in the same source are duplicates when **all five** hold:

1. same day, **and**
2. same organizer/owner, **and**
3. overlapping **client** participants, **and**
4. start times within 60 minutes, **and**
5. neither is `is_recurring`.

### What the data actually contains

Swept every same-day/same-organizer pair in both files:

| Source | Candidate pairs within 4h | Verdict |
|---|---|---|
| Calendar | `CAL-A5`/`CAL-A6`, 30 min apart, 2 shared attendees | the planted duplicate — collapse |
| CRM | **none** — no two records share a day *and* an owner | nothing to do |

So the rule must fire exactly once across 42 records.

### Why "client" participants, not any participants

Every internal meeting's attendees are all `@firma.com`. Requiring an overlapping *client*
participant means two internal team meetings on the same day with the same organizer can never
collapse — which is what protects `CAL-A3`/`CAL-A18` (Weekly Team Sync) even before the recurrence
check. The internal domain moves to settings (`internal_domain`, default `firma.com`) because step 11
needs the same distinction to score participant overlap.

### The recurrence carve-out is untestable against this data

`CAL-A3` and `CAL-A18` are 7 days apart, so they fail the same-day check first and never reach the
recurrence guard. The guard exists because deleting one instance of a series is a silent data-loss
bug that looks fine in testing — exactly the failure mode a *synthetic* test has to cover. Two
recurring instances at the same time on the same day must **not** collapse.

## What survives, and what is kept

`CAL-A5` is canonical (created 2025-03-02, eight days before `CAL-A6`). Doc 02 rejects
last-write-wins explicitly here: `CAL-A6` is newer *and worse* — a re-created invite with a vaguer
location ("Boston Office" vs "Boston Office - Room 301").

Nothing from the loser is discarded:

| Carried over | How |
|---|---|
| Its id | `source_ids` becomes `["CAL-A5", "CAL-A6"]` |
| Its attendees | unioned — Sandra Mills is only on `A6` and must survive |
| Its raw record | appended to a new `duplicates: list[dict]` field, so the detail view can show both |
| Its flags | unioned, **de-duplicated by (code, field, raw_value)** |

The flag de-duplication is by *value*, not by code: `A5` and `A6` each carry `TIMEZONE_ASSUMED`, but
for different raw timestamps (`11:00:00` vs `11:30:00`). Both are real assumptions and both survive,
so the stats page still reports 21 across the file. Two identical flags would collapse to one.

The survivor also gains a `DUPLICATE_COLLAPSED` flag at `info` — the detail view should be able to
say "this meeting was entered twice" without the reader inferring it from a longer id list.

## Manual test

```bash
cd backend && source venv/bin/activate
python -c "
from app.ingest.calendar import load_calendar
from app.reconcile.normalize_calendar import normalize_calendar_records
from app.reconcile.dedupe import dedupe_events
events = normalize_calendar_records(load_calendar())
deduped = dedupe_events(events)
print(len(events), '->', len(deduped))
m = next(e for e in deduped if 'CAL-A5' in e.source_ids)
print(m.source_ids, m.location)
print(sorted(p.display for p in m.participants))
print(len(m.duplicates), 'raw duplicate(s) retained')
"
```

→ `22 -> 21`; ids `['CAL-A5', 'CAL-A6']`; location the *more specific* `Boston Office - Room 301`;
three participants including `sandra.mills@pinnaclegp.com`; one retained raw record.

```bash
pytest -q
```

## Done when

- [x] Calendar 22 → 21; CRM 20 → 20.
- [x] `CAL-A5`/`CAL-A6` collapse into one event holding both ids.
- [x] Sandra Mills survives the collapse.
- [x] `CAL-A3`/`CAL-A18` do **not** collapse.
- [x] Synthetic: two recurring instances, same day, same time, same client — **not** collapsed.
- [x] Synthetic: two records 90 minutes apart — **not** collapsed.
- [x] Synthetic: two records sharing only internal attendees — **not** collapsed.
- [x] The loser's raw record and distinct flags are retained on the survivor.
- [x] Dedupe is order-independent: shuffling the input yields the same groups.

*25 tests; 196 total. Six mutations, all caught: recurrence carve-out (2 failures),
client-overlap requirement (2), internal-domain exclusion (1), 60-minute window (2),
created_at survivor choice (1), attendee union (1).*

## A purity bug the tests caught

The first implementation absorbed duplicates into the **caller's own objects**. A test comparing the
timezone-flag census before and after dedupe measured the same mutated list twice and "agreed" with
itself — reporting 22 where step 9 had established 21.

Doc 03 requires the reconcile stages to be pure, so the fix was in the implementation, not the test:
survivors are now `model_copy(deep=True)`. `test_dedupe_does_not_mutate_its_input` pins it, because
this class of bug is invisible until some later step happens to read the input again.
