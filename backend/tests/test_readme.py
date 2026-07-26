"""Tests that the README describes this service (step 28).

The brief says the README's quality matters as much as the code's, which makes it the file most
worth checking mechanically: it is the one document a reviewer reads before they can tell
whether it is true. Every number, record ID, URL and weight quoted in it is compared against
the pipeline rather than restated here — a test that hard-codes 24 only agrees with itself.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.models.normalized import FlagCode
from app.models.filters import MeetingFilters
from app.reconcile.matcher import (
    AUTO_MATCH_THRESHOLD,
    LOW_CONFIDENCE_THRESHOLD,
    SIGNAL_WEIGHTS,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
README = REPO_ROOT / "README.md"


@pytest.fixture(scope="module")
def readme() -> str:
    return README.read_text()


@pytest.fixture(scope="module")
def stats(client: TestClient) -> dict:
    return client.get("/api/stats").json()


def _flow(readme: str) -> str:
    """The README as one line. Prose wraps at 100 columns, so a sentence is not a line."""
    return " ".join(readme.split())


def _fenced_blocks(readme: str) -> list[str]:
    blocks, current = [], None
    for line in readme.splitlines():
        if line.startswith("```"):
            if current is None:
                current = []
            else:
                blocks.append("\n".join(current))
                current = None
        elif current is not None:
            current.append(line)
    return blocks


def _cell(readme: str, row_contains: str) -> str:
    return next(line for line in readme.splitlines() if row_contains in line)


# --- what the brief asks a README to cover ---


@pytest.mark.parametrize(
    ("requirement", "heading"),
    [
        ("how to run it", "quick start"),
        ("your approach", "the approach"),
        ("your key decisions", "key decisions"),
        ("time spent", "time spent"),
    ],
)
def test_the_readme_covers_what_the_brief_asks_for(
    readme: str, requirement: str, heading: str
) -> None:
    """"A README covering: how to run it, your approach, and your key decisions" plus "time
    spent (be honest)". Four requirements, four sections."""
    headings = [
        line.lstrip("# ").strip().lower() for line in readme.splitlines() if line.startswith("#")
    ]

    assert heading in headings, requirement


def test_the_single_command_is_documented_and_real(readme: str) -> None:
    """"The service should start with a single command (document it)." The documented command
    has to be the one in the repository."""
    commands = re.findall(r"```bash\n(.*?)```", readme, re.DOTALL)

    assert commands[0].strip() == "docker compose up --build"
    assert (REPO_ROOT / "docker-compose.yml").is_file()


def test_the_local_setup_matches_the_repository(readme: str) -> None:
    assert "pip install -r requirements.txt" in readme
    assert (REPO_ROOT / "backend" / "requirements.txt").is_file()
    assert "uvicorn app.main:app" in readme


# --- the numbers ---


def test_the_headline_counts_match_the_pipeline(readme: str, stats: dict) -> None:
    """The first thing anyone reads, and the last thing anyone re-checks."""
    headline = _cell(readme, "records in →")

    assert (
        f"**{stats['records_in']} records in → {stats['meetings_out']} meetings out.**" in headline
    )


def test_the_source_split_matches(readme: str, stats: dict) -> None:
    sentence = _flow(readme)

    assert f"{stats['matched_pairs']} matched across both sources" in sentence
    assert f"{stats['crm_only']} CRM-only" in sentence
    assert f"{stats['calendar_only']} calendar-only" in sentence
    assert f"{stats['duplicates_collapsed']} duplicate" in sentence
    assert f"{stats['conflicts_by_kind']['contradiction']} genuine conflicts" in sentence


def test_the_conflict_table_matches_the_kinds(readme: str, stats: dict) -> None:
    """The classification argument is the README's central claim about conflicts: 15 fields
    differ and only 4 are disagreements. Both halves have to be true."""
    kinds = stats["conflicts_by_kind"]

    assert f"Only **{kinds['contradiction']}** are marked as conflicts" in readme
    assert f"{sum(kinds.values())} fields differ" in readme
    for kind, count in kinds.items():
        row = _cell(readme, f"**{kind.capitalize()}**")
        assert f"| {count} |" in row, kind


def test_every_flag_row_matches_its_count(readme: str, stats: dict) -> None:
    """Nine codes, nine rows, and the counts are the pipeline's."""
    counts = stats["flags_by_code"]
    rows = [line for line in readme.splitlines() if line.startswith("| ") and "`" in line]

    for code, count in counts.items():
        row = next((row for row in rows if f"`{code}`" in row), None)
        assert row is not None, code
        assert f"| {count} |" in row, code


def test_no_flag_code_is_invented(readme: str, stats: dict) -> None:
    """Codes named in the data-quality table. Settings like `DATA_DIR` look the same to a
    regex, so the table is located by its rows rather than by shouting case."""
    rows = [line for line in readme.splitlines() if line.startswith("| ") and "` |" in line]
    codes = {code for row in rows for code in re.findall(r"`([A-Z][A-Z_]{4,})`", row)}

    assert codes == set(stats["flags_by_code"])
    for code in codes:
        assert code in FlagCode.__members__, code


def test_the_severity_summary_matches(readme: str, stats: dict) -> None:
    severity = stats["flags_by_severity"]

    assert f"{_word(severity['error'])} error" in readme
    assert f"{_word(severity['warning'])} warning" in readme


