"""Merge matched records into unified meetings (doc 02, Decision 4).

The guiding rule, from doc 02: *the service reconciles; it does not adjudicate.* Every field
gets a display default **and** keeps what the other source said. A merge that silently picked
a winner and discarded the loser would destroy the signal this project exists to surface.
"""

import re

from app.models.normalized import MeetingStatus, NormalizedEvent, Source
from app.models.unified import (
    ConflictKind,
    Origin,
    ProvenanceField,
    SourceValue,
    UnifiedMeeting,
)
from app.reconcile.matcher import MatchResult, MatchedPair

_MIN_TOKEN_LENGTH = 4
_LOCATION_STOPWORDS = frozenset({"the", "office", "room", "floor", "at", "in", "-"})

# Ordered least-to-most advanced through a meeting's life. Only used to recognise drift:
# a CRM that has moved on is not disagreeing with a calendar that never updates.
_LIFECYCLE = (
    MeetingStatus.TENTATIVE,
    MeetingStatus.SCHEDULED,
    MeetingStatus.CONFIRMED,
    MeetingStatus.COMPLETED,
)


def merge_all(result: MatchResult) -> list[UnifiedMeeting]:
    """Every matched pair and every unmatched record becomes exactly one meeting."""
    meetings = [merge_pair(pair) for pair in result.pairs]
    meetings += [merge_single(event) for event in result.unmatched_crm]
    meetings += [merge_single(event) for event in result.unmatched_calendar]
    return meetings


def merge_pair(pair: MatchedPair) -> UnifiedMeeting:
    """Both sources described this meeting. Resolve field by field, keeping the losers."""
    crm, calendar = pair.crm, pair.calendar

    return UnifiedMeeting(
        id=_meeting_id([*crm.source_ids, *calendar.source_ids]),
        origin=Origin.BOTH,
        event_date=calendar.event_date or crm.event_date,
        start=calendar.start or crm.start,
        crm_ids=list(crm.source_ids),
        calendar_ids=list(calendar.source_ids),
        # The CRM subject states the business purpose; the calendar title is written for
        # inbox scanning. Different conventions, never a disagreement.
        title=_prefer(crm.title, Source.CRM, calendar.title, Source.CALENDAR),
        start_time=_merge_start(crm, calendar),
        end_time=_prefer(calendar.end, Source.CALENDAR, crm.end, Source.CRM),
        location=_merge_location(crm.location, calendar.location),
        participants=_prefer(
            [p.model_dump() for p in calendar.participants] or None,
            Source.CALENDAR,
            [p.model_dump() for p in crm.participants] or None,
            Source.CRM,
        ),
        client_name=_prefer(crm.client_name, Source.CRM, calendar.client_name, Source.CALENDAR),
        client_company=_prefer(
            crm.client_company, Source.CRM, calendar.client_company, Source.CALENDAR
        ),
        owner_name=_prefer(crm.owner_name, Source.CRM, calendar.organizer, Source.CALENDAR),
        meeting_type=_prefer(
            crm.meeting_type, Source.CRM, calendar.meeting_type, Source.CALENDAR
        ),
        notes=_prefer(crm.text, Source.CRM, calendar.text, Source.CALENDAR),
        status=_merge_status(crm, calendar),
        match_evidence=pair.evidence,
        flags=[*crm.flags, *calendar.flags],
        raw_crm=crm.raw_records,
        raw_calendar=calendar.raw_records,
    )


def merge_single(event: NormalizedEvent) -> UnifiedMeeting:
    """One source only.

    Doc 02, Decision 5: these are first-class, not an error bucket. A CRM meeting with no
    calendar entry means it was never booked; a calendar entry with no CRM record means
    client time is not being logged. Both are the point of the exercise.
    """
    is_crm = event.source is Source.CRM
    source = Source.CRM if is_crm else Source.CALENDAR

    return UnifiedMeeting(
        id=_meeting_id(event.source_ids),
        origin=Origin.CRM_ONLY if is_crm else Origin.CALENDAR_ONLY,
        event_date=event.event_date,
        start=event.start,
        crm_ids=list(event.source_ids) if is_crm else [],
        calendar_ids=[] if is_crm else list(event.source_ids),
        title=_single(event.title, source),
        start_time=_single(event.start, source),
        end_time=_single(event.end, source),
        location=_single(event.location, source),
        participants=_single(
            [p.model_dump() for p in event.participants] or None, source
        ),
        client_name=_single(event.client_name, source),
        client_company=_single(event.client_company, source),
        owner_name=_single(event.owner_name or event.organizer, source),
        meeting_type=_single(event.meeting_type, source),
        notes=_single(event.text, source),
        status=_single(event.status.value, source),
        flags=list(event.flags),
        raw_crm=event.raw_records if is_crm else [],
        raw_calendar=[] if is_crm else event.raw_records,
    )


