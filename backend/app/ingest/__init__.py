"""Source adapters.

One module per upstream system. They read and return; they do not parse, coerce, or repair
— normalization (steps 8-9) derives every data-quality flag by *attempting* to parse a raw
value and recording the failure, so a loader that cleaned its input would make those defects
unreportable.
"""

import json
from pathlib import Path


def read_json_array(path: Path) -> list[dict]:
    """Read a JSON file that must contain an array of objects.

    Missing files raise: a misconfigured DATA_DIR should fail loudly rather than yield an
    empty list that silently reconciles to zero meetings.
    """
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)

    if not isinstance(payload, list):
        raise ValueError(f"{path} does not contain a JSON array (got {type(payload).__name__})")

    return payload
