# 01 — Data Analysis

*Written before any application code. Every claim references a record ID and is verifiable against
the raw files in `/data`.*

## Shape

| | CRM (`crm_events.json`) | Calendar (`calendar_events.json`) |
|---|---|---|
| Records | 20 | 22 |
| ID field | `crm_id` (`CRM-1001`…`CRM-1020`) | `event_id` (`CAL-A1`…`CAL-A22`) |
| Time representation | `meeting_date` + `meeting_time` (two string fields) | `start_time` + `end_time` (ISO-8601) |
| Party representation | `client_name`, `client_company`, `relationship_owner` (human names) | `organizer`, `attendees` (email addresses) |
| Free text | `subject`, `notes` | `title`, `description` |
| Status vocabulary | `Scheduled`, `Confirmed`, `Tentative`, `Completed`, `Cancelled` (title case) | `confirmed`, `tentative` (lower case) |

The two systems share **no identifier and no join key**. Matching must be inferred from time, party,
and text. This is the core of the exercise.

The party representation gap is the important one: CRM stores `"David Park"` while Calendar stores
`"david.park@meridiancap.com"`. Bridging those is the highest-signal matching feature available.

## Anomaly inventory

### A. Malformed values

| Record | Field | Value | Reading |
|---|---|---|---|
| `CRM-1008` | `meeting_date` | `"03-15/2025"` | Mixed separators. Unambiguously 2025-03-15 given the surrounding data range and its calendar counterpart `CAL-A9`. |
| `CAL-A11` | `end_time` | `"2025-03-14T20:00"` | Missing the seconds component that every other record has. Parseable with a lenient ISO parser. |
| `CAL-A16` | `attendees[2]` | `"raj.patel[at]atlasvc.com"` | Obfuscated email — `[at]` for `@`. Recoverable. |
| `CAL-A20` | `attendees[5]` | `"external-guests"` | Not an email at all. A placeholder, not recoverable to a person. |

### B. Missing values

| Record | Missing | Note |
|---|---|---|
| `CRM-1007` | `meeting_time` is `null` | Its counterpart `CAL-A8` supplies 15:00. |
| `CRM-1006`, `CRM-1009`, `CRM-1013`, `CRM-1019` | `client_name`, `client_company` | All four have `meeting_type: "Internal"`. This is not corruption — internal meetings legitimately have no client. |
| `CRM-1003`, `CRM-1007`, `CRM-1014`, `CRM-1018` | `location` is `null` | |
| `CAL-A11` | `attendees` empty, `location` and `description` null | The thinnest record in either file. |
| `CAL-A15` | `location` is `null` | |

### C. Intra-source duplicate

`CAL-A5` and `CAL-A6` are the same real meeting entered twice in the Calendar source:

| | `CAL-A5` | `CAL-A6` |
|---|---|---|
| Title | Investor Update - Pinnacle | Pinnacle Group - Q1 Update |
| Start | 2025-03-17T11:00:00 | 2025-03-17T11:30:00 |
| Organizer | james.wu@firma.com | james.wu@firma.com |
| Attendees | + kevin.obrien@pinnaclegp.com | + kevin.obrien@pinnaclegp.com, sandra.mills@pinnaclegp.com |
| Location | Boston Office - Room 301 | Boston Office |
| `created_at` | 2025-03-02T14:30:00Z | 2025-03-10T09:15:00Z |

Same organizer, same client contact, same day, 30 minutes apart, overlapping windows. `CAL-A6` was
created 8 days later and adds an attendee — consistent with someone re-creating the invite rather
than editing it. Both map to `CRM-1005`.

### D. Timezone trap

`CAL-A4` is the **only** record in either file whose timestamp carries a `Z` suffix
(`2025-03-13T19:00:00Z`). Its CRM counterpart `CRM-1004` says 14:00 on the same date, and both agree
the location is "DC Office - Main Conference Room".

19:00 UTC = 14:00 America/New_York (EDT, UTC-4, in effect on 2025-03-13). **Inference:** every other
timestamp in the Calendar file is naive local Eastern time, and `CAL-A4` is the one record serialized
in UTC. Treating the `Z` literally would place these five hours apart and break a match that is
otherwise unambiguous.

### E. Field conflicts between sources

