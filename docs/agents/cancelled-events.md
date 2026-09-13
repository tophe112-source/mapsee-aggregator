# Cancelled events: the half an upsert cannot reach

> Part of mapsee-aggregator's agent notes — see `AGENTS.md` for the map. Read this file when the bug is about: an event on the map that is not happening, a source that says cancelled and a row that does not, what counts as evidence before a row comes off the map.
> Every note below was measured before it was written; keep the numbers when you edit.

- **NEARLY EVERY CANCELLATION HAPPENS AFTER WE HAVE ALREADY TAKEN THE EVENT, and
  until 2026-09-13 nothing in the repo could see one.** Every check was at
  ingest — Dice and Venuepilot read a status field, Eventbrite reads
  `is_cancelled`, OpenActive honours a `state: deleted` tombstone, the festival
  adapter reads schema.org's `eventStatus` — and all of them protect only rows
  not written yet. An upsert cannot delete and `--only-new` (the CI default)
  means a scheduled run can only ADD, so a row cancelled the week after we took
  it sits there with its pin, its RSVP button and its chat until
  `mapsee_cleanup.py` reaps it a week after it was supposed to END.
  Measured by sampling 2,000 live event pages from mapsee.me's own sitemaps,
  taking the 502 whose "Tickets / info" link is a Meetup event, and re-probing
  every one at its source: **437 scheduled, 47 CANCELLED, 18 deleted outright
  (HTTP 404) — 12.9% of the Meetup events on the map were not happening.**
  Meetup is about a quarter of the map's linked rows (502 of 1,969 sampled rows
  carrying a Tickets / info link), so it is not a corner.
  `mapsee_prune_cancelled.py` is the answer, and `prune-cancelled.yml` runs it
  daily at 07:55, BETWEEN the import (06:17) and the janitor (08:23).

- **THE INGEST-SIDE CHECK CANNOT FIND THOSE ROWS, and the number says so.** The
  same day, a live Meetup `eventSearch` sweep of Seattle returned 30 events and
  **all 30 were ACTIVE** — including the group whose listing was already marked
  cancelled on its own page. A search endpoint answers about the events it is
  willing to show you; it is not a ledger of what got called off. Read the
  adapter checks as a boundary, not as a solution, and expect the post-hoc
  sweep to keep doing nearly all the work.

- **THREE ADAPTERS COULD SEE A CANCELLATION AND WERE NOT LOOKING.**
  `mapsee_ingest_ics.parse_ics` kept eight VEVENT properties and `STATUS` was
  not one of them, so RFC 5545's `STATUS:CANCELLED` — the ONLY way a subscribed
  calendar can tell us, because a library cannot delete a VEVENT it has already
  published — was discarded before anything could read it. The generic
  `mapsee_ingest_jsonld` never read `eventStatus`, which the festival adapter
  has honoured since it was written. And the Meetup GraphQL query did not ask
  for `Event.status` at all. `TENTATIVE` is deliberately NOT treated as a
  cancellation: a lot of municipal software publishes everything that way.

- **`Event.status` on Meetup is a NON_NULL `EventStatus` enum**, verified by
  live introspection 2026-09-13: `ACTIVE, AUTOSCHED, AUTOSCHED_CANCELLED,
  AUTOSCHED_DRAFT, AUTOSCHED_FINISHED, BLOCKED, CANCELLED, CANCELLED_PERM,
  DRAFT, PAST, PENDING, PROPOSED, TEMPLATE`. `DEAD_STATUSES` is a DENYLIST, like
  `mapsee_ingest_dice_venue`'s, not an allowlist: Meetup has renamed fields on
  us twice (the /gql endpoint, `venue.lng` → `venue.lon`), and an allowlist
  would turn the next rename into a silent total outage of the feed while a
  denylist merely lets an unknown state through.

- **PROSE IS NEVER EVIDENCE OF A CANCELLATION, and the counter-examples are all
  on live ticket pages.** "Free cancellation up to 24 hours before the show",
  "Cancellation policy: refunds are issued within 7 days", "In the event of a
  cancelled game, tickets are honoured for the replay", "Cancelled orders are
  refunded automatically". A text rule over event pages would hide healthy rows
  by the thousand. `mapsee_prune_cancelled` acts on exactly two things —
  schema.org `eventStatus` of `EventCancelled`/`EventPostponed` (JSON-LD or
  microdata), and HTTP 404/410 — and `test_cancelled_events.py` pins those five
  sentences as things it must NOT act on. `EventPostponed` counts as off
  because the date WE hold is the one that is not happening; a new date is a new
  event and the adapters will import it.

- **A 403 IS A REFUSAL, NOT A CANCELLATION** — the same rule `destination_verdict`
  learned in `mapsee_menu_links`, and it has to be restated here because the
  consequence is worse: a link prune edits a line, this HIDES a row. Only a
  fetched page or a 404/410 decides anything; a 403, 429, 5xx, timeout or
  non-HTML body is `unknown`, and unknown keeps the event.

- **A CANCELLATION MARKER ON A CALENDAR PAGE IS NOT ABOUT OUR ROW.** A
  "Tickets / info" link sometimes lands on a venue's whole listing page, where
  one cancelled show among twenty would otherwise condemn whatever we happen to
  be holding. So a page counts as cancelled only when EVERY `eventStatus` on it
  says so; a mixed page is `unknown`. A page with no `eventStatus` at all is
  LIVE, not unknown — most event pages carry none, and calling those unknown
  would be the same as not running the sweep.

