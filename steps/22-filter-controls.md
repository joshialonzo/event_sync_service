# Step 22 — Filter controls

**Goal.** A filter form on the list page whose inputs *are* the API's query parameters, so the URL is
the state and the two views can never mean different things by `?origin=crm_only`.

**Files**
- `backend/app/web.py` (edit)
- `backend/app/templates/meetings.html` (edit)
- (tests) `backend/tests/test_web_filters.py`

## A plain GET form, no JavaScript

The form submits with `method="get"` to the same page. That buys, for free, things a fetch-based
filter would have to reimplement:

- **The URL is the state.** `/?origin=crm_only` is bookmarkable, shareable, and survives a reload.
- **Back and forward work**, because each filtered view is a real navigation.
- **No loading state, no error state, no stale data** — the page either rendered or it didn't.

The cost is a round trip per filter change. At 24 rows rendered server-side that is imperceptible,
and doc 03 already accepted "no rich interactivity" as the tradeoff for one process and one language.

## The controls map 1:1 to step 18's parameters

| Control | Parameter |
|---|---|
| Source `<select>` | `origin` |
| Conflicts `<select>` (Any / Only conflicts / No conflicts) | `has_conflicts` |
| From / To `<input type="date">` | `date_from`, `date_to` |
| Owner `<input type="search">` | `owner` |

The route reuses `MeetingFilters` and `apply_filters` — **the same function `/api/meetings` calls**.
Reimplementing the predicates in the template layer is exactly how `?origin=crm_only` would come to
mean two different things.

## Selections must persist in the rendered form

A form that filters but comes back blank is worse than no form: the user cannot see what they asked
for, and the next submission silently drops the previous constraints. So every control re-renders its
current value, which is why the route passes the `MeetingFilters` object back to the template.

`has_conflicts` is the awkward one: it is **tri-state** (unset / true / false) and an HTML checkbox
is binary. A `<select>` with an empty-valued default keeps "any" distinct from "no conflicts" —
without that, "show meetings with no conflicts" is unexpressible.

## Empty-string parameters

A submitted form sends every field, including the ones left blank: `?origin=&owner=&date_from=`.
Those must be treated as absent, not as "match the empty string". FastAPI's `Optional[str]` gives
`""`, not `None`, so the route normalizes blanks before building the filters — otherwise submitting
an untouched form would return zero meetings.

## Manual test

```bash
uvicorn app.main:app --reload --port 8000
```

1. Open <http://localhost:8000/> — 24 rows, form empty.
2. Choose **CRM only** → 3 rows, and the URL shows `?origin=crm_only`.
3. Reload → still 3 rows, still selected in the form.
4. Choose **Only conflicts** → 4 rows, matching `?has_conflicts=true` from the API.
5. Type `sarah` in Owner → 14 rows, including the calendar-only ones.
6. Combine Owner `sarah` + Source **Calendar only** → 3 rows.
7. Set From `2030-01-01` → 0 rows and an empty-state message, not a blank table.
8. Press **Clear** → back to 24 rows and a bare `/`.
9. Press Back → the previous filtered view returns.

## Done when

- [x] Submitting an untouched form returns all 24 rows.
- [x] Each control filters, and the count matches the equivalent API call.
- [x] Selections persist after submission and after a reload.
- [x] `has_conflicts` distinguishes "any" from "no conflicts" (20 rows).
- [x] Filters combine.
- [x] A filter matching nothing shows an empty state, not a bare table.
- [x] Clear returns to an unfiltered `/`.
- [x] The row count text reflects the filtered total, not always 24.

*21 tests; 455 total.*

Exercised against a real server:

```
unfiltered:                        24 rows
?origin=crm_only:                   3
?has_conflicts=true:                4
?has_conflicts=false:              20
?owner=sarah:                      14
?owner=sarah&origin=calendar_only:  3
blank form submit:                 24
```

Selections persist — `<option value="crm_only" selected>` and `<option value="false" selected>` —
and a filtered view reports "3 of 24 meetings match".

### The list page is lenient where the API is strict

The API declares typed query parameters, so `?origin=banana` is a 422 — there a typo is a
programming error worth reporting. The page takes strings and normalizes them, so a hand-edited URL
with a nonsense origin or `date_from=last tuesday` renders all 24 meetings instead of an error page.

That is also what makes the untouched form work at all: a submitted form sends `?origin=&date_from=`
for every field left alone, and a typed parameter would reject the empty string — turning "I pressed
Filter without choosing anything" into a 422.

### One earlier test needed rescoping

Step 21's `test_origin_labels_are_readable` asserted `"crm_only" not in html` page-wide. The form's
`<option value="crm_only">` is a legitimate use of the wire value — it is what goes in the URL — so
the assertion now applies to the table body, which is where it was aimed.