# Step 13 — Merge with provenance

**Goal.** Turn matched pairs and unmatched records into `UnifiedMeeting`s where every field carries
its source, its alternatives, and whether the sources genuinely disagree.

> **The service reconciles; it does not adjudicate.** Where the sources disagree, the merged record
> presents a default *and* preserves the disagreement. — doc 02

**Files**
- `backend/app/reconcile/merge.py`
- (tests) `backend/tests/test_merge.py`

## Precedence (doc 02, Decision 4)

| Field group | Winner | Why |
|---|---|---|
| Time, end time, participants | **Calendar** | System of record for logistics — it is what people look at on the day and edit when things move |
| Client, company, owner, notes, meeting type | **CRM** | System of record for the relationship; calendar attendee lists are incomplete and full of internal staff |
| Location | **Calendar, unless granularity** | See below |
| Status | **Neither by default** | See below |
| Title | **CRM** | Not in doc 02's table — a call made here. The CRM `subject` states the business purpose ("Annual Allocation Review"); the calendar `title` is written for inbox scanning and prefixes the company ("Horizon Wealth - Year-End Review"). Differing titles are a **convention difference, never a conflict** |

## Classifying disagreement — swept from the real pairs first

Running the 17 pairs through a naive containment check produced two results that contradict doc 01.
Both are recorded here because they change what the UI shows.

### Finding 1 — `CRM-1017` is granularity, but containment can't see it

`"The Palm - DC"` vs `"The Palm Restaurant"`. Doc 01 lists this under "compatible at different
granularity", but **neither string contains the other**, so a containment test calls it a
contradiction. Sharing a significant token (`palm`) is what makes them compatible.

**Rule:** equal → agreement; one contains the other → granularity, more specific wins; sharing a
significant token → granularity, longer value wins; otherwise → contradiction.

Sweeping all 17 pairs with that rule gives **one** location contradiction — `CRM-1002`, the
In-Person/Zoom case the brief names.

### Finding 2 — three status *differences*, only one is a *contradiction*

| Pair | CRM | Calendar | Reading |
|---|---|---|---|
| `CRM-1001`/`CAL-A1` | Completed | confirmed | **Lifecycle drift.** The CRM knows the meeting happened; the calendar never updates after the fact |
| `CRM-1007`/`CAL-A8` | Scheduled | confirmed | **Near-synonyms.** Two vocabularies for "it's on" |
| `CRM-1009`/`CAL-A10` | Cancelled | confirmed | **Contradiction.** A cancelled meeting showing as confirmed sends someone to an empty room |

Doc 01 lists only the third. Flagging all three would put a conflict badge on 3 of 17 meetings for
what is mostly vocabulary drift — the badge-inflation doc 02 warns about.

**Rule:** `CANCELLED` against anything else is a contradiction. `COMPLETED` against a
scheduled/confirmed/tentative calendar entry is lifecycle drift, not disagreement. `SCHEDULED` and
`CONFIRMED` are synonyms. `TENTATIVE` vs `CONFIRMED` *is* a contradiction (booked or not is a real
question) — it does not occur in this data, but the rule should not be silent about it.

**Default value:** the more conservative status, which for `CRM-1009` means `Cancelled` — for
filtering only. Both values are shown and the conflict is badged. A human decides.

### Time

Different start times are a contradiction; there is no benign reading of "13:00" vs "15:00".
Two occur:

- `CRM-1016`/`CAL-A17` — 120 minutes (doc 01 lists it)
- `CRM-1004`/`CAL-A4` — 60 minutes, the residue of the DST correction from step 7

## Expected output

**4 contradictions across 24 meetings**: `CRM-1002` (location), `CRM-1004` (time), `CRM-1016` (time),
`CRM-1009` (status). Doc 02's summary line predicts "4 conflicts" — the count matches, though the
composition differs from doc 01's table by one in each direction.

Non-conflicts that must **not** raise the badge: 5 location granularity cases, 2 location absences
(`CRM-1007`, `CRM-1018`), 2 status lifecycle drifts, and every title.

## Manual test

```bash
cd backend && source venv/bin/activate
python -c "
from app.reconcile.merge import merge_all
from app.ingest.crm import load_crm
from app.ingest.calendar import load_calendar
from app.reconcile.normalize_crm import normalize_crm_records
from app.reconcile.normalize_calendar import normalize_calendar_records
from app.reconcile.dedupe import dedupe_events
from app.reconcile.matcher import match_events
crm = normalize_crm_records(load_crm())
cal = dedupe_events(normalize_calendar_records(load_calendar()))
meetings = merge_all(match_events(crm, cal))
print(len(meetings), 'meetings')
for m in meetings:
    if m.has_conflicts:
        print(' ', m.id, m.conflicting_fields)
"
```

→ 24 meetings, 4 with conflicts.

## Done when

- [x] 24 meetings: 17 `both`, 3 `crm_only`, 4 `calendar_only`.
- [x] Exactly 4 meetings have conflicts, on the fields listed above.
- [x] `CRM-1002`'s location shows both values, `conflict=True`, kind `contradiction`.
- [x] `CRM-1017`'s location is `granularity`, **not** a conflict.
- [x] `CRM-1001`'s status difference is not a conflict; `CRM-1009`'s is, and defaults to cancelled.
- [x] `CRM-1018`'s location is `absence` — the calendar value wins, no badge.
- [x] `CRM-1001`'s location keeps the *more specific* `HQ - Conference Room B` (CRM), overriding
      calendar precedence.
- [x] Every meeting carries its raw records from both sides; `CRM-1005` carries two calendar records.
- [x] Flags from both sources are unioned onto the meeting.

*32 tests; 293 total.*

## Final census

```
24 meetings | {'both': 17, 'crm_only': 3, 'calendar_only': 4}
kinds:      | {'granularity': 8, 'absence': 3, 'contradiction': 4}
conflicts:  | crm-1002-cal-a2 [location], crm-1004-cal-a4 [start_time],
            | crm-1009-cal-a10 [status], crm-1016-cal-a17 [start_time]
```

## Two rules that only exist because the counts looked wrong

**A `conflict_kind` records a judgement, not a difference.** The first implementation labelled every
precedence decision `granularity` — 76 of them, including every title and every "Sarah Chen" against
"sarah.chen@firma.com". Those are differences of convention, not of specificity. Fields resolved by
plain precedence now keep the alternative with **no kind at all**, and `granularity` drops to 8
genuine cases.

**`absence` is opt-in per field.** Marking every null as an absence would report 17 "missing clients"
— the calendar has no `client_name` field to begin with. The marker is set only for `location` and
`start_time`, the fields both sources genuinely model, where a null is an editorial gap rather than a
schema difference.

## A test gap mutation testing found twice

Deleting the location-containment rule broke **no test**: on the real data every contained pair also
shares a long token, so the shared-token rule silently covers it. It stops being redundant when the
distinguishing words are short or stopworded ("Room 4A" tokenises to nothing significant).

The first synthetic test I added still didn't catch it — it exercised the calendar-more-specific
branch while the mutation removed the CRM-more-specific one. The test is now parametrized over both
directions, and each branch fails independently.
