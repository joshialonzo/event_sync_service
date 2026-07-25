"""Event Sync Service — application entrypoint.

Serves the JSON API and, from step 19 on, the server-rendered pages. The reconciliation
pipeline is wired in at step 15; until then this module only proves the process starts.
"""

from fastapi import FastAPI

from app.config import get_settings

app = FastAPI(title="Event Sync Service", version="0.1.0")


@app.get("/api/health")
def health() -> dict[str, str]:
    """Liveness, plus the resolved configuration.

    Reporting `data_dir` here makes a misconfigured mount visible in one request, which is
    the failure this service is most likely to hit once it runs in a container. It says
    nothing about whether a sync has run — that lands in step 15.
    """
    settings = get_settings()
    return {
        "status": "ok",
        "data_dir": str(settings.data_dir),
        "timezone": settings.timezone,
    }
