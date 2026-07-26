# Event Sync Service

Reconciles meeting records from two upstream systems (a CRM and a calendar) that share no common identifier, and serves a unified view that shows where each field came from and where the sources disagree.

See [problem_statement.md](problem_statement.md) for the assessment brief.

> **Status: planning complete, implementation pending.** The setup guide, approach summary, key
> decisions, and time accounting land here once the service is built.

## Planning documents

The problem statement encourages AI assistance and transparency in its usage. The planning work is
recorded in [docs/ai-collaboration/](docs/ai-collaboration/):

- **[01 — Data Analysis](docs/ai-collaboration/01-data-analysis.md)** — every anomaly in the two
  source files, located by record ID, and the hand-derived 24-meeting expected outcome that serves as
  the correctness fixture.
- **[02 — Reconciliation Design](docs/ai-collaboration/02-reconciliation-design.md)** — the matching
  algorithm, merge precedence, and conflict handling, with rejected alternatives.
- **[03 — Architecture](docs/ai-collaboration/03-architecture.md)** — a single FastAPI service under
  Docker Compose serving both the JSON API and server-rendered pages, with an in-process store
  rebuilt from the source files on each sync.
- **[04 — Implementation Plan](docs/ai-collaboration/04-implementation-plan.md)** — the ordered build
  steps, each with the manual check that has to pass before the next one starts.
- **[AI collaboration log](docs/ai-collaboration/README.md)** — how AI was used at each phase, and
  what was not delegated.

## Planned quick start

```bash
docker-compose up -d
```

Everything on `http://localhost:8000` — the meeting list at `/`, the JSON API under `/api`, and the
OpenAPI page at `/docs`.
