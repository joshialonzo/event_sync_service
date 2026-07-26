# Step 28 — Project README

**Goal.** The brief says *"the quality of your README matters as much as the quality of your code"*
and asks it to cover how to run it, the approach, the key decisions, and honest time spent. This is
that file, plus the tests that stop it from quietly becoming false.

**Files**
- `README.md`
- `steps/README.md` (edit — the index was still showing step 03 as "next")
- (tests) `backend/tests/test_readme.py`

## What it has to do

A reviewer opens the README before they can tell whether anything in it is true. So it leads with the
result — **42 records in → 24 meetings out** — then the single command, then a five-minute tour of
four specific URLs chosen because each one shows something different:

| URL | What it proves |
|---|---|
| `/stats` | Every count, linked to the records behind it |
| `/meetings/crm-1002-cal-a2` | A conflict shown from both sides, with the match evidence |
| `/meetings/crm-1005-cal-a5-cal-a6` | The collapsed duplicate, keeping both raw records |
| `/meetings/crm-1010` | A CRM record that was never booked — present, not dropped |

Then the pipeline, the decisions, and the numbers. The decisions section leads with **conflict
classification** because it is the one place where the service does something a reviewer might
disagree with: 15 fields differ between the sources and only 4 are badged.

## Time spent

Derived from the commit history rather than remembered: roughly 14 hours of elapsed time across three
days. Broken out by phase, with the honest observation that the largest cost was not the matching
algorithm — it was deciding what *should* happen to the ambiguous records, and that work lives in doc
02 rather than in the code.

## Testing prose, again

Step 27 did this for the architecture document; the README is the higher-value target, because it is
the file most likely to be read and least likely to be re-checked. `test_readme.py` compares it
against the running service:

- Headline counts, source split, conflict-kind counts, and every row of the nine-code data-quality
  table come from `/api/stats`, not from the test.
- Signal weights and both thresholds are read out of `SIGNAL_WEIGHTS`, `AUTO_MATCH_THRESHOLD` and
  `LOW_CONFIDENCE_THRESHOLD`. The weights table is what a reviewer will argue with, so it is the part
  most worth keeping true — and the quoted weights are checked to sum to 1.0.
- The documented filters are compared to `MeetingFilters`' fields.
- Every `localhost:8000` URL in the file is fetched and must return 200 — a dead link in the
  five-minute tour is the worst first impression available.
- Every cited record ID (`CRM-1008`, `CAL-A16`, …) is looked up in the source file it is attributed
  to; every flag code must exist in `FlagCode`; the four conflicting meetings named must be exactly
  the four the API reports.
- The quoted test count is checked against a real `--collect-only` run.
- The four things the brief asks for are checked as sections.

## Manual test

Follow the README top to bottom in a fresh clone, copy-pasting only what it says. Both `curl`
examples were run against a live server:

```
$ curl -s localhost:8000/api/stats | jq -c '{records_in, meetings_out, matched_pairs}'
{"records_in":42,"meetings_out":24,"matched_pairs":17}

$ curl -s 'localhost:8000/api/meetings?has_conflicts=true' | jq -c 'map(.id)'
["crm-1002-cal-a2","crm-1004-cal-a4","crm-1009-cal-a10","crm-1016-cal-a17"]
```

## Done when

- [x] The brief's four requirements each have a section: how to run it, the approach, key decisions,
      time spent.
- [x] The single command is `docker compose up --build`, and it is the one in the repository.
- [x] Both run paths — Docker and venv — are copy-pasteable.
- [x] Every number in the file matches `/api/stats`.
- [x] Every URL, record ID, flag code and link resolves.
- [x] The commands quoted produce the output quoted.
- [x] `steps/README.md` reflects 28 completed steps rather than "step 03 is next".

*25 tests; 582 total.*

### Two things the tests changed about the README

**The layout block was nested, and nesting hides a lie.** It listed `ingest/` and `reconcile/`
indented under `backend/app/`, which reads fine and checks badly — a bare `ingest/` does not name a
path. Rewritten with full paths, so every line in the block is something a test can look for on disk.
The clarity was a side effect of making it checkable.

**The test count was three commits stale before it was ever committed.** The README said 557; adding
`test_docs.py` and `test_readme.py` made it 582. Numbers about the repository, written into the
repository, are stale the moment anything moves — which is the entire argument for
`test_the_test_count_is_current` rather than a promise to remember.

### The number this step cannot verify

The time accounting is derived from commit timestamps, which measure elapsed time and not attention.
It is the one claim in the README that no test can check, and the brief specifically asks for honesty
about it — so it is flagged here rather than presented as measured.
