"""The four matching signals (doc 02, Decision 3).

Each scorer takes a CRM event and a calendar event and returns a score in [0, 1] plus the
sentence that will appear in the UI's evidence panel. Weights, thresholds, and assignment
belong to step 12 — keeping them out means each signal can be argued with on its own.

Nothing here reads the raw records: the normalizers already resolved every parsing question,
so a scorer that needed `raw` would be a sign the normalizer left something undone.
"""

import re
from typing import NamedTuple

from app.config import get_settings
from app.models.normalized import FlagCode, NormalizedEvent

MAX_TIME_GAP_HOURS = 4.0
"""Beyond this, time contributes nothing. Doc 02's decay window."""

DATE_ONLY_SCORE = 0.5
"""CRM-1007 has no time. Scoring it 0 would punish the record for a gap the *source* has;
scoring it 1.0 would invent agreement. A neutral half says "no evidence either way"."""

_TITLE_STOPWORDS = frozenset(
    {
        "a", "an", "and", "the", "of", "for", "with", "to", "in", "on", "at", "call",
        "meeting", "session", "sync", "review", "update", "prep", "discussion", "check",
        "annual", "quarterly", "q1", "q2", "q3", "q4", "year", "end",
    }
)

_VIRTUAL_MARKERS = ("zoom", "teams", "meet", "webex", "virtual", "http", "dial")
_MIN_TOKEN_LENGTH = 4


class SignalScore(NamedTuple):
    """A score and the reason for it. The detail is a product feature, not a debug aid."""

    score: float
    detail: str


# --------------------------------------------------------------------------- participants


def participant_overlap(crm: NormalizedEvent, calendar: NormalizedEvent) -> SignalScore:
    """Bridge the CRM's names to the calendar's email addresses.

    Averaged over the components that are *available* rather than over all three: an
    internal meeting has no client, and penalising it for fields the source never had would
    make every internal pair unmatchable.
    """
    components: list[tuple[float, float, str]] = []  # (share, score, description)

    owner_score, owner_detail = _owner_presence(crm, calendar)
    if owner_score is not None:
        components.append((0.4, owner_score, owner_detail))

    if _has_usable_client(crm):
        client_key = _name_key(crm.client_name)
        attendee_keys = {_email_key(p.email) for p in calendar.participants if p.email}
        hit = bool(client_key) and client_key in attendee_keys
        components.append((0.4, 1.0 if hit else 0.0, f"client {'found' if hit else 'absent'}"))

    if crm.client_company:
        domains = {p.domain for p in calendar.participants if p.domain}
        hit = any(_company_matches_domain(crm.client_company, domain) for domain in domains)
        components.append((0.2, 1.0 if hit else 0.0, f"company {'=' if hit else '≠'} domain"))

    if not components:
        return SignalScore(0.0, "no comparable party information")

    total_share = sum(share for share, _, _ in components)
    score = sum(share * value for share, value, _ in components) / total_share
    return SignalScore(score, ", ".join(description for _, _, description in components))


def _owner_presence(
    crm: NormalizedEvent, calendar: NormalizedEvent
) -> tuple[float | None, str]:
    """Did the relationship owner convene the meeting, or merely attend it?

    Both are evidence, and treating only the first as such loses a real pair: CAL-A14 was
    created by Priya Sharma while CRM-1013's owner, Sarah Chen, is in the attendee list.
    Organising is the stronger signal, so attending scores lower rather than equal —
    otherwise every internal meeting the whole team attends would look like every other.
    """
    owner_local = _name_key(crm.owner_name)
    if not owner_local:
        return None, ""

    if owner_local == _email_key(calendar.organizer):
        return 1.0, "owner organised it"

    attendee_keys = {_email_key(p.email) for p in calendar.participants if p.email}
    if owner_local in attendee_keys:
        return 0.7, "owner attended"

    if not calendar.organizer and not attendee_keys:
        return None, ""

    return 0.0, "owner absent"


def _has_usable_client(crm: NormalizedEvent) -> bool:
    """`CRM-1017`'s client is literally "Multiple" (flagged in step 8). Deriving a
    `multiple` local-part from it would score a person who does not exist."""
    if not crm.client_name:
        return False
    return FlagCode.PLACEHOLDER_CLIENT not in crm.flag_codes


def _company_matches_domain(company: str, domain: str) -> bool:
    """Every domain in the file is an *abbreviation* of its company, not a truncation:
    Atlas Ventures → atlasvc, Horizon Wealth Partners → horizonwp. No string metric covers
    both, but all ten share one property — the domain root begins with the company's first
    significant token."""
    root = domain.split(".")[0].lower()
    tokens = _tokens(company)
    if not root or not tokens:
        return False

    first = tokens[0]
    if len(first) >= _MIN_TOKEN_LENGTH and (root.startswith(first) or first.startswith(root)):
        return True

    return root == "".join(token[0] for token in tokens)


# ---------------------------------------------------------------------------------- time


