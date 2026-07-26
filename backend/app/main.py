"""Event Sync Service — application entrypoint.

Serves the JSON API and, from step 20 on, the server-rendered pages. The reconciliation
pipeline runs during startup, so the service is populated the moment it is reachable.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from app.api.routes import router as api_router
from app.config import get_settings
from app.dependencies import get_repository, sync_now
from app.repository import Repository

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Reconcile before accepting traffic.

    Doc 03 treats this as a design constraint rather than a convenience: `docker compose up`
    has to produce a working service, and a lazily-populated store makes the first request
    pay for the sync while a concurrent second one races it.

    Nothing is caught here on purpose. A misconfigured DATA_DIR should stop the process at
    boot, not leave it serving an empty dataset that looks like a working service with no
    meetings.
    """
    sync_now()
    yield


app = FastAPI(title="Event Sync Service", version="0.1.0", lifespan=lifespan)
app.include_router(api_router)


@app.get("/api/health")
def health(repository: Repository = Depends(get_repository)) -> dict:
    """Liveness, resolved configuration, and whether the service actually has data.

    Reporting `data_dir` makes a misconfigured mount visible in one request — the failure
    this service is most likely to hit in a container. Reporting the meeting count answers
    the question that matters just after it: a process that booted but reconciled nothing is
    not healthy.
    """
    settings = get_settings()
    summary = repository.get_stats()

    return {
        "status": "ok",
        "data_dir": str(settings.data_dir),
        "timezone": settings.timezone,
        "meetings": summary.meetings_out,
        "last_sync": summary.generated_at.isoformat(),
    }
