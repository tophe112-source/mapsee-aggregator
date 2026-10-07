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

- **THE HOST-REFUSAL GUARD FIRED ON ITS FIRST REAL RUN, and it was right.**
  First `--apply` pass, 2026-09-13: `live=1025 · unknown=406 · cancelled=116 ·
  gone=60`, and then `HOST REFUSING: ubereats.com — 43 of 44 listings read as
  off`. Forty-three rows that a per-URL reading would have hidden, on a host
  that simply does not answer a plain client — the exact failure
  `mapsee_prune_links` was nearly destroyed by, reproduced on a different tool
  within an hour of shipping. **161 rows hidden, 43 correctly refused.** Do not
  soften this rule; it is the difference between a sweep and a vandal.

- **PROBING ORDER AND BOOKING LINKS IS MOST OF THE COST AND NONE OF THE YIELD.**
  That run spent 2,100 seconds on 1,607 URLs and stopped with 2,393 still
  unprobed, and the `unknown=406` is nearly all hosts that can never answer the
  question: Uber Eats storefronts, leisure-centre booking grids, restaurant
  order pages. A "Tickets / info" line is written for every row this pipeline
  stores, not just for events with a listing page, so the sweep asks thousands
  of hosts a question only a handful can answer. The next real gain here is a
  host-level skip — a host that has never once returned an `eventStatus` is not
  worth a daily probe — which would roughly double useful throughput without
  touching the evidence rules. Not yet done; measure per-host yield first.

