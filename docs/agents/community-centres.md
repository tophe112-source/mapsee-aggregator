# Community-centre schedules

> Part of mapsee-aggregator's agent notes — see `AGENTS.md` for the map. Read this file when the bug is about: what is on at a community centre, a drop-in timetable (lane swim, pickleball, open gym, seniors' socials, family drop-ins), a recreation-booking platform, Toronto / PerfectMind / Linked Events rows, or where a centre's schedule lives
> Every note below was measured before it was written; keep the numbers when you edit.

- **A DROP-IN WITH A FEE IS A SCHEDULE; A COURSE IS A CATALOGUE.** The owner's
  request of 2026-10-03 was "what is going on at the local community place", and
  most of it costs a few dollars: of Toronto's 28,155 drop-in sessions (facility
  slots aside) 50.0% are free and 41.4% charge $4.39-$10.64 at the door; of
  51,960 PerfectMind drop-ins over 15 cities, 115 (0.2%) cost over $20. The 2026-09-27 review
  that refused "paid registration catalogues" was refusing COURSES - numbered
  sessions you sign up for weeks ahead (Cranberry 131 of 134, Amilia 77%,
  Calgary's registered programmes 98% paid of 15,432). So the line is
  registration, not price: a session anyone can turn up to is in, with its fee
  stated; a registration-only course, a private booking or rental, childminding,
  a card-holder-only session and a closure are out. "Free" is written only when
  the source says so, in words 0227's strict `free` reads ("Admission: free",
  "Free drop-in"); a bare "Free." does not match it.

- **THE OSM WALK FINDS A CENTRE ONLY WHEN OSM KNOWS ITS WEBSITE, AND IN NORTH
  AMERICA THE TIMETABLE IS NOT ON THE WEBSITE.** Audit of 2026-10-03: 676 of
  3,748 configured sources were about community centres, but the US had 330 of
  them and only 2 were OSM centres - the rest are town category calendars. 143
  of the 261 swept metros had no community-centre source at all (12 of 14 UK, 11
  of 13 French, 39 of 82 US). A sample of 117 centres the ledger calls
  no-calendar found ActiveNet linked on 14 (Canada 9 of 25), Xplor 2, RecDesk 1,
  WebTrac 1, and 99 of 117 with a plain HTML programme page. Those links say
  "Register" or "Drop-in schedule", which CAL_LINK_RX never read, so
  `REC_BOOKING_HOSTS` in `catalog_discover_osm.py` makes the host the signal
  and files them `offsite:<host>`. The platforms themselves are measured in
  `platforms-probed.md`: PerfectMind is the one that is permitted and drop-in.

- **CITY OPEN DATA ALMOST NEVER PUBLISHES A CENTRE'S TIMETABLE, AND THE FEW THAT
  DO ARE THE BIGGEST SINGLE SOURCES.** Swept 2026-10-03: 65 terms over the US
  Socrata catalogue (3,067 datasets), every dataset on 50+ US Socrata domains
  (17,838), 13 CKAN portals, 38 ArcGIS hubs, and the Canadian, European, APAC
  and Latin American portals by hand. Dated community-centre schedules exist in
  Toronto (31,734 drop-in sessions in 90 days at 176 locations), Helsinki/Espoo
  (Linked Events), Montreal (13,873 occurrences, 70.3% needing registration),
  Philadelphia (CARTO, 7,077 weekly occurrences at 55 sites), Madrid (1,388
  events, 594 at 93 municipal community places), Barcelona (4,141, 1,305 within
  40 m of a centre civic, every clock time a 03:00/12:00 placeholder) and Hong
  Kong (LCSD: 336 walk-in programmes, 895 cultural events). Everything else is a
  facility list, an archive (Ottawa and Mississauga end in 2022) or a course
  catalogue (Calgary 2.0% free of 15,432; Winnipeg 72% lessons). NYC's
  "program" datasets are past-only attendance logs (18,821 aquatics rows, 0
  after 2026-10-03).

- **CHICAGO PARK DISTRICT WAS 16 DAYS OF A 90-DAY WINDOW AND HALF COURSES.** The
  configured `tn7v-6rnw` read 500 rows ordered by start, which reached
  2026-10-19 of a window published to 2026-12-31, and took every activity type:
  795 Instruction and 155 Camp rows in 90 days. Since 2026-10-04 it keeps
  `activity_type` Open (drop-in, 1,068) and the untyped Events (408): 1,464 rows,
  read whole at limit 3,000. Its Programme rows have no description worth the
  name ("October 3, 2026 to October 3, 2026"), so the opendata adapter's new
  `map.fee` writes the price: 288 of 1,186 stored rows say "Admission: free.".
  277 same-day repeats (two freestyle-ice hours at McFetridge) still merge on
  title|date|venue and keep one time - the opendata adapter does not collapse.

- **A SEED LIST OF CENTRES THE MAP CANNOT SEE YIELDS ABOUT ONE FEED IN TEN, AND
  JCCs ARE THE BEST OF IT.** 2026-10-03, find_calendar under the production UA:
  US 153 probed (JCC Association directory, settlement houses, 27 thin metros) ->
  52 with a calendar platform -> 19 with rows -> 12 kept, 6 of them JCCs (12 of
  39 JCCs run The Events Calendar); senior centres 0 of 16, settlement houses 2 of
  47, LGBTQ centres 2 of 23. Canada 155 of 455 directory hosts (Calgary
  associations, Edmonton leagues, Winnipeg centres) -> 53 calendars, but 15
  Google embeds (robots.txt refuses the export; the Calendar API needs
  GOOGLE_CALENDAR_API_KEY), 14 Wix sites with 0 event blocks and 28 empty -> 5
  kept. Elsewhere 157 probed -> 30 platforms -> 6 kept; none of 37 Melbourne
  neighbourhood houses publishes more than a term PDF. 302 Canadian hosts and
  153 JCCs were left unprobed (`seeds_unprobed.json`, `jcc_unprobed.json` in the
  research scratch).

- **A BOM IN FRONT OF `Disallow: /` READ AS ALLOW-ALL, AND TODAY IT CHANGES NO
  CONFIGURED VERDICT.** `robots_txt.parse` kept U+FEFF glued to the first field
  name (`str.strip()` does not remove it), so FrontDesk Suite's BOM + `User-agent:
  *` + `Disallow: /` parsed as no groups. Fixed 2026-10-04. Fetched every
  configured origin's robots.txt once to size it: 2 of 2,759 serve a BOM
  (cal.laget.se, ville-hoenheim.fr) and neither file refuses any of their 3
  configured requests, so nothing configured moves; the fix is for the next one.

- **THE PINNED COMMUNITY-CENTRE WALK IS HALF DONE.** 13 runs (2026-09-22..10-03)
  moved its cursor 0 -> 121 of 261 and read 11,797 centre and library venues:
  5,086 known-dead, 3,610 no-calendar, 1,235 unreachable, 1,496 HTTP errors, 323
  bot challenges, 25 offsite; 396 candidates, 203 merged (median 12 future
  rows). Throughput fell from 31 metros a run to 3-7. 140 metros remain, 82 of
  them in the US.
