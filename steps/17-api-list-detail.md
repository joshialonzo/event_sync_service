# Step 17 — API list and detail

**Goal.** Expose the reconciled data over HTTP. The route layer holds **no logic** — it reads the
repository and serializes.

**Files**
- `backend/app/api/__init__.py`
- `backend/app/api/routes.py`
- `backend/app/main.py` (edit — include the router)
- (tests) `backend/tests/test_api_meetings.py`

## Two endpoints

| Endpoint | Returns |
|---|---|
| `GET /api/meetings` | All 24, date-ordered, **without** raw source records |
| `GET /api/meetings/{id}` | One meeting with full provenance, match evidence, and both sides' raw records |

## Why the list omits raw records

Doc 03 assigns the raw payload to the *detail* view: *"One meeting with full provenance, match
evidence, and both raw source records."*

Measured, not assumed: all 24 meetings serialize to **90 KB** with raw records and **66 KB** without
— the raws are ~27% of the payload, and they are the part duplicating data the list already shows in
merged form. Not a dramatic saving at this scale; the reason to drop them is that a list endpoint
returning every source record it was built from is confusing to read, not that it is slow.

`match_evidence` **stays** in the list: the list page badges low-confidence matches, so it needs
`confidence` without a second request.

Getting this right took two attempts, both recorded because the first is the obvious one:

1. **`response_model_exclude={"raw_crm", "raw_calendar"}`** — does **not** work for a list response.
   Verified empirically: the fields come through anyway, because the exclusion applies to the
   top-level list rather than to each item.
2. **A subclass with the heavy fields marked excluded:**

   ```python
   class MeetingListItem(UnifiedMeeting):
       raw_crm: list[dict] = Field(default_factory=list, exclude=True)
       raw_calendar: list[dict] = Field(default_factory=list, exclude=True)
   ```

   Drops them from the response body *and* from the OpenAPI schema, inherits everything else, and
   cannot drift from `UnifiedMeeting` because it *is* one.

A hand-written summary model was rejected: it would duplicate 19 fields and silently fall behind the
first time step 13 gains one.

## 404 is an ordinary answer

`get_meeting` returns `None` for an unknown id (step 14) and the route turns that into a 404 with a
message naming the id. The store does not raise, because "no such meeting" is not exceptional — it is
what a stale bookmark looks like after a re-sync.

## Manual test

```bash
cd backend && source venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

```bash
curl -s localhost:8000/api/meetings | jq 'length'                      # 24
curl -s localhost:8000/api/meetings | jq '.[0] | keys_unsorted[:6]'
curl -s localhost:8000/api/meetings | jq '.[0] | has("raw_crm")'       # false
curl -s localhost:8000/api/meetings/crm-1002-cal-a2 | jq '.location'
curl -s localhost:8000/api/meetings/crm-1002-cal-a2 | jq '.raw_crm | length'   # 1
curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/api/meetings/nope      # 404
```

The `location` object must be exactly doc 02's Decision 4 shape — value, source, alternatives,
conflict, conflict_kind.

Then open <http://localhost:8000/docs> and confirm both endpoints are documented with schemas.

## Done when

- [x] `GET /api/meetings` returns 24 items in date order.
- [x] List items carry no `raw_crm` / `raw_calendar`, and neither appears in the OpenAPI schema.
- [x] `GET /api/meetings/{id}` returns the full record including both raw sides.
- [x] `crm-1005-cal-a5-cal-a6` returns **two** calendar raw records.
- [x] An unknown id returns 404 with the id in the message.
- [x] The `location` field on `crm-1002-cal-a2` matches doc 02's documented JSON exactly.
- [x] Match evidence is present on matched meetings and `null` on single-source ones.
- [x] Both endpoints appear in `/docs`.

*17 tests; 357 total. Verified against a real server: 24 items, 66 KB list, `raw_crm` absent,
2 raw calendar records on the merged duplicate, 404 on an unknown id, `/docs` 200.*

One test worth naming: `test_every_listed_meeting_is_retrievable` walks all 24 ids from the list and
fetches each one. It is the end-to-end form of the store's consistency guarantee — nothing appears
in the list that 404s when clicked.