# US state fairs as events

> Part of mapsee-aggregator's agent notes — see `AGENTS.md` for the map. Read this file when the bug is about: marketing-copy dates, dates never written down, a year on the wrong side, a town that is not a venue, Photon and fair addresses, a one-day festival dated only in prose, a name rule on an organiser's homepage, an entity dash, robots.txt and Crawl-delay on a fair's site.
> Every note below was measured before it was written; keep the numbers when you edit.

- **A STATE FAIR'S WEBSITE IS MARKETING COPY, SO ITS DATES ARE THE HEADLINE AND
  SO IS EVERYTHING THAT LOOKS LIKE THEM.** Measured 2026-09-04: of 47 reachable
  US state fair sites, SIX have a machine-readable calendar and those calendars
  are the fairgrounds' year-round bookings — a circus, a gun show — not the fair.
  34 fingerprint as nothing. Meanwhile 969 future events in the catalog carry
  "fair" in the title and nearly all are career fairs, Fairleigh Dickinson and
  the Fairfield Stags; the real ones arrive through Ticketmaster as per-DAY
  admission rows, never as "the Iowa State Fair runs 13–23 August". So the fair
  itself is scraped, and four rules decide whether a date range on a fair's page
  IS the fair — every one of them a false positive it produced first:
  a sale window (`Advance Tickets on Sale Now from September 1 – 22, 2026`), a
  different event with real dates (`World's Championship Horse Show August
  22-29`), anything over 21 days (the longest real one is The Big E at 17), and
  anything already over. The disqualifying words are checked within 45
  characters, not 90: at 90 a fair's own hero sits within reach of its
  navigation, and Colorado's correct dates were thrown away for a "Deals" menu
  item and Kansas's for a "Commercial Vendor application".

- **THE DATES ARE NEVER WRITTEN DOWN, AND THAT IS THE POINT.** A fair moves by
  up to a fortnight year to year, so a curated date list across 48 rows is
  accurate for one season and then wrong in a way nothing can see. Scraping every
  run means a fair that has not announced next year contributes nothing and
  starts contributing the day it does. 27 of 48 publish a usable future date
  today; most of the rest closed last week.

