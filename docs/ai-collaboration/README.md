# AI Collaboration Log

The problem statement says: *"AI assistance and transparency in its usage is encouraged."* This
folder is my answer to the transparency half. It is not a disclaimer bolted on at the end — it is
the actual working record that preceded the code.

## How AI was used on this project

| Phase | Tool | What it did | What I did |
|---|---|---|---|
| Data profiling | Claude Code | Wrote throwaway scripts to enumerate anomalies and candidate matches across both files | Chose what to look for; verified every claim against the raw JSON |
| Reconciliation design | Claude Code | Drafted the matching strategy and argued alternatives | Set the constraint that matching must be *explainable*, rejected fuzzy-only approaches |
| Architecture | Claude Code | Drafted the module layout and the store's data shapes | Chose the stack; cut a serverless deployment it had drafted, since the single command is what gets graded |
| Implementation | Claude Code | Wrote the ingest/normalize/match/merge modules, API, and UI | Reviewed each module; owned the merge precedence rules |
| Documentation | Claude Code | Drafted README and these documents | Edited for accuracy; wrote the honest time accounting |

## What I did *not* delegate

The reconciliation rules in [02-reconciliation-design.md](02-reconciliation-design.md) are decisions,
not outputs. The statement deliberately withholds guidance on the ambiguous cases, so those cases are
where the real evaluation lives. Every rule there is one I chose and can defend, including the ones
where I chose to *not* resolve something automatically.

## Documents

1. **[01-data-analysis.md](01-data-analysis.md)** — what is actually in the two files, every anomaly
   located by ID, and the expected reconciliation outcome. Written before any application code.
2. **[02-reconciliation-design.md](02-reconciliation-design.md)** — the matching algorithm, the merge
   precedence rules, and the alternatives I rejected with reasons.
3. **[03-architecture.md](03-architecture.md)** — stack, module layout, the in-process data model, and
   how the "single command" requirement is satisfied.

## A note on verification

AI-assisted profiling is fast and confidently wrong at times. Every factual claim in
`01-data-analysis.md` is anchored to a specific record ID so it can be checked against the source
JSON in seconds. Where a conclusion is inference rather than observation (for example, the timezone
reading of `CAL-A4`), it is labelled as inference.

## Time spent

See the "Time Spent" section of the root [README](../../README.md). Tracked honestly, including the
time spent on this documentation.