| Meeting | Conflict |
|---|---|
| `CRM-1002` / `CAL-A2` | **Modality.** CRM: `In-Person` at "NYC Office - 30th Floor". Calendar: "Zoom - https://zoom.us/j/98765432100". Genuinely contradictory — this is the case the statement calls out by name. |
| `CRM-1009` / `CAL-A10` | **Status.** CRM says `Cancelled`; Calendar says `confirmed`. A stale calendar invite for a killed meeting. |
| `CRM-1016` / `CAL-A17` | **Time.** CRM 13:00, Calendar 15:00 — a 2-hour gap on the same date with the same client and same platform (Microsoft Teams). |
| `CRM-1018` / `CAL-A21` | **Location.** CRM `null`, Calendar "Zoom". Not a contradiction — an absence. Distinct from the above and must be treated differently. |
| Several | **Location specificity.** `CRM-1001` "HQ - Conference Room B" vs `CAL-A1` "Conference Room B"; `CRM-1011` "NYC Office" vs `CAL-A12` "NYC Office - 12th Floor"; `CRM-1017` "The Palm - DC" vs `CAL-A20` "The Palm Restaurant". Compatible at different granularity, not conflicting. |
| Most pairs | **Title wording.** `CRM-1011` "Annual Allocation Review" vs `CAL-A12` "Horizon Wealth - Year-End Review". Titles are never identical across sources — the Calendar convention prefixes the company name. |

Distinguishing *contradiction* from *absence* from *differing granularity* is a design decision, not
a data property. It is resolved in [02-reconciliation-design.md](02-reconciliation-design.md).

### F. Recurrence

`CAL-A3` and `CAL-A18` are both titled "Weekly Team Sync", both `is_recurring: true`, one week apart
(2025-03-11 and 2025-03-18, both 09:00, identical attendees). These are **two instances of one
recurring series, not duplicates** — deduping them would delete a real meeting. `CAL-A7` is also
`is_recurring: true` and does match `CRM-1006`.

The distinction from case C: `A3`/`A18` are 7 days apart with identical everything; `A5`/`A6` are 30
minutes apart with drifting details. Time separation is the discriminator.

## Expected reconciliation outcome

Derived by hand from the above, to be used as the correctness fixture for the matcher.

**17 matched pairs:**

| CRM | Calendar | Note |
|---|---|---|
| CRM-1001 | CAL-A1 | Clean exact match |
| CRM-1002 | CAL-A2 | Modality conflict |
| CRM-1004 | CAL-A4 | Requires timezone normalization |
| CRM-1005 | CAL-A5 + CAL-A6 | Requires intra-source dedupe first |
| CRM-1006 | CAL-A7 | Internal, no client |
| CRM-1007 | CAL-A8 | CRM time is null; calendar fills it |
| CRM-1008 | CAL-A9 | Requires lenient date parsing |
| CRM-1009 | CAL-A10 | Status conflict |
| CRM-1011 | CAL-A12 | Title wording differs substantially |
| CRM-1012 | CAL-A13 | Clean |
| CRM-1013 | CAL-A14 | Internal; "LP Advisory Committee" vs "LPAC" acronym |
| CRM-1014 | CAL-A15 | Clean |
| CRM-1015 | CAL-A16 | Malformed attendee email |
| CRM-1016 | CAL-A17 | 2-hour time conflict |
| CRM-1017 | CAL-A20 | Client is literally `"Multiple"` |
| CRM-1018 | CAL-A21 | Location absent in CRM |
| CRM-1019 | CAL-A22 | Internal |

**3 CRM-only:** `CRM-1003` (Lakeshore intro call), `CRM-1010` (Northwind strategy, Tentative),
`CRM-1020` (Northwind Fund VII, Tentative). Note both Tentative CRM records lack calendar entries —
plausibly meetings not yet actually booked.

**4 Calendar-only:** `CAL-A3` and `CAL-A18` (recurring internal team syncs), `CAL-A11` (Client
Reception, tentative, no attendees), `CAL-A19` (Fund VII Roadshow Prep, internal).

**Total unified meetings: 24.** Every one of the 42 input records is accounted for; nothing is
dropped.

## Implications for the design

1. Matching must be **explainable**, not merely accurate — 42 records is small enough that a reviewer
   will check the pairs by hand, and a scoring model that cannot say *why* it matched is unreviewable.
2. Normalization must be **lossy-free**: a record that fails to parse must still appear in the
   output, flagged, or the UI cannot surface data quality.
3. Conflicts are the **product**, not an error condition. The requirement is that the user can see
   where sources disagree, so conflicts must be preserved through the merge rather than resolved away.
