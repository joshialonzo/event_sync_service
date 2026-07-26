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
| Implementation | Claude Code | Wrote the ingest/normalize/match/merge modules, API, and UI, one small step at a time with its own tests | Set the step size and the order; reviewed each module; owned the merge precedence rules |
| Documentation | Claude Code | Drafted README and these documents | Edited for accuracy; wrote the honest time accounting |

## How the implementation phase actually ran

[04-implementation-plan.md](04-implementation-plan.md) breaks the build into 28 steps of one to three
files each. Every step ran the same loop, and the loop is the point:

1. Write [`steps/NN-*.md`](../../steps) first — what is being built, and *why this way* rather than
   the alternative. Writing the argument before the code is what stops the code from becoming the
   argument.
2. Implement it.
3. Write the tests, then run the whole suite.
4. **Mutation-test**: deliberately break the new code and confirm the right test fails. A test that
   passes either way is a comment with a runtime cost.
5. Verify by hand against a running server, and record the observed output in the step file.

Two habits came out of that loop and are worth naming, because both started as mistakes:

**A rule the real dataset cannot exercise is documentation, not behaviour, until a synthetic test
pins it.** Three separate mutations survived a full green suite — a widened internal-participant
check, a silently dropped record, a skipped dedupe pass — because the 42 provided records happen not
to hit those paths. Each was closed with a hand-built record rather than by arguing the rule was
obviously right.

**The docs were wrong about the data twice, and the code was right.** The DST arithmetic in
[01-data-analysis.md](01-data-analysis.md) had `19:00Z` as 14:00 Eastern when it is 15:00, which
turned a genuine one-hour conflict into a rounding story; and the anomaly inventory missed
`CRM-1010`'s empty-string `notes`. Both were found by tests asserting the document's numbers and
failing. The documents were corrected, not the tests.

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
4. **[04-implementation-plan.md](04-implementation-plan.md)** — the ordered build steps, each scoped
   to a few files and paired with the manual check that has to pass before the next one starts.
5. **[`steps/`](../../steps)** — one note per step as it was built: the decision it turned on, the
   manual check, and the observed output. Where a step's plan met the real data and lost, the note
   says so.

## A note on verification

AI-assisted profiling is fast and confidently wrong at times. Every factual claim in
`01-data-analysis.md` is anchored to a specific record ID so it can be checked against the source
JSON in seconds. Where a conclusion is inference rather than observation (for example, the timezone
reading of `CAL-A4`), it is labelled as inference.

The checkable parts of that are now enforced rather than promised. `backend/tests/test_docs.py` reads
these documents and fails if the layout tree names a file that does not exist, if a documented route
is missing from the app, if an undocumented one appears, or if the counts quoted in prose disagree
with what the pipeline produces. Documentation that drifts silently is worse than none, and this is
the cheapest way to make drift loud.

## Time spent

See the "Time Spent" section of the root [README](../../README.md). Tracked honestly, including the
time spent on this documentation.
