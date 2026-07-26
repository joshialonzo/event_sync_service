"""Tests for the four matching signals (step 11).

Each signal is argued with on its own here; step 12 tests the weighted combination and the
assignment. Synthetic events are used for the boundary cases, real records for the bridging
cases that motivated the rules.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.ingest.calendar import load_calendar
from app.ingest.crm import load_crm
from app.models.normalized import FlagCode, NormalizedEvent, Participant, Source
from app.reconcile import signals
from app.reconcile.dedupe import dedupe_events
from app.reconcile.normalize_calendar import normalize_calendar_records
from app.reconcile.normalize_crm import normalize_crm_records

EASTERN = ZoneInfo("America/New_York")
BASE = datetime(2025, 3, 10, 14, 0, tzinfo=EASTERN)

ALL_SIGNALS = (
    signals.participant_overlap,
    signals.time_proximity,
    signals.title_similarity,
    signals.structural_agreement,
)


@pytest.fixture(scope="module")
def crm_events():
    return {e.primary_id: e for e in normalize_crm_records(load_crm())}


@pytest.fixture(scope="module")
def calendar_events():
    deduped = dedupe_events(normalize_calendar_records(load_calendar()))
    return {e.primary_id: e for e in deduped}


def _crm(**overrides) -> NormalizedEvent:
    kwargs = {
        "source": Source.CRM,
        "source_ids": ["CRM-X"],
        "start": BASE,
        "owner_name": "Sarah Chen",
        "organizer": "Sarah Chen",
        "client_name": "David Park",
        "client_company": "Meridian Capital",
        "title": "Q1 Portfolio Review",
        "location": "HQ - Conference Room B",
        "meeting_type": "In-Person",
        "raw": {},
    }
    kwargs.update(overrides)
    return NormalizedEvent(**kwargs)


def _calendar(*, attendees=("sarah.chen@firma.com", "david.park@meridiancap.com"), **overrides):
    kwargs = {
        "source": Source.CALENDAR,
        "source_ids": ["CAL-X"],
        "start": BASE,
        "organizer": attendees[0] if attendees else None,
        "participants": [
            Participant(
                email=email,
                display=email,
                raw=email,
                is_organizer=(index == 0),
            )
            for index, email in enumerate(attendees)
        ],
        "title": "Q1 Portfolio Review - Meridian Capital",
        "location": "Conference Room B",
        "raw": {},
    }
    kwargs.update(overrides)
    return NormalizedEvent(**kwargs)


# --- participants: bridging names to addresses ---


def test_client_name_matches_its_email_local_part() -> None:
    assert signals.participant_overlap(_crm(), _calendar()).score == 1.0


def test_a_different_person_does_not_match() -> None:
    result = signals.participant_overlap(
        _crm(client_name="Someone Else"),
        _calendar(attendees=("sarah.chen@firma.com", "david.park@meridiancap.com")),
    )

    assert result.score < 1.0
    assert "absent" in result.detail


@pytest.mark.parametrize(
    ("company", "domain"),
    [
        ("Meridian Capital", "meridiancap.com"),
        ("Horizon Wealth Partners", "horizonwp.com"),
        ("Atlas Ventures", "atlasvc.com"),
        ("Granite Point Capital", "granitepointcap.com"),
        ("Crestview Holdings", "crestviewhold.com"),
        ("Pinnacle Group", "pinnaclegp.com"),
        ("Evergreen Capital", "evergreencap.com"),
        ("Summit Advisors", "summitadv.com"),
        ("Redwood Institutional", "redwoodinst.com"),
    ],
)
def test_every_company_abbreviation_in_the_file_matches(company: str, domain: str) -> None:
    """The domain is always an abbreviation, never a truncation — no single string metric
    covers both `horizonwp` and `atlasvc`, so the rule was derived from all ten."""
    crm = _crm(client_name=None, client_company=company)
    calendar = _calendar(attendees=(f"someone@{domain}",))

    assert signals.participant_overlap(crm, calendar).score > 0


def test_an_unrelated_domain_does_not_match() -> None:
    crm = _crm(client_name=None, client_company="Meridian Capital")
    calendar = _calendar(attendees=("someone@totallyunrelated.com",))

    assert signals.participant_overlap(crm, calendar).score == 0.0


def test_owner_organising_beats_owner_merely_attending() -> None:
    """CAL-A14 was created by Priya Sharma while CRM-1013's owner Sarah Chen only attends.
    Scoring that 0 loses a real pair; scoring it 1.0 would make every internal meeting the
    whole team attends look like every other."""
    organised = _calendar(attendees=("sarah.chen@firma.com",))
    attended = _calendar(attendees=("priya.sharma@firma.com", "sarah.chen@firma.com"))
    internal = _crm(client_name=None, client_company=None)

    high = signals.participant_overlap(internal, organised).score
    mid = signals.participant_overlap(internal, attended).score

    assert high == 1.0
    assert 0 < mid < high


def test_owner_absent_from_the_invite_scores_zero() -> None:
    internal = _crm(client_name=None, client_company=None)
    elsewhere = _calendar(attendees=("someone.else@firma.com",))

    assert signals.participant_overlap(internal, elsewhere).score == 0.0


def test_internal_record_is_scored_on_what_it_has(crm_events, calendar_events) -> None:
    """CRM-1013 has no client at all. Averaging over three components when the source only
    supplies one would cap an internal pair at 0.4 for fields that never existed."""
    result = signals.participant_overlap(crm_events["CRM-1013"], calendar_events["CAL-A14"])

    assert result.score > 0.5


def test_placeholder_client_contributes_no_person_match(crm_events, calendar_events) -> None:
    """CRM-1017's client is literally "Multiple" (flagged in step 8). A `multiple`
    local-part would be a fabricated person scored as evidence."""
    result = signals.participant_overlap(crm_events["CRM-1017"], calendar_events["CAL-A20"])

    assert FlagCode.PLACEHOLDER_CLIENT in crm_events["CRM-1017"].flag_codes
    assert "client" not in result.detail


def test_no_party_information_scores_zero() -> None:
    bare_crm = _crm(owner_name=None, client_name=None, client_company=None)
    bare_calendar = _calendar(attendees=(), organizer=None)

    assert signals.participant_overlap(bare_crm, bare_calendar).score == 0.0


# --- time ---


def test_identical_starts_score_one() -> None:
    assert signals.time_proximity(_crm(), _calendar()).score == 1.0


@pytest.mark.parametrize(
    ("hours", "expected"),
    [(0, 1.0), (1, 0.75), (2, 0.5), (3, 0.25), (4, 0.0), (6, 0.0)],
)
def test_time_decays_linearly_to_four_hours(hours: float, expected: float) -> None:
    calendar = _calendar(start=BASE + timedelta(hours=hours))

    assert signals.time_proximity(_crm(), calendar).score == pytest.approx(expected)


def test_decay_is_symmetric() -> None:
    early = signals.time_proximity(_crm(), _calendar(start=BASE - timedelta(hours=2))).score
    late = signals.time_proximity(_crm(), _calendar(start=BASE + timedelta(hours=2))).score

    assert early == late


def test_date_only_record_is_neutral_not_zero() -> None:
    """CRM-1007 has no time. Zero would punish the record for a gap the source has."""
    result = signals.time_proximity(_crm(start=None, event_date=BASE.date()), _calendar())

    assert result.score == 0.5
    assert "no time of day" in result.detail


# --- title ---


def test_shared_terms_score(crm_events, calendar_events) -> None:
    result = signals.title_similarity(crm_events["CRM-1001"], calendar_events["CAL-A1"])

    assert result.score > 0.5
    assert "portfolio" in result.detail


def test_company_name_bridges_unrelated_titles(crm_events, calendar_events) -> None:
    """"Annual Allocation Review" vs "Horizon Wealth - Year-End Review" share no content
    words; the company name is the only bridge."""
    result = signals.title_similarity(crm_events["CRM-1011"], calendar_events["CAL-A12"])

    assert result.score > 0


def test_acronym_matches_its_expansion() -> None:
    """LPAC ↔ LP Advisory Committee. Tokens of two characters or fewer contribute whole."""
    crm = _crm(title="LP Advisory Committee Prep", client_company=None, text=None)
    calendar = _calendar(title="LPAC Prep Working Session", text=None)

    assert signals.title_similarity(crm, calendar).score > 0


def test_unrelated_titles_score_zero() -> None:
    crm = _crm(title="Portfolio Rebalancing", client_company=None, text=None)
    calendar = _calendar(title="Fire Drill", text=None)

    assert signals.title_similarity(crm, calendar).score == 0.0


def test_missing_text_scores_zero_without_raising() -> None:
    crm = _crm(title=None, text=None, client_company=None)

    assert signals.title_similarity(crm, _calendar()).score == 0.0


# --- structure ---


def test_containment_counts_as_location_agreement() -> None:
    """"Conference Room B" ⊂ "HQ - Conference Room B" — different specificity, not conflict."""
    assert signals.structural_agreement(_crm(), _calendar()).score == 1.0


def test_shared_location_token_partially_agrees() -> None:
    crm = _crm(location="NYC Office", meeting_type=None)
    calendar = _calendar(location="NYC Office - 12th Floor")

    assert signals.structural_agreement(crm, calendar).score > 0.5


def test_missing_location_is_neutral_not_disagreement() -> None:
    """CRM-1018 has no location while CAL-A21 says Zoom — an absence, not a contradiction."""
    crm = _crm(location=None, meeting_type=None)

    result = signals.structural_agreement(crm, _calendar())

    assert result.score == pytest.approx(0.5)
    assert "unknown" in result.detail


def test_modality_contradiction_scores_low(crm_events, calendar_events) -> None:
    """CRM-1002 says In-Person, CAL-A2 says Zoom — the case the brief calls out by name.
    At weight 0.10 this costs the pair 0.06, which doc 02 says is a fact to display rather
    than grounds to reject the pairing."""
    result = signals.structural_agreement(crm_events["CRM-1002"], calendar_events["CAL-A2"])

    assert result.score == 0.0
    assert "In-Person" in result.detail


def test_virtual_modality_agrees_with_a_platform_location() -> None:
    crm = _crm(location="Microsoft Teams", meeting_type="Virtual")
    calendar = _calendar(location="Virtual - Microsoft Teams")

    assert signals.structural_agreement(crm, calendar).score == 1.0


# --- properties that must hold for every pair in the real data ---


def test_every_signal_is_bounded_and_explained(crm_events, calendar_events) -> None:
    """20 x 21 pairs. A score outside [0,1] would break MatchEvidence's arithmetic check,
    and an empty detail would leave the UI's evidence panel blank."""
    for crm in crm_events.values():
        for calendar in calendar_events.values():
            for signal in ALL_SIGNALS:
                result = signal(crm, calendar)

                assert 0.0 <= result.score <= 1.0, (signal.__name__, crm.primary_id)
                assert result.detail, (signal.__name__, crm.primary_id)


