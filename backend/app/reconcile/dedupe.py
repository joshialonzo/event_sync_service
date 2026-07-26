"""Intra-source dedupe (doc 02, Decision 2).

Collapses near-duplicates *within* one source so cross-source matching stays a 1:1 problem.
CAL-A5 and CAL-A6 both describe the same Pinnacle meeting and both match CRM-1005; folding
them here means step 12 never has to handle a 1:N pairing as a special case.

The asymmetry of risk drives every choice below: under-collapsing leaves a visible duplicate,
while over-collapsing silently deletes a real meeting. Every condition is therefore a reason
*not* to merge.
"""

from datetime import timedelta

from app.config import get_settings
from app.models.normalized import DataQualityFlag, FlagCode, NormalizedEvent, Participant

MAX_START_GAP = timedelta(minutes=60)
"""Doc 02's window. Wide enough for a re-created invite that drifted (A5/A6 are 30 minutes
apart), narrow enough to separate two genuine meetings on the same day."""


def dedupe_events(events: list[NormalizedEvent]) -> list[NormalizedEvent]:
    """Collapse duplicate records within one source, preserving order.

    Greedy and single-pass: each event either joins an existing group or starts one. At 22
    records the quadratic scan is free, and the alternative (clustering) would merge A into
    C because both resemble B, which is exactly the over-collapse this must avoid.

    The input is left untouched — survivors are copies. Doc 03 requires the reconcile stages
    to be pure, and without the copy a caller's own list would silently gain the merged
    participants and flags, making any before/after comparison meaningless.
    """
    survivors: list[NormalizedEvent] = []

    for event in events:
        match = next((s for s in survivors if _is_duplicate(s, event)), None)
        if match is None:
            survivors.append(event.model_copy(deep=True))
        else:
            _absorb(match, event.model_copy(deep=True))

    return survivors


def _is_duplicate(first: NormalizedEvent, second: NormalizedEvent) -> bool:
    """All five conditions from doc 02, Decision 2. Any one failing means "not a duplicate"."""
    if first.source is not second.source:
        return False

    # The carve-out. Two instances of a series are different meetings however alike they
    # look, and deleting one is a silent data-loss bug that testing does not surface.
    if first.is_recurring or second.is_recurring:
        return False

    if first.event_date is None or first.event_date != second.event_date:
        return False

    if not _same_convener(first, second):
        return False

    if not _share_a_client(first, second):
        return False

    return _within_the_window(first, second)


def _same_convener(first: NormalizedEvent, second: NormalizedEvent) -> bool:
    """Organizer (calendar) or relationship owner (CRM). Absent on both sides is not a match
    — it would make every anonymous record a duplicate of every other."""
    left = (first.organizer or "").strip().lower()
    right = (second.organizer or "").strip().lower()
    return bool(left) and left == right


def _share_a_client(first: NormalizedEvent, second: NormalizedEvent) -> bool:
    """Overlapping *client* participants, not merely overlapping participants.

    Every internal meeting shares the same handful of colleagues, so counting them would
    make two unrelated internal meetings on one day look like duplicates. This is the
    condition that protects the team syncs before the recurrence guard is even reached.
    """
    return bool(_client_keys(first) & _client_keys(second))


def _client_keys(event: NormalizedEvent) -> set[str]:
    internal = get_settings().internal_domain.lower()
    keys = set()

    for participant in event.participants:
        if participant.is_organizer:
            continue
        if participant.domain and participant.domain.lower() == internal:
            continue
        keys.add((participant.email or participant.display).strip().lower())

    # The CRM has no attendee list; its client name is the only party information it holds.
    if event.client_name:
        keys.add(event.client_name.strip().lower())

    return keys


def _within_the_window(first: NormalizedEvent, second: NormalizedEvent) -> bool:
    """Date-only records (CRM-1007) have no start; the shared date already got them here,
    and inventing a time to compare would fabricate precision."""
    if first.start is None or second.start is None:
        return True

    return abs(first.start - second.start) <= MAX_START_GAP


def _absorb(survivor: NormalizedEvent, duplicate: NormalizedEvent) -> None:
    """Fold the duplicate into the survivor without discarding anything it held.

    The survivor is whichever record was *created first*: doc 02 rejects last-write-wins
    here because CAL-A6 is both newer and worse — a re-created invite with a vaguer location.
    """
    if _created_before(duplicate, survivor):
        _swap_canonical(survivor, duplicate)

    survivor.source_ids = _ordered_unique([*survivor.source_ids, *duplicate.source_ids])
    survivor.duplicates = [*survivor.duplicates, duplicate.raw, *duplicate.duplicates]
    survivor.participants = _union_participants(survivor.participants, duplicate.participants)
    survivor.flags = _union_flags(survivor.flags, duplicate.flags)

    if FlagCode.DUPLICATE_COLLAPSED not in survivor.flag_codes:
        survivor.add_flag(
            FlagCode.DUPLICATE_COLLAPSED,
            field="event_id",
            raw_value=", ".join(survivor.source_ids),
        )


def _created_before(candidate: NormalizedEvent, current: NormalizedEvent) -> bool:
    if candidate.created_at is None or current.created_at is None:
        return False
    return candidate.created_at < current.created_at


def _swap_canonical(survivor: NormalizedEvent, duplicate: NormalizedEvent) -> None:
    """Make the earlier-created record the canonical one, in place.

    Only the descriptive fields move; ids, flags, and duplicates are merged by the caller.
    """
    fields = (
        "start",
        "end",
        "event_date",
        "title",
        "text",
        "location",
        "organizer",
        "owner_name",
        "client_name",
        "client_company",
        "meeting_type",
        "status",
        "status_raw",
        "is_recurring",
        "created_at",
        "raw",
    )
    for field in fields:
        current = getattr(survivor, field)
        setattr(survivor, field, getattr(duplicate, field))
        setattr(duplicate, field, current)


def _union_participants(
    first: list[Participant], second: list[Participant]
) -> list[Participant]:
    """Sandra Mills is only on CAL-A6 and must survive the collapse."""
    merged = list(first)
    seen = {(p.email or p.display).strip().lower() for p in first}

    for participant in second:
        key = (participant.email or participant.display).strip().lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(participant)

    return merged


def _union_flags(
    first: list[DataQualityFlag], second: list[DataQualityFlag]
) -> list[DataQualityFlag]:
    """De-duplicated by value, not by code.

    Both A5 and A6 carry TIMEZONE_ASSUMED, but for different raw timestamps — two real
    assumptions, both worth reporting. Two *identical* flags would be one fact counted twice.
    """
    merged = list(first)
    seen = {(f.code, f.field, f.raw_value) for f in first}

    for flag in second:
        key = (flag.code, flag.field, flag.raw_value)
        if key in seen:
            continue
        seen.add(key)
        merged.append(flag)

    return merged


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    return [v for v in values if not (v in seen or seen.add(v))]