- **A TITLE CAN SAY THE EVENT IS OFF, OR THAT THE BUILDING IS SHUT, and every
  adapter now listens.** Over 87,572 rows from the 837-feed corpus, 37 titles
  carried a cancellation ("CANCELLED - Tech Tips Tuesday", "POSTPONED: Sunday
  Concert", "Chess Club CANCELLED") and 293 were closure notices ("Library
  closed for Thanksgiving", "NATURE CENTER CLOSED", "CLOSED: Christmas Day").
  All 28 distinct cancelled titles and all 107 distinct closure titles were
  read: every one is a notice, not an event. `notice_reason` in
  `mapsee_ingest.py` refuses them in `EventStore.upsert`, after the spam gate
  and before the dedupe (a cancellation shares its date and venue with the
  event it cancels). They are counted per source as `notices`, apart from
  `rejected`, which `mapsee_spam_audit` reads as advertising. It is a title
  rule only, so this note's prose rule still holds. It is anchored for the
  traps the closure note in `curation-and-discovery.md` lists: "(Closed)" game
  tables, "Closing Reception" and "Closed Captioned" stay, and so do "Cancelled
  Plans" and "Everything Is Cancelled". It stops NEW rows only. A title edited
  to "CANCELLED - X" after we took "X" is a new fingerprint, so the original
  row stays up until `mapsee_prune_cancelled` finds its source page cancelled.
  The rule was English-only at first. The same corpus held 14 more
  cancellations it let through: "Entfällt:" x6, "FÄLLT AUS:", "***ABGESAGT***",
  "Club ados - ANNULE", "(CANCELADO)" x2 and "**CANCELED**". A 2026-09-28
  review found a 【開催中止】 tour that its feed publishes as CONFIRMED.
  `_OFF_WORDS` now carries the German, French, Spanish, Dutch and Italian word
  under the same anchoring, and "Reporte anual" and "Suspendidos en el tiempo"
  stay.

- **THE SWEEP VISITED PAGES ROBOTS.TXT REFUSES, AND RETIRING A FEED DID NOT
  STOP IT.** A row's "Tickets / info" link is `primary_url`, the source's own
  event page, and the sweep fetched up to 4,000 of them a run, twice a day,
  without asking robots.txt. On 2026-09-30, 76 feeds were retired because
  their robots.txt refuses us, and the rows they had already imported stay on
  the map until their dates pass. So the sweep kept visiting those libraries'
  event pages: `https://santafelibrary.org/event/...` and
  `https://denverlibrary.libcal.com/event/...` both answer `Disallow: /`,
  and Santa Fe alone had 1,109 upcoming programs. `cancellation_verdict` now
  asks first, through a `robots_txt.Robots` that reads over urllib
  (`_RobotsSession`), so the twice-daily run still needs nothing outside the
  standard library. A page robots.txt does not plainly allow is not fetched.
  That includes a challenged or unreachable robots.txt. Its verdict is
  `robots`, which keeps the row as `unknown` does, and the run prints the
  hosts it did not read. Measured the same day, the job's reason to exist is
  untouched: meetup.com and eventbrite.com event pages are allowed.
  `test_cancelled_events.py` pins it, and three mutations fail it: no check,
  a challenge read as allowed, and the shim dropping an error's body.

- **A CANCELLATION NOW REACHES THE ROW WE ALREADY WROTE (2026-10-05/06, owner: "we don't want users to go to an event or center that is closed").** An adapter that sees a cancellation calls `EventStore.cancel(ev)` instead of refusing the record: a TOMBSTONE under the fingerprint, source and source_id the live row has. `mapsee_supabase_sync` then sets `cancelled_at` and `hidden_at` on that row in every run, ONE stamp in ONE conditional PATCH (`claimed_at=is.null`, `external_source=eq.mapsee`, both columns null), so people who RSVP'd see "Cancelled" in My events (../mapsee 0143) and the row leaves the map and every feed. A "CANCELLED - X" title tombstones X when the store can reproduce the adapter's fingerprint recipe (3 of 3 ics fixtures; 16 of 16 titles in the 2026-09-27 corpus recover). A community-centre source read WHOLE (`mark_complete`) that stops listing a session has it cancelled under `--retire-absent`, against the last complete read's manifest kept in the Actions cache: never within 2 h of now or on a window's last day, and not at all when a source loses more than max(3, 10%) of its in-window sessions, or a whole unit of 2+, at once. A session the source still lists but we could not place is `mark_seen`, never absent (a Toronto point-table glitch replayed live: 1,236 cancelled before, 0 of 23,085 after). `mapsee_uncancel.py --since` is the undo. `test_cancellations.py` pins it.

- **ONLY THE SOURCE THAT READ ITS WHOLE TIMETABLE MAY LIFT A CANCELLATION, AND ONLY WHILE BOTH STAMPS ARE STILL OURS.** Un-cancelling is what brings a reopened centre or a session listed again back, and it is where harm hides. Two rules, decided 2026-10-06 after review: (1) a lift needs the row written live this run by a unit that `mark_complete`d - a fingerprint can be shared by a venue's feed and a platform's copy, and a store read in passing that still lists an event another source called off must not put it back on the map; (2) the row's `cancelled_at` and `hidden_at` must be the same instant, the one stamp the sync wrote, and the lift PATCH names that exact stamp in both columns. Every other writer of `hidden_at` - an organizer take-down (README Conduct), mapsee_prune_cancelled, the retire scripts, the legacy re-key - sets it alone, so anything hidden again on top of our cancellation can never be lifted, whatever procedure the person followed. The first build instead re-stamped `cancelled_at` daily and relied on a take-down clearing it; both are gone. A 30 h hold remains so a session that blinks out of one complete read and back into the next does not bounce. `mapsee_uncancel.py` applies the same equal-stamp rule (a row hidden again on top is listed and left alone). Three mutations (drop the equality, drop the complete-read filter, drop the undo's equality) each fail the gate.

- **THE DAILY DETAIL REFRESH NEVER WROTE, AND A TIME CHANGE WAITED FOR WEDNESDAY.** `keep_new` kept rows that `needs_detail_sync` picked, then `existing = set(state) - moved` dropped them again. Now it is minus `daily` too, and the same state read also returns starts_at, ends_at, hidden_at and cancelled_at. It is still 100 ids per request (URL 5,073 → 5,122 bytes), with 7,701 → 19,201 response bytes per 100 rows. So an --only-new day keeps a stored row whose time moved: 100,000 rows judged in 0.33 s (aware times), or 1.44 s (naive times, with timezone lookups now cached per point; uncached was 3.85 s per 20,000).

