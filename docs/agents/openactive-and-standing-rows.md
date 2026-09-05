# OpenActive/RPDE: booking grids, weekly collapse, standing rows, retirements

> Part of mapsee-aggregator's agent notes — see `AGENTS.md` for the map. Read this file when the bug is about: an RPDE feed paging from the oldest end, `ScheduledSession`, sessions every ten minutes, forty thousand standing rows, retirement rules, the licence line.
> Every note below was measured before it was written; keep the numbers when you edit.

- **AN RPDE FEED PAGES FROM THE OLDEST END, so reading page one tells you
  nothing — and it lies in BOTH directions.** OpenActive publishes over RPDE:
  items ordered by modification time ASCENDING, followed via `next` until the
  page repeats. Measured 2026-08-26: Our Parks reports **0 future sessions on
  page one and 854 when walked to the end**; England Netball reports 0 either
  way and has been dead since **2019** — 14,798 records, still answering 200,
  still listed in the official catalog seven years later. A verifier that stops
  at page one retires a working feed and configures a dead one. Same family as
  BikeReg's search endpoint answering 200 with a silent hundred-row ceiling, and
  as "counting records is not checking dates". `walk()` in
  `mapsee_ingest_openactive.py`; `test_ingest_openactive.py` pins it, along with
  the `state: "deleted"` tombstone that must REMOVE a cancelled session rather
  than be dropped on the floor.

- **A `ScheduledSession` does not know its own name, and 93 of 127 publishers
  are not publishing events at all.** An OpenActive occurrence carries a date
  and a `superEvent` pointer; the title, place, price and activity live on the
  `SessionSeries` it names, so that shape needs both feeds read and joined —
  and the occurrence must win on the DATE, or every week of a fifty-week block
  inherits the series' own `startDate` and lands on one Monday (MyListing,
  exactly). Of the 127, only 34 publish dated sessions; the other 93 publish
  `FacilityUse` and `Slot` — bookable courts and halls. A bookable badminton
  court at 19:00 is an empty room somebody may or may not take, and mapping it
  is the Traders Village mistake. Also unread and worth ~30,000 records: ~13
  Legend-platform publishers ship a `SessionSeries` feed and NO occurrence feed,
  so their sessions exist only as an `eventSchedule` PATTERN. Expanding that is
  the same job `mapsee_ingest_markets` and `mapsee_ingest_parkrun` already do,
  and it is the highest-value thing to add here next; `_not_included` in
  `openactive_sources.json` has the measurements.

- **A SESSION PUBLISHED EVERY TEN MINUTES IS A BOOKING GRID, and it walks past
  the refusal written for exactly that.** This repo already declines OpenActive's
  `FacilityUse` and `Slot` — "a bookable badminton court at 19:00 is an empty
  room somebody may or may not take" — and the same thing arrives through the
  front door as a `ScheduledSession`, which that rule never sees. Measured
  2026-08-28 in a ±0.03 box on central London: one pool published **"Swim For
  Fitness" 255 times in a week, 110 of them on a single day** at ten-minute
  spacing from 05:40, and three title/venue pairs like it were about **half of
  the 800 rows `events_near` will return for that viewport**. Since that RPC
  sorts every candidate in the box to take its top N, the grid is most of why
  central London exceeds the API role's ~3s statement timeout while Seattle and
  New York do not. `collapse_booking_grids` keeps ONE row per venue per day,
  opening when the first slot opens and closing when the last one closes, saying
  in its own description how many slots it stands for — which is a truer listing
  than any ten-minute slice of it. The threshold is per DAY at ONE venue for ONE
  title because that is the shape a grid has and a programme does not: 308 of
  321 distinct title/venue pairs in that box occurred exactly once, and the
  busiest genuine one ran four times in a WEEK.

- **AND THE HORIZON WAS THE OBVIOUS LEVER AND THE WRONG ONE.** `OpenActive`'s
  `DEFAULT_HORIZON_DAYS` is 120 where every sibling adapter uses 42, which reads
  like the cause of a bloated pool and is not: measured in the same box, every
  WEEK inside 42 days fills the 800-row cap, while 42-120 days holds 82, 87 and
  80 rows and beyond 120 holds one. Cutting it to 42 would have dropped ~250
  rows out of thousands, changed nothing, and lost four months of real
  programming. The density is near-term and genuine. Check which end of the
  distribution the volume is actually at before trimming the tail.