- **A YEAR CAN SIT ON THE WRONG SIDE OF ITS DATES.** iowastatefair.org publishes
  a table of future dates with the year LEADING each row — `YEAR FAIR DATES 2027
  Aug 12-22 2028 Aug 10-20` — which reads left to right as the 2027 fair wearing
  2028's label, parses perfectly, and put the Iowa State Fair a year late. The
  guard is NOT to prefer the leading year (a page can say "Thank you for 2026!
  August 5-15, 2027" and mean the trailing one); it is to notice two years are in
  play and refuse. One fair contributing nothing beats one contributing a wrong
  date. Any adapter reading dates out of prose needs this shape of refusal.

- **A TOWN IS NOT A VENUE, AND THE DIFFERENCE IS A `venue` BLOCK.** Every
  candidate `catalog_discover_osm` emits carries the surveyed point of the one
  place whose calendar it is. On a town-wide calendar that block is a lie with
  coordinates on it: it would pin every event that arrived without an address
  onto the town hall. `catalog_discover_civic` emits city-wide DEFAULTS instead
  (`geocode_suffix` for ics, `default_city/region/country` for tribe and
  mylisting) and proposes NOTHING for the adapters that have no such mechanism —
  `why_no_candidate` returns `no-citywide-shape(jsonld)` and the gap is a
  counted to-do rather than a silent wrong pin. Visit Issaquah is the shape:
  510 events across a zoo, a wine bar, a hatchery and a theatre, none of them
  separately configured, and no single point anywhere in it.

- **PHOTON CANNOT READ "VENUE - STREET CITY ST ZIP", WHICH IS HOW EVERY
  CIVICPLUS SITE WRITES EVERY LOCATION.** Measured:
  `Elmer W. Oliver Nature Park - 1650 Matlock Road  Mansfield TX 76063` returns
  **None**; `1650 Matlock Road, Mansfield TX 76063` and
  `Elmer W. Oliver Nature Park, Mansfield TX` both resolve. Four newly found
  city feeds ingested 0 of 34, 0 of 6, 0 of 3 and 1 of 16 events while carrying
  a full street address on every one — and the adapter's log said "no
  LOCATION/GEO", which was the one thing not wrong with them. `location_attempts`
  in `mapsee_ingest_ics.py` tries the whole string, then the address half (found
  by its house number, not by position), then the venue. Those four now ingest
  100%, and the batch of fourteen went 194 -> 328 events. Any adapter geocoding
  a string somebody else composed needs this shape of retry, not a better query.

- **A ONE-DAY FESTIVAL'S DATE IS ONE SENTENCE, AND A SINGLE DAY IS BELIEVED ONLY
  NEXT TO THE EVENT'S NAME, SO IT HAS ITS OWN ADAPTER (2026-11-08).** Short Run
  Comix Festival (shortrun.org) publishes its date in exactly one place, its
  homepage hero: `<h1>Short Run Comix Festival</h1><p>Our 14th annual festival
  is coming up <strong>Saturday, November 7th, 2026</strong> at Seattle Design
  Center. 11am &#8211; 6 pm. FREE!</p>`. It has no Event JSON-LD, no iCal and no
  calendar plugin, so every verifier refused it, and the fairs adapter (ranges
  only) answers "no date range on the page". Widening the fairs adapter was
  measured and rejected: on the 45 fair homepages read 2026-11-07, letting
  single days in gave 3 false fairs (Maryland's "Sun, Nov 08", Washington's
  "Saturday, Nov 7", Wyoming's "Board Meeting Notice Nov. 10, 2026"), and the
  rule that makes a single day safe, the event's name near it, loses 5 correct
  fairs (CO, NJ, WV, ID, NE). So `mapsee_ingest_annual.py` imports the fairs'
  range parser, their refusals and `Polite`, and adds single days for
  `annual_sources.json` entries of shape `day` only, where `validate_site`
  refuses an entry without `name_rx`: the name within `NAME_BEFORE` (90)
  characters before the date, never across another date, or in the date's own
  paragraph. The row (source `annual`, id `short-run-2026`) carries the hero
  paragraph word for word as its description, and
  ../mapsee/tools/measure_deals.py (the Python twin of migration 0227's
  `event_offers()`) tags it `free` while the title alone gets nothing: the
  adapter never writes "free" itself. The clock is stamped with the entry's
  timezone, 2026-11-07T19:00:00Z to 2026-11-08T02:00:00Z, because the sync
  converts a naive clock from the event's coordinates and an entry without them
  would read 11:00 as 11:00 UTC. The date is never stored. Live at 18:01 PST on
  11-07 it kept 0 rows ("past: the 2026-11-07 edition ended at 18:00 where it is
  held") in 11.6 s, nearly all of it the `Crawl-delay: 10` that shortrun.org's
  robots.txt asks before the homepage. `test_ingest_annual.py` (122 cases) pins
  the hero, the clock, the UTC stamps and the description. UNMEASURED: how the
  homepage reads once the 2027 edition is announced, and any second annual site.

- **ON AN ORGANISER'S HOMEPAGE THE NAME RULE MUST NAME THE EVENT, AND A STATED
  YEAR OUTRANKS NEARNESS TO IT (2026-11-08).** Among the single days that
  survive the refusals, `pick` ranks a stated year first, then distance to
  `name_rx`, then the earliest. Short Run's `name_rx` is `short\s+run\s+comix`,
  not `short\s+run`: every post on an organiser's homepage says the organiser's
  name, and with `short\s+run` each dated post inserted into the saved homepage
  beat the hero (a Marathon Art Show pre-party gave 11-06, a Spring Market
  2027-05-15, a Sunday panel 11-08, a Trailer Blaze residency
  2027-04-04..04-18); with the festival's name all four give the hero. That
  homepage carries 48 date candidates and 20 WordPress bylines,
  `Posted on <date> <edit date>`, whose edit date is today on the day somebody
  fixes a typo. A daily sweep of it over 2026-01-01..11-07, re-run on the final
  code 2026-11-08: with `short\s+run\s+comix` the hero (11:00-18:00, FREE!) on
  all 311 days with the byline rule on AND off, 10-29 and 11-06 included (a
  byline's edit date is today on both), and nothing on 11-08; with `short\s+run`
  and the byline rule off, a "June 30, 2026" byline on the 181 days before 07-01
  (days a real page could not show, before that post existed); with neither a
  name nor the byline rule, a byline on 310 of the 311 days, the hero only on
  11-07 itself. A name never vouches across another date: an excerpt ending
  "...to Short Run on Nov. 7th! Read more…" ends its name 35 characters before
  the date of "Posted on July 10, 2026", and with that rule removed (same sweep,
  `short\s+run`, byline rule off) the July 10 byline took 07-01..07-10 and an
  "August 3, 2026" one 24 more days. Year before distance: the hero is 48
  characters after "Short Run Comix" (its `<h1>`) and has a year; an older
  post's "Saturday, Nov. 7th" is 16 after a "Short Run Comix Festival" category
  link and has none (54 against 4 with `short\s+run`), and only the hero has the
  clock and the FREE after it. `test_ingest_annual.py` runs the byline cases
  both ways and pins each inserted post.

- **WORDPRESS WRITES THE DASH AS `&#8211;`, AND THE FAIRS ADAPTER MISSED IDAHO
  AND NEBRASKA FOR IT (2026-11-07).** `text_of` stripped tags but never decoded
  entities, and `RANGE_RX`'s `[-–—]` does not match the literal `&#8211;`, so
  idahofair.com's `August 13 &#8211; 29, 2027` and statefair.org's `August 27
  &#8211; September 6, 2027` both read as "no date range on the page". 6 of the
  45 homepages carry an entity dash after a day number (CT ID MN NE NH WY).
  Decoding after the tags are stripped (so a literal `&lt;b&gt;` in prose stays
  text) and before whitespace is collapsed (so `&nbsp;` collapses too) took the
  homepages read 2026-11-07 from 16 dated fairs to 18 and moved none of the
  other 16 (re-run on the saved pages 2026-11-08: only ID and NE changed); the
  live run on 2026-11-08 confirms both. `mapsee_ingest_annual.page_text` decodes
  the same way, which is how Short Run's `11am &#8211; 6 pm` reads as
  11:00-18:00. `test_ingest_fairs.py` proves the decode through `F.text_of`
  itself, `&nbsp;` and `&ndash;` included. UNMEASURED: Idaho's 17-day
  `August 13 - 29, 2027` has not been checked against a second source; it is
  long for a county fair, and the parser cannot tell a run from a season under
  `MAX_FAIR_DAYS` (21).

- **THE FAIRS ADAPTER NOW READS ROBOTS.TXT AND PAYS CRAWL-DELAY, AND THE STEP
  TAKES 9 MINUTES INSTEAD OF 3 (2026-11-08).** Until 2026-11-08 it fetched 48
  homepages and up to six hinted pages each with no robots.txt read at all,
  1.0 s between fairs and 0.4 s between one fair's pages. Read 2026-11-07:
  azstatefair.com and oregonstatefair.org answer /robots.txt with a bot
  challenge, castatefair.org's cannot be reached (RFC 9309: assume everything is
  disallowed), durhamfair.com and sdstatefair.com ask `Crawl-delay: 30`, and
  floridastatefair.com and kystatefair.org ask 10. `mapsee_ingest_fairs.Polite`
  is now the one door, and `mapsee_ingest_annual.py` uses the same class. It
  reads robots.txt per origin through `robots_txt.Robots` and asks it about
  every URL, each redirect hop included. It keeps max(`min_interval`,
  Crawl-delay) between two requests to one host, the robots.txt read counting as
  the first (`min_interval` is the fairs' `--delay`, 1.0 s by default, and 1.0 s
  in the annual adapter). A 403, a 429 or a challenge page ends that host for
  the run with no retry, and so does a 403 or a 429 on /robots.txt itself, which
  `robots_txt` alone reads as "allow all" like any 4xx (the 48 fair origins and
  shortrun.org answered 39 ok, 2 html, 5 http404 that stay allow-all, 2
  challenge, 1 unreachable and no plain 403 or 429, so no fair is lost). A
  challenge is looked for in the first `CHALLENGE_HEAD` (6,000) characters only:
  Cloudflare injects `/cdn-cgi/challenge-platform/` at the END of pages it
  serves normally, on 4 of the 45 homepages (FL NJ TX WY) within 800 characters
  of `</html>` and on none in the first 8,192. No wait crosses the run's
  deadline (`--max-minutes` 45 inside the fairs step's 60; `--max-seconds` 180
  inside the annual step's 5), and out of time is a reason that
  `startswith("run deadline")` in both adapters, never a substring: a refused
  page at /entry-deadlines/ is quoted in its own reason and used to end the walk
  (`test_ingest_annual.py`, mutant caught). Live 2026-11-08: 535 s for the 48,
  164 page requests, 428 s of it waiting; Arizona, Oregon and California were
  not read; 25 fairs were dated. The step took 3 min 01 s and 3 min 07 s in CI
  on 11-06 and 11-07. A known wrong answer this did not cause (old and new
  parsing agree): MontanaFair's site is MetraPark's, and its buy-tickets page's
  "PBR Velocity Tour Apr 16 - Apr 17, 2027" is read as the fair.
  `test_ingest_fairs.py` pins the robots, pace and refusal cases. UNMEASURED:
  the runtime on GitHub's runner (this was measured through a sandbox proxy),
  and whether California's robots.txt is down for a day or for good.
