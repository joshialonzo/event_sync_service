# 02 — Reconciliation Design

*Decision log. Each section states the decision, the reasoning, and the alternatives rejected.*

The statement says: *"We have intentionally not told you how to handle any of these cases."* So this
document is the deliverable that answers that. The guiding principle throughout:

> **The service reconciles; it does not adjudicate.** Where the sources disagree, the merged record
> presents a default *and* preserves the disagreement. A sales tool that silently picks a winner and
> discards the loser is worse than useless — it destroys the signal the user needs.

## Pipeline

```
  raw JSON ──► normalize ──► dedupe (intra-source) ──► match (cross-source) ──► merge ──► persist ──► API ──► UI
                   │              │                          │                   │
              quality flags   dup groups              match evidence      field provenance
```

Each stage emits metadata alongside its output. That metadata is what makes the UI able to explain
itself, so it is a first-class output, not logging.

---

## Decision 1 — Normalization is non-destructive

**Decision.** Every raw record becomes a `NormalizedEvent` regardless of how malformed it is. Parse
failures attach a `DataQualityFlag` (`code`, `field`, `raw_value`, `severity`) rather than raising.
The raw record is retained verbatim on the normalized object.

**Why.** The statement asks the UI to expose data quality. A pipeline that drops `CRM-1008` because
its date is `"03-15/2025"` cannot report that `CRM-1008` has a bad date. Dropping bad records also
makes the counts lie — 20 CRM records in, 20 out, always.

**Rejected.** Strict Pydantic validation with rejection to a dead-letter list. Cleaner code, but it
splits the dataset into two shapes and the UI then has to render both. The flag approach keeps one
shape.

### Specific normalization rules

| Concern | Rule |
|---|---|
| Dates | Try ISO first, then a small ordered list of tolerated patterns (`%m-%d/%Y` catches `CRM-1008`). Record `MALFORMED_DATE` with the original string whenever the fallback fires. Never guess a date that cannot be parsed. |
| Timezones | All timestamps are coerced to `America/New_York`, DST-aware (the dataset straddles the 2025-03-09 change). Naive timestamps are *assumed* Eastern; `Z`-suffixed timestamps are converted from UTC. This is what brings `CAL-A4` within an hour of `CRM-1004` rather than five (see doc 01, section D — the conversion leaves a genuine 1h conflict). The assumption is recorded as a `TIMEZONE_ASSUMED` flag on every naive record so it is visible rather than hidden. |
| Missing time | `CRM-1007` becomes a date-only event with `TIME_MISSING`. It still participates in matching, on the date signal alone. |
| Emails | Repair `[at]` → `@`, flag `MALFORMED_EMAIL` (`CAL-A16`). Non-email attendee strings such as `"external-guests"` are kept as opaque participant labels with `NON_EMAIL_ATTENDEE` (`CAL-A20`), not discarded — "external guests attended" is real information. |
| Status | Map both vocabularies onto one enum (`SCHEDULED`, `CONFIRMED`, `TENTATIVE`, `COMPLETED`, `CANCELLED`). Preserve each source's original string for display. |
| Internal meetings | A null client on a `meeting_type: Internal` record is **valid**, not a defect. Flagged `INTERNAL_NO_CLIENT` at `info` severity so it does not pollute the data-quality count. |

**Why severity levels.** Without them, `CRM-1006` (an internal meeting, perfectly fine) looks as
broken as `CRM-1008` (a genuinely corrupt date). Three levels: `info`, `warning`, `error`.

---

## Decision 2 — Intra-source dedupe runs *before* cross-source matching

**Decision.** Collapse near-duplicates within a source first. The surviving record keeps every source
ID in a list, and the union of the duplicates' attendees.

**Why.** `CAL-A5` and `CAL-A6` both match `CRM-1005`. If dedupe ran after matching, the matcher would
have to handle 1:N pairings as a special case. Running it first keeps the cross-source stage a clean
1:1 problem.

**The rule.** Two records in the same source are duplicates when: same day **and** same organizer/owner
**and** overlapping client participants **and** start times within 60 minutes **and** neither is
`is_recurring`.

**The recurrence carve-out matters.** `CAL-A3` and `CAL-A18` are identical in every field except a
7-day date offset. The 60-minute window already separates them, but the explicit `is_recurring`
exclusion is a second guard — deleting one instance of a recurring series would be a silent data loss
bug, and this is the kind of thing that looks correct in testing and is wrong in production.

**Which survives.** The earlier `created_at` (`CAL-A5`) is canonical, since it is the original entry;
but attendees are unioned so Sandra Mills from `CAL-A6` is not lost, and the merged record carries
both IDs. Neither record's information is discarded.

**Rejected.** Keeping both and letting the UI show them as separate meetings. Honest, but it fails
the "reconciles records that refer to the same real-world meeting" requirement — the duplicate is
planted precisely to see whether it gets caught.

---

## Decision 3 — Matching is a transparent weighted score, not a black box

**Decision.** Block by date (±1 day), then score each candidate pair on four independent signals.
Every match stores its score and per-signal contributions as `MatchEvidence`, which the API returns
and the UI displays.

| Signal | Weight | How it is computed |
|---|---|---|
| Participant overlap | 0.40 | CRM `client_name` → email local-part (`"David Park"` → `david.park`) and `client_company` → email domain (`"Meridian Capital"` → `meridiancap`), matched against calendar attendees. Owner name → organizer likewise. |
| Time proximity | 0.30 | Exact start = 1.0, decaying to 0 at ±4 hours. Date-only records (`CRM-1007`) score a neutral 0.5 rather than 0. |
| Title/text similarity | 0.20 | Token-set overlap on stopworded, lowercased subject/title, plus company name and a small acronym expansion (`LPAC` ↔ `LP Advisory Committee`). |
| Structural agreement | 0.10 | Normalized location compatibility (substring containment counts as agreement, so "Conference Room B" agrees with "HQ - Conference Room B"), plus modality vs. virtual-platform detection. |

