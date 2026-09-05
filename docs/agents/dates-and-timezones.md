# Dates, offsets and timezones

> Part of mapsee-aggregator's agent notes — see `AGENTS.md` for the map. Read this file when the bug is about: an offset that belongs to the server, a bare date, a sentinel date, `starts_at`'s first ten characters, two dates on one listing, a year on the wrong side.
> Every note below was measured before it was written; keep the numbers when you edit.

- **When a listing carries two dates, the one the template shows a human is the
  wrong one.** A MyListing card ships its occurrence on the wrapper and the
  LISTING'S NEXT DATE on the inner span the visitor actually reads — stamped
  identically into every card that listing produces, because it is the badge on
  the tile. 55 of 80 live cards on bainbridgeisland.com disagreed between the
  two. Take the span and a fifty-night music residency becomes the same Friday,
  fifty times, each row well-formed and individually plausible. This is the same
  shape as SLU's `date_time`-on-the-series-start and Squarespace's default pin,
  and the tell is the same: when a source offers two spellings of one fact, find
  a record where they DIFFER before choosing, rather than after.
  `mapsee_ingest_mylisting.py`, pinned in `test_ingest_mylisting.py`.

- **A recurring event with no end date projects pins forever, and nothing
  downstream will ever remove them.** `mapsee_cleanup.py` deletes the PAST; a
  2050 Fourth of July is not past and never will be within anyone's planning
  horizon. Live on bainbridgeisland.com: 423 occurrences in 2026, 184 in 2027,
  then a one-or-two-a-year tail — Hometown Halloween, the Polar Bear Plunge —
  running to 2050. So the horizon belongs at INGEST, where the count that was
  dropped can still be printed. Any adapter reading an expanded recurrence
  needs one; `horizon_days` (400) is the MyListing config's.

- **A date-dependent assertion is not flaky, it is wrong one day in seven.**
  `test_osm_food.py` checked that a Saturday-only venue resolves LATER than a
  seven-day-a-week venue — a proxy for "not today" that is false on Saturdays,
  when both resolve to today and `>` fails on equal dates. It went red every
  Saturday and green again on Sunday, which reads as flakiness and gets
  re-run rather than fixed. Assert the property (the row lands on a Saturday),
  never a comparison that happens to hold on the day you wrote it.

- **A timestamp's offset can belong to the SERVER, not the event.** BikeReg
  stamps every `startDate` `00:00:00` with `-04:00` or `-05:00` — including a
  Kailua-Kona race whose own `eventTimeZone` says "Hawaiian". Read as an instant
  that race lands at 18:00 the day BEFORE. Only the date is true. The tell was
  the same as MyListing's two dates and SLU's series start: find the record
  where the two spellings DISAGREE before choosing.

- **A single-venue calendar's `startDate` may carry no offset at all, and that is
  the venue block's real job.** WP Event Manager emits `2026-08-19 19:30:00` —
  naive local, space-separated, no zone, on every event. It becomes an instant
  only because `venue` supplies coordinates the sync turns into a timezone; read
  as UTC, a 7:30pm show is served at 12:30pm. So on a site like this the venue
  block is not a fallback for a missing pin, it is what makes the TIME true, and
  the two halves live in different files with no single place either can be seen
  to be wrong. The round trip is asserted end to end.

- **Counting records is not checking dates.** wisconsinbikefed.org's iCal export
  parses beautifully — 50 VEVENTs, 46 with LOCATION, 41 with a GEO line, real
  riding in the titles — and every event in it is in the PAST: 2025-11-02 to
  2026-04-18, read on 2026-08-16. It was configured on the strength of those
  counts, ingested 0 of 50, and came straight back out. A feed's shape says
  nothing about its horizon. `empty` is not `fail`, so it is recorded in
  `_not_included` as a stale export to re-probe in a season, not as a dead feed.

- **A source can hand you a SENTINEL date, and `mapsee_cleanup.py` can never
  remove it.** British Cycling's Let's Ride returns 172 rides dated in the
  **year 2500**. Cleanup deletes the PAST; 2500 is not past and will not be
  within anyone's planning horizon. It is the "recurring event with no end date
  pins forever" trap arriving pre-made from the publisher rather than built by
  us, and the defence is the same one MyListing needed: a horizon at INGEST,
  where the count that was dropped can still be printed.

- **A BARE DATE NAMES A DAY *SOMEWHERE*, AND FOR MONTHS THAT SOMEWHERE WAS
  GREENWICH.** Six adapters deliberately emit `YYYY-MM-DD` when the source
  publishes no clock — Ticketmaster's `timeTBA` listings, parkrun (whose note on
  why guessing a start time is worse than saying nothing is the clearest
  statement of the rule), BikeReg, RunSignup, Seattle Center's "All Day", every
  civic feed whose exact-midnight stamp is a date in a timestamp column — and
  each one's comment says the same true thing: the row claims a DAY, not a
  minute. `_to_utc_if_naive` returned date-only untouched, which reads as
  honouring that and is the opposite: a bare date handed to a `timestamptz` is
  read at the SERVER's clock, and `_compute_end`'s naive `T23:59:59` landed the
  same way. So the Mariners' ballpark tour reached a phone in Seattle as
  **"Today, 5:00 PM → Tomorrow, 4:59 PM"** — the wrong two days, a nineteen-hour
  window, and precise hours nobody had published. East of Greenwich it fails the
  other way (02:00 → 01:59 the day after). It was every all-day row on earth,
  shifted by its own venue's offset, and nothing could report it because every
  value involved is well-formed.
  `_anchor_all_day` brackets the day using the EVENT's coordinates, which is the
  same fix `_to_utc_if_naive` already made for TIMED rows — date-only was simply
  the branch that returned early. Both edges are converted rather than one plus
  24 hours, because a day is 23 or 25 hours long twice a year and only the tz
  database knows which. A free consequence: `_norm_cmp` could never parse a
  date-only value, so `--skip-unchanged` read every all-day row as `_Unknown`
  and rewrote it on every run.
  ../mapsee holds the other half — `fmtRange` renders a whole-day window as a
  date, because "12:00 AM → 11:59 PM" is still a claim about hours — and
  `tools/check-venue-order.mjs` there grades it. Two repos, one invariant,
  neither wrong on its own; the same shape as `looks_like_ordering` having to
  agree with `looksLikeOrdering`.

- **`starts_at`'s FIRST TEN CHARACTERS ARE NOT RELIABLY THE LOCAL DATE.** A
  `timestamptz` normalises to UTC in the column and PostgREST renders it in the
  connection's zone, so a 00:30 class in British Summer Time reads as the
  previous day — and a 19:40 one reads as 18:40, which is a weekly pattern that
  matches nothing. Convert into the zone the sync derived from the venue's own
  coordinates instead. Both retirement rules now do; only the grid rule's
  per-day grouping still reads the string, where an hour's error costs at worst
  one group boundary.
