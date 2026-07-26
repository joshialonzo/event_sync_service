"""Tests that the documents describe the repository that exists (step 27).

Doc 03 was written before the code, which is the right order — but it means every claim in it
is a prediction until something checks. These tests read the documents and fail when the
layout tree names a file that is not there, when a documented route is missing, when an
undocumented route appears, or when a number quoted in prose disagrees with the pipeline.

Documentation that drifts silently is worse than none. This is the cheapest way to make the
drift loud, and it is why the numbers below are read out of the markdown rather than restated
here — a test that hard-codes 24 proves the test agrees with itself.
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.repository import Repository

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS = REPO_ROOT / "docs" / "ai-collaboration"
ARCHITECTURE = DOCS / "03-architecture.md"


@pytest.fixture(scope="module")
def architecture() -> str:
    return ARCHITECTURE.read_text()


def _code_block(text: str, contains: str) -> str:
    blocks = re.findall(r"```[a-z]*\n(.*?)```", text, re.DOTALL)
    return next(block for block in blocks if contains in block)


def _documented_routes(text: str) -> set[tuple[str, str]]:
    """Every `METHOD /path` in a table cell of doc 03's API sections."""
    return {
        (method, path)
        for method, path in re.findall(r"`(GET|POST) (/[^`]*)`", text)
        if not path.startswith("/static")  # a mount, not a route; checked separately
    }


# --- the layout tree ---


def test_every_path_in_the_layout_tree_exists(architecture: str) -> None:
    """The tree is a map. A map naming `reconcile/normalize.py` when the file is
    `normalize_crm.py` sends a reviewer looking for something that was never there."""
    entries = _tree_paths(_code_block(architecture, "event_sync_service/"))

    assert len(entries) > 20, "the tree parser stopped matching — it would pass vacuously"
    for entry in entries:
        assert (REPO_ROOT / entry).exists(), entry


def test_the_tree_lists_every_module_in_reconcile(architecture: str) -> None:
    """The one directory the reader is being sent to inspect. A file missing from the tree is
    a decision that was made and never explained."""
    tree = _code_block(architecture, "event_sync_service/")
    on_disk = {
        path.name
        for path in (REPO_ROOT / "backend" / "app" / "reconcile").glob("*.py")
        if path.name != "__init__.py"
    }

    for name in on_disk:
        assert name in tree, name


def test_the_tree_lists_every_template(architecture: str) -> None:
    tree = _code_block(architecture, "event_sync_service/")
    stems = {
        path.stem for path in (REPO_ROOT / "backend" / "app" / "templates").glob("*.html")
    }

    for stem in stems:
        assert stem in tree, stem


