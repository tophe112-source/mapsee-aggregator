# Geocoding, addresses and coordinates

> Part of mapsee-aggregator's agent notes — see `AGENTS.md` for the map. Read this file when the bug is about: the Census geocoder, Photon, a source that hands wrong coordinates, the city in the address, a geocoder that answers everything.
> Every note below was measured before it was written; keep the numbers when you edit.

- **A source that HANDS you coordinates can hand you the wrong ones, and nothing
  downstream will catch it.** Two live examples, both now covered by
  `test_ingest_places.py`. Squarespace ships a default map pin at
  `40.7207559,-74.0007613` — lower Manhattan — for every event whose location
  was never filled in; that was 17 of Volunteer Park Trust's 22 upcoming events,
  all of them in Seattle. The defence is not a coordinate blocklist but the rule
  that *a location with no address text is not a location*, falling back to the
  config's `venue` block — which is now the ONLY way a Squarespace event gets
  placed, because the adapter reads the page rather than `?format=json` and the
  page carries no coordinates at all. Luma reports a US postal code only inside
  `full_address`, where a bare five-digit search finds the STREET NUMBER
  ("15600 NE 8th St, Bellevue, WA 98007" → 15600, a real ZIP in Pennsylvania).
  Well-formed, plausible, wrong is the worst failure this pipeline produces.

- **A comma-counting address parser assumes a street.** Reading Google's
  "street, city, Region ZIP, Country" right-to-left by POSITION works until an
  organiser leaves the street blank: "Bainbridge Island, Washington 98110,
  United States" then lands as `city="Washington 98110"`, `region="United
  States"` — which is what six live events did on the first run of
  `mapsee_ingest_mylisting.py`. Anchor on SHAPE instead: locate the part that
  IS a region-plus-postal, and read outward from it. The residual ambiguity —
  one part left, street or city? — is resolved toward CITY, because a city name
  in `address` gets geocoded as a street and pins the event somewhere real and
  wrong, while a missing street just leaves the coordinates to place it.

- **The only geocoder is US Census, so OUTSIDE the US a source must bring its
  own coordinates.** A row with no lat/lon is dropped at the sync, and nothing
  upstream says so: `mapsee_ingest_tribe` reported "kept 43 events" for Calgary
  Buddhist Temple and every one of them was coordless, so "ingested" and "put
  zero on the map" read identically. The Events Calendar only carries
  `venue.geo_lat` when the organiser filled the map fields in, which outside the
  US is often never. The config's `venue` block is the fix — FILLING gaps, never
  overriding a real value — and `discover osm` already ships one on every
  candidate, because the surveyed point is what OSM was queried for in the first
  place. 35 of 51 tribe sources now carry one; the 20 Canadian ones merged on
  2026-08-19 would otherwise all have ingested into nothing.

- **The city is not a street, and putting it in `address` invites the geocoder
  to move the pin.** The Overpass loader glued street and city into one
  `address` and set no `city` at all, so every OSM market reached the database
  with `locality` NULL and `street_address` "Berlin" — and most OSM
  marketplaces have no `addr:street`, so "Berlin" was the whole of it.
  `_addr_parts` treats `address` as a street and hands it to the US Census batch
  geocoder, so a SURVEYED OSM point was offered up to be overwritten by a lookup
  of a bare city name. It survived only because Census returns nothing for
  "Berlin"/"Paris"/"Hamburg"; a US city of the same name and it would not. The
  loader now keeps them apart and sets `coords_exact`, which is what
  `mapsee_ingest_osm_food` had all along.