- **A CANCEL-AND-RELIST IS NOT A CANCELLATION.** Chicago Public Library (BiblioCommons, 2026-10-05) had 25 isCancelled rows, and 6 were one series cancelled and relisted under new ids. The new listings had the same title, branch, day and clock, so they share the cancelled ones' fingerprint. Under "tombstone beats live" all 6 live sessions were hidden. A status cancellation now gives way to a live listing from the same source under a different source_id, in either order (EventStore stats: cancel_beside_relist). A NOTICE still wins. Notices are the store's "CANCELLED - X" title path, plus cancel(..., notice=True) from Revize, Glen Echo, Drupal FullCalendar, PerfectMind and Barcelona. Revize's weekly row plus a one-day "CANCELLED - Toddler Drop-In" row is how a town calls off one occurrence.

- **MEETUP AND EVENTBRITE CANCEL ONLY THEIR OWN ROW.** Their tombstones carry the listing URL. The sync hides only a row whose "Tickets / info:" line is that URL, followed by whitespace or the end of the text.

- **A CANCELLED RECORD IS BUILT BY THE LIVE BUILDER AND TOMBSTONED, NOT REFUSED.** Before 2026-10-05, ics (STATUS:CANCELLED), jsonld (EventCancelled, EventPostponed), festivals, BiblioCommons (isCancelled) and Localist (status canceled) all refused the record. A refusal keeps a cancellation off the map only if we had not stored the event yet, because an upsert cannot delete. Each adapter now builds the record with the same function as the live row, passing `tombstone=True`, and calls store.cancel. test_cancelled_events.py, test_ingest_ics.py, test_ingest_bibliocommons.py and test_ingest_festivals.py prove that store.cancel keys exactly what store.upsert would have keyed (fingerprint, source and source_id). A tombstone never geocodes, because the fingerprint reads no coordinates. Measured on first pages: BiblioCommons 8 of 600 rows isCancelled (Toronto 2, Chicago 6, Vancouver 0); Localist 3 canceled and 4 soldout of 5,417 across 56 sources (sold out is still happening).

- **A RETITLED CANCELLATION IS KEYED BY THE TITLE INSIDE THE NOTICE.** UGA's Localist row read "CANCELED: Ecology Seminar Series: Joseph Hoyt". The row we stored was "Ecology Seminar Series: Joseph Hoyt", because EventStore refuses a notice title. So each cancel path runs strip_notice before keying; in ics it runs before skip_title, the title-keyed overrides and the legacy key. A title that is only the word (MSU Denver's "CANCELLED") names no row: it is counted and skipped, and absence or prune_cancelled is the remedy.

- **MOBILIZON IMPORTED ITS CANCELLED EVENTS AS LIVE.** QUERY never asked for Event.status. Asked of all 65 instances that answer (0 errors), page 1 held 3,558 CONFIRMED, 29 TENTATIVE and 7 CANCELLED, and all 7 were going on the map. They are now never imported, and TENTATIVE stays, as in ics. Mobilizon is open registration, so store.cancel returns "untrusted" and nothing hides a row stored earlier.

- **TWO PLATFORMS CARRY NO STATUS AT ALL.** The Events Calendar REST v1 has 45 keys on njaudubon.org and gnps.org and no event status; `status` is the WordPress post status. TEC's "Canceled" reaches only the page's JSON-LD, which mapsee_prune_cancelled reads. Gancio /api/events has 10 keys (bonn.jetzt, 89 events). On both, a cancellation arrives only as a title.

