"""Application settings.

Everything environment-dependent lives here, so no other module hard-codes a path or a
timezone. `data_dir` is a setting rather than a constant because step 25 mounts the source
files at a different path inside the container.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> backend/app -> backend -> repo root
_REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_dir: Path = _REPO_ROOT / "data"
    """Directory holding crm_events.json and calendar_events.json. Env: DATA_DIR."""

    timezone: str = "America/New_York"
    """The single timezone every timestamp is coerced to (doc 02, Decision 1)."""

    @field_validator("data_dir")
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        """A relative DATA_DIR would resolve against the process's cwd, which differs
        between `uvicorn` from backend/, pytest, and the container. Pin it once."""
        return value.expanduser().resolve()


@lru_cache
def get_settings() -> Settings:
    """Cached so the environment is read once per process."""
    return Settings()
