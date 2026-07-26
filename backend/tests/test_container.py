"""Tests for the container definition (step 26).

These do not build an image — that is the manual check, and a suite that shells out to a
daemon is a suite that fails on machines where the daemon is off. What they do pin is the
handful of things that are wrong in a way `docker compose up` reports as "started": a
loopback bind, a mount that does not line up with `DATA_DIR`, a host venv copied into the
image. Every one of those produces a running container and a broken service.

The cwd tests are the other half: the container starts the process from `/app` with the data
somewhere else entirely, so anything resolved relative to the working directory works in
development and fails only in the image.
"""

import os
from pathlib import Path

import pytest
import yaml

from app.config import Settings, get_settings
from app.jobs.sync import run_sync
from app.web import STATIC_DIR, TEMPLATES_DIR

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"
DOCKERFILE = BACKEND / "Dockerfile"
DOCKERIGNORE = BACKEND / ".dockerignore"
COMPOSE = REPO_ROOT / "docker-compose.yml"


@pytest.fixture(scope="module")
def dockerfile() -> str:
    """The instructions only.

    Comments are stripped because this file argues for its own choices in prose — a naive
    `"--reload" not in text` would trip over the comment explaining why --reload is absent,
    and pass or fail on the wording rather than on the build.
    """
    lines = DOCKERFILE.read_text().splitlines()
    return "\n".join(line for line in lines if line.strip() and not line.startswith("#"))


@pytest.fixture(scope="module")
def cmd(dockerfile: str) -> str:
    (line,) = [line for line in dockerfile.splitlines() if line.startswith("CMD ")]
    return line


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


@pytest.fixture(scope="module")
def service(compose: dict) -> dict:
    (only,) = compose["services"].values()
    return only


# --- the files exist where the build expects them ---


def test_the_three_files_exist() -> None:
    for path in (DOCKERFILE, DOCKERIGNORE, COMPOSE):
        assert path.is_file(), path


def test_the_dockerignore_sits_in_the_build_context(compose: dict, service: dict) -> None:
    """Docker reads .dockerignore from the context root. At the repo root — the obvious
    place to put it — it is silently ignored and the venv gets copied anyway."""
    context = REPO_ROOT / str(service["build"]).lstrip("./")

    assert context.resolve() == BACKEND
    assert (context / ".dockerignore").is_file()


def test_everything_the_dockerfile_copies_exists_in_the_context(dockerfile: str) -> None:
    """A COPY path is relative to the context, not to the Dockerfile's directory. They
    coincide here; this test is what notices if the context ever moves to the repo root."""
    sources = [
        line.split()[1] for line in dockerfile.splitlines() if line.startswith("COPY ")
    ]

    assert sources  # a Dockerfile that copies nothing would pass the loop vacuously
    for source in sources:
        assert (BACKEND / source).exists(), source


# --- the image ---


def test_the_base_image_is_pinned(dockerfile: str) -> None:
    """`:latest` means a reviewer running this in six months gets a different interpreter
    than the one the suite passed against."""
    assert "FROM python:3.12-slim" in dockerfile
    assert ":latest" not in dockerfile


def test_dependencies_are_installed_before_the_source_is_copied(dockerfile: str) -> None:
    """Application code changes every commit; requirements.txt almost never does. Copying
    app/ first would throw away the pip layer on every rebuild."""
    requirements = dockerfile.index("COPY requirements.txt")
    install = dockerfile.index("pip install")
    source = dockerfile.index("COPY app")

    assert requirements < install < source


def test_dependencies_come_from_the_pinned_file(dockerfile: str) -> None:
    """The image must not resolve versions of its own; requirements.txt is the record of
    what the suite ran against."""
    assert "-r requirements.txt" in dockerfile
    assert "--no-cache-dir" in dockerfile


def test_the_server_binds_all_interfaces(cmd: str) -> None:
    """The failure this exists for: a container bound to 127.0.0.1 starts cleanly, logs the
    sync, reports healthy in `docker ps` — and refuses every connection from the host,
    whatever the port mapping says."""
    assert '"--host", "0.0.0.0"' in cmd
    assert "127.0.0.1" not in cmd
    assert "localhost" not in cmd


def test_the_image_does_not_run_with_reload(dockerfile: str) -> None:
    """--reload watches a source tree that is baked in and cannot change, at the cost of a
    file-watching subprocess."""
    assert "--reload" not in dockerfile


