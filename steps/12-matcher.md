# Step 12 — Matcher

**Goal.** Turn the four signals into pairings: block, score, assign. This is the **correctness gate**
— every later step reads the pairs this produces, so nothing downstream is worth building until the
fixture is green.

**Files**
- `backend/app/reconcile/matcher.py`
- (tests) `backend/tests/test_matcher.py`

## Three stages

### 1. Block by date, ±1 day

Only compare records whose `event_date` is within one day. Measured on the real data: **420
combinations → 61 candidates**, with **zero true pairs lost**.

Why ±1 rather than same-day: `CAL-A4`'s UTC timestamp lands on a different local date if the timezone
rule is ever wrong. A wider block means a timezone mistake produces a *low-confidence match* the UI
badges, rather than a silent miss nobody sees. Cheap insurance at this scale.

A record with no `event_date` is never a candidate — nothing in this dataset lacks one, but a future
record that did would otherwise be compared against all 21 calendar entries on no evidence.

### 2. Score

```
score = 0.40·participants + 0.30·time + 0.20·title + 0.10·structure
```

The total is computed **as the sum of the contributions**, not independently — `MatchEvidence`
(step 6) rejects a score that doesn't equal its own breakdown, and computing it twice invites the
two to drift by floating-point noise.

Thresholds from doc 02:

| Score | Outcome |
|---|---|
| ≥ 0.70 | auto-match, `HIGH` confidence |
| 0.45 – 0.70 | merged but badged `LOW` in the UI |
| < 0.45 | not a match |

### 3. Assign greedily, highest score first

Each record is consumable once. Sorting is `(-score, crm_id, calendar_id)` — the id tie-break makes
the result **deterministic regardless of input order**, which matters because a matcher whose output
depends on dict iteration order is untestable.

Doc 02 rejects the Hungarian algorithm here: with 20×21 records and a 0.10-wide gap between the
lowest true pair (0.763) and the highest false one (0.660), optimal assignment buys nothing anyone
can perceive, and it would make the pairing unexplainable.

## The fixture

Doc 01's hand-derived outcome, produced before any of this code existed:

- **17 matched pairs**, exactly as listed
- **3 CRM-only**: `CRM-1003`, `CRM-1010`, `CRM-1020`
- **4 calendar-only**: `CAL-A3`, `CAL-A11`, `CAL-A18`, `CAL-A19`

17 + 3 + 4 = **24 meetings** — the number the whole project is judged on, reached here for the first
time. Every one of the 42 input records is accounted for: 20 CRM (17 paired + 3 alone) and 22
calendar (17 paired, with `CAL-A5`/`A6` already folded into one, + 4 alone).

## Manual test

```bash
cd backend && source venv/bin/activate
python -c "
from app.ingest.crm import load_crm
from app.ingest.calendar import load_calendar
from app.reconcile.normalize_crm import normalize_crm_records
from app.reconcile.normalize_calendar import normalize_calendar_records
from app.reconcile.dedupe import dedupe_events
from app.reconcile.matcher import match_events
crm = normalize_crm_records(load_crm())
cal = dedupe_events(normalize_calendar_records(load_calendar()))
result = match_events(crm, cal)
print(len(result.pairs), 'pairs |', len(result.unmatched_crm), 'crm-only |', len(result.unmatched_calendar), 'calendar-only')
print('total meetings:', len(result.pairs) + len(result.unmatched_crm) + len(result.unmatched_calendar))
for p in result.pairs[:3]:
    print(f'{p.crm.primary_id}/{p.calendar.primary_id} {p.evidence.score:.3f} {p.evidence.confidence.value}')
"
```

→ `17 pairs | 3 crm-only | 4 calendar-only`, `total meetings: 24`.

## Done when

- [x] The **exact 17 pairs** from doc 01, no more and no fewer.
- [x] Exactly `CRM-1003`, `CRM-1010`, `CRM-1020` unmatched on the CRM side.
- [x] Exactly `CAL-A3`, `CAL-A11`, `CAL-A18`, `CAL-A19` unmatched on the calendar side.
- [x] 17 + 3 + 4 = 24 meetings.
- [x] Every pair's evidence has four signals whose contributions sum to its score.
- [x] Shuffling either input list produces identical pairings.
- [x] No record appears in two pairs, and no record is both matched and unmatched.
- [x] Blocking loses no true pair (420 → 61 candidates).

*26 tests; 261 total. Six mutations, all caught: block window (1 failure), auto threshold (2), low
threshold (2), single-use records (5, including the fixture itself), deterministic sort (1),
participant weight (5).*

## Result — matched doc 01 on the first run

| Pair | Score | | Pair | Score |
|---|---|---|---|---|
| CRM-1005/CAL-A5 | 1.000 | | CRM-1004/CAL-A4 | 0.870 |
| CRM-1012/CAL-A13 | 0.978 | | CRM-1002/CAL-A2 | 0.860 |
| CRM-1011/CAL-A12 | 0.967 | | CRM-1016/CAL-A17 | 0.850 |
| CRM-1001/CAL-A1 | 0.960 | | CRM-1006/CAL-A7 | 0.850 |
| CRM-1019/CAL-A22 | 0.943 | | CRM-1017/CAL-A20 | 0.843 |
| CRM-1008/CAL-A9 | 0.933 | | CRM-1013/CAL-A14 | 0.766 |
| CRM-1015/CAL-A16 | 0.933 | | CRM-1007/CAL-A8 | 0.763 |
| CRM-1018/CAL-A21 | 0.913 | | | |
| CRM-1009/CAL-A10 | 0.900 | | | |
| CRM-1014/CAL-A15 | 0.870 | | | |

All 17 are `HIGH` confidence — **no pair in this dataset relies on the 0.45–0.70 band**, which is
asserted so that a regression pushing one into it fails rather than quietly degrading.

`CAL-A6` is present inside its merged event, so all 42 raw ids are still reachable
(`test_every_source_id_including_the_collapsed_duplicate`).