# ------------------------------------------------------------------ field resolution


def _single(value: object, source: Source) -> ProvenanceField:
    return ProvenanceField.empty() if value is None else ProvenanceField.single(value, source)


def _prefer(
    winner: object,
    winner_source: Source,
    loser: object,
    loser_source: Source,
    *,
    mark_absence: bool = False,
) -> ProvenanceField:
    """Apply a precedence rule, optionally recording that one side was empty.

    An absence is deliberately *not* a conflict (doc 02): one source simply had nothing to
    say, and badging that would mean nearly every record shows a conflict.

    `mark_absence` is opt-in because the two sources model different things. The calendar
    has no `client_name` field at all, so labelling every pair's client an "absence" would
    report 17 gaps that are really just schema differences. It is set only for fields both
    sources genuinely have — where a null is a real editorial gap, as with CRM-1018's
    missing location against CAL-A21's "Zoom".
    """
    if winner is None and loser is None:
        return ProvenanceField.empty()

    if winner is None:
        return ProvenanceField.resolved(
            value=loser,
            source=loser_source,
            other_source=winner_source,
            other_value=None,
            kind=ConflictKind.ABSENCE,
        )

    if loser is None:
        if mark_absence:
            return ProvenanceField.resolved(
                value=winner,
                source=winner_source,
                other_source=loser_source,
                other_value=None,
                kind=ConflictKind.ABSENCE,
            )
        return ProvenanceField.single(winner, winner_source)

    if winner == loser:
        return ProvenanceField.single(winner, winner_source)

    # Both spoke and precedence decided. No `conflict_kind` is set: the kinds record a
    # *judgement* about compatibility, and none was made here. A CRM subject differing from
    # a calendar title, or "Sarah Chen" differing from sarah.chen@firma.com, is a difference
    # of convention rather than of fact — labelling those "granularity" would put a kind on
    # 76 fields and drain the word of meaning.
    return ProvenanceField(
        value=winner,
        source=winner_source,
        alternatives=[SourceValue(source=loser_source, value=loser)],
    )


def _merge_start(crm: NormalizedEvent, calendar: NormalizedEvent) -> ProvenanceField:
    """The calendar owns logistics, but a differing start time is a real contradiction —
    there is no benign reading of 13:00 against 15:00."""
    if crm.start is None or calendar.start is None:
        return _prefer(
            calendar.start, Source.CALENDAR, crm.start, Source.CRM, mark_absence=True
        )

    if crm.start == calendar.start:
        return ProvenanceField.single(calendar.start, Source.CALENDAR)

    return ProvenanceField.resolved(
        value=calendar.start,
        source=Source.CALENDAR,
        other_source=Source.CRM,
        other_value=crm.start,
        kind=ConflictKind.CONTRADICTION,
    )