def time_proximity(crm: NormalizedEvent, calendar: NormalizedEvent) -> SignalScore:
    """1.0 at an exact start, decaying linearly to 0 at ±4 hours."""
    if crm.start is None or calendar.start is None:
        return SignalScore(DATE_ONLY_SCORE, "one side has no time of day")

    gap_hours = abs((crm.start - calendar.start).total_seconds()) / 3600
    if gap_hours >= MAX_TIME_GAP_HOURS:
        return SignalScore(0.0, f"{gap_hours:.1f}h apart")

    score = 1.0 - (gap_hours / MAX_TIME_GAP_HOURS)
    if gap_hours == 0:
        return SignalScore(score, "same start time")
    return SignalScore(score, f"{gap_hours * 60:.0f} min apart")


# --------------------------------------------------------------------------------- title


def title_similarity(crm: NormalizedEvent, calendar: NormalizedEvent) -> SignalScore:
    """Token overlap, plus the two allowances this data requires.

    Titles are never identical across the sources — the calendar convention prefixes the
    company name — so raw string similarity would reject real pairs.
    """
    left = _content_tokens(crm.title, crm.text, crm.client_company)
    right = _content_tokens(calendar.title, calendar.text)

    if not left or not right:
        return SignalScore(0.0, "no comparable text")

    shared = left & right
    if shared:
        score = len(shared) / min(len(left), len(right))
        return SignalScore(min(score, 1.0), f"shared: {', '.join(sorted(shared))}")

    if _acronym_match(crm.title, calendar.title):
        return SignalScore(0.6, "acronym match")

    return SignalScore(0.0, "no shared terms")


def _content_tokens(*parts: str | None) -> set[str]:
    """Comparable words: lowercased, stopworded, and long enough to mean something."""
    tokens: set[str] = set()
    for part in parts:
        if not part:
            continue
        tokens.update(t for t in _tokens(part) if t not in _TITLE_STOPWORDS and len(t) > 2)
    return tokens


def _acronym_match(left: str | None, right: str | None) -> bool:
    """`LPAC` ↔ `LP Advisory Committee`.

    Works on the titles *in order*: an acronym is a sequence, so the tokens cannot be
    sorted or setted first. Stopwords stay in, since "Prep" is a stopword for overlap
    purposes but could still be part of an abbreviation.
    """
    left_tokens, right_tokens = _tokens(left), _tokens(right)
    return bool(
        _acronyms(left_tokens) & set(right_tokens)
        or _acronyms(right_tokens) & set(left_tokens)
    )


def _acronyms(ordered: list[str]) -> set[str]:
    """Every 2-to-4 token run, initials only — except tokens of two characters or fewer,
    which contribute whole so `LP` stays `LP` rather than becoming `L`."""
    if len(ordered) < 2:
        return set()

    candidates = set()
    for size in range(2, min(len(ordered), 4) + 1):
        for start in range(len(ordered) - size + 1):
            window = ordered[start : start + size]
            candidates.add("".join(t if len(t) <= 2 else t[0] for t in window))
    return candidates


# ----------------------------------------------------------------------------- structure


def structural_agreement(crm: NormalizedEvent, calendar: NormalizedEvent) -> SignalScore:
    """Location compatibility and modality, weighted 0.6/0.4 within this signal.

    Deliberately the smallest signal (0.10 in step 12): CRM-1002 says In-Person while its
    calendar entry says Zoom, and doc 02 treats that as a conflict to *display*, not a
    reason to reject an otherwise obvious pairing.
    """
    location, location_detail = _location_agreement(crm.location, calendar.location)
    modality, modality_detail = _modality_agreement(crm.meeting_type, calendar.location)

    score = 0.6 * location + 0.4 * modality
    return SignalScore(score, f"{location_detail}; {modality_detail}")


def _location_agreement(left: str | None, right: str | None) -> tuple[float, str]:
    if not left or not right:
        return 0.5, "location unknown on one side"

    a, b = left.strip().lower(), right.strip().lower()
    if a == b:
        return 1.0, "same location"
    if a in b or b in a:
        return 1.0, "one location contains the other"

    shared = {t for t in _tokens(a) if len(t) >= _MIN_TOKEN_LENGTH} & {
        t for t in _tokens(b) if len(t) >= _MIN_TOKEN_LENGTH
    }
    if shared:
        return 0.6, f"locations share {', '.join(sorted(shared))}"

    return 0.0, "locations differ"


def _modality_agreement(meeting_type: str | None, location: str | None) -> tuple[float, str]:
    if not meeting_type or not location:
        return 0.5, "modality unknown"

    virtual_location = _looks_virtual(location)
    declared_virtual = meeting_type.strip().lower() == "virtual"

    if declared_virtual == virtual_location:
        return 1.0, "modality agrees"
    return 0.0, f"{meeting_type} vs {'virtual' if virtual_location else 'physical'} location"


def _looks_virtual(location: str) -> bool:
    lowered = location.lower()
    return any(marker in lowered for marker in _VIRTUAL_MARKERS)


# ------------------------------------------------------------------------------- helpers


def _tokens(text: str | None) -> list[str]:
    if not text:
        return []
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


def _name_key(name: str | None) -> str:
    """"David Park" → davidpark, so it can meet david.park@… on equal terms."""
    return "".join(_tokens(name))


def _email_key(email: str | None) -> str:
    if not email:
        return ""
    local = email.split("@")[0]
    return "".join(_tokens(local))


def internal_domain() -> str:
    return get_settings().internal_domain.lower()