- **A PERFORMANCE MEASUREMENT ON THIS DATABASE IS A MEASUREMENT OF THE CACHE,
  and it will invert under you mid-session.** ../mapsee 0207 baked
  `enable_indexscan = off` into `events_near` on a forced-plan comparison
  showing 17,870ms -> 5,320ms, and 0208 reverted it after measuring 3.2s
  medians warm through the anon key. The same thing happened again while
  verifying the client-side retry for it: London ±0.08 measured 3.28s median
  with 4/6 raising 57014, then six runs later answered on the FIRST try in
  0.67-0.96s, because the earlier attempts had warmed shared_buffers. Both
  readings are true and neither is the answer on its own. Interleave the targets,
  say which cache state a number came from, and treat a single cold sample as
  evidence of nothing.

- **THE OCCURRENCE FEED IS USELESS WITHOUT THE SERIES FEED, so one transient
  500 cost 63,141 sessions.** An OpenActive `ScheduledSession` carries a date
  and a `superEvent` pointer and nothing else — the title, the place and the
  price live on the `SessionSeries` it names. `walk()` gave up on the FIRST
  exception, so when Better (GLL)'s series feed answered HTTP 500 on page four
  only 1,500 of its series were read, and **63,141 occurrences were then
  discarded for naming a series nobody had**. The log said so plainly and it
  reads like the publisher's fault; it was one retry. `_get_retrying` retries
  the transient statuses (408/425/429/5xx, timeouts, resets) and re-raises the
  permanent ones immediately — a 404 is the feed's answer, not a blip, and
  asking again is noise. Same distinction `mapsee_cleanup` draws between a
  statement timeout and an Envoy 503, one layer out.

- **AND THE PAGE CAP IS A CEILING, NOT A PLAN — the same lesson as the job
  timeout, one file over.** `MAX_PAGES = 250` stopped Better at 125,000
  ScheduledSession records and reported, correctly and loudly, that the feed
  was NOT read to the end and its newest sessions were missing. Reporting it is
  not reading it. `max_pages` is per-source now and Better has 700; the walk is
  affordable precisely because `collapse_booking_grids` takes what it returns
  from ~59,700 rows to ~9,200, so the expensive half is the HTTP and the cheap
  half is what survives.

- **EVERY SLOT IN THAT GRID WAS PUBLISHED TWICE, and only counting distinct
  start times showed it.** 255 live "Swim For Fitness" rows held 217 distinct
  instants — two lanes, or one series carried in two feeds. Above the grid
  threshold the collapse absorbs that either way, which is exactly why it went
  unnoticed; below it both rows survive and the list stutters. Same title, same
  point, same INSTANT is one listing, and a minute apart is two.

- **AN UPSERT CANNOT DELETE, so the run that landed the collapse made the map
  WORSE before it made it better.** `collapse_booking_grids` worked exactly as
  designed on 2026-08-29 — Better (GLL) 51,922 slot rows into 2,512 day rows —
  and central London went from 4/6 to **6/6** raising 57014, because the
  collapsed rows carry NEW fingerprints and were INSERTED beside the ~50k they
  replace. 112,408 rows added, none removed. The companion retirement script is
  not a footnote to a collapse, it is half of it:
  `mapsee_retire_openactive_slots.py`, `hidden_at` and dry by default, hiding a
  slot row ONLY where the collapsed day row for that title, venue and date is
  already in the table — because a publisher whose feed died mid-import must not
  have its sessions vanish on the strength of the run that failed.

