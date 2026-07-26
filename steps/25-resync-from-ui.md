# Step 25 — Re-sync from the UI

**Goal.** The last affordance the overview page promised in step 24: a button that re-runs the
pipeline and lands the reader back on `/stats` with fresh numbers.

**Files**
- `backend/app/web.py` (edit)
- `backend/app/templates/stats.html` (edit)
- (tests) `backend/tests/test_web_resync.py`

`POST /api/sync` has existed since step 19. This step is the human-facing edge of it: a form, a
redirect, and a line of feedback — no new pipeline behaviour.

## POST-redirect-GET, with a 303

The route does not render the overview itself. It runs the sync and answers
`303 See Other` with `Location: /stats?synced=ok`, so the browser issues a fresh `GET`.

Rendering the result directly from the `POST` would leave the browser sitting on a POST response:
refresh re-posts, and the back button offers to re-submit. Neither is dangerous here — the sync is
idempotent — but a page that asks "resend this form?" teaches the reader that pressing it twice might
matter, which is exactly the wrong thing to imply.

**303 rather than 302** because 302's historical behaviour is ambiguous: some clients preserve the
method and re-POST to the redirect target. 303 is defined as "GET the other resource" and is the
status the pattern exists for.

## Why pressing it twice is safe

Not because of a lock. `run_sync` builds a complete `SyncResult` and `replace_all` swaps a single
reference, so:

- two presses produce 24 meetings, not 48 — each run starts from the JSON files, not from the store;
- a reader mid-render sees one run's numbers throughout, never a mixture;
- if a run raises, nothing was replaced yet and **the previous dataset stays published**.

That last property is worth showing rather than only documenting, so a failed re-sync redirects with
`?synced=failed` and the page says the previous data is still being served. A 500 stack trace would
hide the one guarantee the design is proud of.

## The feedback line

The banner is driven by a query parameter, not by session state — the service has no session, and
inventing one to carry a flash message would be a large piece of machinery for a single sentence.
An unrecognised `?synced=` value renders no banner, in the same spirit as step 22's lenient filter
parsing: query strings arrive from bookmarks and hand-editing, and neither should produce an error
page.

The success line quotes the run: `Re-synced — 24 meetings from 42 records.` A bare "Done" is not
checkable, and the whole point of this page is that its numbers can be checked.

The route owns the valid set (`ok` / `failed`); the template branches on `== 'ok'` and then on
plain truthiness. Having the template also name `'failed'` would put the whitelist in two places —
and, as the mutation run below showed, would make the route's copy untestable.

## Manual test

```bash
uvicorn app.main:app --reload --port 8000
```

Open <http://localhost:8000/stats>:

- Press **Re-sync now**. The URL becomes `/stats?synced=ok`, the banner names the counts, and the
  "Last run" timestamp advances.
- Every tile reads the same as before — 42 in, 24 out, 17 matched. Press it five more times and the
  numbers still do not move.
- Press the back button: the previous page renders with no re-submit prompt.
- Reload `/stats?synced=ok`: no second sync runs (the timestamp holds).
- `curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' -X POST localhost:8000/sync` → `303
  http://localhost:8000/stats?synced=ok`.

## Done when

- [x] The overview page carries a form posting to `/sync`.
- [x] `POST /sync` answers 303 with `Location: /stats?synced=ok`.
- [x] The redirect target renders a banner naming the meeting and record counts.
- [x] Re-syncing leaves the meeting ids, the counts and the conflicts identical.
- [x] The "Last run" timestamp advances on each press.
- [x] A plain `GET /stats` renders no banner; an unknown `?synced=` value renders none either.
- [x] A failing sync redirects with `?synced=failed`, says the previous data is still served, and
      leaves the published dataset intact.
- [x] `GET /sync` is a 405 — the route is a form target, not a link.

*17 tests; 515 total.*

Rendered after a press:

```
Re-synced — 24 meetings from 42 records.

Sync overview
Last run 2026-07-26 02:31 EDT from …/data
```

### The idempotence test is the one that matters

`test_a_second_sync_leaves_the_dataset_identical` snapshots the meeting ids, the tile counts and the
conflict rows, presses the button, and asserts all three are unchanged. Making `replace_all` append
to `by_date` instead of rebinding leaves every other test on this page green — the button still
redirects, the banner still renders — and fails this one, along with
`test_the_rendered_page_is_unchanged_apart_from_the_run_time`.

### A mutation that survived, and the duplication it exposed

Widening the route to `"synced": synced` — passing whatever arrived in the query string straight to
the template — killed no test. It was harmless only because the template happened to compare against
both literals itself, so the same whitelist existed twice and neither copy was load-bearing on its
own. Collapsing the template's second branch to `{% elif synced %}` makes the route's guard the only
one, and `test_an_unknown_synced_value_shows_no_banner` now fails when it is removed.
