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