- **THE GRID WAS THE VISIBLE TENTH, AND THE OTHER 88% WAS ONE PUBLISHER
  PUBLISHING HONESTLY.** Everyone Active wrote **98,871** of that run's 112,408
  rows and is not a grid at all: its per-day collapse found 714 rows in 94
  groups. It runs ~200 leisure centres and publishes ~56,000 distinct classes,
  one occurrence at a time. Sampling what `events_near` RETURNS (its top 800,
  distance-ranked, from a central-London box where Better's pools dominate) and
  inferring what is in the POOL is how that was missed for a whole cycle. The
  returned rows and the scanned rows are different populations; measure the one
  you are trying to shrink.

- **AND "THREE POINTS MAKE A PATTERN" FOLDED NOTHING.** `collapse_weekly_series`
  turns a class that recurs on the same weekday at the same hour into ONE
  standing row with a weekly `recurring_days` — the model 0156 already gives an
  imported restaurant. At a threshold of three it folded ZERO of Everyone
  Active's rows, because that publisher lists only a FORTNIGHT ahead: 38,562 of
  its 55,771 title/venue groups hold exactly two occurrences, 16,990 hold one,
  and no slot repeats three times. At two it takes it to 56,153. Two points is
  thin, so the pair must be CONSECUTIVE weeks — a standing row is rolled forward
  for ever by `roll_recurring_windows` and cleanup only deletes the past, so a
  two-part workshop read as an arrangement is a finished course pinned to the
  map permanently. The threshold that works is a fact about the publisher, not
  about patterns.

- **THE LICENCE LINE IS THE LAST LINE, AND THE SYNC CUT FROM THE END.**
  `_cap_prose` trims a description over `DESCRIPTION_MAX` (800) by taking the
  head — and four adapters close with a licence ATTRIBUTION: OpenActive's "via
  OpenActive, licensed CC-BY 4.0." and the "OpenStreetMap contributors (ODbL)."
  line the three OSM adapters carry. `mapsee_ingest_openactive._text` allows a
  900-character body ON ITS OWN, so any session near that overflowed 800 and
  lost the licence, silently, on a row that otherwise looked perfect — and
  `collapse_weekly_series` prepending "🔁 Runs weekly …" makes it likelier, not
  less. The adapter's own comment says "THE LICENCE CONDITION, not a footer",
  which was true when written and could not survive a cut it never saw. It cost
  more than the licence: `mapsee_retire_openactive_slots`,
  `mapsee_retire_perday_osm` and `mapsee_retire_thin_artwork` all identify their
  own rows BY that mark — "a row without it is not ours to judge" — so a
  truncated row could never be retired, audited or corrected by any of them. A
  SHORT (<=200 char) final paragraph now survives and the head is trimmed to make
  room; fixed in the sync rather than in four adapters, because the cut is what
  is wrong and there is one of it. Pinned end to end in
  `test_ingest_openactive.py`, through the real `to_event` and the real
  `_cap_prose` — the two halves live in different files and neither can be seen
  to be wrong on its own.

- **A STANDING ROW NEVER DIES, AND RUN #55 CREATED FORTY THOUSAND OF THEM.**
  `roll_recurring_windows` moves a row with `recurring_hours` forward for ever
  and `mapsee_cleanup` only deletes the PAST, so a standing row's window is
  always in the future and nothing can ever remove it. That is the intended
  model for a shop (0156) and a much weaker claim for a leisure class, which is
  seasonal: when a term ends the class simply stops appearing in the feed, and
  the row keeps advertising it. The scale changed the day `collapse_weekly_series`
  turned 77,346 of Everyone Active's occurrences into 38,227 standing rows —
  built, for that publisher, on a FORTNIGHT of evidence, because it lists two
  weeks ahead. It is the "recurring event with no end date projects pins
  forever" trap arriving through our own collapse rather than from a publisher.
  **The mechanism to fix it does not exist yet**: `events.updated_at` is on the
  table with `default now()`, no trigger bumps it, and `to_row` never writes it,
  so it means FIRST seen, not last. Wednesday's full refresh rewrites every row
  the feed still carries, so a last-seen stamp would make "gone from the feed"
  exactly detectable and a retirement sweep trivial — but writing the column with
  nothing reading it is one more value written and never read, so it is a
  decision, not a tidy-up. Nothing in the roller or in cleanup is a substitute.

- **A SECOND COLLAPSE NEEDS A SECOND RETIREMENT RULE, and the first one cannot
  see the second's orphans.** `mapsee_retire_openactive_slots.py` groups by
  title, venue and DAY and needs six rows in a group, which is the shape a
  booking grid has. A weekly fold's orphans are one occurrence per week, each
  alone in its own day — so every one of them fails that test and is never even
  considered. Run #55 folded 77,346 of Everyone Active's occurrences into 38,227
  standing rows and INSERTED them beside the originals, exactly as the grid
  collapse had a cycle earlier, and the retirement reported nothing to do.
  `weekly_superseded` is the second rule: same safety rule (hide only where the
  replacement is already in the table), keyed on title and venue with no day in
  it, and matched on the row's LOCAL weekday and start time against the standing
  row's own `recurring_hours` — because the fold deliberately leaves the
  bank-holiday special dated and still writes it every run. The two compose: a
  grid day row folded into a standing row is superseded in turn.

- **AND THE ESCAPE HATCH HAD NEVER WORKED.** That script's whole licence to
  write `hidden_at` rather than DELETE is "hiding is reversible", and its scan
  hard-coded `hidden_at=is.null` — so after a successful `--apply` the reverse
  pass could not see a single row it had hidden, reported zero and wrote
  nothing. The test agreed with it, because the stub answered every URL with the
  same fixture and never looked at the query it was asked; a test that drives a
  real query has to ANSWER the query, not the call. The reverse direction needs
  no hidden filter at all rather than the opposite one, because the keepers stay
  VISIBLE while the rows they replaced are hidden and the judgement needs both
  halves; the direction of the write is then decided per row on its own
  `hidden_at`, which is also what stops a second `--apply` restamping.