def test_the_file_count_claim_matches_reconcile(architecture: str) -> None:
    """Doc 03 says "Why `reconcile/` is seven files". Written as a word, so it goes stale
    quietly the moment a module is added."""
    words = {
        "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    }
    (claimed,) = re.findall(r"Why `reconcile/` is (\w+) files", architecture)
    actual = len(
        [
            path
            for path in (REPO_ROOT / "backend" / "app" / "reconcile").glob("*.py")
            if path.name != "__init__.py"
        ]
    )

    assert words[claimed] == actual


# --- the routes ---


def _app_routes() -> set[tuple[str, str]]:
    routes = set()
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        for method in methods & {"GET", "POST"}:
            routes.add((method, route.path))
    return routes


def test_every_documented_route_exists(architecture: str) -> None:
    """A documented endpoint that 404s is the single most expensive kind of doc error: the
    reader assumes their setup is broken, not the document."""
    actual = {(method, _template(path)) for method, path in _app_routes()}

    for method, path in _documented_routes(architecture):
        assert (method, _template(path)) in actual, f"{method} {path}"


def test_every_route_is_documented(architecture: str) -> None:
    """The other direction, which is the one that rots: routes get added and the table does
    not. FastAPI's own /openapi.json, /redoc and the oauth redirect are not this service's
    surface."""
    generated = {"/openapi.json", "/redoc", "/docs/oauth2-redirect"}
    documented = {_template(path) for _, path in _documented_routes(architecture)}

    for method, path in _app_routes():
        if path in generated:
            continue
        assert _template(path) in documented, f"{method} {path}"


def test_the_static_mount_is_documented(architecture: str) -> None:
    assert "/static/app.css" in architecture
    assert (REPO_ROOT / "backend" / "app" / "static" / "app.css").is_file()


def _template(path: str) -> str:
    """`/meetings/{meeting_id}` and the doc's `/meetings/{id}` are the same route."""
    return re.sub(r"\{[^}]+\}", "{id}", path)


# --- the numbers ---


def test_the_headline_numbers_match_the_pipeline(
    architecture: str, client: TestClient
) -> None:
    """"42 records in, 24 meetings out, 17 matched, 4 conflicts" is quoted as the five-second
    verification. It is the sentence most likely to be believed and least likely to be
    re-checked."""
    stats = client.get("/api/stats").json()
    sentence = next(
        line for line in architecture.splitlines() if "records in," in line
    )
    quoted = [int(number) for number in re.findall(r"\d+", sentence)]

    assert quoted == [
        stats["records_in"],
        stats["meetings_out"],
        stats["matched_pairs"],
        stats["conflicts_by_kind"]["contradiction"],
    ]


def test_the_record_count_in_prose_matches_the_sources(
    architecture: str, client: TestClient
) -> None:
    """Doc 03 argues against a database on the grounds that the input is 42 records."""
    records_in = client.get("/api/stats").json()["records_in"]

    assert f"input is {records_in} records in two static JSON files" in architecture


def test_the_meeting_count_in_the_ui_section_matches(
    architecture: str, client: TestClient
) -> None:
    """"display a 24-row list" is the premise of the whole no-frontend argument."""
    meetings = len(client.get("/api/meetings").json())

    assert f"display a {meetings}-row list" in architecture


def test_the_flag_link_cap_matches_the_page(architecture: str) -> None:
    from app.web import FLAG_LINK_LIMIT

    assert "capped at six links" in architecture.lower()
    assert FLAG_LINK_LIMIT == 6


# --- the interfaces ---


def test_the_documented_repository_methods_are_the_protocol(architecture: str) -> None:
    """Doc 03 names the seam it claims makes the store swappable. If the protocol grows a
    method the document does not list, the seam described is not the seam that exists."""
    (listing,) = re.findall(r"`Repository` protocol \(([^)]+)\)", architecture)
    documented = set(re.findall(r"`(\w+)`", listing))
    actual = {name for name in vars(Repository) if not name.startswith("_")}

    assert documented == actual


def test_the_settings_named_in_the_tree_exist(architecture: str) -> None:
    from app.config import Settings

    (line,) = [
        line for line in architecture.splitlines() if "config.py" in line and "Settings" in line
    ]
    for name in re.findall(r"\b([A-Z_]{4,})\b", line):
        assert name.lower() in Settings.model_fields, name


# --- the links ---


@pytest.mark.parametrize("document", sorted(DOCS.glob("*.md")), ids=lambda p: p.name)
def test_relative_links_resolve(document: Path) -> None:
    """Every cross-reference between these documents, and out to steps/ and the README."""
    for target in re.findall(r"\]\(([^)#]+)\)", document.read_text()):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        assert (document.parent / target).exists(), f"{document.name} → {target}"


def test_the_single_command_is_the_one_that_works(architecture: str) -> None:
    """The command in the document has to be the command in the repository."""
    command = _code_block(architecture, "docker compose").strip()

    assert command == "docker compose up --build"
    assert (REPO_ROOT / "docker-compose.yml").is_file()


def _tree_paths(tree: str) -> list[str]:
    """Repo-relative paths from the layout tree, rebuilt from its indentation.

    Depth is four characters per level, whichever glyphs fill them, so a nested entry is
    only meaningful once joined to its ancestors — `routes.py` says nothing on its own.
    """
    paths: list[str] = []
    stack: list[str] = []

    for line in tree.splitlines():
        match = re.match(r"^([\s│├└─]*)(\S.*)$", line)
        if match is None:
            continue

        prefix, rest = match.groups()
        name = rest.split("#")[0].strip().rstrip("/")
        if not name or name == "event_sync_service":
            continue

        depth = len(prefix) // 4
        stack = stack[: depth - 1]
        stack.append(name)
        paths.append("/".join(stack))

    return paths