- **A RESCHEDULED JSON-LD EVENT TOMBSTONES ITS OLD DATE.** The key is name|date|venue, so a show moved to another day is a new row, and the old day's row stayed on the map until that day passed. When the eventStatus is EventRescheduled, each previousStartDate's record is tombstoned and the new date is written live. A new time on the same day is the same key and is never tombstoned. EventMovedOnline is also dead now, as it already was in openactive.

- **A PLATFORM'S OWN "CANCELLED" IS NOW A TOMBSTONE, NOT A REFUSAL, and on a search it is rare.** Meetup (CANCELLED, CANCELLED_PERM, AUTOSCHED_CANCELLED), Eventbrite ("canceled"; discovery now hydrates `is_cancelled` listings instead of skipping them), dice_venue and the DICE partner API (cancelled/postponed), Venuepilot ("cancel" in the status), SeatGeek and AXS (a cancelled/postponed denylist, unverified with no keys) each build the cancelled listing EXACTLY as the live row and call `EventStore.cancel`. Same refusals, same source_id, same fingerprint: a tombstone under any other fingerprint hides nothing. `test_platform_cancellations.py` compares cancel's fingerprint with upsert's through each adapter's own loop; 23 mutations, all caught. Meetup's DRAFT/PAST/BLOCKED/TEMPLATE/PROPOSED stay plain refusals: their names do not say the event is off. Measured live 2026-10-05 over Seattle: 9,700 eventSearch hits (1,022 rows), 4 CANCELLED hits = 2 events, 1 tombstone (the other had no venue, so never a row). About 0.1%, so mapsee_prune_cancelled still does most of the work. The keyword sweep repeats an event, so the Meetup log counts distinct fingerprints.

- **LUMA, RUNSIGNUP, ROLODEX AND MOSHTIX CARRY NO CANCELLED STATE; a title is their only signal.** Probed 2026-10-05. Luma's event has visibility/waitlist_status only, and the entry status is calendar approval. RunSignup's 184 WA races carry is_registration_open (F on 10) and nothing else. Rolodex's 8 shows have no status. Moshtix's EventStatus enum has no CANCELLED. Its "Definitely Oasis (UK) (Oasis Tribute) - CANCELLED" was LIVE (1 in 200), and the core's notice path tombstones the untitled event's own fingerprint (tested). BikeReg was not introspected: outsideapi.com/robots.txt answered 503 twice, which RFC 9309 reads as disallow.

- **THE TIMETABLES THAT ARE READ WHOLE NOW SAY SO, AND A CALLED-OFF SESSION IS A TOMBSTONE, NOT A GAP.** (2026-10-05) Toronto rec (both sources), Linked Events (per instance) and PerfectMind (per tenant, `id_prefix=<slug>:`) call `store.mark_complete` only after a read with no deadline, no failed or capped page, no page on another host and no unfetched place. Measured live, two whole reads 4 minutes apart lost 0 fingerprints on all 6 units (toronto-rec 25,469 rows, earlyon 1,886, Kamloops 253, Menlo Park 269, Moose Jaw 1,544, Tukwila 380). So under `--retire-absent` a session gone from the next complete read is a real change, not noise.
  - Toronto has no status column (0 of 33,457 rows say cancel), so absence is its only signal. The drop-in window ends at the last day the City's weekly-rebuilt table holds, not at horizon_days. An empty EarlyON table is never complete.
  - Linked Events: Helsinki had 14 EventCancelled rows in 90 days; 12 are now tombstones keyed by the row's id fingerprint, so a "PERUTTU:" retitle still hits the stored row. Espoo had 2, and neither had ever been listed.
  - PerfectMind has no status field. `called_off()` recovers the title from "CANCELLED X", "X - Cancelled", "'Cancelled' X" and "**CANCELLED**X 2:35 - 3:25 PM**CANCELLED**". At Moose Jaw that gave 20 of 20 tombstones, all 4 distinct recovered titles live on other days. A grid day with every slot called off tombstones the DAY row, because that is what was stored. A tombstone never names a fingerprint the same read writes live, because the store lets a tombstone beat a live record.