- **A GEOCODER THAT ANSWERS EVERY QUESTION IS NOT ONE THAT KNOWS EVERY ANSWER,
  and the defence is the CONFIG rather than the geocoder.** The obvious unblock
  for every non-US source is to lift `make_location_geocoder` out of
  `mapsee_ingest_ics.py` into the shared `geocode_venue()` stub — Photon, OSM,
  global, already paced, cached and budgeted here. Measured 2026-08-30 against
  43 Mapas Culturais venues in Ceará that publish a surveyed point AND a street
  address, so the true answer is known: on the full address Photon is good,
  **median error 64m, 24 of 43 inside 100m**. What it never does is say "I do
  not know". Every query is answered, so a thin address degrades silently to a
  same-named street in another state or to a centroid — **7 of 43 over 10km
  out, the worst 603km away in Maranhão**, and one query that reduced to
  "Brazil" was answered with the country centroid 1,779km from the venue.
  Validating the ANSWER does not rescue it: rejecting vague result types
  (`place/city`, `place/country`) caught 1 of 43, and cross-checking the
  returned city and state against the asked-for ones threw away 30 of 43 — 70%
  of the supply — while STILL keeping a 240km error, because the addresses that
  produce bad answers are precisely the ones with no city in them to check
  against. This repo already solves it from the other end and always has: all
  **319** `ics_sources.json` entries carry a `geocode_suffix` (", Seattle, WA")
  — 100%, which is a requirement and not a coincidence — and `expect_region`
  does the same job for Luma. So a shared geocoder must take the expected area
  as a REQUIRED argument and refuse a result outside it; `geocode_venue`'s
  address-only signature is the trap and its docstring now says so.

- **A LICENCE THAT SAYS YES IS THE EASY HALF, AND THE GEOCODER IS THE HARD
  ONE.** Humanitix is the platform 20 of the 121 `offsite:` venues in the
  ledger use — the largest gap with no adapter — and it says yes in writing:
  `Allow: /` with Content-Signal `search=yes, ai-train=no, use=reference`, which
  is exactly what this repo does and exactly what it does not. Its pages carry
  well-formed schema.org `Event` with offset-bearing instants, a structured
  `PostalAddress` and an `offers` block naming the free ones. It is still a
  decline, on three things that only matter together: there are NO coordinates
  anywhere in the public surface (the one `latLng` on a place page is the city
  centroid, the pin `_addr_parts` exists to refuse), a place page
  server-renders four featured events and loads the rest client-side, and the
  API is organiser-scoped. The first is OURS — US Census is the only geocoder
  here, so a well-addressed Australian event ingests and places nothing, which
  is Calgary Buddhist Temple at platform scale. Recorded in `OFFSITE_HOSTS`
  rather than as a config, because there is nothing to configure: a non-US
  geocoder is the unlock, and until there is one this is not a to-do.
  **`offsite:<host>` is a routing signal, not a failure** — that counter is the
  only measurement this repo has of what venues worldwide actually use.

- **A LOCATION THAT SAYS "NOT HERE" MUST NOT BE GEOCODED, NOR PINNED TO THE
  VENUE, and Communico hides the word behind a dash.** Communico
  (`*.libnet.info`) writes LOCATION as "Branch - Room", so a programme with no
  room reads "Offsite -", "Bookmobile -", "Outreach -" or "External -".
  LibraryMarket files online sessions under the LOCATION "US". Over 70,904 ics
  rows from the same 837-feed corpus, 393 carried one of these, and the
  placeholder rule of the same day matched none of them, because it only knew
  bare words. Each went to Photon with the feed's suffix ("US, Kaysville, UT",
  "Bookmobile -, ...") and came back as a real-looking pin. The biggest were
  Clearview's 204 bookmobile stops, 71 "US" rows (59 say online or virtual in
  the title) and 64 "Offsite -". `PLACEHOLDER_LOC_RX` now accepts trailing
  separators, and `ELSEWHERE_LOC_RX` marks the subset that names another place
  or no place at all: offsite, outreach, external, bookmobile, various or
  multiple locations, and a bare country. Those never take the source's
  `venue` fallback either, because "Offsite" at a library means not at the
  library. Only "TBD" and "See description" may still fall back. Look-alikes
  pinned as places: "US Bank Stadium", "Canada Water Library", "Outreach
  Center, 5 Oak St", "Main Library - Room 2".

