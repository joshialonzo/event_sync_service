"""Meeting filters, shared by the JSON API and the HTML list.

Kept out of the `Repository` protocol on purpose: filtering is a view concern, both readers
apply it to the same full list, and a future persistent store should not be obliged to
reimplement predicate pushdown for 24 rows. Keeping it in one pure function is also what
stops `?origin=crm_only` meaning two different things in two places.
"""

import re
from datetime import date

from pydantic import BaseModel

from app.models.unified import Origin, UnifiedMeeting


class MeetingFilters(BaseModel):
    """Every filter is optional; supplied ones combine with AND."""

    origin: Origin | None = None
    has_conflicts: bool | None = None
    date_from: date | None = None
    date_to: date | None = None
    owner: str | None = None

    @property
    def is_empty(self) -> bool:
        return not any(
            value is not None for value in self.model_dump().values()
        )


def apply_filters(
    meetings: list[UnifiedMeeting], filters: MeetingFilters
) -> list[UnifiedMeeting]:
    """Return the meetings matching every supplied filter, in the order given."""
    return [meeting for meeting in meetings if _matches(meeting, filters)]


def _matches(meeting: UnifiedMeeting, filters: MeetingFilters) -> bool:
    if filters.origin is not None and meeting.origin is not filters.origin:
        return False

    if filters.has_conflicts is not None and meeting.has_conflicts is not filters.has_conflicts:
        return False

    if filters.date_from is not None:
        if meeting.event_date is None or meeting.event_date < filters.date_from:
            return False

    if filters.date_to is not None:
        if meeting.event_date is None or meeting.event_date > filters.date_to:
            return False

    if filters.owner:
        if not _owner_matches(meeting, filters.owner):
            return False

    return True


def _owner_matches(meeting: UnifiedMeeting, query: str) -> bool:
    """Match "Sarah Chen" against both `"Sarah Chen"` and `"sarah.chen@firma.com"`.

    Owner values are heterogeneous by construction: matched and CRM-only meetings carry the
    CRM's `relationship_owner` (a name), while calendar-only meetings carry the organizer's
    email, because there is no CRM record to take a name from.

    A plain substring test would return 11 meetings for "Sarah Chen" and silently drop the 3
    calendar-only ones she organised — which are precisely the records worth surfacing, since
    a calendar entry with no CRM record means client time is not being logged.
    """
    needle = _identity_key(query)
    if not needle:
        return False

    candidates = {
        _identity_key(meeting.owner_name.value),
        *(_identity_key(alt.value) for alt in meeting.owner_name.alternatives),
    }
    return any(needle in candidate for candidate in candidates if candidate)


def _identity_key(value: object) -> str:
    """Lowercase alphanumerics of a name, or of an email's local part."""
    if value is None:
        return ""

    text = str(value).strip().lower()
    if "@" in text:
        text = text.split("@", 1)[0]

    return re.sub(r"[^a-z0-9]", "", text)