def test_signals_do_not_mutate_their_inputs(crm_events, calendar_events) -> None:
    """Doc 03 requires the reconcile stages to be pure — the bug step 10 actually hit."""
    crm = crm_events["CRM-1001"]
    calendar = calendar_events["CAL-A1"]
    before = (crm.model_dump_json(), calendar.model_dump_json())

    for signal in ALL_SIGNALS:
        signal(crm, calendar)

    assert (crm.model_dump_json(), calendar.model_dump_json()) == before


def test_the_true_pairs_outscore_every_false_pair(crm_events, calendar_events) -> None:
    """The property the 0.70 threshold in step 12 depends on: the weighted score separates
    the 17 documented pairs from all 403 other combinations, with a gap.

    Observed: lowest true pair 0.763 (CRM-1007/CAL-A8), highest false pair 0.660.
    """
    expected = {
        ("CRM-1001", "CAL-A1"), ("CRM-1002", "CAL-A2"), ("CRM-1004", "CAL-A4"),
        ("CRM-1005", "CAL-A5"), ("CRM-1006", "CAL-A7"), ("CRM-1007", "CAL-A8"),
        ("CRM-1008", "CAL-A9"), ("CRM-1009", "CAL-A10"), ("CRM-1011", "CAL-A12"),
        ("CRM-1012", "CAL-A13"), ("CRM-1013", "CAL-A14"), ("CRM-1014", "CAL-A15"),
        ("CRM-1015", "CAL-A16"), ("CRM-1016", "CAL-A17"), ("CRM-1017", "CAL-A20"),
        ("CRM-1018", "CAL-A21"), ("CRM-1019", "CAL-A22"),
    }
    weights = (0.40, 0.30, 0.20, 0.10)

    true_scores, false_scores = [], []
    for crm_id, crm in crm_events.items():
        for cal_id, calendar in calendar_events.items():
            total = sum(
                weight * signal(crm, calendar).score
                for weight, signal in zip(weights, ALL_SIGNALS)
            )
            bucket = true_scores if (crm_id, cal_id) in expected else false_scores
            bucket.append((total, crm_id, cal_id))

    assert len(true_scores) == 17
    assert min(true_scores)[0] > 0.70 > max(false_scores)[0]
