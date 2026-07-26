# Step 24 — Sync overview page

**Goal.** The page that answers "did the reconciliation work?" without reading code or JSON — the
`/api/stats` numbers, plus a data-quality list that links to the records it is talking about.

**Files**
- `backend/app/web.py` (edit)
- `backend/app/templates/stats.html`
- (tests) `backend/tests/test_web_stats.py`

## Four sections

### 1. Headline tiles

`42 records in → 24 meetings out`, then matched / CRM-only / calendar-only, duplicates collapsed,
conflicts, low-confidence matches. The two numbers doc 03 says a reviewer checks first sit together
at the top.

### 2. Conflicts

By kind (contradiction 4, granularity 8, absence 3) and by field, with the four contradiction
meetings linked by name. Showing the kinds side by side is what makes the design decision legible:
15 fields differ between the sources, and only 4 of those differences are disagreements.

### 3. Data quality

One row per flag code: severity, count, and links to the affected meetings.

**Capped at six links, then "+N more".** `TIMEZONE_ASSUMED` fires on **all 24 meetings** — a row of
24 links is a wall that hides the eight codes below it, each of which affects one or two records and
is the interesting part.

Rows are ordered by severity, not by count. The single `error` (`CRM-1008`'s corrupt date) must not
sit below 40 timezone assumptions.

### 4. Run metadata

When the sync ran, and where the data came from. The re-sync button lands in step 25.

## The links are the point

Doc 03 asks for "the data-quality flag list linking to the affected records". A count on its own is
a claim; a link is an invitation to check it. `MALFORMED_DATE: 1` is unfalsifiable until it is
`MALFORMED_DATE: 1 — crm-1008-cal-a9`, one click from the raw record showing `"03-15/2025"`.

The joining happens in `web.py`: the summary carries counts, and turning those into "which meetings"
is a presentation concern the store should not have to model.

## Manual test

```bash
uvicorn app.main:app --reload --port 8000
```

Open <http://localhost:8000/stats>:

- The tiles read 42 in / 24 out / 17 matched, matching `curl localhost:8000/api/stats`.
- The conflict section lists exactly four meetings; each link opens a detail page whose badge agrees.
- `MALFORMED_DATE` is the only `error` row and sits at the top of the quality table.
- `TIMEZONE_ASSUMED` shows 40 and a truncated link list, not 24 links.
- The nav's "Sync overview" link works from every page.

## Done when

- [x] Every number matches `/api/stats`.
- [x] The four conflict meetings are listed and linked.
- [x] Each flag code appears once with its severity and count.
- [x] The `error` row sorts above the `info` rows.
- [x] Flag links resolve to detail pages that actually carry that flag.
- [x] `TIMEZONE_ASSUMED`'s link list is truncated with a "+18 more" marker.
- [x] The page renders with an empty store rather than dividing by zero.

*19 tests; 498 total.*

Rendered:

```
Last run 2026-07-26 01:42 EDT from …/data

42 records in (20 CRM + 22 calendar)   24 meetings out   17 matched
3 CRM only — never booked              4 calendar only — never logged   1 duplicates collapsed

Conflicts
4 genuine contradictions. 8 differences of specificity and 3 absences are recorded but not
flagged — marking those too would put a badge on nearly every record.

  location    crm-1002-cal-a2
  start time  crm-1004-cal-a4, crm-1016-cal-a17
  status      crm-1009-cal-a10

Data quality
No match relied on the low-confidence band — nothing here was merged on a hunch.

  error    MALFORMED_DATE      Date required a fallback pattern to parse   1   crm-1008-cal-a9
  warning  MALFORMED_DATETIME  Timestamp was missing a component          1   cal-a11
  warning  TIME_MISSING        No time of day was supplied                1   crm-1007-cal-a8
  warning  MALFORMED_EMAIL     Email address required repair              1   crm-1015-cal-a16
  info     TIMEZONE_ASSUMED    Naive timestamp assumed to be Eastern     40   …6 links, +18 more
```

### Sorting by severity, not by count

`TIMEZONE_ASSUMED` outnumbers everything 40 to 1. Sorted by count, the single genuinely corrupt
record — `CRM-1008`'s `"03-15/2025"` — sits at the bottom of the table under 40 rows of an assumption
the service is merely disclosing. Severity-first puts the one thing needing a human at the top.

### The links are what make the counts checkable

`test_flag_links_reach_meetings_that_carry_the_flag` and
`test_the_conflict_links_open_meetings_that_agree` follow the links and assert the destination
actually shows what the row claimed. A count nobody can verify is a claim; that is precisely what
this page exists not to be.