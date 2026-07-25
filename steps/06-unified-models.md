# Step 06 — Unified models

**Goal.** The output shape of the whole pipeline: a merged meeting where **every field carries its
own provenance**, plus the store object the API and templates read from.

**Files**
- `backend/app/models/unified.py`
- `backend/app/models/__init__.py` (edit — re-export)
- (tests) `backend/tests/test_models_unified.py`

Still no logic. Step 13 decides *which* source wins; this step defines what a decision looks like
once made.

## What to build

### `ProvenanceField` — the structure the whole UI rests on

Doc 02, Decision 4: every field is an object, not a scalar.

```python
class SourceValue(BaseModel):
    source: Source
    value: Any

class ProvenanceField(BaseModel):
    value: Any                              # the display default
    source: Source | None                   # where it came from
    alternatives: list[SourceValue] = []    # what the other source said
    conflict: bool = False
    conflict_kind: ConflictKind | None = None
```

`ConflictKind` is `CONTRADICTION | ABSENCE | GRANULARITY` — doc 02's three kinds of disagreement.

**The invariant to enforce:** `conflict is True` **iff** `conflict_kind is CONTRADICTION`. Doc 02 is
explicit that only contradictions raise the badge; if absence and granularity also set it, nearly
every record shows a conflict and the badge stops carrying information — the feature defeats itself.
Encoding that as a model validator means step 13 cannot get it wrong in one branch and right in
another.

Constructors so callers don't hand-assemble the flags:

| Constructor | Produces |
|---|---|
| `ProvenanceField.single(value, source)` | one source had it, no conflict |
| `ProvenanceField.empty()` | neither source had it |
| `ProvenanceField.resolved(value, source, other_source, other_value, kind)` | both had values; `kind` decides whether it's a conflict |

### `MatchEvidence` — why the matcher believed two records were the same meeting

```python
class MatchSignal(BaseModel):
    name: str          # participants | time | title | structure
    weight: float
    score: float       # 0..1
    detail: str | None

    @property
    def contribution(self) -> float:   # weight * score
```

```python
class MatchEvidence(BaseModel):
    score: float
    signals: list[MatchSignal]
    confidence: MatchConfidence        # HIGH >= 0.70, LOW 0.45-0.70
```

**Invariant:** `score` must equal the sum of the signals' contributions (to a tolerance). Doc 02's
whole argument for a weighted-explainable matcher is that a reviewer can check the arithmetic; a
score that doesn't add up would make the evidence display a decoration.

### `UnifiedMeeting`

Scalars — identity and things you filter/sort on:

| Field | Note |
|---|---|
| `id` | stable, derived from the source ids |
| `origin` | `BOTH` / `CRM_ONLY` / `CALENDAR_ONLY` (doc 02, Decision 5) |
| `event_date`, `start` | denormalized for sorting and date filters |
| `crm_ids`, `calendar_ids` | lists — `CAL-A5`+`CAL-A6` collapse into one meeting |
| `raw_crm`, `raw_calendar` | `list[dict]`, the untouched source records, carried inline |
| `match_evidence` | `None` for single-source meetings |
| `flags` | union of both sides' data-quality flags |

Provenance fields — everything the user reads: `title`, `start_time`, `end_time`, `location`,
`participants`, `client_name`, `client_company`, `owner_name`, `meeting_type`, `notes`, `status`.

**Invariant:** `origin` must agree with which id lists are populated. A `BOTH` meeting with no CRM ids
is a merge bug that would otherwise surface as an empty column in the UI rather than as an error.

Properties: `has_conflicts`, `conflicting_fields`, `provenance_fields` (name → field, so a template
can iterate without a hard-coded list).

### `SyncRunSummary` and `SyncResult`

`SyncResult` is the store (doc 03): `meetings: dict[str, UnifiedMeeting]`, `by_date: list[str]`,
`summary: SyncRunSummary`. **Frozen** — `POST /api/sync` builds a whole new one and rebinds a single
reference, so a reader never sees a half-written mix.

**Invariant:** `by_date` must be exactly a permutation of `meetings.keys()`. A dropped id there means
a meeting that exists in the API but never appears in the list view — the kind of bug that looks like
a UI problem for an hour.

`SyncRunSummary` carries what `GET /api/stats` reports: records in per source, meetings out, matched
pairs, source-only counts, duplicates collapsed, low-confidence matches, conflicts by kind and by
field, flags by code and by severity.

## Manual test

```bash
cd backend && source venv/bin/activate
python -c "
from app.models.unified import ProvenanceField, ConflictKind
from app.models.normalized import Source
f = ProvenanceField.resolved(
    value='Zoom - https://zoom.us/j/98765432100', source=Source.CALENDAR,
    other_source=Source.CRM, other_value='NYC Office - 30th Floor',
    kind=ConflictKind.CONTRADICTION)
print(f.model_dump_json(indent=1))
"
```

The output must match the JSON block in doc 02, Decision 4 field for field — that block is the
contract the frontend was designed against.

```bash
pytest -q
```

## Done when

- [x] The `ProvenanceField` dump matches doc 02's example exactly (keys and values).
- [x] `conflict=True` with a non-contradiction kind is rejected, and vice versa.
- [x] `MatchEvidence` rejects a score that doesn't equal the sum of contributions.
- [x] A `BOTH` meeting with an empty `crm_ids` is rejected.
- [x] `SyncResult` rejects a `by_date` that isn't a permutation of `meetings`.
- [x] `SyncResult` is frozen — assignment raises.

*25 tests; 82 total. Each of the four invariants was removed in turn and failed only its own
tests — conflict/kind (1), score arithmetic (1), origin/ids (2), by_date permutation (2).*

## Notes

- `value: Any` rather than a generic. The fields hold strings, datetimes, and participant lists;
  parameterising would buy type-checking that nothing in this codebase consumes, at the cost of a
  generic model in every signature.
- These models are what step 13 fills and steps 17–25 read. Nothing here imports from `reconcile`.
