"""Calendar source adapter — `data/calendar_events.json`, 22 records keyed by `event_id`."""

from pathlib import Path

from app.config import get_settings
from app.ingest import read_json_array

FILENAME = "calendar_events.json"


def load_calendar(data_dir: Path | None = None) -> list[dict]:
    """Return the calendar records verbatim, including the malformed ones."""
    directory = data_dir if data_dir is not None else get_settings().data_dir
    return read_json_array(directory / FILENAME)
