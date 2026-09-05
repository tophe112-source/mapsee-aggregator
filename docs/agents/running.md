# Running things

> Part of mapsee-aggregator's agent notes — see `AGENTS.md` for the map. Read this file when the bug is about: you need to run an adapter, a test script, curation or a cleanup.
> Every note below was measured before it was written; keep the numbers when you edit.

```bash
pip install -r requirements.txt

python test_categories.py           # the classifier: which lens an event reaches
python test_ingest_categories.py    # adapters + the shared vocabulary
python test_link_series.py          # which repeats count as one thing
python test_health_check.py         # the alarm itself, against a faked transport
python test_ingest_places.py        # coordinates: default pins, address parsing
python test_menu_links.py           # what may be called an "Order pickup" link
python test_osm_food.py             # opening hours we may act on, and the ones we must refuse
python test_prune_links.py          # what a description must look like after a dead link is cut
python test_sweep_global.py         # the international sweep's argv, below the equator
python test_ingest_slu.py           # occurrence vs series start; end_time vs end_date
python test_ingest_mylisting.py     # which of a card's two dates is the occurrence
python test_ingest_bikereg.py       # cycling: the server's offset, and one id per occurrence
python test_ingest_tribe.py         # feed shapes: one bad record must not cost a whole site
python test_ingest_jsonld.py        # placeholder locations, naive times, and calendar entries that are not events
python test_discover_osm.py         # discovery: a site builder is not a calendar, and a 200 can be a refusal
python test_indexnow.py             # paging our own database: keyset, not offset
python test_ingest_parkrun.py       # the free weekly runs, and configs a guarded job needs
python test_ingest_markets.py       # a metro that loses its Overpass slot, city-vs-street, and monthly markets
python test_ingest_seattlecenter.py # yearless dates, another event's coordinates, and matinees
python test_ingest_openactive.py    # an RPDE feed's first page is its oldest, and it lies both ways
python test_ingest_osm_amenities.py # which civic pins earn a sheet, and which are just the map
python test_retire_thin_artwork.py  # which already-written artwork rows may be hidden
python test_ingest_mapasculturais.py # an accepted filter that never ran, and a coordinate of "0"
python test_retire_openactive_slots.py # which superseded slot rows may be hidden
python gen_amenity_fixtures.py      # regenerate ../mapsee's amenity fixtures from to_event + to_row
python test_sync_unknown_column.py  # a column the database has not got YET must cost one feature, not the night
python test_sync_all_day.py         # a bare date names a day SOMEWHERE, and the day belongs to the venue
python test_skip_unchanged.py       # which rows a rewrite-every-run adapter may leave alone
python test_cleanup.py              # a statement timeout and an outage want opposite things
python test_retire_perday.py        # collapsing per-day rows never empties a venue
python test_curate_reapply.py       # putting a finished curation run back on a main that moved
python catalog_curate.py coverage   # where the catalog is thin, per lens category
python mapsee_health_check.py       # needs SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY
```

The 30 test scripts are the CI gate (`tests.yml`). They print one line per
case and exit non-zero on failure — no runner needed. `timezonefinder` has no Windows
wheel above 6.0.1, but it is a lazy optional import with a fallback, so the tests
run without it.

`MAPSEE_TODAY=YYYYMMDD` fixes "today" for reproducible curation runs.
