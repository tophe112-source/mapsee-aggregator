# mapsee event aggregator

Open-source toolkit that imports **publicly advertised community events** into a
normalized store, then syncs them to a database for map display. It powers the
Nearby map at [mapsee.me](https://www.mapsee.me), but nothing here is
mapsee-specific: the adapters, the source configs, and the curation tooling all
work standalone.

The goal is unglamorous and useful: **most community events are published
somewhere public - a library's iCal feed, a city's open-data portal, a venue's
calendar - and almost none of it is discoverable on one map.** This pulls those
scattered feeds together.

## What it ingests

Roughly 200 curated sources across 20+ countries, plus keyed API sweeps:

| Kind | Adapter | Notes |
|---|---|---|
| iCal / `.ics` | `mapsee_ingest_ics.py` | LibCal, Trumba, CivicPlus, WordPress "The Events Calendar". Conditional GETs (ETag/If-Modified-Since) so unchanged feeds cost ~0 bytes |
| City open data (Socrata) | `mapsee_ingest_opendata.py` | SODA API; ISO **and** free-text date columns |
| City open data (OpenDataSoft) | `mapsee_ingest_ods.py` | Explore v2.1 records API; the EU civic workhorse |
| Localist | `mapsee_ingest_localist.py` | University/city calendars (`/api/2/events`) |
| schema.org JSON-LD | `mapsee_ingest_jsonld.py` | Venue sites that embed `Event` blocks |
| Ticketmaster / SeatGeek / DICE / AXS / Moshtix | `mapsee_ingest*.py` | Keyed APIs, skipped silently when unset |
| Eventbrite | `mapsee_ingest_eventbrite.py` | Official API, organizer feeds |
| Parks / recreation | `mapsee_ingest_nps.py`, `_recreation.py` | US National Park Service, Recreation.gov |
| parkrun | `mapsee_ingest_parkrun.py` | ~2,900 free weekly timed 5k / junior 2k events across 21 countries, from parkrun's own public event list. Start times are not in the feed, so events are all-day and link to the event page unless pinned in the config |
| Farmers markets | `mapsee_ingest_markets.py` | City open data, OpenStreetMap `amenity=marketplace`, and the **USDA Local Food Directories** (`USDA_LOCALFOOD_API_KEY`, free at [usdalocalfoodportal.com](https://www.usdalocalfoodportal.com/fe/datasharing)). All three publish weekly schedules, which this expands into dated occurrences |
| Seoul Open Data | `mapsee_ingest_seoul.py` | Korea's own government API standard |
| Restaurants / takeout | `mapsee_ingest_restaurants.py`, `_affiliates.py`, `_ubereats.py` | Pickup windows from published hours |

Every adapter **no-ops silently when its key is unset**, so the pipeline runs
fine with any subset configured.

## Pipeline

```
mapsee_ingest*.py  →  <store>.json  →  mapsee_supabase_sync.py  →  database  →  map
(fetch + normalize)   (deduped store)   (upsert, idempotent)
```

Events are deduplicated by fingerprint, geocoded only when a feed carries no
coordinates (Photon/OSM, with a persistent cross-run cache and a per-run
budget), and filtered to upcoming dates.

One case the fingerprint cannot close on its own is a **market carried by
several sources at once** — a curated city list, OpenStreetMap and the USDA
directories agree on the name and the coordinates but not on the address string.
Market identity is therefore the name plus a ~5km geohash cell rather than the
address, and `mapsee_dedupe_markets.py` collapses what is left (a market sitting
on a cell boundary, or rows imported before that change) in the database, which
is the only place every source's rows meet — the sources run on different
weekdays into a store that is rebuilt each run. It runs after each feeds sync,
keeps the claimed or oldest row, and is safe to re-run.

## Curation tooling

`catalog_curate.py` keeps the source configs honest:

```bash
python catalog_curate.py coverage             # where the catalog is thin (no network)
python catalog_curate.py verify cands.json    # probe candidates, record results
python catalog_curate.py merge  cands.verified.json
python catalog_curate.py audit                # re-check every configured source
python catalog_curate.py ledger               # what's been tried, what's dead
```

Two rules make this work at scale:

1. **Nothing is added unverified.** A candidate must return real, *future* events
   under the production User-Agent. Feeds that only answer a browser UA, or that
   hold nothing but past events, are rejected - they would silently ingest zero.
2. **Every attempt is remembered.** `curation_ledger.json` records each URL tried
   and why it failed, so dead sources are never re-probed (and get one recheck
   after 90 days, in case they come back).

## Conduct

This project only reads **public, self-published** feeds:

- Requests identify themselves honestly (`MapseeAggregator/1.0 (+https://mapsee.me)`)
- `robots.txt` and rate limits are respected; feeds that gate their API are asked
  for permission, not worked around
- Imported listings link back to their source, and are marked as imported
- Anything requiring a partner agreement stays behind an unset key until that
  agreement exists

If you operate a feed here and want it removed, open an issue.

## Running it

```bash
pip install -r requirements.txt
python mapsee_ingest_ics.py --config ics_sources.json --store events.json
python mapsee_supabase_sync.py --store events.json --only-new
```

CI runs on a daily schedule (`.github/workflows/aggregate-events.yml`). Secrets
are optional per adapter; see the workflow header for the list.

## Adding a source

Most additions are **config, not code** - append an entry to `ics_sources.json`,
`localist_sources.json`, `opendata_sources.json`, or `ods_sources.json`, verify
it, and open a PR. New *platforms* need a small adapter; the existing ones are
the template.

## Known limit: Trumba .ics caps at 500

Every `trumba.com/calendars/<name>.ics` feed returns **at most 500 VEVENTs**,
chronologically. On a busy municipal calendar that is roughly a four-week
horizon, not the 90 days the rest of the pipeline works to — Seattle's city-wide
feed, measured, covered 26 Jul to 22 Aug. Nothing in the config can widen it.

What follows from that:

- **Keep `limit` at 500 for Trumba sources.** A lower `limit` throws away events
  the feed already paid to deliver. Seattle city-wide sat at 400 and was dropping
  a hundred, including two of the eleven outdoor-movie nights.
- **The daily cron is what gives coverage**, not the window: each run's 500 rolls
  forward a day, so events enter the map about four weeks out rather than ninety.
  Skipping days loses events permanently — they age past the horizon unseen.
- **One department calendar is not the city.** Seattle publishes several. The
  city-wide feed carries only a subset of Parks' programming, which is why Parks
  is configured separately. To find a department's own feed, open its events page
  and look for `$Trumba.addSpud({ webName: "..." })` in the source — that webName
  is the .ics filename. A `filterview` in the same block is a saved view, not a
  separate calendar, and the unfiltered feed is the one to subscribe to.

Titles are the series, not the programme. Seattle's "Movies in the Park" page
lists nights that appear in the calendar as `Center City Cinema`; searching the
app for the page's heading finds nothing. Worth remembering before concluding a
source was missed.

## License

MIT - see [LICENSE](LICENSE).
