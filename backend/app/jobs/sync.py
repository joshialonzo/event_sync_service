"""The sync job: load, reconcile, and assemble one publishable `SyncResult`.

Deliberately thin. Every judgement was made in steps 7-13; anything here that looked like a
rule would mean a stage had left something undone. What this module *does* own is the two
things only the whole run can know: the canonical ordering, and the counts.
"""

from collections import Counter
from datetime import date, datetime, time
from pathlib import Path

from app.config import get_settings
from app.ingest.calendar import load_calendar
from app.ingest.crm import load_crm
from app.models.unified import Origin, SyncResult, SyncRunSummary, UnifiedMeeting
from app.reconcile.dedupe import dedupe_events
from app.reconcile.matcher import MatchResult, match_events
from app.reconcile.merge import merge_all
from app.reconcile.normalize_calendar import normalize_calendar_records
from app.reconcile.normalize_crm import normalize_crm_records
from app.reconcile.parse import local_timezone


def run_sync(data_dir: Path | None = None) -> SyncResult:
    """Run the pipeline end to end and return a complete, publishable result.

    Pure with respect to the store: it builds a result and hands it back. Publishing is the
    caller's decision, which is what lets step 25's re-sync swap atomically and lets tests
    run the pipeline without a repository.
    """
    directory = data_dir if data_dir is not None else get_settings().data_dir

    raw_crm = load_crm(directory)
    raw_calendar = load_calendar(directory)

    crm_events = normalize_crm_records(raw_crm)
    calendar_events = normalize_calendar_records(raw_calendar)

    # Run on both sources. It is a no-op on the CRM with this data, but deduping only the
    # calendar would make the pipeline asymmetric for a reason that is a property of the
    # dataset rather than of the design.
    deduped_crm = dedupe_events(crm_events)
    deduped_calendar = dedupe_events(calendar_events)
    duplicates_collapsed = (len(crm_events) - len(deduped_crm)) + (
        len(calendar_events) - len(deduped_calendar)
    )

    matches = match_events(deduped_crm, deduped_calendar)
    meetings = merge_all(matches)

    ordered = _order(meetings)

    return SyncResult(
        meetings={meeting.id: meeting for meeting in meetings},
        by_date=[meeting.id for meeting in ordered],
        summary=_summarise(
            raw_crm=raw_crm,
            raw_calendar=raw_calendar,
            duplicates_collapsed=duplicates_collapsed,
            matches=matches,
            meetings=meetings,
        ),
    )


def _order(meetings: list[UnifiedMeeting]) -> list[UnifiedMeeting]:
    """Chronological, earliest first.

    Sorting happens once, here: the normalizers preserve input order so earlier tests can be
    positional, and the store is where a canonical order gets decided. The fallbacks matter
    even though nothing in this dataset needs them — a date-only meeting sorts to the start
    of its day rather than failing the sync.
    """
    far_future = date.max
    day_start = time.min

    def key(meeting: UnifiedMeeting) -> tuple:
        day = meeting.event_date or far_future
        moment = meeting.start.timetz() if meeting.start else day_start
        return (day, str(moment), meeting.id)

    return sorted(meetings, key=key)


def _summarise(
    *,
    raw_crm: list[dict],
    raw_calendar: list[dict],
    duplicates_collapsed: int,
    matches: MatchResult,
    meetings: list[UnifiedMeeting],
) -> SyncRunSummary:
    """Every number `GET /api/stats` reports.

    Counted from the pipeline's own intermediate results rather than re-derived from the
    output, so the summary cannot quietly disagree with what actually happened.
    """
    origins = Counter(meeting.origin for meeting in meetings)

    conflicts_by_kind: Counter[str] = Counter()
    conflicts_by_field: Counter[str] = Counter()
    for meeting in meetings:
        for name, field in meeting.provenance_fields.items():
            if field.conflict_kind is not None:
                conflicts_by_kind[field.conflict_kind.value] += 1
            if field.conflict:
                conflicts_by_field[name] += 1

    flags = [flag for meeting in meetings for flag in meeting.flags]

    return SyncRunSummary(
        generated_at=datetime.now(tz=local_timezone()),
        crm_records_in=len(raw_crm),
        calendar_records_in=len(raw_calendar),
        duplicates_collapsed=duplicates_collapsed,
        meetings_out=len(meetings),
        matched_pairs=len(matches.pairs),
        crm_only=origins[Origin.CRM_ONLY],
        calendar_only=origins[Origin.CALENDAR_ONLY],
        low_confidence_matches=sum(
            1 for pair in matches.pairs if pair.evidence.is_low_confidence
        ),
        conflicts_by_kind=dict(conflicts_by_kind),
        conflicts_by_field=dict(conflicts_by_field),
        flags_by_code=dict(Counter(flag.code.value for flag in flags)),
        flags_by_severity=dict(Counter(flag.severity.value for flag in flags)),
    )