def _merge_location(crm_location: str | None, calendar_location: str | None) -> ProvenanceField:
    """Three outcomes, and telling them apart is the whole point (doc 02).

    `"The Palm - DC"` against `"The Palm Restaurant"` is the case that forced the shared-token
    rule: doc 01 calls it compatible, but neither string contains the other, so containment
    alone would report a contradiction.
    """
    if crm_location is None or calendar_location is None:
        return _prefer(
            calendar_location, Source.CALENDAR, crm_location, Source.CRM, mark_absence=True
        )

    left, right = crm_location.strip(), calendar_location.strip()

    if left.lower() == right.lower():
        return ProvenanceField.single(right, Source.CALENDAR)

    if _is_more_specific(left, right):
        return _granularity(left, Source.CRM, right, Source.CALENDAR)

    if _is_more_specific(right, left):
        return _granularity(right, Source.CALENDAR, left, Source.CRM)

    if _shares_a_significant_token(left, right):
        # Compatible descriptions of one place at different specificity. The longer value
        # carries more information, so it is shown.
        if len(right) >= len(left):
            return _granularity(right, Source.CALENDAR, left, Source.CRM)
        return _granularity(left, Source.CRM, right, Source.CALENDAR)

    return ProvenanceField.resolved(
        value=calendar_location,
        source=Source.CALENDAR,
        other_source=Source.CRM,
        other_value=crm_location,
        kind=ConflictKind.CONTRADICTION,
    )


def _granularity(
    value: str, source: Source, other: str, other_source: Source
) -> ProvenanceField:
    return ProvenanceField.resolved(
        value=value,
        source=source,
        other_source=other_source,
        other_value=other,
        kind=ConflictKind.GRANULARITY,
    )


def _merge_status(crm: NormalizedEvent, calendar: NormalizedEvent) -> ProvenanceField:
    """Doc 02's deliberate exception: neither source wins by default.

    Only genuine incompatibility is badged. Three pairs in this data have differing statuses
    but just one is a contradiction — flagging all three would put a badge on vocabulary
    drift and teach the reader to ignore it.
    """
    crm_status, calendar_status = crm.status, calendar.status

    if crm_status is calendar_status:
        return ProvenanceField.single(crm_status.value, Source.CRM)

    if _is_contradictory(crm_status, calendar_status):
        # Default to the more conservative value for filtering only; both are displayed.
        conservative, other = (
            (crm, calendar) if crm_status is MeetingStatus.CANCELLED else (calendar, crm)
        )
        conservative_source = (
            Source.CRM if conservative is crm else Source.CALENDAR
        )
        other_source = Source.CALENDAR if conservative is crm else Source.CRM

        return ProvenanceField.resolved(
            value=conservative.status.value,
            source=conservative_source,
            other_source=other_source,
            other_value=other.status.value,
            kind=ConflictKind.CONTRADICTION,
        )

    # Lifecycle drift: the CRM has moved on and the calendar never updates after the fact.
    ahead, behind = (
        (crm, calendar) if _lifecycle_rank(crm_status) >= _lifecycle_rank(calendar_status)
        else (calendar, crm)
    )
    ahead_source = Source.CRM if ahead is crm else Source.CALENDAR
    behind_source = Source.CALENDAR if ahead is crm else Source.CRM

    return ProvenanceField.resolved(
        value=ahead.status.value,
        source=ahead_source,
        other_source=behind_source,
        other_value=behind.status.value,
        kind=ConflictKind.GRANULARITY,
    )


def _is_contradictory(left: MeetingStatus, right: MeetingStatus) -> bool:
    """Cancelled against anything else sends someone to an empty room, or loses a client
    meeting. Tentative against confirmed is a real question about whether it is booked —
    it does not occur in this data, but the rule should not be silent about it."""
    pair = {left, right}

    if MeetingStatus.CANCELLED in pair:
        return True

    return pair == {MeetingStatus.TENTATIVE, MeetingStatus.CONFIRMED}


def _lifecycle_rank(status: MeetingStatus) -> int:
    return _LIFECYCLE.index(status) if status in _LIFECYCLE else -1


# ----------------------------------------------------------------------------- helpers


def _is_more_specific(candidate: str, other: str) -> bool:
    return other.lower() in candidate.lower() and candidate.lower() != other.lower()


def _shares_a_significant_token(left: str, right: str) -> bool:
    return bool(_location_tokens(left) & _location_tokens(right))


def _location_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.split(r"[^a-z0-9]+", value.lower())
        if len(token) >= _MIN_TOKEN_LENGTH and token not in _LOCATION_STOPWORDS
    }


def _meeting_id(source_ids: list[str]) -> str:
    """Readable and stable: derived from the source ids, so the same records always produce
    the same URL across sync runs."""
    return "-".join(source_ids).lower()
