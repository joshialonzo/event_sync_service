"""Event Sync Service — application entrypoint.

Serves the JSON API and, from step 19 on, the server-rendered pages. The reconciliation
pipeline is wired in at step 15; until then this module only proves the process starts.
"""

from fastapi import FastAPI

app = FastAPI(title="Event Sync Service", version="0.1.0")


@app.get("/api/health")
def health() -> dict[str, str]:
    """Liveness. Says nothing about whether a sync has run — that lands in step 15."""
    return {"status": "ok"}
