"""The process-wide store and the dependency routes use to reach it.

A module-level repository rather than `app.state`: the templates in steps 20-25 render from
the same store, and reaching `request.app.state` from a template context is more indirection
than an attribute. The `Repository` protocol is still the seam — `get_repository()` is the
only way anything obtains the store, so a test can override it without touching this module.
"""

import logging

from app.jobs.sync import run_sync
from app.models.unified import SyncRunSummary
from app.repository import Repository
from app.repository.memory import InMemoryRepository

logger = logging.getLogger("event-sync")

_repository = InMemoryRepository()


def get_repository() -> Repository:
    """FastAPI dependency. Returns the one store for this process."""
    return _repository


def sync_now() -> SyncRunSummary:
    """Run the pipeline and publish the result atomically.

    Idempotent: `run_sync` builds a complete new result and `replace_all` swaps a single
    reference, so calling this twice leaves 24 meetings rather than 48. That is what makes
    the re-sync button in step 25 safe to press repeatedly.
    """
    result = run_sync()
    _repository.replace_all(result)

    summary = result.summary
    logger.info(
        "sync complete - %d meetings from %d records (%d matched, %d conflicts)",
        summary.meetings_out,
        summary.records_in,
        summary.matched_pairs,
        sum(summary.conflicts_by_field.values()),
    )
    return summary
