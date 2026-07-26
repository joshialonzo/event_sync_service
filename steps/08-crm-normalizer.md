# Step 08 — CRM normalizer

**Goal.** 20 raw CRM dicts → 20 `NormalizedEvent`s. Never raises, never drops, records every defect
as a flag.

**Files**
- `backend/app/reconcile/normalize_crm.py`
- `backend/app/models/normalized.py` (edit — one new flag code, see below)
- (tests) `backend/tests/test_normalize_crm.py`

## Field mapping

| Raw | Normalized | Notes |
|---|---|---|
| `crm_id` | `source_ids[0]` | |
| `subject` | `title` | |
| `notes` | `text` | `""` (`CRM-1010`) becomes `None` — same meaning, one representation |
| `meeting_date` | `event_date` | `parse_date`; `MALFORMED_DATE` on `CRM-1008` |
| `meeting_time` | (combined) | `parse_time`; `TIME_MISSING` on `CRM-1007` |
| date + time | `start` | `combine` → Eastern-aware. **`end` stays `None`** — the CRM has no end time at all |
| `location` | `location` | |
| `client_name`, `client_company` | same | |
| `relationship_owner` | `owner_name` **and** `organizer` | The CRM's only notion of who convened the meeting |
| `meeting_type` | `meeting_type` | `In-Person` / `Virtual` / `Internal` |
| `status` | `status` + `status_raw` | Enum plus the original string, for display |
| `created_at` | `created_at` | Always `Z`-suffixed; converted, no flag |
| whole record | `raw` | untouched |

## Participants from a source that has no emails

The CRM stores `"David Park"`; the calendar stores `david.park@meridiancap.com`. Bridging those is
step 11's job, but the normalizer still builds `Participant`s so both sources present the same shape:

- The **owner** becomes a participant with `is_organizer=True`, `email=None`.
- The **client** becomes a participant when there is one.

`email` is `None` for both — the CRM genuinely has no addresses. Inventing
`david.park@meridiancap.com` here would be fabricating data that then looks like evidence when the
matcher scores it.

## Flags this step can emit

| Code | Fires on | Count |
|---|---|---|
| `MALFORMED_DATE` | `CRM-1008` — `03-15/2025` | 1 |
| `TIME_MISSING` | `CRM-1007` — null time | 1 |
| `TIMEZONE_ASSUMED` | every record with a time, since date+time are naive | 19 |
| `INTERNAL_NO_CLIENT` | the four `Internal` records with no client | 4 |
| `PLACEHOLDER_CLIENT` | `CRM-1017` — `client_name` is literally `"Multiple"` | 1 |

### The new code: `PLACEHOLDER_CLIENT`

`CRM-1017`'s client is the string `"Multiple"` (doc 01 notes it; no flag was planned for it). Adding
one, at `info`:

- **It is not a person.** Step 11 derives an email local-part from `client_name` (`"David Park"` →
  `david.park`). Left unmarked, `"Multiple"` would generate a `multiple` token and be scored as
  though it were a name — a fabricated signal.
- **The UI should say so.** "Client: Multiple" is confusing; "Client: Multiple ⓘ placeholder, not a
  single client" is honest.

`info` severity because the record is not defective — a dinner with several clients is a real thing
the CRM had no field for.

## Rules that are not obvious

**Every record gets `TIMEZONE_ASSUMED` if it has a time.** The CRM has no timezone information at
all, so building an Eastern timestamp is exactly the same assumption made for naive calendar records
and is flagged identically. Consistency here is what makes the stats page's count mean something.

**A null client on an `Internal` record is `info`, not an error** (doc 02): an internal meeting
legitimately has no client. Flagging it as a defect would put four false problems on the stats page
and make `CRM-1008`'s genuinely corrupt date look routine.

**`CRM-1007` keeps its `event_date` and gets no `start`.** The model's invariant (step 5) means a
date-only event is representable; step 11 scores it a neutral 0.5 on time rather than 0.

## Manual test

```bash
cd backend && source venv/bin/activate
python -c "
from app.ingest.crm import load_crm
from app.reconcile.normalize_crm import normalize_crm_records
events = normalize_crm_records(load_crm())
print(len(events))
from collections import Counter
print(Counter(f.code.value for e in events for f in e.flags))
"
```

→ `20`, and the flag counts in the table above.

```bash
pytest -q
```

## Done when

- [x] 20 in, 20 out, in input order, with no exception on any record.
- [x] `CRM-1008` has a parsed date of 2025-03-15 *and* `MALFORMED_DATE`.
- [x] `CRM-1007` has `event_date`, `start is None`, and `TIME_MISSING`.
- [x] The four internal records carry `INTERNAL_NO_CLIENT` at `info`.
- [x] `CRM-1017` carries `PLACEHOLDER_CLIENT`.
- [x] `CRM-1010`'s `""` notes normalize to `None`.
- [x] Owners are participants with `is_organizer=True` and `email is None`.
- [x] Every event's `raw` is the untouched input dict.

*25 tests; 147 total. Observed census: `TIMEZONE_ASSUMED` 19, `INTERNAL_NO_CLIENT` 4,
`MALFORMED_DATE` 1, `TIME_MISSING` 1, `PLACEHOLDER_CLIENT` 1 — matching the table above.*

### Two tests that only exist because mutation testing found the gaps

The first pass of this suite passed while the normalizer was mutated to (a) flag *every* null client
as `INTERNAL_NO_CLIENT` regardless of meeting type, and (b) silently drop records with no
`meeting_date`. Both survived because the real data cannot distinguish them: every null client in the
file *is* internal, and every record *does* have a date.

Real data alone therefore cannot verify either rule. Two synthetic-record tests close it:
`test_a_null_client_outside_an_internal_meeting_is_not_the_internal_flag` and
`test_the_batch_normalizer_never_filters`. Both now fail under their mutation.
