"""Cross-source matching (doc 02, Decision 3): block, score, assign.

The output of this module is the project's correctness claim — 17 pairs, 3 CRM-only, 4
calendar-only, 24 meetings — so every decision here is deliberately boring and inspectable.
The interesting judgement lives in `signals.py`; this file only combines and allocates.
"""

from datetime import timedelta
from typing import Callable, NamedTuple

from app.models.normalized import NormalizedEvent
from app.models.unified import MatchConfidence, MatchEvidence, MatchSignal
from app.reconcile import signals

BLOCK_WINDOW = timedelta(days=1)
"""Only records within a day of each other are compared.

Wider than same-day on purpose: CAL-A4's UTC timestamp would land on a different local date
if the timezone rule were ever wrong, and this way that mistake produces a badged
low-confidence match instead of a silent miss. Measured on the real data, blocking cuts 420
combinations to 61 without losing a single true pair.
"""

AUTO_MATCH_THRESHOLD = 0.70
LOW_CONFIDENCE_THRESHOLD = 0.45

SIGNAL_WEIGHTS: tuple[tuple[str, float, Callable[..., signals.SignalScore]], ...] = (
    ("participants", 0.40, signals.participant_overlap),
    ("time", 0.30, signals.time_proximity),
    ("title", 0.20, signals.title_similarity),
    ("structure", 0.10, signals.structural_agreement),
)


class MatchedPair(NamedTuple):
    crm: NormalizedEvent
    calendar: NormalizedEvent
    evidence: MatchEvidence


class MatchResult(NamedTuple):
    pairs: list[MatchedPair]
    unmatched_crm: list[NormalizedEvent]
    unmatched_calendar: list[NormalizedEvent]

    @property
    def meeting_count(self) -> int:
        """17 + 3 + 4 = 24 on the real data."""
        return len(self.pairs) + len(self.unmatched_crm) + len(self.unmatched_calendar)


def score_pair(crm: NormalizedEvent, calendar: NormalizedEvent) -> MatchEvidence:
    """Weighted sum of the four signals, with the per-signal breakdown retained.

    The total is the sum of the contributions rather than a separately-computed figure:
    `MatchEvidence` rejects a score that disagrees with its own breakdown, and computing it
    twice is how the two would drift.
    """
    scored = [
        MatchSignal(name=name, weight=weight, score=result.score, detail=result.detail)
        for name, weight, scorer in SIGNAL_WEIGHTS
        for result in (scorer(crm, calendar),)
    ]
    total = sum(signal.contribution for signal in scored)

    return MatchEvidence(
        score=total,
        signals=scored,
        confidence=(
            MatchConfidence.HIGH if total >= AUTO_MATCH_THRESHOLD else MatchConfidence.LOW
        ),
    )


def match_events(
    crm_events: list[NormalizedEvent], calendar_events: list[NormalizedEvent]
) -> MatchResult:
    """Pair records across the two sources, greedily by descending score.

    Greedy rather than optimal: doc 02 rejects the Hungarian algorithm here because at 20x21
    records, with a 0.10-wide empty band between the lowest true pair and the highest false
    one, optimality buys nothing observable and costs explainability.
    """
    candidates = _score_candidates(crm_events, calendar_events)

    pairs: list[MatchedPair] = []
    taken_crm: set[str] = set()
    taken_calendar: set[str] = set()

    for _, crm_id, calendar_id, crm, calendar, evidence in candidates:
        if crm_id in taken_crm or calendar_id in taken_calendar:
            continue
        taken_crm.add(crm_id)
        taken_calendar.add(calendar_id)
        pairs.append(MatchedPair(crm=crm, calendar=calendar, evidence=evidence))

    return MatchResult(
        pairs=pairs,
        unmatched_crm=[e for e in crm_events if e.primary_id not in taken_crm],
        unmatched_calendar=[e for e in calendar_events if e.primary_id not in taken_calendar],
    )


def _score_candidates(
    crm_events: list[NormalizedEvent], calendar_events: list[NormalizedEvent]
) -> list[tuple]:
    """Blocked, thresholded, and sorted into the order the greedy pass consumes.

    The id tie-break is not cosmetic: without it two pairs with identical scores would be
    allocated in whatever order the inputs happened to arrive, and the matcher's output
    would depend on the order records were read from disk.
    """
    candidates = []

    for crm in crm_events:
        for calendar in calendar_events:
            if not _within_block(crm, calendar):
                continue

            evidence = score_pair(crm, calendar)
            if evidence.score < LOW_CONFIDENCE_THRESHOLD:
                continue

            candidates.append(
                (
                    -evidence.score,
                    crm.primary_id,
                    calendar.primary_id,
                    crm,
                    calendar,
                    evidence,
                )
            )

    return sorted(candidates, key=lambda row: (row[0], row[1], row[2]))


def _within_block(crm: NormalizedEvent, calendar: NormalizedEvent) -> bool:
    """A record with no date is never a candidate — comparing it against all 21 calendar
    entries would be scoring on no evidence at all."""
    if crm.event_date is None or calendar.event_date is None:
        return False

    return abs(crm.event_date - calendar.event_date) <= BLOCK_WINDOW
