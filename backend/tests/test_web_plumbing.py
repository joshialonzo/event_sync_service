"""Tests for the template plumbing (step 20).

Nothing here asserts what the page *says* — that arrives with the table in step 21. These
check that the wiring works and, more importantly, that adding HTML did not disturb the JSON
API or the store it reads from.
"""

import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app.web import STATIC_DIR, TEMPLATES_DIR, templates


# --- the page renders ---


def test_the_root_page_returns_html(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_the_page_extends_the_base_layout(client: TestClient) -> None:
    body = client.get("/").text

    assert "<!doctype html>" in body.lower()
    assert "Event Sync" in body
    assert "Sync overview" in body, "the nav is present"


def test_the_page_reads_the_same_store_as_the_api(client: TestClient) -> None:
    """The entire argument for server-rendered templates: one code path to the data, so the
    page and the API cannot disagree about a record."""
    body = client.get("/").text
    api_count = len(client.get("/api/meetings").json())

    assert str(api_count) in body
    assert api_count == 24


# --- static files ---


def test_the_stylesheet_is_served(client: TestClient) -> None:
    response = client.get("/static/app.css")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    assert ".badge" in response.text


def test_the_page_links_the_stylesheet_through_the_mount(client: TestClient) -> None:
    """`url_for` rather than a hard-coded path — if the mount moves, one line changes."""
    body = client.get("/").text

    assert "/static/app.css" in body


def test_a_missing_static_file_is_404(client: TestClient) -> None:
    assert client.get("/static/nope.css").status_code == 404


# --- paths ---


def test_templates_resolve_from_the_package_not_the_cwd() -> None:
    """uvicorn runs from backend/, pytest from wherever it was invoked, the container from
    /app. A bare "templates" string would work in exactly one of those."""
    assert TEMPLATES_DIR.is_absolute()
    assert (TEMPLATES_DIR / "base.html").is_file()
    assert (STATIC_DIR / "app.css").is_file()


def test_the_app_starts_from_an_unrelated_working_directory(tmp_path: Path) -> None:
    """The regression this guards is invisible locally and fatal in a container."""
    script = (
        "from fastapi.testclient import TestClient\n"
        "from app.main import app\n"
        "with TestClient(app) as c:\n"
        "    r = c.get('/')\n"
        "    assert r.status_code == 200, r.status_code\n"
        "    assert c.get('/static/app.css').status_code == 200\n"
        "print('ok')\n"
    )
    backend = Path(__file__).resolve().parents[1]

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(backend)},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


# --- safety ---


def test_autoescaping_is_on() -> None:
    """The rendered data includes raw source strings. A location containing a `<` must not
    be able to break the page — or worse."""
    rendered = templates.env.from_string("{{ value }}").render(
        value="<script>alert('x')</script>"
    )

    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered


# --- the API is undisturbed ---


def test_the_json_api_still_works(client: TestClient) -> None:
    assert len(client.get("/api/meetings").json()) == 24
    assert client.get("/api/stats").json()["meetings_out"] == 24


def test_the_openapi_page_still_works(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200


def test_pages_are_absent_from_the_api_schema(client: TestClient) -> None:
    """HTML routes in the OpenAPI spec would clutter the contract a reviewer reads."""
    paths = client.get("/openapi.json").json()["paths"]

    assert "/" not in paths
    assert "/api/meetings" in paths