- **A CANCELLATION CARD IS NOT ALWAYS THE ROW IT CANCELS: GLEN ECHO'S 5 WERE NOTICES OF THEIR OWN.** On 2026-10-05 the 5 "CANCELLED TODAY-" cards (nodes 8845-8852, Nov 20 to Dec 31) were titled "CAPITAL BLUES DANCE" and "FRIDAY NIGHT CONTRA DANCE". The weekly series are "BLUES DANCE" (nodes 7569-7578) and "CONTRA DANCE" (9627-9635). Their ids are contiguous in date order, with no gap where a deleted node would sit. Rebuilt under the stripped title, which takes 1 detail request each because the room is only on the page, all 5 tombstones match no stored row. The path still pays for a dance retitled in place. A series node that is deleted is caught by absence instead. `mapsee_ingest.notice_reason` does not read "CANCELLED TODAY-", because the word is followed by TODAY rather than a separator. `test_ingest_glenecho.py` pins this.

- **A SECOND PASS "AS IF NOTHING WAS CALLED OFF" GIVES THE EXACT FINGERPRINT OF A FOLDED TIMETABLE ROW.** Montreal and Barcelona fold sessions into stretches whose identity is the first clock. A cancelled 10:00 hour in a 10:00-12:00 stretch moves the surviving row to 11:00, so the row to tombstone is the old 10:00 one. Building that fingerprint by hand would be a guess. Instead the run calls events() again with the cancelled sessions read as running and subtracts today's rows. Barcelona's pass also includes every register that shares the title, so a cancelled register inside a live register's stretch tombstones nothing. Live on 2026-10-05: Montreal's est_annulee gave 8 tombstones (Patinage libre tous, Aréna Vincent-Lecavalier, Dec 22 to Jan 3).

- **BARCELONA MARKS A CALLED-OFF ROW IN CATALAN, AFTER AN ASTERISK.** 10 of 5,741 agenda rows on 2026-10-05 did: "**Anul·lat**", "**Cancel·lat**", "*Ajornat*", "*Ajornada per motius meteorològics*", "*CANCEL·LADA TEMPORALMENT". None was at a community place that day. Before this rule, one at a casal would have been written live, because the store's notice_reason reads no Catalan. The asterisk is required: "Performance participativa, suspensió, noves dramatúrgies" is a show about suspension.

- **THE TOWN'S OWN CLOSURE IS A TOMBSTONE, NOT ONLY A SKIP.** Revize already skipped host sessions on a closure-row day and on "No program on M/D" days. Those sessions are now also tombstoned with the live fingerprint, because a closure added after the session was written would otherwise stay on the map. Manassas, 2026-10-05: 7 tombstones (6 closure-day, 1 no-session). A club the town only lists (Run Club) is neither skipped nor tombstoned.

- **TWO READS 12 MINUTES APART LOST 0 ROWS; THE RISK IS A READ THAT DID NOT HAPPEN.** Across montreal (895), phl (141), barcelona (1,144), revize manassas/pacific (212/5), lcsd culture/smartplay (1,114/1,979), drupal (119) and glenecho (179), 0 in-window rows went missing. On read 1, Revize Bladensburg's and Olympia's robots.txt was unreachable. Both were SKIPPED, so not complete, and answered 12 minutes later. The breaker's floor, max(3, 10%), allows every row of a 1-5 row unit to be cancelled. So an empty Revize list, a configured calendar with no rows, an empty Drupal calendar, zero PHL schedules or an empty Madrid resource is never a complete read.