def _word(number: int) -> str:
    return {1: "One", 2: "Two", 3: "three", 4: "four"}.get(number, str(number))


def test_the_test_count_is_current(readme: str) -> None:
    """The README quotes a test count three times. Cheap to state, easy to leave behind."""
    claimed = {int(count) for count in re.findall(r"(\d+) tests", readme)}
    collected = subprocess.run(
        # -o addopts= drops pytest.ini's own -q, which would otherwise suppress the total.
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-o", "addopts=",
         "-p", "no:cacheprovider"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT / "backend",
    ).stdout
    (actual,) = re.findall(r"(\d+) tests? collected", collected)

    assert claimed == {int(actual)}, f"README says {claimed}, suite collects {actual}"


# --- the claims about the algorithm ---


def test_the_signal_weights_match_the_matcher(readme: str) -> None:
    """The weights table is the part of the README a reviewer is most likely to argue with,
    which makes it the part most worth keeping true."""
    for name, weight, _ in SIGNAL_WEIGHTS:
        label = {"participants": "Participant overlap", "time": "Time proximity",
                 "title": "Title similarity", "structure": "Structural agreement"}[name]
        row = _cell(readme, f"| {label} |")

        assert f"| {weight:.2f} |" in row, name


def test_the_weights_sum_to_one_in_both_places(readme: str) -> None:
    quoted = [float(weight) for weight in re.findall(r"\| (0\.\d0) \|", readme)]

    assert sum(quoted) == pytest.approx(1.0)
    assert sum(weight for _, weight, _ in SIGNAL_WEIGHTS) == pytest.approx(1.0)


def test_the_thresholds_match(readme: str) -> None:
    assert f"Above {AUTO_MATCH_THRESHOLD:.2f} the pair is matched" in readme
    assert f"{LOW_CONFIDENCE_THRESHOLD:.2f}–{AUTO_MATCH_THRESHOLD:.2f}" in readme


def test_the_documented_filters_are_the_real_ones(readme: str) -> None:
    row = _cell(readme, "`GET /api/meetings`")
    documented = set(re.findall(r"`(\w+)`", row)) - {"GET"}

    assert documented == set(MeetingFilters.model_fields)


# --- the things it points at ---


def test_every_linked_meeting_exists(readme: str, client: TestClient) -> None:
    """The README sends a reviewer to four specific pages. A 404 there is the worst first
    impression available."""
    ids = re.findall(r"localhost:8000/meetings/([\w-]+)", readme)

    assert len(ids) >= 3
    for meeting_id in ids:
        assert client.get(f"/meetings/{meeting_id}").status_code == 200, meeting_id


def test_every_linked_path_is_a_real_route(readme: str, client: TestClient) -> None:
    for path in set(re.findall(r"localhost:8000(/[\w/.-]*)", readme)):
        assert client.get(path).status_code == 200, path


def test_every_cited_record_id_exists_in_the_sources(readme: str) -> None:
    """`CRM-1008`'s corrupt date, `CAL-A16`'s broken email — each is quoted as evidence, so
    each has to be findable in the file it is attributed to."""
    sources = {
        "CRM": json.loads((REPO_ROOT / "data" / "crm_events.json").read_text()),
        "CAL": json.loads((REPO_ROOT / "data" / "calendar_events.json").read_text()),
    }
    known = {
        record.get("crm_id") or record.get("event_id")
        for records in sources.values()
        for record in records
    }
    cited = set(re.findall(r"`(CRM-\d+|CAL-A\d+)`", readme))

    assert len(cited) >= 5
    assert cited <= known, cited - known


def test_the_conflicting_meetings_named_are_the_conflicting_meetings(
    readme: str, client: TestClient
) -> None:
    """The README names all four by id. Naming three, or naming one that has since stopped
    conflicting, is exactly the drift this catches."""
    paragraph = readme.split("The four are ")[1].split("\n\n")[0]
    named = set(re.findall(r"`([\w-]+)`", paragraph))
    actual = {
        meeting["id"]
        for meeting in client.get("/api/meetings", params={"has_conflicts": "true"}).json()
    }

    assert named == actual


def test_the_quoted_conflict_is_still_a_conflict(readme: str, client: TestClient) -> None:
    """"the CRM says NYC Office, the calendar says a Zoom link" — quoted values, from a live
    record, on the page the reader is sent to."""
    meeting = client.get("/api/meetings/crm-1002-cal-a2").json()
    location = meeting["location"]

    assert location["conflict"] is True
    assert "NYC Office - 30th Floor" in readme
    assert location["value"] in readme or "Zoom" in readme


def test_the_relative_links_resolve(readme: str) -> None:
    for target in re.findall(r"\]\(([^)#]+)\)", readme):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        assert (REPO_ROOT / target).exists(), target


def test_the_layout_block_names_real_directories(readme: str) -> None:
    block = next(
        block for block in _fenced_blocks(readme) if "backend/app/" in block
    )
    paths = [line.split()[0] for line in block.splitlines() if line.strip()]

    assert len(paths) > 8
    for path in paths:
        assert (REPO_ROOT / path).exists(), path


def test_the_settings_named_exist(readme: str) -> None:
    from app.config import Settings

    sentence = _cell(readme, "every setting has a working")
    for name in re.findall(r"`([A-Z_]{4,})`", sentence):
        assert name.lower() in Settings.model_fields, name
