# Step 21 — Meeting list page

**Goal.** The 24 meetings as a readable table, with the two indicators the brief asks for: where each
record came from, and where the sources disagree.

**Files**
- `backend/app/web.py` (edit)
- `backend/app/templates/meetings.html`
- `backend/app/models/unified.py` (edit — `worst_flag_severity`)
- (tests) `backend/tests/test_web_meetings.py`

## Columns

| Column | Content |
|---|---|
| Date / Time | `event_date`, and the start time or `—` for a date-only meeting |
| Meeting | Title, with the client beneath it |
| Owner | Relationship owner, or the organizer's email for calendar-only records |
| Source | `Both` / `CRM only` / `Calendar only` badge |
| Signals | Conflict badge, and a data-quality badge at the worst severity present |

## `worst_flag_severity` belongs on the model

"Which quality badge does this row get?" is a question about the data, not about markup. Computing it
in the template means the detail page and the stats page each get their own chance to answer it
differently, so it is a property on `UnifiedMeeting`: error beats warning beats info, `None` when
clean.

Distribution across the 24: **1 error, 3 warning, 20 info**, and none are flag-free — every meeting
carries at least a `TIMEZONE_ASSUMED`.

## What the badges must not do

Doc 02's argument against badge inflation applies to the rendering too. **The conflict badge appears
on 4 of 24 rows**, not on the 8 granularity or 3 absence cases. A row where the CRM said
"HQ - Conference Room B" and the calendar said "Conference Room B" is *not* flagged — those are shown
on the detail page as alternatives, where the space exists to explain them.

The quality badge is the opposite case: it appears on every row, because every meeting carries at
least one flag. That is why it shows the **severity** rather than a count — 20 rows reading "info" is
noise; 1 row reading "error" is the signal.

## The detail link

Rows link to `/meetings/{id}`, which **does not exist until step 23**. The markup belongs to this
step and the route to that one; clicking a row returns 404 in between. Called out here rather than
discovered later.

## Manual test

```bash
cd backend && source venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

Open <http://localhost:8000/> and check against the API:

- 24 rows, earliest first — the first is the Q1 Portfolio Review on 2025-03-10.
- Exactly 4 rows carry a conflict badge; they are the same 4 that
  `curl 'localhost:8000/api/meetings?has_conflicts=true' | jq -r '.[].id'` returns.
- `cal-a11` (the thin record) renders without a client and without breaking the row.
- `crm-1007-cal-a8` shows a time even though the CRM had none — the calendar supplied it.
- One row shows an `error` quality badge: `crm-1008-cal-a9`, whose date was malformed.

## Done when

- [x] 24 rows in date order.
- [x] Origin badges total 17 / 3 / 4.
- [x] Exactly 4 rows carry a conflict badge, matching the API's `has_conflicts=true` set.
- [x] Exactly 1 row carries an `error` quality badge.
- [x] A meeting with no client renders an em dash rather than "None".
- [x] Titles are escaped, not injected.
- [x] The page and `/api/meetings` agree on every id.

*19 tests; 434 total.*

Rendered output, flattened to text:

```
24 rows

2025-03-10 | 14:00 | Q1 Portfolio Review / David Park       | Sarah Chen             | Both          | info
2025-03-11 | 09:00 | Weekly Team Sync                       | sarah.chen@firma.com   | Calendar only | info
2025-03-12 | 10:00 | Portfolio Walkthrough / Mark Johnson   | Sarah Chen             | Both          | Conflict info
2025-03-13 | 15:00 | Due Diligence Review / Patricia Langley| Sarah Chen             | Both          | Conflict info
2025-03-14 | 15:00 | Introductory Call / Rachel Torres      | James Wu               | CRM only      | info
2025-03-14 | 16:00 | Pipeline Review                        | Sarah Chen             | Both          | info
2025-03-14 | 18:00 | Client Reception                       | priya.sharma@firma.com | Calendar only | warning

conflict badges: 4 | error: 1 | warning: 3 | info: 20
```

Row 4 is the DST discovery made visible: `CRM-1004`/`CAL-A4` carries a conflict badge because
converting the UTC timestamp leaves the two sources an hour apart. Doc 01 originally called that pair
an exact match.

### Tests assert against the API, not against markup

`test_the_page_and_the_api_agree_on_every_id`, `test_rows_are_in_the_stores_order`, and
`test_the_conflicted_rows_are_the_ones_the_api_names` all compare the page to `/api/meetings`. A test
that checked the page in isolation would keep passing if the two views drifted apart — which is the
one failure server-rendering from a shared store is supposed to make impossible.