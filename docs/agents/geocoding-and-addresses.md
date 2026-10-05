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