- **THE EVENTS CALENDAR'S iCAL LOCATION HAS NO DASH, so the adapter sent
  Photon one string and gave up.** `location_attempts` split only on " - "
  (CivicPlus's "Venue - street"). The Events Calendar writes "Venue, street,
  city, ST zip, Country". The 837-feed corpus held 125 such distinct
  LOCATIONs, on 527 rows from 38 feeds. Measured on 40 of them (2026-09-28,
  Photon, one request a second, a hit counted only in the LOCATION's own
  town or postcode):
  - the whole string placed 21;
  - the street-onwards remainder placed 33, with 0 wrong, 2 of them to the
    house number in a town Photon calls "County of Brant";
  - the venue name alone placed 28 but put 4 in the wrong town, and 2 of the 5
    it was the last hope for.

  So the remainder is now the second attempt, and the venue name is not
  added. Because a hit is cached under the feed's own string, a LOCATION
  that needs the second attempt costs two lookups once and none after. A
  miss is not cached, so today each one costs a lookup on every run.
  The 2026-09-28 reviewers dropped Green Umbrella's volunteer view on this.
  12 of its 16 distinct LOCATIONs missed as sent, and 9 of those 12 resolved
  when the address half was sent alone, some only to a street centroid, so
  it can be proposed again. GTM's single LOCATION misses either way.

- **ON A SMALL TOWN'S CALENDAR, PHOTON ANSWERS FROM ANYWHERE, AND THE CENSUS PASS CAN MAKE A GOOD PIN WORSE.** Measured on four Revize towns, 2026-10-04/05:
  - Far answers: 'Downtown Olympia' gave a memorial in Tacoma (40 km away), 'Gymnasium, Pacific, WA' gave Pacific County's centroid (140 km), and a bare '2600 East Bay Drive NE' (NE read as Nebraska) gave Massapequa NY. A 25 km radius from each site's `centre` unplaces such answers.
  - Answers inside the town: '9317 Center St' (the cemetery tours) gave a roofer 2.5 km away, and the Census geocoder has no match to correct it. 'Downtown Manassas' gave a post office. Photon placed the city library on its building, but the sync's Census pass moved it 370 m to an address interpolation, because coords_exact was False.
  - In-town errors are fixed only by venue-book pins (OSM id, coords_exact true). The shared ics geocoder returns a point, not Photon's properties, so a street-mismatch check is not possible there.

- **A CITY'S OWN COORDINATE CAN SIT ON THE SAME STREET NAME IN ANOTHER BOROUGH.** Montreal's Loisirs dataset (2026-10-05, 176 sites, 153 distinct points) was checked against each site's postal area:
  - **Misplaced:** "504 5e Avenue, Verdun, H4G" is pinned in Pointe-aux-Trembles, 20 km off. "35 rue Sainte-Anne, Sainte-Geneviève, H9H" is 34 km off, beside it.
  - Five Lachine (H8S) sites land 8-27 km off, on downtown's Notre-Dame, Sherbrooke and Saint-Antoine, on Verdun's 5e Avenue, and on the east end's 19e Avenue. One Montréal-Nord site is 11 km off on the wrong end of boulevard Gouin.
  - All of them are inside the island's box. Two were written: 68 rows, 44 of them free Zumba and basketball, marked coords_exact so the sync never re-geocoded them.
  - **The check:** compare a point with the distinct points of other sites sharing its FSA, and refuse it when strictly more of them lie beyond 5 km than within. Correct sites sit a median 0.8 km from their peers (p90 1.6, max 3.1); the 8 wrong ones sit 8-34 km off.
  - **Limit:** 17 points share their postal area with no other site, so this check cannot reach them.

- **RUNSIGNUP PUTS THE PLACE IN `street` AND THE NUMBER IN `street2`, AND
  CENSUS CANNOT PLACE A PARK'S NAME (2026-10-11).** One race in four writes
  "FDR Park (at the boathouse)" over "1500 Pattison Avenue", and the sync
  dropped the unmatched row. Ohio through `build_rows` with a real Census batch:
  110 of 415 rows had this shape and 2 were placed; sent street2, 84 of 99
  (p2p's 1,830-race sample: 8 of 388 as sent, 315 of 388 with street2). Placed
  upcoming rows went 225 -> 307 and none was lost. `_geocode_line` takes street2
  only when street is not numbered and street2 is; the label and fingerprint
  still come from `street`, so the 383 rows already stored kept their identity.
  28 of 244 venue-only lines carry the number inside the name ("Wicker Memorial
  Park 8554 Indianapolis Blvd") and are not split. `python test_ingest_runsignup.py`.

- **THE CENSUS BATCH CAN MATCH THE SAME STREET NAME IN THE NEXT TOWN, AND A
  PUBLISHER'S PIN CAN DISAGREE WITH ITS OWN ADDRESS (OC Parks, 2026-10-11).**
  batch_geocode matched 14 of 21 streets: 28373 Alicia Parkway (21 rows in 90
  days) got nothing, and "10 Phillips St, Laguna Beach" matched Phillips Circle
  in Laguna Niguel, 10.1 km away; the matches landed 59 to 1,041 m from OC
  Parks' own park pins. OC Parks' pin for Laguna Coast is a staging area 4.6 km
  from the park's address. The drupal_fullcalendar street book holds 13 OC
  Parks pins and 4 OSM pins. `python test_ingest_drupal_fullcalendar.py`.

- **A ROOM NAME CAN CARRY A REAL BRANCH ADDRESS THAT THE ROW DID NOT.** The
  public Mapsee preview for New Orleans Public Library's Oct 13, 2026 Storytime
  had a Norman Mayer Library - Children's Area name and source GEO, but all four
  address columns were NULL. The library's own calendar entry confirms the same
  event at 3001 Gentilly Boulevard, New Orleans, LA 70122:
  https://neworleans.libnet.info/event/17285306. Add that address with the
  existing exact location_overrides rule for this branch-room LOCATION. In the
  3-event ICS fixture, both matching Norman Mayer rows get the address, the
  Algiers row keeps its own GEO and no address, and the exact override uses the
  known Norman Mayer branch pin. The Oct 13 wall clock remains 10:30–11:30
  America/Chicago (15:30–16:30 UTC). The source UID and the
  title/date/original-LOCATION fingerprint stay intact. This is one confirmed
  room string, not a rule for all 15 branches; do not infer an address for a
  different branch or room.

- **ABROAD, PHOTON ANSWERS A BARE SUBURB OR A PROVINCE-CITY WITH ANOTHER CITY,
  AND A VENUE BLOCK WITH ALIASES IS THE FIX.** 2026-10-23: '53 Wellington
  Street, Hamilton East, Hamilton 3216, New Zealand' came back in Wellington
  and '43 Church Road, Pukete, Hamilton 3200' in Vancouver (2 of 6 rows); in
  Córdoba, a province and a city, 'Maipú 671' landed about 180 km away (4 of 17
  rows more than 10 km off); Barranquilla venue names put 5 events on a
  hardware shop. Strings that name a hall and a room before the street
  ('Dom kulture ..., dvorana, Mestni trg 4') come back empty. A venue block
  with `aliases` and a `street_hint` placed 14 of 14 rows in Slovenske Konjice
  and 16 in Kutno without Photon.

- **A 9,000-address Census request outlasted its 180 s timeout, and a
  different 9,000 pins flipped every day.** Meetup's US leg lost its first
  chunk every run 2026-10-21..23 (10-21: 0 matched in 180.3 s) while a
  5,471-address chunk took 76.1 s; civic's 2,073 ran at 22 ms an address. A
  set's order changes per process, so the dead chunk's rows moved between
  Census's point and the source's: 9,424 (US leg) and 13,203 (final) Meetup
  rows differed on lat/lon on 10-23, 28,926 extra_sources rows on 10-22.
  `batch_geocode` sends 2,500 at a time, retries a failed chunk once as two
  halves (and goes on at the half size when both answer), and stops once
  failed requests would pass `CENSUS_FAIL_BUDGET` = 360 s. Without the budget,
  a fake that timed out at 2,500 and answered one half of 1,250 cost 18
  requests and ~1,780 s for 14,500 addresses; with it, 3 requests and ~298 s.
  Every answer is kept in `census_cache.json` (a match a year, a miss 30
  days), so an address whose chunk fails keeps its last answer. A STORED row
  at an address Census has never answered waits at most `CENSUS_HOLD_HOURS` =
  18 h, marked `PENDING` in the file so a second sync on it holds the row too
  (it used to read "Census answered nothing this run" and write the source's
  pin); a NEW row is written on the source's pin (holding it kept 5,000 of
  7,500 new rows off the map in a mid-run failure). The file persists in a
  cache pair of its own (actions/cache, always() saved) in the feeds,
  extra_sources, meetup, ticketmaster and races jobs.

- **A backslash ending a field makes the Census batch refuse the whole
  request.** Measured live 2026-10-24: a 2-row CSV with `400 Broad St\` got
  HTTP 400 "Malformed input file" (the backslash escapes the comma after it);
  the same rows without it got 200 with both matched, and `400 Bro\ad St`
  matched 400 Broad St, so Census drops a backslash anyway. The same day's
  review probe found newlines, quotes, NUL, tabs, emoji, CJK and 300-character
  streets all answered 200. Before `_census_post` stripped backslashes, one
  such venue held 1,250, 625 and 313 rows back on three runs and was never
  written. A 4xx other than 408/429 is now a refusal: its addresses keep their
  last answer or the source's pin and are never held, because asking again
  gets the same answer.

- **A PIN INSIDE ITS DISTRICT CAN STILL BE 20-36 KM FROM THE TOWN ITS OWN PAGE
  NAMES (KČT, 2026-10-24).** Czech districts reach ~40 km from their town, so
  a 50 km district-radius guard let through 5 of 193 organiser pins:
  "Prachatice žst." on a bog by Nové Hutě (29.6 km), Kozákov's finish at
  Dětenice in another district (27.0), Hrejkovice in Písek (23.0), Brušperk at
  Vyšní Lhoty (20.1), Zábřeh at Borotín in another district (35.7).
  mapsee_ingest_kct.py now checks the pin against the municipality or part its
  place text names (kct_places.json, 15,048 names from Wikidata P31 Q5153359 +
  P2788, built by kct_places_build.py), measured from the place's EDGE (point
  plus the radius of a circle of its P2046 area). The five lie 18.3-32.4 km
  beyond the edge; the 114 good ones at most 6.9 (median 2.3 inside); measured
  from the point alone, the widest good one is 10.0 km from Kyjov. Same-name
  villages are the trap: without skipping a name followed by nad/pod/u/v/na,
  "Suchdol n.Luž." matched a Suchdol 37 km away, "Nové Město n/Met" one 28 km
  away and Prague's Ládví a Praha-východ village 28 km away. Wikidata hills
  and quarters checked 16 more pins and refused 2 good ones ("Kamenná bouda",
  a different Homolka). `python test_ingest_kct.py`.

- **PHOTON ANSWERS A VENUE IT DOES NOT KNOW WITH THE TOWN, AND IN A CITY THE
  TOWN'S OWN NODE IS NOT THE POSTCODE'S ANSWER (repair-cafe.ch, 2026-10-24).**
  "Kuhstall, 7503 Samedan" and "Schulhaus, 7416 Almens" came back
  byte-identical to the postcode-and-town answer, the village node.
  "Kuhstallhalle, 8048 Zürich" came back as Altstetten station, which is also
  what "8048 Zürich" alone returns. But "Luzern, Schweiz" is place=city 0.48
  km from "6003 Luzern", and "Genève" is 0.34 km from "1205 Genève". So a
  guard that compares a geocoded answer only with the postcode point lets the
  city node through as a street. mapsee_ingest_repaircafe_ch refuses an answer
  within 50 m of either point. The 16 Photon placements on the full cursor lie
  0.14 to 1.2 km from their town's own node, so the rule costs none of them.
  Do not simply refuse every venue name with no street: Photon found 3 of the
  5 street-less names on the cursor, 22, 21 and 377 m from their cafés' pins,
  and the other 2 were the village node. A town name alone can be ambiguous:
  "Saas, Schweiz" answers Saas-Fee, 169 km from 7247 Saas. That only matters
  if the town-alone point is trusted, not when it is used to refuse. `python
  test_ingest_repaircafe_ch.py`.
