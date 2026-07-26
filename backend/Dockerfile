# The build context is backend/, not the repo root: the image needs requirements.txt and
# app/, and nothing else. data/ is mounted at runtime rather than copied, so the same image
# can be pointed at a different dataset without a rebuild.
FROM python:3.12-slim

# Pinned to a minor version rather than :latest — a reviewer running this in six months
# should get the interpreter it was tested against.

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
# Unbuffered because the startup sync's "sync complete - 24 meetings from 42 records" line
# is the first thing anyone looks at in `docker compose up`, and a buffered stdout holds it
# until the process exits.

WORKDIR /app

# Dependencies before source: application code changes on every commit, requirements.txt
# almost never does, so this ordering keeps the pip layer cached across rebuilds.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Where docker-compose.yml mounts the source files. Also the default for a bare
# `docker run -v .../data:/data`, so the image is usable without compose.
ENV DATA_DIR=/data

# Nothing here writes to disk, so the process has no reason to be root.
RUN useradd --create-home --uid 1000 appuser
USER appuser

EXPOSE 8000

# Uses the service's own readiness answer — the endpoint reports the meeting count, so a
# process that started but synced nothing is not reported as healthy by accident.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health').read()"

# 0.0.0.0, not 127.0.0.1: a loopback bind inside the container is unreachable from the host
# no matter what the port mapping says. No --reload — the source is baked in.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
