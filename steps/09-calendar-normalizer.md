# Step 09 — Calendar normalizer

**Goal.** 22 raw calendar dicts → 22 `NormalizedEvent`s, same rules as step 8: never raises, never
drops, every defect recorded.

**Files**
- `backend/app/reconcile/normalize_calendar.py`
- (tests) `backend/tests/test_normalize_calendar.py`

## Field mapping

| Raw | Normalized | Notes |
|---|---|---|
| `event_id` | `source_ids[0]` | |
| `title` | `title` | |
| `description` | `text` | |
| `start_time` | `start` | `parse_datetime` → Eastern-aware |
| `end_time` | `end` | Same. The calendar is the only source with an end |
| `start` | `event_date` | Derived by the model's invariant (step 5) |
| `location` | `location` | |
| `organizer` | `organizer` | An email, unlike the CRM's owner name |
| `attendees[]` | `participants` | See below |
| `status` | `status` + `status_raw` | `confirmed` / `tentative` |
| `is_recurring` | `is_recurring` | The dedupe carve-out (step 10) |
| `created_at` | `created_at` | Z-suffixed, converted |
| whole record | `raw` | untouched |

`client_name`, `client_company`, `owner_name`, `meeting_type` stay `None` — the calendar has no such
fields, and populating them by guessing at a domain would put invented data where step 13's
precedence rules expect a genuine absence.

## Participants

Each attendee goes through `repair_email`, which returns one of three outcomes:

| Attendee | Result | Flag |
|---|---|---|
| `sarah.chen@firma.com` | `email` set, `domain` derived | — |
| `raj.patel[at]atlasvc.com` (`CAL-A16`) | repaired to `raj.patel@atlasvc.com`, **`raw` keeps the original** | `MALFORMED_EMAIL` |
| `external-guests` (`CAL-A20`) | `email=None`, kept as an opaque label | `NON_EMAIL_ATTENDEE` |

**The organizer appears exactly once.** In the real data the organizer is always also in
`attendees` — so building participants from both lists naively would double them. The rule: build
from `attendees`, mark the one matching `organizer` with `is_organizer=True`, and append the
organizer only if the list didn't contain them. `CAL-A11` has an organizer and *zero* attendees, so
it exercises the append branch.

## The timezone flag, counted once per record

`parse_datetime` flags each naive timestamp, and most records have two (`start_time` and `end_time`).
Attaching both would report 42 timezone assumptions across 21 records and make the stats page
overstate the problem by 2×. **One `TIMEZONE_ASSUMED` per record**, on `start_time`.

`CAL-A4` is the exception with no flag at all: it states its offset, so nothing is assumed. That is
the single record the whole timezone inference in doc 01 rests on.

## Flags this step can emit

| Code | Fires on | Count |
|---|---|---|
| `TIMEZONE_ASSUMED` | every record except `CAL-A4` | 21 |
| `MALFORMED_DATETIME` | `CAL-A11` — `end_time` missing seconds | 1 |
| `MALFORMED_EMAIL` | `CAL-A16` — `[at]` | 1 |
| `NON_EMAIL_ATTENDEE` | `CAL-A20` — `external-guests` | 1 |

**`CAL-A11`'s empty attendee list gets no flag.** A meeting with no invitees is thin, not corrupt,
and the UI shows the emptiness directly. Inventing a code for it would inflate the data-quality
count with something no one can act on.

## Manual test

```bash
cd backend && source venv/bin/activate
python -c "
from collections import Counter
from app.ingest.calendar import load_calendar
from app.reconcile.normalize_calendar import normalize_calendar_records
events = normalize_calendar_records(load_calendar())
print(len(events))
print(Counter(f.code.value for e in events for f in e.flags))
a4 = next(e for e in events if e.primary_id == 'CAL-A4')
print('CAL-A4 start:', a4.start, '| flags:', a4.flag_codes)
"
```

→ `22`, the counts above, and `CAL-A4` at **15:00-04:00** with no flags.

```bash
pytest -q
```

## Done when

- [x] 22 in, 22 out, in input order, no exception on any record.
- [x] `CAL-A4` normalizes to 15:00 Eastern with **no** `TIMEZONE_ASSUMED`.
- [x] Every other record carries exactly one `TIMEZONE_ASSUMED`.
- [x] `CAL-A16`'s attendee is repaired, flagged, and keeps its original string in `raw`.
- [x] `CAL-A20` keeps `external-guests` as a participant with `email is None`.
- [x] `CAL-A11` parses its truncated `end_time` with `MALFORMED_DATETIME` and has one participant
      (its organizer) despite an empty attendee list.
- [x] `CAL-A3`, `CAL-A7`, `CAL-A18` carry `is_recurring=True`.
- [x] No event has `client_name`, `client_company`, `owner_name`, or `meeting_type` set.

*24 tests; 171 total. Observed census: `TIMEZONE_ASSUMED` 21, `MALFORMED_DATETIME` 1,
`MALFORMED_EMAIL` 1, `NON_EMAIL_ATTENDEE` 1.*

Four mutations, all caught: appending the organizer unconditionally (3 failures), flagging the
timezone per timestamp instead of per record (2), removing duplicate collapse (1), never marking the
organizer (2).

## Notes

Same lesson as step 8: verify the rules the *real data cannot distinguish* with synthetic records —
here, that the organizer is not duplicated when they are also an attendee, and that a record with no
attendees still normalizes.
