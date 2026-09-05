# US state fairs as events

> Part of mapsee-aggregator's agent notes — see `AGENTS.md` for the map. Read this file when the bug is about: marketing-copy dates, dates never written down, a year on the wrong side, a town that is not a venue, Photon and fair addresses.
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