def test_the_container_does_not_run_as_root(dockerfile: str) -> None:
    assert "USER appuser" in dockerfile
    assert dockerfile.index("RUN useradd") < dockerfile.index("USER appuser")


def test_the_healthcheck_uses_the_services_own_answer(dockerfile: str) -> None:
    """`/api/health` reports the meeting count, so a process that came up but synced nothing
    is not reported healthy by accident."""
    assert "HEALTHCHECK" in dockerfile
    assert "/api/health" in dockerfile


def test_the_image_defaults_data_dir_to_the_mount_point(dockerfile: str) -> None:
    """So `docker run -v $PWD/data:/data -p 8000:8000 <image>` works without compose."""
    assert "ENV DATA_DIR=/data" in dockerfile


# --- compose ---


def test_there_is_exactly_one_service(compose: dict) -> None:
    """Doc 03's choice of server-rendered templates, cashed in: no UI build, no second
    container, no CORS."""
    assert len(compose["services"]) == 1


def test_no_obsolete_version_key(compose: dict) -> None:
    """Compose v2 ignores it and warns on every invocation."""
    assert "version" not in compose


def test_port_8000_is_published(service: dict) -> None:
    assert "8000:8000" in [str(mapping) for mapping in service["ports"]]


def test_the_data_directory_is_mounted_read_only(service: dict) -> None:
    """Doc 02 says the pipeline never writes to its inputs. `:ro` is the kernel enforcing
    it rather than the code promising it."""
    (mount,) = service["volumes"]
    source, target, options = mount.split(":")

    assert (REPO_ROOT / source.lstrip("./")).resolve() == REPO_ROOT / "data"
    assert target == "/data"
    assert options == "ro"


def test_data_dir_points_at_the_mount_target(service: dict) -> None:
    """The mismatch that produces a container which starts, syncs zero records, and serves
    an empty list with no error anywhere."""
    (mount,) = service["volumes"]
    target = mount.split(":")[1]

    assert service["environment"]["DATA_DIR"] == target


def test_the_mounted_directory_holds_the_source_files() -> None:
    for name in ("crm_events.json", "calendar_events.json"):
        assert (REPO_ROOT / "data" / name).is_file(), name


# --- the assumptions the image makes about the process ---


def test_the_dockerignore_excludes_the_host_venv() -> None:
    """A macOS-linked venv copied into a Linux image is both the largest thing in the
    context and, on the import path, actively harmful."""
    ignored = [
        line.strip()
        for line in DOCKERIGNORE.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]

    # Patterns only. The comment above them names `venv/` while explaining why it is there,
    # so splitting the raw text would pass on the prose after the pattern was deleted.
    assert "venv/" in ignored
    assert "__pycache__/" in ignored
    assert "tests/" in ignored


def test_data_dir_is_read_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_settings: None
) -> None:
    """The whole mount arrangement rests on this: the container sets DATA_DIR and expects
    the app to follow it."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    assert get_settings().data_dir == tmp_path.resolve()


def test_the_pipeline_runs_with_a_foreign_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_settings: None
) -> None:
    """The container starts uvicorn from /app with the data at /data. Anything resolved
    against the cwd works in development and produces an empty service in the image."""
    monkeypatch.setenv("DATA_DIR", str(REPO_ROOT / "data"))
    monkeypatch.chdir(tmp_path)

    result = run_sync()

    assert result.summary.records_in == 42
    assert result.summary.meetings_out == 24


def test_the_templates_and_static_files_resolve_from_anywhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Package-relative, not cwd-relative — `Jinja2Templates(directory="templates")` would
    only ever have worked when uvicorn was launched from backend/."""
    monkeypatch.chdir(tmp_path)

    assert (TEMPLATES_DIR / "base.html").is_file()
    assert (STATIC_DIR / "app.css").is_file()


def test_a_relative_data_dir_is_resolved_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_settings: None
) -> None:
    """If DATA_DIR arrives relative, it must be pinned at startup rather than re-resolved
    per call against whatever cwd the process happens to have."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "mounted").mkdir()

    settings = Settings(data_dir=Path("mounted"))

    assert settings.data_dir.is_absolute()
    assert settings.data_dir == (tmp_path / "mounted").resolve()


def test_the_app_does_not_depend_on_a_dot_env_file() -> None:
    """`.env` is in .dockerignore, so the image is configured entirely by environment
    variables — the defaults have to stand on their own."""
    assert not os.path.exists(BACKEND / ".env")
    assert Settings().timezone == "America/New_York"
