# Step 07 — Parsing primitives

**Goal.** Pure functions that turn one raw string into one typed value, reporting *how* they had to
do it. No records, no models, no I/O — steps 8 and 9 assemble these into normalizers.

**Files**
- `backend/app/reconcile/__init__.py`
- `backend/app/reconcile/parse.py`
- (tests) `backend/tests/test_parse.py`

## The shape every parser returns

```python
class Parsed(NamedTuple):
    value: Any | None
    code: FlagCode | None
```

A parser never raises and never guesses silently: it returns what it got and the code for how it got
it. The **normalizer** attaches the field name and raw value (steps 8-9), because `parse.py` doesn't
know whether the string it was handed came from `meeting_date` or `start_time`.

This split is what makes doc 02 Decision 1 enforceable: a defect can't be reported unless a parser
returns a code, and a parser can't drop a record because it has no way to.

## What the data actually contains

Audited from the files rather than assumed:

| Input | Values present |
|---|---|
| CRM `meeting_date` | ISO `2025-03-10` ×19, plus `03-15/2025` (`CRM-1008`) |
| CRM `meeting_time` | `HH:MM` ×19, plus `null` (`CRM-1007`) |
| Calendar `start_time` | ISO with seconds ×21, plus one `Z`-suffixed (`CAL-A4`) |
| Calendar `end_time` | ISO with seconds ×21, plus `2025-03-14T20:00` (`CAL-A11`, no seconds) |
| `created_at` (both files) | **always** `Z`-suffixed UTC, 42/42 |

## What to build

| Function | Returns | Flag it can emit |
|---|---|---|
| `parse_date(raw)` | `date` | `MALFORMED_DATE` if a fallback pattern was needed; `UNPARSABLE_DATE` if none matched |
| `parse_time(raw)` | `time` | `TIME_MISSING` when null/blank |
| `parse_datetime(raw)` | aware `datetime` | `MALFORMED_DATETIME` if a fallback was needed, `TIMEZONE_ASSUMED` if it was naive |
| `to_eastern(value)` | aware `datetime` | `TIMEZONE_ASSUMED` when the input was naive |
| `combine(date, time)` | aware `datetime` | — |
| `repair_email(raw)` | `str` or `None` | `MALFORMED_EMAIL` on repair, `NON_EMAIL_ATTENDEE` when it isn't an address |
| `normalize_status(raw)` | `MeetingStatus` | `UNKNOWN_STATUS` outside both vocabularies |

### Date parsing — ordered, not clever

Try ISO first, then a **small explicit list** of tolerated patterns. `%m-%d/%Y` is what catches
`CRM-1008`. Never infer a date that no pattern matches; return `UNPARSABLE_DATE` and let the record
through without one.

The ordering matters and the list stays short on purpose: adding `%d-%m-%Y` would make `03-15/2025`
ambiguous with a day-first reading, and the dataset gives no way to tell which was meant. Doc 01
resolves `CRM-1008` as 2025-03-15 from its calendar counterpart `CAL-A9`, so month-first is the only
supported fallback.

### Timezone — the one inference in the project

Doc 01 section D: `CAL-A4` is the only `Z`-suffixed *event* timestamp in either file, and treating it
literally would put it five hours from its CRM counterpart and break an otherwise unambiguous match.
So:

- **Naive** timestamps are assumed Eastern, and every one gets `TIMEZONE_ASSUMED` at `info`. The
  assumption is visible in the UI rather than hidden in a code comment.
- **Aware** timestamps are converted to Eastern. No flag — nothing was assumed.

Use `zoneinfo.ZoneInfo` with the tz name from settings, not a fixed `-04:00` offset. The dataset sits
just after the 2025-03-09 DST change, so every *event* is EDT (UTC-4) while the January and February
`created_at` values are EST (UTC-5). A hard-coded offset would be right for the events and wrong for
the metadata.

### Email repair

`[at]` → `@` with `MALFORMED_EMAIL` (`CAL-A16`). A string with no `@` after repair is **not** an
error: `"external-guests"` is a real fact about the meeting, returned as `None` with
`NON_EMAIL_ATTENDEE` so the caller keeps it as an opaque label.

## Manual test

```bash
cd backend && source venv/bin/activate
python -c "
from app.reconcile.parse import parse_date, parse_datetime, repair_email, normalize_status
print(parse_date('03-15/2025'))
print(parse_datetime('2025-03-13T19:00:00Z'))
print(parse_datetime('2025-03-14T20:00'))
print(repair_email('raj.patel[at]atlasvc.com'))
print(repair_email('external-guests'))
print(normalize_status('Cancelled'), normalize_status('confirmed'))
"
```

Expect `2025-03-15` with `MALFORMED_DATE`; `19:00Z` arriving as **15:00-04:00**; the truncated
timestamp parsed with `MALFORMED_DATETIME`; the repaired address with `MALFORMED_EMAIL`; and
`external-guests` as `None` with `NON_EMAIL_ATTENDEE`.

```bash
pytest -q
```

## Done when

- [x] `03-15/2025` parses to 2025-03-15 with `MALFORMED_DATE`; a clean ISO date carries no flag.
- [x] An unparsable date returns `None` with `UNPARSABLE_DATE` rather than raising or guessing.
- [x] `2025-03-13T19:00:00Z` becomes **15:00** Eastern — the `CAL-A4` case. *(Doc 01 said 14:00;
      see the correction below.)*
- [x] A naive timestamp gets `TIMEZONE_ASSUMED`; an aware one does not.
- [x] `2025-03-14T20:00` parses with `MALFORMED_DATETIME`.
- [x] A February `created_at` converts at EST while a March event converts at EDT.
- [x] `[at]` is repaired with a flag; `external-guests` returns `None` with `NON_EMAIL_ATTENDEE`.
- [x] Both status vocabularies map onto the enum; an unknown string is `UNKNOWN` plus a flag.
- [x] No function in `parse.py` raises on any input, including `None` and `""`.

*40 tests; 122 total. Replacing the DST-aware zone with a fixed -04:00 offset fails
`test_dst_boundary_is_respected_across_the_dataset` and nothing else.*

## Correction to doc 01, section D

Writing these tests surfaced an arithmetic error in the data analysis. Doc 01 stated that
`CAL-A4`'s `2025-03-13T19:00:00Z` equals **14:00** Eastern and therefore matches `CRM-1004` exactly.
It equals **15:00**: DST began 2025-03-09, so the offset is -4, not -5.

The decision stands — converting leaves the pair 1 hour apart, reading the `Z` literally would leave
them 5 apart — but two downstream facts changed and are recorded in doc 01:

1. The pair scores ~0.75 on time proximity, not 1.0. Still far above the 0.70 threshold.
2. `CRM-1004`/`CAL-A4` is a **real 1-hour time conflict** for step 13, a second instance of the
   `CRM-1016` case rather than the clean match doc 01 first described.

## Notes

- `parse.py` imports only from `app.models` and `app.config`. It must stay free of record shapes so
  steps 8 and 9 can use the same primitives on differently-shaped inputs.
- Every function is pure. That is what lets these tests run without the data files.
