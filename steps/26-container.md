# Step 26 — Container and single command

**Goal.** `docker compose up --build` on a clean clone, and nothing else. No Python install, no venv,
no environment variables to discover.

**Files**
- `backend/Dockerfile`
- `backend/.dockerignore`
- `docker-compose.yml`
- (tests) `backend/tests/test_container.py`

## One service

Doc 03 chose server-rendered templates over a separate frontend, and this is where that decision is
cashed in: one build, one image, one port. No UI toolchain, no second container, no CORS
configuration, nothing to start in a particular order.

## The context is `backend/`, not the repo root

The image needs `requirements.txt` and `app/`. Building from the repo root would put `data/`,
`docs/`, `steps/` and the host venv in the context, and every one of them would bust the cache.

This is also why **`.dockerignore` lives in `backend/`**: Docker reads it from the context root, so
the obvious place — the repo root — is the one place it would be silently ignored.

`venv/` is the entry that matters. It is by far the largest thing in the directory, and it holds
macOS-linked binaries that are worse than useless inside a Linux image.

## `data/` is mounted, not copied

```yaml
volumes:
  - ./data:/data:ro
environment:
  DATA_DIR: /data
```

Read-only because doc 02 says the pipeline never writes to its inputs; `:ro` turns that from a claim
in a document into something the kernel enforces. `touch /data/x` inside the running container
answers `Read-only file system`.

Mounted rather than baked in because a corrected source file plus a press of the step 25 Re-sync
button is then enough — no rebuild. `DATA_DIR` was made a setting back in step 2 for exactly this
moment; the image defaults it to `/data` as well, so a plain
`docker run -v "$PWD/data:/data:ro" -p 8000:8000 <image>` works without compose.

## Two failures that look like success

**Binding loopback.** `--host 127.0.0.1` inside a container starts cleanly, logs the sync, and shows
as healthy in `docker ps` — while refusing every connection from the host, whatever the port mapping
says. The `CMD` binds `0.0.0.0`, and a test reads the `CMD` line specifically.

**A `DATA_DIR` that does not match the mount.** The pipeline finds nothing, publishes a result with
zero meetings, and serves an empty list with no error anywhere. A test asserts the environment
variable equals the mount target, parsed out of the compose file rather than restated.

## Layer order

`requirements.txt` is copied and installed before `app/` is copied. Application code changes on every
commit and the dependency list almost never does; the other order throws away the pip layer each
time. Verified: touching `app/main.py` and rebuilding reports `CACHED` for both dependency layers.

## Non-root

Nothing in the service writes to disk, so there is no reason for the process to be root. `useradd`,
then `USER appuser` — `id` in the running container reports `uid=1000(appuser)`.

## Healthcheck

`/api/health` rather than a TCP probe, because that endpoint reports the meeting count: a process
that came up but synced nothing should not be reported healthy. Done with `urllib` from the
interpreter that is already installed rather than adding `curl` to the image.

## What the tests do and do not do

They do not build an image. A suite that shells out to a daemon fails on any machine where the
daemon is off, and the build is the manual check. What they pin is the set of mistakes that produce
a *running* container and a broken service — the loopback bind, the mismatched mount, the copied
venv, the unpinned base image — plus the cwd assumptions the container makes: it starts uvicorn from
`/app` with the data at `/data`, so anything resolved relative to the working directory works in
development and only fails in the image.

`test_the_pipeline_runs_with_a_foreign_working_directory` chdirs into a tmpdir and still gets
42 records in and 24 meetings out. `test_the_templates_and_static_files_resolve_from_anywhere` is
what step 20's package-relative `TEMPLATES_DIR` was for.

The Dockerfile fixture strips comments before matching. The file argues for its own choices in prose,
so a naive `"--reload" not in text` trips over the comment explaining why `--reload` is absent — and
would then pass or fail on the wording rather than on the build.

## Manual test

From a clean clone, with only Docker installed:

```bash
docker compose up --build
```

- <http://localhost:8000/> — the meeting list.
- <http://localhost:8000/docs> — OpenAPI.
- <http://localhost:8000/api/health> — `"data_dir": "/data"`, `"meetings": 24`.

## Done when

- [x] `docker compose up --build` is the only command needed.
- [x] The startup log reads `sync complete - 24 meetings from 42 records (17 matched, 4 conflicts)`.
- [x] `/api/health` reports `/data` and 24 meetings; `/api/stats` reports 42 in, 24 out, 17 matched.
- [x] `/`, `/docs`, `/static/app.css` and a detail page all return 200 through the published port.
- [x] The step 25 button still answers `303 → /stats?synced=ok` from inside the container.
- [x] `docker ps` reports `(healthy)`.
- [x] The process runs as `uid=1000(appuser)`.
- [x] `/data` is read-only inside the container.
- [x] `/app` contains `app` and `requirements.txt` — no venv, no tests.
- [x] Rebuilding after a source edit reuses the cached dependency layers.

*23 tests; 538 total. Image 269 MB.*

Observed:

```
$ docker compose up -d --build
 Container event_sync_service-event-sync-1  Started

$ docker logs event_sync_service-event-sync-1
INFO  event-sync: sync complete - 24 meetings from 42 records (17 matched, 4 conflicts)
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)

$ curl -s <published>/api/health
{"status":"ok","data_dir":"/data","timezone":"America/New_York","meetings":24,
 "last_sync":"2026-07-26T10:15:32.487858-04:00"}

$ docker ps
event_sync_service-event-sync-1  Up (healthy)  0.0.0.0:8000->8000/tcp

$ docker exec … id
uid=1000(appuser) gid=1000(appuser)

$ docker exec … touch /data/x
touch: cannot touch '/data/x': Read-only file system
```

### A mutation that survived, and why

Deleting `venv/` from `.dockerignore` killed nothing. The test read the raw file and split it on
whitespace — and the comment above the patterns names `venv/` while explaining why it is there, so
the prose kept the assertion true after the pattern was gone. It now parses comments out first, the
same way the Dockerfile fixture does. Two files in this step argue for themselves in comments, and
both times that prose was able to stand in for the thing it describes.

Everything else was caught by exactly one test each: a loopback bind, `python:latest`, the layer
order reversed, `USER` removed, the healthcheck downgraded to `/`, a `DATA_DIR` that misses the
mount, a writable mount, the context moved to the repo root, and a second service.

### The verification nearly reported the wrong process

The first `curl localhost:8000/api/health` against the running container answered
`"data_dir": "/Users/josue-alonzo-chavarria/…/data"` — a host path, from inside a container that has
no such directory. A development `uvicorn` was still holding `127.0.0.1:8000`, and `localhost`
resolved to it before the published port. The container was fine; the check was reading the wrong
server.

Worth recording because it is exactly the shape of mistake this step is about: two processes, one
port, and an answer that looks plausible. The tell was the path — `data_dir` in the health response
is what distinguished them, which is the reason step 2 put it there.
