# Step 04 — Raw JSON loaders

**Goal.** Read both source files into plain dicts. No parsing, no validation, no models — those are
later steps, and mixing them in here is what makes ingest untestable.

**Files**
- `backend/app/ingest/__init__.py`
- `backend/app/ingest/crm.py`
- `backend/app/ingest/calendar.py`

## What to build

Each module exposes one function:

```python
def load_crm(data_dir: Path | None = None) -> list[dict]: ...
def load_calendar(data_dir: Path | None = None) -> list[dict]: ...
```

- Default `data_dir` comes from settings (step 02).
- Read the file, `json.load` it, return the list unchanged. **Do not** rename keys, coerce types, or
  skip records — normalization owns all of that, and a loader that "helpfully" cleans data hides the
  anomalies this project is about.
- Let a missing file raise. A misconfigured `DATA_DIR` should fail loudly at startup, not yield an
  empty list that silently produces zero meetings.

The two files exist separately, rather than as one `load_all`, because they become different adapters
in steps 08 and 09 and because a real version of this service would fetch them from two different APIs.

## Reference — the shapes you are loading

CRM record (`crm_id`, 20 records):

```json
{"crm_id": "CRM-1001", "subject": "Q1 Portfolio Review", "client_name": "David Park",
 "client_company": "Meridian Capital", "relationship_owner": "Sarah Chen",
 "meeting_date": "2025-03-10", "meeting_time": "14:00", "meeting_type": "In-Person",
 "location": "HQ - Conference Room B", "notes": "...", "status": "Completed",
 "created_at": "2025-02-28T09:15:00Z"}
```

Calendar record (`event_id`, 22 records):

```json
{"event_id": "CAL-A1", "title": "Q1 Portfolio Review - Meridian Capital",
 "organizer": "sarah.chen@firma.com", "attendees": ["sarah.chen@firma.com", "..."],
 "start_time": "2025-03-10T14:00:00", "end_time": "2025-03-10T15:30:00",
 "location": "Conference Room B", "description": "...", "is_recurring": false,
 "status": "confirmed", "created_at": "2025-02-27T10:00:00Z"}
```

## Manual test

```bash
cd backend && source venv/bin/activate
python -c "
from app.ingest.crm import load_crm
from app.ingest.calendar import load_calendar
c, k = load_crm(), load_calendar()
print(len(c), len(k))
print(c[0]['crm_id'], k[0]['event_id'])
print(sorted({key for r in c for key in r}))
"
```

Expect:

```
20 22
CRM-1001 CAL-A1
['client_company', 'client_name', 'created_at', 'crm_id', 'location', 'meeting_date', 'meeting_time', 'meeting_type', 'notes', 'relationship_owner', 'status', 'subject']
```

Then confirm the anomalies survived the load untouched:

```bash
python -c "
from app.ingest.crm import load_crm
from app.ingest.calendar import load_calendar
print([r['meeting_date'] for r in load_crm() if r['crm_id']=='CRM-1008'])
print([r['attendees'] for r in load_calendar() if r['event_id']=='CAL-A16'])
"
```

→ `['03-15/2025']` and an attendee list still containing `raj.patel[at]atlasvc.com`.

## Done when

- [x] Counts are exactly 20 and 22.
- [x] `CRM-1008`'s broken date and `CAL-A16`'s `[at]` email come through verbatim.
- [x] A bogus `DATA_DIR` raises `FileNotFoundError` rather than returning `[]`.
- [x] `tests/test_ingest.py` covers the above and `pytest -q` is green. *(16 tests; 28 total)*

## Notes

The "loaders must not clean" rule is load-bearing for steps 08/09: the data-quality flags are derived
by *attempting* to parse the raw value and recording the failure. A loader that pre-fixes `[at]`
would make `MALFORMED_EMAIL` unreportable, and the UI's data-quality panel would be a lie.
