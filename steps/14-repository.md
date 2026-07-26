# Step 14 — Repository

**Goal.** The store the API and templates read from, behind an interface — and the atomic swap that
makes a re-sync safe while someone is reading.

**Files**
- `backend/app/repository/__init__.py` — the `Repository` protocol
- `backend/app/repository/memory.py` — `InMemoryRepository`
- (tests) `backend/tests/test_repository.py`

## The contract (doc 03)

```python
class Repository(Protocol):
    def list_meetings(self) -> list[UnifiedMeeting]: ...   # date-ordered
    def get_meeting(self, meeting_id: str) -> UnifiedMeeting | None: ...
    def get_stats(self) -> SyncRunSummary: ...
    def replace_all(self, result: SyncResult) -> None: ...
```

A `Protocol` rather than an ABC: nothing needs to inherit from it, and `InMemoryRepository` shouldn't
have to import the interface to satisfy it. The seam exists because the store is the one component
chosen for the *dataset size* rather than the problem — if the sources became live APIs, this is the
file that changes and nothing in `reconcile/` notices.

## Atomicity is the whole point

Doc 03: *"`POST /api/sync` builds a complete new `SyncResult` and then rebinds a single reference, so
a reader either sees the entire previous dataset or the entire new one, never a half-written mix."*

That claim is only true if `replace_all` does exactly one thing: `self._result = result`. Any
implementation that clears a dict and refills it, or updates `meetings` before `by_date`, has a
window where a concurrent request sees a half-built store — a meeting in the list that 404s when
clicked.

`SyncResult` is frozen (step 6) and its validator already guarantees `by_date` permutes `meetings`,
so a *published* result is internally consistent by construction. The repository's only job is to
never publish a partial one.

**This is tested with real threads**, not by inspection. A writer alternates between two results of
different sizes while readers assert that what they see is self-consistent — the list length always
matches the summary, never a mixture of the two.

## Empty state

The repository starts empty rather than `None`: an `EMPTY` `SyncResult` with no meetings and a
zeroed summary. Step 16 runs a sync during startup so this is never observed in practice, but a
store whose "not yet loaded" state is a null reference pushes an `if store is None` check into every
route and template.

## Manual test

The sync job that assembles a `SyncResult` arrives in step 15; until then the pipeline is wired by
hand:

```bash
cd backend && source venv/bin/activate
python -c "
from app.ingest.crm import load_crm
from app.ingest.calendar import load_calendar
from app.reconcile.normalize_crm import normalize_crm_records
from app.reconcile.normalize_calendar import normalize_calendar_records
from app.reconcile.dedupe import dedupe_events
from app.reconcile.matcher import match_events
from app.reconcile.merge import merge_all
from app.models.unified import SyncResult, SyncRunSummary
from app.repository.memory import InMemoryRepository
from datetime import datetime

meetings = merge_all(match_events(
    normalize_crm_records(load_crm()),
    dedupe_events(normalize_calendar_records(load_calendar())),
))
by_id = {m.id: m for m in meetings}
order = [m.id for m in sorted(meetings, key=lambda m: (m.event_date, m.start or m.event_date))]
repo = InMemoryRepository()
print('before:', len(repo.list_meetings()), 'meetings')
repo.replace_all(SyncResult(
    meetings=by_id, by_date=order,
    summary=SyncRunSummary(generated_at=datetime.now(), meetings_out=len(meetings)),
))
print('after :', len(repo.list_meetings()), 'meetings')
print('first :', repo.list_meetings()[0].id)
print('lookup:', repo.get_meeting('crm-1001-cal-a1') is not None, '| unknown:', repo.get_meeting('nope'))
"
```

## Done when

- [x] `InMemoryRepository` satisfies `Repository` without inheriting from it.
- [x] A fresh repository returns `[]` and a zeroed summary rather than raising.
- [x] `list_meetings()` returns meetings in `by_date` order, not dict order.
- [x] `get_meeting` returns `None` for an unknown id (the route turns that into a 404).
- [x] `replace_all` twice leaves only the second result visible.
- [x] **Concurrency:** with a writer swapping results in a loop, no reader ever observes a list whose
      length disagrees with the summary it reads alongside it.
- [x] The repository does not copy or mutate the result it is given.

*12 tests; 305 total.*

## The atomicity test earns its keep

To check the threaded tests aren't theatre, `replace_all` was replaced with a plausible-looking
in-place implementation — assign `by_date`, then `meetings`, then `summary`, with a yield between —
which is roughly what someone would write if they thought of the store as "a dict to keep updated".

Result: **11 of 12 tests fail**, including both concurrency tests. Readers observed meetings listed
in `by_date` that were not yet retrievable from `meetings` — precisely the "in the list, 404s when
clicked" bug the single-reference rebind exists to prevent.