- **A COMMUNITY CENTRE THE CITY CALLS CLOSED IS CANCELLED, NOT LEFT ROLLING.** mapsee_ingest_facility_hours used to skip a refused own row, and a skipped standing row never expires, so its last week showed for ever. Now every refused own row, and every taken-over OSM listing the city calls closed or no longer lists, goes to store.cancel under the live row's own fingerprint, together with a closed centre's Late Night evenings. A taken-over listing with merely unreadable hours stays the "opening times are not listed" listing, because the building is open. Measured 2026-10-05, two live reads: 434 written and 21 cancelled each time (10 closed, 9 no hours, 2 unreadable), 3 kept as listings, 0 of 434 absent between the reads. Over the real EventStore, every tombstone's fingerprint, source and source_id equal the live row's (test_ingest_facility_hours.py, 19 mutations killed).

- **A BROKEN CITY READ CANCELS NOTHING.** The sync puts no breaker on tombstones. A renamed hours field reads as "no hours" for every centre, and a re-keyed layer makes every claimed listing "gone". So when one read would cancel more than max(3, a third) of a source's centres, the adapter cancels none, warns, and does not mark the read complete. The most measured on 2026-10-05 was 4 of 28 (Seattle) and 3 of 21 (Cleveland), 14%.

- **LATE NIGHT IS ONE COMPLETE UNIT PER CENTRE.** id_prefix is "<name>|". An evening dropped from LN_HOURS is retired by absence (3 of 6 is within the sync's max(3, 10%)), and a centre whose data contradicts its own page is left out. A centre whose whole programme disappears trips that breaker by design, so its evenings already written (21 days at most) stay.

- **A RENAMED OR RE-TIMED SESSION MOVES TO ITS NEW KEY; ABSENCE NO LONGER CANCELS IT (2026-10-07).** The first absence comparison (run 116, 2026-10-06) cancelled 10 sessions, and the live timetables show 8 of them were real schedule edits: Thanksgiving and winter-break dates dropped at Brampton, Surrey and Vaughan, and one Surrey slot given to a new class. The other 2 were the same occurrence under a new key. Barcelona retitled "Nit d'ànimes" (same register, day, 17:00 and venue). Surrey moved a Pickleball from 17:15 to 17:00 on the same classId and date, and PerfectMind's fingerprint includes the clock. Both new rows were inserted in the same run, so the map was right, but the old rows said "Cancelled" to anyone who had RSVP'd.
  - The manifest now records [link hash, venue hash, title words] per session (`absence_ident`). `absent_successors` matches a gone key to exactly one key NEW to its unit, at the same venue and under a similar title, by either:
    - the same occurrence link on the same day, carried by one session in each read; or
    - the same start, where the old title is gone from the venue and the new title is new to it.
  - `rekey_absent` moves the stored row before the upsert, keeping its id, link and RSVPs, and rewrites it that run even if the PATCH reply is lost.
  - SIMILAR IS STRICT, because a wrong move hands an RSVP to a different session. One title must contain the other word for word, and no differing word may name an audience, age or level. The first draft accepted titles sharing half their words, and run 116 holds 3,457 same-slot pairs of DIFFERENT sessions that pass that, e.g. "Drop In Badminton - Adult" / "Drop In Volleyball - Adult". A simulated same-venue class swap moved 27-29% of rows at Surrey and Brampton under that draft. The strict title rule alone accepts 0.0-1.4% of 20,000 such swaps per unit (left: "Yoga" / "Chair Yoga"), and the series guard then refuses all of them.
  - The PATCH reply is the row AFTER the write, so a move is confirmed by the NEW key. Counting the keys sent (as `patch_imports` does) read every move as failed. The test's FakeDB answers the same way now.
  - Replayed on run 115 to run 116, exactly the 2 move and the 8 are still cancelled. An ambiguous match, a new key that is already stored, and a claimed row all fall back to the cancellation. Matching runs only inside the breaker's allowance, so a mass retitle still cancels and moves nothing. `--dry-run` reports what would move.
  - The rec manifest grows from 6.5 MB to 16.3 MB. A manifest written before 2026-10-07 has no identities and matches nothing for one run.