- **A WHOLE HOST ANSWERING DEAD AT ONCE IS A STATEMENT ABOUT US.** Lifted from
  `mapsee_prune_links`, which learned it by nearly deleting every Uber Eats link
  on the map: gigs are cancelled independently, so a high concentration on one
  domain has a common cause and the common cause is nearly always our own
  client. The floor is **5** here rather than prune_links' 3, because a real
  venue can genuinely cancel three nights running and this rule hides rows
  rather than editing text.

- **IT HIDES, IT DOES NOT DELETE, and that is not timidity.** `hidden_at` comes
  off the map immediately, is one PATCH to undo, and does not cascade through
  `event_messages`, `event_rsvps`, `invites` and `cohosts` — which matters most
  for exactly these rows, because somebody may have RSVP'd to the thing that got
  cancelled. `mapsee_cleanup` deletes it a week after it would have ended like
  every other imported row, so nothing accumulates. And hiding is durable
  against re-import: `fetch_import_state` does not filter on `hidden_at`, so a
  hidden row still counts as existing and `--only-new` will not put it back.

- **THE SWEEP IS ORDERED BY START DATE, NOT BY HOW MANY ROWS SHARE A URL.**
  There are far more upcoming rows than one polite run can probe, so the cap has
  to truncate somewhere, and `mapsee_prune_links`' most-used-first ordering
  would re-probe the same popular listings every day and never reach the tail.
  Soonest-first truncates the FAR end instead, which is both the least harmful
  place for a stale row and the part that gets probed anyway as it comes closer:
  a cancelled gig tomorrow is a wasted evening, a cancelled gig in November has
  four months of runs to be caught in.

- **A ONCE-DAILY SWEEP CANNOT CATCH A SAME-DAY CANCELLATION, and that is the case
  that actually costs somebody an evening.** The first version of
  `prune-cancelled.yml` ran only at 07:55, straight after the import — which is
  the right slot for the standing STOCK of cancelled rows and useless for the
  FLOW. Measured on the reported row the day it shipped: "Seattle Gay Online
  Speed Dating" started at 22:00 UTC that night, its Meetup listing already said
  `EventCancelled`, and the next sweep was not until 07:55 the following
  morning — **six hours after the event was due to END**. Hiding it then is
  bookkeeping, not a fix. A second cron at 16:55 passes `--days 2`, so it probes
  only what happens inside 48 hours: a few hundred URLs instead of several
  thousand, minutes instead of half an hour. 16:55 UTC is 09:55 Pacific / 12:55
  Eastern / 17:55 UK — hours of warning for a US evening event and still ahead of
  most UK ones. `github.event.schedule` (the cron that fired) is the only thing
  that tells two scheduled runs of one workflow apart; it is EMPTY on a dispatch,
  which is what makes a manual run default to the full sweep.

- **WHEN SOMETHING IS WRONG TONIGHT, NARROW THE SWEEP RATHER THAN WAITING OUT THE
  BUDGET.** A full pass spends its whole `--max-seconds` probing events four
  months away before it can report. The `days` dispatch input exists so a first
  production run, or an urgent one, can answer in minutes over a two-day window —
  which is also the safest way to make the FIRST write of a delete-shaped tool:
  a bounded dry run you can read in full, then the same bounded scope with
  `--apply`. Note that `mapsee_prune_cancelled` has no `--unhide` (its sibling
  `mapsee_retire_online_events` does), so a bounded first run is the whole of the
  safety net.

- **THE SWEEP IS TWENTY TIMES BIGGER THAN THE SITEMAPS SUGGEST, and the first
  live run is what said so.** Sizing `--max-checks` off a sitemap sample gave
  "a few hundred URLs in a two-day window". The real number, measured
  2026-09-13 on the first production dry run, is **88,128 rows and 25,311
  distinct source URLs inside a THREE-DAY window** — the sitemaps index event
  pages, not the OpenActive standing rows and OSM places that make up most of
  the table. At roughly one probe a second that is seven hours of work, so
  every run is capped and every run leaves most of the map unexamined. Which
  part it examines is therefore the entire design, not a detail.

- **A CAPPED SOONEST-FIRST SWEEP MUST NOT INCLUDE THE PAST, and `--back 1` cost
  the first run everything.** It was there so a multi-day event cancelled
  mid-run could still be caught — true, and irrelevant next to what it costs:
  already-started events sort to the FRONT of a soonest-first queue, so the run
  spent its whole 900-second budget on them. It examined 894 of 25,311 URLs,
  found 44 listings called off, and **every one of the 47 rows had started the
  previous day**. It never reached the current day at all, let alone the event
  it had been dispatched to remove. `--back` is 0 now. An event that has already
  begun is the least useful thing this can hide, and `mapsee_cleanup` deletes it
  a week later regardless.

- **THE HIT RATE IS REAL, THOUGH, AND IT IS HIGH.** That same run: 894 URLs
  probed, `live=677 · unknown=173 · cancelled=27 · gone=17` — **44 of 894, 4.9%,
  called off at the source** in a single day's slice, across Meetup and
  Eventbrite. The 173 unknowns are hosts that refuse a plain client, and they
  keep their rows, as designed. The tool works; what was wrong was where it was
  pointed.
