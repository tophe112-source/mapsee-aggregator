# BiblioCommons library programmes

> Part of mapsee-aggregator's agent notes — see `AGENTS.md` for the map. Read this file when the bug is about: geocoding cost, an ignored date filter, the date-granular fingerprint, stock image tiles.
> Every note below was measured before it was written; keep the numbers when you edit.

- **THE EXPENSIVE HALF OF A CIVIC FEED IS THE GEOCODING, AND BIBLIOCOMMONS
  HANDS IT OVER.** Every `ics_sources.json` entry carries a `geocode_suffix` and
  pays ~1.1s of Photon per venue; a library system is 40-80 branches. The
  BiblioCommons gateway ships `mapLocation.centrePoint` with every event, so the
  adapter sets `coords_exact` and nothing is geocoded. Spot-checked over 47
  Chicago branches the points sit on the buildings, and re-geocoding
  "S. Hoyne Avenue, Chicago" would be the Renton restaurant again.

- **ITS DATE FILTER IS ACCEPTED AND IGNORED.** `?startDate=&endDate=` over a
  three-day window returns the same 4,584 rows as no filter at all. Same trap as
  Mapas Culturais, and the same tell: a 200, a plausible response, and the whole
  archive. The horizon is applied client-side and the page loop stops once a page
  is entirely past it.

- **THE SHARED FINGERPRINT IS DATE-GRANULAR, AND A LIBRARY RUNS THE SAME
  PROGRAMME TWICE IN A DAY.** `make_fingerprint` is a CROSS-SOURCE key
  (name | YYYY-MM-DD | venue) - exactly right for "is this the same gig in two
  ticketing feeds", and wrong for a branch running Family Storytime at 10:15 and
  again at 11:15. Measured on one page of Vancouver: 9 rows of 197 merged into
  another and were never written. The clock time now joins the BASIS, never the
  stored name, the way `mapsee_ingest_affiliates` folds its own discriminator in.
  Any adapter whose source runs one thing several times a day needs this.

- **MOST OF A LIBRARY'S EVENT IMAGES ARE ONE STOCK TILE.** `featuredImageId`
  usually resolves to an image tagged `EventType` - a single "Author Event"
  graphic shared by every author event in the system. Importing it gives four
  hundred rows the same picture, which is what `mapsee_retire_thin_artwork.py`
  exists to undo. Only an image tagged to the event itself is taken.

- **A 5xx IS THE GATEWAY FAILING TO BUILD A 200-ROW PAGE, and the same rows
  come back as four pages of 50.** Santa Clara County's page 2 answers 500 at
  `limit=200` on every try, and rows 201-400 answer 200 at `limit=50`
  (2026-09-24): not a bad record and not a refusal. The loop stopped at the
  first non-200, so a system lost everything after that page - Boston Public
  Library "p13 HTTP 500" in the 2026-09-24 CI run, 1,856 kept of the 4,051
  measured three weeks earlier - and 7 of the 38 systems probed that day
  stopped the same way. `_split_page` re-reads a 5xx page as PAGE_LIMIT //
  SPLIT_LIMIT smaller ones and merges their entities per KIND (a row names its
  branch and audience by id, and those live beside it on its own page); a 403,
  404 or 410 still stops the system. Every failed page came back 4 of 4: Pima
  450 -> 2,761, Santa Clara 151 -> 1,383, Contra Costa 619 -> 1,488,
  Christchurch 2,233 -> 3,197, Boston 2,330 at even a 90-day horizon.

- **THREE MORE LIBRARY SYSTEMS YIELDED 3,683 UNIQUE UPCOMING EVENTS.** Probed
  2026-09-25 with the production User-Agent and the BiblioCommons adapter, using
  a separate store and a 90-day horizon: Cincinnati & Hamilton County kept
  2,607 (2,605 unique), Stouffville kept 371 (370 unique), and Stark kept 708
  (708 unique). The three repeated sessions merged by fingerprint; all 3,683
  stored rows have branch or off-site coordinates. The adapter also skipped 13
  past, 2,536 beyond the horizon, and 180 unplaceable rows. Primary category
  `kids` accounts for 1,651 Cincinnati, 271 Stouffville and 280 Stark rows
  (2,202 total); the stored start dates run 2026-09-25 through 2026-12-24.
  This is event yield, not a count of calendar URLs or portal facets.
