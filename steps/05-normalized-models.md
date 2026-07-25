# Step 05 — Normalized models

**Goal.** One shape that both sources normalize into — capable of representing a *broken* record
without losing anything, because doc 02 Decision 1 says nothing is ever dropped.

**Files**
- `backend/app/models/__init__.py`
- `backend/app/models/normalized.py`
- (tests) `backend/tests/test_models_normalized.py`

No parsing happens in this step. These are the containers; steps 7–9 fill them.

## What to build

### `Source`, `Severity`, `MeetingStatus`

```python
class Source(str, Enum):        # CRM = "crm", CALENDAR = "calendar"
class Severity(str, Enum):      # INFO = "info", WARNING = "warning", ERROR = "error"
class MeetingStatus(str, Enum): # SCHEDULED CONFIRMED TENTATIVE COMPLETED CANCELLED UNKNOWN
```

`str`-based enums so they serialize to readable JSON without a custom encoder, and so a template can
compare against a plain string.

**Why `UNKNOWN` exists:** normalization must never raise. An unmappable status string becomes
`UNKNOWN` plus a flag, not an exception. Both source vocabularies are pinned in step 4's tests
(5 title-case CRM values, 2 lower-case calendar values), so `UNKNOWN` should never fire on this
dataset — it is there so that a *new* vocabulary degrades instead of crashing.

### `FlagCode` and `DataQualityFlag`

The codes the pipeline actually needs, each with a fixed severity:

| Code | Severity | Fires on |
|---|---|---|
| `MALFORMED_DATE` | error | `CRM-1008` — `"03-15/2025"` |
| `MALFORMED_DATETIME` | warning | `CAL-A11` — `"2025-03-14T20:00"`, missing seconds |
| `UNPARSABLE_DATE` | error | reserved: a date no pattern matches (none in this dataset) |
| `TIME_MISSING` | warning | `CRM-1007` — null `meeting_time` |
| `TIMEZONE_ASSUMED` | info | every naive timestamp — the assumption made visible |
| `MALFORMED_EMAIL` | warning | `CAL-A16` — `[at]` |
| `NON_EMAIL_ATTENDEE` | info | `CAL-A20` — `"external-guests"` |
| `INTERNAL_NO_CLIENT` | info | the four `Internal` CRM records |
| `UNKNOWN_STATUS` | warning | a status outside both vocabularies |

Severity belongs to the *code*, not to the call site — otherwise the same defect gets logged at two
severities by two normalizers and the stats page becomes meaningless. Expose it as a property on the
enum and build flags through a constructor that fills it in:

```python
DataQualityFlag.of(FlagCode.MALFORMED_DATE, field="meeting_date", raw_value="03-15/2025")
```

**Why severity at all** (doc 02): without it, `CRM-1006` (a valid internal meeting) looks as broken
as `CRM-1008` (a genuinely corrupt date), and the data-quality count reports 9 problems where there
are 2.

### `Participant`

```python
email: str | None      # None for opaque labels
display: str           # what the UI shows
domain: str | None     # derived from email — the company signal for matching
is_organizer: bool
raw: str               # exactly what the source said
```

`domain` is derived from `email` by a validator rather than passed in, so it cannot disagree with it.
`email` is `None` for `"external-guests"` — that is a participant the system cannot resolve to a
person, not an error, and step 11 scores it as no signal rather than dropping it.

### `NormalizedEvent`

| Field | Type | Note |
|---|---|---|
| `source` | `Source` | |
| `source_ids` | `list[str]`, min 1 | A **list from the start** — step 10 merges `CAL-A5`+`CAL-A6` into one event holding both ids. Making this a string now means touching every consumer twice. |
| `start` / `end` | `datetime \| None` | tz-aware once steps 8–9 fill them |
| `event_date` | `date \| None` | Separate from `start`: `CRM-1007` has a date and no time, and step 11 must distinguish that from midnight |
| `title`, `text`, `location` | `str \| None` | |
| `participants` | `list[Participant]` | |
| `organizer` | `str \| None` | email (calendar) or owner name (CRM) |
| `owner_name`, `client_name`, `client_company`, `meeting_type` | `str \| None` | CRM-shaped fields, null on calendar records |
| `status` | `MeetingStatus` | |
| `status_raw` | `str \| None` | the source's original string, preserved for display |
| `is_recurring` | `bool` | the dedupe carve-out |
| `created_at` | `datetime \| None` | dedupe uses it to pick the survivor |
| `flags` | `list[DataQualityFlag]` | |
| `raw` | `dict` | the untouched source record |

**Named `event_date`, not `date`,** to avoid shadowing `datetime.date` inside the module.

**Invariant enforced by a model validator:** if `start` is set, `event_date` must equal
`start.date()`. Absent → derived; present and inconsistent → `ValueError`. A normalizer that sets one
and forgets the other would put an event on two different days depending on which field the consumer
reads, and blocking in step 12 reads `event_date` while scoring reads `start`.

`has_time` is a **property** (`start is not None`), not a stored field, so it cannot drift.

## Manual test

```bash
cd backend && source venv/bin/activate
python -c "
from app.models.normalized import NormalizedEvent, Source, MeetingStatus
e = NormalizedEvent(source=Source.CRM, source_ids=['CRM-1001'], raw={'crm_id': 'CRM-1001'})
print(e.status, e.has_time, e.flags, e.primary_id)
"
```

→ `MeetingStatus.UNKNOWN False [] CRM-1001` — a maximally sparse event constructs with three
arguments and no exception. That is the whole point of the step: the model must be able to hold the
worst record in the dataset.

```bash
pytest -q
```

## Done when

- [x] A `NormalizedEvent` with only `source`, `source_ids`, and `raw` constructs without raising.
- [x] `source_ids=[]` and a missing `raw` are both rejected.
- [x] `event_date` derives from `start`; an inconsistent pair raises.
- [x] Every `FlagCode` has a severity, and `MALFORMED_DATE` is `error` while `INTERNAL_NO_CLIENT`
      is `info`.
- [x] `Participant` derives `domain` from `email`, and accepts `email=None` for an opaque label.
- [x] `model_dump_json()` round-trips `raw` unchanged.

*20 tests; 57 total. Verified by mutation: removing the `event_date`/`start` invariant fails
exactly `test_event_date_disagreeing_with_start_is_rejected`.*

## Notes

- Models are **not** frozen. Pipeline stages return new objects rather than mutating in place
  (`model_copy(update=...)`), but freezing would make the ergonomic `event.flags.append(...)` inside a
  normalizer impossible for no real safety gain at this size.
- Nothing here imports from `app.ingest` or `app.reconcile`. The models are the bottom of the
  dependency graph — that is what lets step 11's scoring tests build events by hand in two lines
  instead of loading JSON.