**Thresholds.** ≥ 0.70 auto-match; 0.45–0.70 flagged as a *low-confidence* match, still merged but
badged in the UI; < 0.45 no match. Assignment is greedy on descending score with each record
consumable once — with 20×22 candidates this is exact enough, and the alternative (Hungarian
algorithm) buys optimality nobody can perceive at this scale.

**Why weighted-explainable.** The reviewer of this assessment will check the pairs by hand. A model
that outputs "0.83, matched" is unreviewable; one that outputs "participant 0.40 + time 0.30 + title
0.11 + structure 0.02" can be argued with. The requirement to show data provenance implies the same
standard applies to the matching itself.

**Rejected alternatives.**

- **Exact key on (date, normalized-client).** Fails `CRM-1008` (bad date), `CRM-1016` (2h drift),
  `CRM-1004` (timezone), and every internal meeting with a null client. Roughly half the dataset.
- **Fuzzy string match on title alone.** Titles follow different conventions per source
  ("Annual Allocation Review" vs "Horizon Wealth - Year-End Review"). Would also falsely fuse the two
  Atlas Ventures meetings (`CRM-1008` lunch, `CRM-1015` pitch) that share a client 11 days apart.
- **Embeddings / LLM-based matching.** Overkill for 42 records, non-deterministic, unexplainable,
  and it would make the reconciliation logic — the actual substance of this assessment —
  unreviewable. AI was used to *build* this service, not to *be* the matching logic. That is a
  deliberate line.
- **Time-window-only matching.** `CAL-A19` (Roadshow Prep, 3/28 14:00) and `CRM-1017` (Closing Dinner,
  3/28 19:00) are the same day; only participants and text keep them apart.

**Why ±1 day blocking rather than same-day.** `CAL-A4`'s UTC timestamp would land on a different
local date if the timezone rule were ever wrong. The wider block means a timezone mistake produces a
low-confidence match instead of a silent miss.

---

## Decision 4 — Merge preserves conflicts with field-level provenance

**Decision.** Every field in a unified meeting is an object, not a scalar:

```jsonc
"location": {
  "value": "Zoom - https://zoom.us/j/98765432100",  // the display default
  "source": "calendar",                              // where it came from
  "alternatives": [                                  // what the other source said
    { "source": "crm", "value": "NYC Office - 30th Floor" }
  ],
  "conflict": true,                                  // sources genuinely disagree
  "conflict_kind": "contradiction"                   // vs. "absence" / "granularity"
}
```

**Why.** This satisfies both frontend requirements — "see which source each piece of data came from"
and "see where data conflicts exist" — with one structure, and it makes them properties of the data
rather than of the UI.

### Precedence, and why

| Field group | Winner | Reasoning |
|---|---|---|
| Time, location, attendees, duration | **Calendar** | The calendar is the system of record for logistics. It is what people actually look at on the day, and it is edited when things move. |
| Client, company, relationship owner, notes, meeting type | **CRM** | The CRM is the system of record for the relationship. Calendar attendee lists are incomplete and full of internal staff. |
| Status | **Neither, by default** | See below. |

**Status is the deliberate exception.** `CRM-1009` is `Cancelled` in CRM and `confirmed` in Calendar.
A cancelled meeting showing as confirmed sends someone to an empty room; a confirmed meeting showing
as cancelled means someone misses a client. Since both errors are costly and the sources are equally
authoritative here, the merged record surfaces **both values and marks the conflict prominently**,
defaulting to the more conservative `Cancelled` for filtering purposes only. A human decides.

**Three kinds of disagreement, treated differently.** Collapsing these would be the easy mistake:

1. **Contradiction** — both sources have values and they are incompatible (`CRM-1002` In-Person vs
   Zoom; `CRM-1016` 13:00 vs 15:00). Flagged as a conflict, both shown.
2. **Absence** — one source has a value, the other is null (`CRM-1018` location). Not a conflict; the
   present value simply wins, with provenance noted.
3. **Granularity** — compatible values at different specificity ("Conference Room B" ⊂ "HQ -
   Conference Room B"). Not a conflict; the more specific value wins, the other is kept as an
   alternative.

Only category 1 raises the conflict badge. If all three were flagged, nearly every record would show
a conflict and the badge would carry no information — the feature would defeat itself.

**Rejected.** A single `winning_source` per record, or last-write-wins on `created_at`. Both throw
away exactly the information the assessment asks to display. Last-write-wins is additionally wrong
here: `CAL-A6` has a later `created_at` than `CAL-A5` and is the *worse* record.

---

## Decision 5 — Unmatched records are first-class citizens

**Decision.** CRM-only and Calendar-only records appear in the unified list with an `origin` of
`crm_only` / `calendar_only`, not as an error bucket.

**Why.** A meeting in the CRM with no calendar entry (`CRM-1010`, `CRM-1020` — both `Tentative`) is a
real, actionable business signal: *the meeting was never actually booked*. A calendar entry with no
CRM record (`CAL-A19`) means client time is not being logged. For a sales tool these gaps are
arguably the most valuable output of the whole exercise, so the UI filters on `origin` prominently.

---

## Correctness fixture

The 24-meeting expected outcome in [01-data-analysis.md](01-data-analysis.md) — derived by hand from
the raw data before the matcher existed — is the test fixture. The suite asserts the exact pairings,
the `A5`/`A6` collapse, the `A3`/`A18` non-collapse, and the four named conflicts. Test coverage
percentage is explicitly not being evaluated, so the tests target the decisions in this document
rather than lines of code.
