# Step 11 — Match signals

**Goal.** Four pure scorers, each `(crm_event, calendar_event) -> (score in [0,1], human-readable
detail)`. No weights, no thresholds, no assignment — step 12 owns all three.

**Files**
- `backend/app/reconcile/signals.py`
- (tests) `backend/tests/test_signals.py`

Doc 02, Decision 3 rejects a black-box matcher because a reviewer checks the pairs by hand. That
requirement lands *here*: each scorer returns the sentence that will appear in the UI's evidence
panel, so "why 0.83?" is answerable without reading the code.

## The bridging problem

The sources share no join key and describe parties differently:

| CRM | Calendar |
|---|---|
| `client_name: "David Park"` | `attendees: ["david.park@meridiancap.com"]` |
| `client_company: "Atlas Ventures"` | domain `atlasvc.com` |
| `relationship_owner: "Sarah Chen"` | `organizer: "sarah.chen@firma.com"` |

**Names → local parts.** Strip separators and compare: `"David Park"` → `davidpark`,
`david.park@…` → `davidpark`. Exact on all five bridging pairs in the file.

**Companies → domains.** Checked every company/domain pair in the data; the domain is always an
*abbreviation* of the company, never a truncation:

| Company | Domain root |
|---|---|
| Meridian Capital | `meridiancap` |
| Horizon Wealth Partners | `horizonwp` |
| Atlas Ventures | `atlasvc` |
| Granite Point Capital | `granitepointcap` |

No single string metric covers `horizonwp` and `atlasvc` (`vc` is not a prefix of `ventures`, and
`atlasvc` is not a subsequence of `atlasventures`). What *does* hold for all ten domains in the file:
**the domain root starts with the company's first significant token.** That is the rule — narrow,
explainable, and checked against every pair rather than the two that inspired it.

## The four signals

### 1. Participants — weight 0.40 in step 12

Three components, each scored independently and averaged over those that are *available*:

| Component | Share | Skipped when |
|---|---|---|
| Owner presence | 0.4 | either side missing |
| Client person ↔ attendee | 0.4 | CRM has no client (internal) or a placeholder (`CRM-1017`) |
| Client company ↔ attendee domain | 0.2 | CRM has no company |

**Owner presence is tiered, not binary.** `CAL-A14` was created by Priya Sharma, while `CRM-1013`'s
relationship owner is Sarah Chen — who is in the attendee list. Scoring only "owner == organizer"
sends that pair to 0.486 and loses it. Organising scores 1.0, merely attending 0.7: both are
evidence, but if they scored equally, every internal meeting the whole team attends would look like
every other one.

**Renormalizing over available components** is what lets internal meetings match at all: `CRM-1013`
has no client, so its participant score comes from the owner alone rather than being penalised to 0.4
for fields the source never had.

The placeholder skip is why step 8 flags `CRM-1017` — `"Multiple"` would otherwise produce a
`multiple` local-part and score as a fabricated person.

### 2. Time — weight 0.30

Exact start = 1.0, decaying linearly to 0 at ±4 hours. **A date-only record scores a neutral 0.5**,
not 0: `CRM-1007` has no time, and scoring it 0 would penalise a record for a gap the source is
responsible for.

### 3. Title — weight 0.20

Token-set overlap on lowercased, stopworded text, plus two extras the data demands:

- **Company names count**, since the calendar convention prefixes them (`"Horizon Wealth - Year-End
  Review"` vs `"Annual Allocation Review"` share *no* content tokens).
- **Acronyms expand**: `LPAC` ↔ `LP Advisory Committee`. Built by taking each token's first letter,
  except tokens of ≤2 characters which contribute whole — so `LP`+`A`+`C` = `LPAC`.

### 4. Structure — weight 0.10

`0.6 × location + 0.4 × modality`.

Location: equal or **containing** → 1.0 (`"Conference Room B"` ⊂ `"HQ - Conference Room B"`), sharing
a significant token → 0.6, neither → 0.0, either side missing → 0.5 (no evidence, not disagreement).

Modality: CRM `Virtual` against a calendar location naming a platform (zoom/teams/meet) agrees;
`In-Person` against a physical location agrees; the mismatch that is `CRM-1002` scores 0. At weight
0.10 a total disagreement costs 0.06 — deliberately small, because the sources genuinely disagree
here and doc 02 says that is a fact to display, not a reason to reject the pairing.

## Manual test

```bash
cd backend && source venv/bin/activate
python -c "
from app.ingest.crm import load_crm
from app.ingest.calendar import load_calendar
from app.reconcile.normalize_crm import normalize_crm_records
from app.reconcile.normalize_calendar import normalize_calendar_records
from app.reconcile import signals
crm = {e.primary_id: e for e in normalize_crm_records(load_crm())}
cal = {e.primary_id: e for e in normalize_calendar_records(load_calendar())}
for c, k in [('CRM-1001','CAL-A1'), ('CRM-1013','CAL-A14'), ('CRM-1002','CAL-A2')]:
    a, b = crm[c], cal[k]
    print(c, k, [f'{s(a,b).score:.2f}' for s in (signals.participant_overlap, signals.time_proximity, signals.title_similarity, signals.structural_agreement)])
"
```

## Done when

- [x] `"David Park"` matches `david.park@meridiancap.com`; a different person does not.
- [x] All nine company/domain abbreviations in the data score as matches.
- [x] An internal CRM record (no client) still scores on its owner alone.
- [x] `CRM-1017`'s `"Multiple"` does not contribute a person match.
- [x] Identical starts score 1.0; 2 hours apart scores 0.5; 4+ hours apart scores 0.
- [x] A date-only record scores exactly 0.5.
- [x] `LPAC` matches `LP Advisory Committee`.
- [x] `"Conference Room B"` and `"HQ - Conference Room B"` agree.
- [x] Every scorer returns a value in `[0, 1]` and a non-empty detail string, across all 420 pairs.

*39 tests; 235 total. Five mutations, all caught: date-only score (2 failures), owner-attended tier
(3), placeholder skip (1), component renormalization (3), location containment (2).*

## Observed separation

Weighted with step 12's 0.40/0.30/0.20/0.10, across all 420 combinations:

| | Score |
|---|---|
| Lowest true pair (`CRM-1007`/`CAL-A8`) | **0.763** |
| Highest false pair (`CRM-1007`/`CAL-A1`) | **0.660** |

The 0.70 threshold sits in a 0.10-wide empty band, and `test_the_true_pairs_outscore_every_false_pair`
asserts the separation rather than the constant — so a signal change that narrows the gap fails here
rather than in step 12's fixture.

Two things this exposed:

1. **The tiered owner rule was not optional.** Without it `CRM-1013`/`CAL-A14` scores 0.486 — below
   even the low-confidence floor — and the documented 17 pairs become 16.
2. **The acronym rule is insurance, not load-bearing.** The real `LPAC` pair already shares
   `advisory`, `committee`, and `lpac` through the two descriptions, so it matches without it. The
   rule is kept because the *titles* alone share nothing, and it is tested synthetically.
