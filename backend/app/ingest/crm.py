"""CRM source adapter — `data/crm_events.json`, 20 records keyed by `crm_id`."""

from pathlib import Path

from app.config import get_settings
from app.ingest import read_json_array

FILENAME = "crm_events.json"


def load_crm(data_dir: Path | None = None) -> list[dict]:
    """Return the CRM records verbatim.

    Separate from `load_calendar` rather than one `load_all` because the two become
    genuinely different adapters in steps 8-9, and because a production version would
    fetch them from two different APIs.
    """
    directory = data_dir if data_dir is not None else get_settings().data_dir
    return read_json_array(directory / FILENAME)
