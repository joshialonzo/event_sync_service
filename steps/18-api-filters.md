# Step 18 — API filters

**Goal.** The five query parameters from doc 03, implemented once so the JSON endpoint and the HTML
form in step 22 cannot disagree about what `?origin=crm_only` means.

**Files**
- `backend/app/models/filters.py`
- `backend/app/api/routes.py` (edit)
- (tests) `backend/tests/test_api_filters.py`

## Where the filtering lives, and why not in the repository

Doc 03's `Repository` protocol is `list_meetings()` with no arguments. Filtering is a *view* concern —
both the JSON route and the HTML page apply it to the same full list — so it goes in a pure function
over meetings rather than into the store's interface. The protocol stays as documented, and a future
persistent repository is not obliged to reimplement predicate pushdown for 24 rows.

```python
class MeetingFilters(BaseModel):
    origin: Origin | None
    has_conflicts: bool | None
    date_from: date | None
    date_to: date | None
    owner: str | None

def apply_filters(meetings, filters) -> list[UnifiedMeeting]
```

## The five filters

| Parameter | Behaviour |
|---|---|
| `origin` | Exact: `both` / `crm_only` / `calendar_only`. An unknown value is a 422, not an empty list |
| `has_conflicts` | `true` keeps the 4 contradiction meetings; `false` keeps the other 20 |
| `date_from` / `date_to` | **Inclusive** on both ends, on `event_date` |
| `owner` | Normalized substring — see below |

Filters combine with AND. Omitting all of them returns all 24.

## Owner matching is not a substring match

Owner values are heterogeneous, and this is a property of the data rather than an implementation
detail:

| Meeting kind | `owner_name` value | Source |
|---|---|---|
| Matched or CRM-only | `"Sarah Chen"` | CRM `relationship_owner` |
| Calendar-only | `"sarah.chen@firma.com"` | calendar `organizer` — there is no CRM record to take a name from |

A plain `"Sarah Chen" in owner` test returns 11 meetings and **silently hides the 3 calendar-only
meetings Sarah organised** — which are exactly the records this tool exists to surface, since a
calendar entry with no CRM record means client time is not being logged (doc 02, Decision 5).

**The rule:** reduce both sides to lowercase alphanumerics, taking the local part of an email first.
`"Sarah Chen"` → `sarahchen`; `"sarah.chen@firma.com"` → `sarahchen`. Then substring-match, so
`?owner=sarah` and `?owner=Sarah%20Chen` both work.

Expected: `?owner=sarah chen` → **14** meetings (11 + 3), not 11.

## Manual test

```bash
curl -s 'localhost:8000/api/meetings' | jq length                          # 24
curl -s 'localhost:8000/api/meetings?origin=crm_only' | jq length          # 3
curl -s 'localhost:8000/api/meetings?origin=calendar_only' | jq length     # 4
curl -s 'localhost:8000/api/meetings?has_conflicts=true' | jq length       # 4
curl -s 'localhost:8000/api/meetings?date_from=2025-03-20' | jq length
curl -s 'localhost:8000/api/meetings?owner=sarah%20chen' | jq length       # 14
curl -s 'localhost:8000/api/meetings?owner=sarah&origin=calendar_only' | jq length   # 3
curl -s -o /dev/null -w '%{http_code}\n' 'localhost:8000/api/meetings?origin=nonsense'  # 422
```

## Done when

- [x] No parameters returns all 24.
- [x] `origin` returns 17 / 3 / 4, and rejects an unknown value with 422.
- [x] `has_conflicts=true` returns the 4 conflict meetings; `false` returns the other 20.
- [x] Date bounds are inclusive — `date_from` equal to the earliest date keeps that meeting.
- [x] `owner=sarah chen` returns 14, including the calendar-only meetings.
- [x] Filters AND together (`owner=sarah&origin=calendar_only` → 3).
- [x] An impossible combination returns `[]` and 200, not an error.
- [x] Every parameter appears in the OpenAPI schema for step 22's form to mirror.

*30 tests; 387 total. Four mutations, all caught: email local-part extraction (1 failure), inclusive
`date_from` (2), `has_conflicts=false` as a real filter (1), searching alternatives (1).*

## The owner filter searches alternatives too — discovered by a failing test

I first assumed the three owners would partition the 24 meetings. They do not, and the reason is
worth keeping:

`crm-1013-cal-a14` has **Sarah Chen** as the CRM relationship owner and **Priya Sharma** as the
calendar organizer — she is the *alternative* on the merged `owner_name` field. Searching either name
finds the meeting.

That is the documented behaviour ("relationship owner or organizer"), and the right one: a filter
that ignored the alternative would answer "nothing" to *"what did Priya convene?"* for a meeting she
demonstrably convened. So `owner=sarah` → 14, `owner=priya` → 2, and the two sets overlap by that one
meeting. The test now asserts the overlap rather than a partition that was never true.