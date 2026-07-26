# Step 23 — Detail page

**Goal.** The page that answers both of the brief's frontend requirements at once: *where did each
piece of data come from*, and *why do you think these two records are the same meeting*.

**Files**
- `backend/app/web.py` (edit)
- `backend/app/templates/detail.html`
- `backend/app/templates/not_found.html`
- (tests) `backend/tests/test_web_detail.py`

This step also makes step 21's row links work — they have pointed at `/meetings/{id}` since then.

## Four sections

### 1. Merged record

Every provenance field as a row: value, a source label, and — where the other source said something
different — the alternative beneath it. Conflicts are marked in the value itself, not only in a badge
at the top, so the disagreement is visible where the reader is looking.

On `crm-1002-cal-a2` that renders as:

| Field | Value | Source | Alternative |
|---|---|---|---|
| location | Zoom - https://zoom.us/j/98765432100 | calendar | **crm: NYC Office - 30th Floor** ← conflict |
| title | Portfolio Walkthrough | crm | calendar: Summit Advisors - Portfolio Discussion |
| owner_name | Sarah Chen | crm | calendar: sarah.chen@firma.com |

Nine of the eleven fields carry an alternative on a matched meeting, and only one is a conflict —
which is the point doc 02 makes about badge inflation, shown rather than argued.

### 2. Match evidence

The per-signal breakdown, with the arithmetic visible:

```
participants  0.40 × 1.00 = 0.400   owner organised it, client found, company = domain
time          0.30 × 1.00 = 0.300   same start time
title         0.20 × 0.80 = 0.160   shared: advisors, portfolio, summit, walkthrough
structure     0.10 × 0.00 = 0.000   locations differ; In-Person vs virtual location
                            0.860   high confidence
```

Doc 02 rejected a black-box matcher on the grounds that a reviewer checks pairs by hand. This is
where that promise is kept: the weights, the scores, the products, and the total, so the number can
be argued with.

Absent on single-source meetings — there was no pairing decision to explain.

### 3. Side-by-side raw records

The untouched source dicts, as key/value tables rather than JSON blobs, with the keys behind a
conflict highlighted. A CRM-only meeting shows "No calendar record" in the empty column — an
explanation, not a blank.

Highlighting needs a small map from unified field to source keys (`start_time` → CRM's
`meeting_date` + `meeting_time`, calendar's `start_time`), because the two sources spell the same
fact differently. That map lives in `web.py` next to the route that uses it.

### 4. Data quality

Flags with their severity, field, and the raw value that caused them — so `MALFORMED_DATE` shows
`"03-15/2025"` rather than just a code.

## 404

An unknown id renders an HTML page with a link back to the list, not FastAPI's JSON error body. A
reviewer following a stale link should get a page, not `{"detail": "..."}`.

## Manual test

```bash
uvicorn app.main:app --reload --port 8000
```

- <http://localhost:8000/meetings/crm-1002-cal-a2> — location shows both values, marked as a
  conflict; the evidence table sums to 0.860.
- <http://localhost:8000/meetings/crm-1005-cal-a5-cal-a6> — **two** calendar raw records, and the
  `DUPLICATE_COLLAPSED` flag.
- <http://localhost:8000/meetings/crm-1010> — CRM-only; the calendar column says "No calendar
  record" and there is no evidence section.
- <http://localhost:8000/meetings/cal-a11> — the thin record: no attendees, no location, no CRM side.
- <http://localhost:8000/meetings/nope> — an HTML 404 with a link home.
- Clicking any row on `/` now arrives here.

## Done when

- [x] Every one of the 24 ids renders a page.
- [x] The merged record shows a source label per field and the alternative where one exists.
- [x] The four conflict meetings mark the conflicting field; the other 20 mark nothing.
- [x] The evidence table's contributions sum to the displayed total.
- [x] Single-source meetings show no evidence section and an explanatory empty column.
- [x] `crm-1005-cal-a5-cal-a6` shows both calendar records.
- [x] Flags show severity, field, and raw value.
- [x] An unknown id returns 404 as HTML.
- [x] Raw values are escaped.

*25 tests; 479 total. All 24 ids render, and every `href` on the list page resolves.*

Rendered merged record for `crm-1002-cal-a2`:

```
Field         Value                                  From      Other source said
title         Portfolio Walkthrough                  crm       calendar: Summit Advisors - Portfolio Discussion
start time    2025-03-12 10:00:00-04:00              calendar  —
location      Zoom - https://zoom.us/j/98765432100   calendar  crm: NYC Office - 30th Floor   ← conflict
participants  sarah.chen@firma.com (organizer),
              mark.johnson@summitadv.com, …          calendar  crm: Sarah Chen (organizer), Mark Johnson
owner name    Sarah Chen                             crm       calendar: sarah.chen@firma.com
```

Nine fields carry an alternative; exactly one is a conflict. Doc 02's badge-inflation argument,
shown rather than argued.

### Two defects the rendering surfaced

**Participants printed as Python.** The field holds a list of dumped models, so the first render put
`[{'email': 'sarah.chen@firma.com', 'domain': ..., 'is_organizer': True}, …]` in the middle of the
page — technically the data, practically unreadable. Now rendered as names with the organizer marked,
and a test asserts the dict repr never returns.

**The raw panels used `<h2>` inside a `<h2>` section**, which both misdescribed the document outline
and broke every test that reads the page by section. They are `<h3>` now — the failing tests were
right about the markup, not just about themselves.