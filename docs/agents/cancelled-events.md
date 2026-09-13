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
