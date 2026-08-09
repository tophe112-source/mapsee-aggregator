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
| ~~parkrun~~ | `mapsee_ingest_parkrun.py` | **Parked — not ingesting.** The adapter works, but parkrun's terms do not permit this use, so its config is held at `parkrun_sources.json.pending-permission` and the workflow skips the step. It stays here, disabled, pending written permission. See [Conduct](#conduct) |
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

## Monitoring: how you find out something broke

Every ingest step is deliberately failure-tolerant — `set +e`, bare `exit 0`,
`|| true` — so one dead feed cannot abort a sweep of forty others. That is the
right trade, and it has a sharp edge: **the aggregate workflow reports green
whether or not it ingested anything.** A rotated key or a feed that starts
403ing produces a successful, silent, empty run.

`mapsee_health_check.py` is the counterweight. It doesn't re-run the ingest; it
asks the database what actually landed and compares it to a committed baseline:

```bash
python mapsee_health_check.py --update-baseline   # after a run you trust
python mapsee_health_check.py                     # exit 1 if a source went quiet
```

It reports four things, and it is careful about false positives — staleness
budgets are per-source and default to 8 days, because the heavy sweep only runs
Mon & Thu and a 72-hour rule would cry wolf every Sunday:

| | Meaning |
|---|---|
| `SILENT` | A baseline source's newest event is older than its budget. The job still exits 0; nothing new is arriving. |
| `MISSING` | The source has no rows left at all. |
| `DRAINED` | Still ingesting, but upcoming events collapsed >60% against the baseline. |
| `LEDGER` | How many curated feeds `catalog_curate.py` has marked dead, and whether that's growing. Advisory. |

Two workflows run it (`.github/workflows/source-health.yml`):

- **daily, 08:40 UTC** — one cheap RPC call. If anything is unhealthy it opens a
  GitHub issue labelled `ingest-health` (which emails you), comments on that same
  issue on subsequent days rather than opening new ones, and closes it once
  everything recovers.
- **Sundays, 04:10 UTC** — the deep probe: `catalog_curate.py audit` re-fetches
  every curated feed and commits the refreshed ledger.

Notification is a GitHub issue rather than Slack precisely because it needs no
secret, no external account and no upkeep.

## Conduct

This project only reads **public, self-published** feeds:

- Requests identify themselves honestly (`MapseeAggregator/1.0 (+https://mapsee.me)`)
- `robots.txt` and rate limits are respected; feeds that gate their API are asked
  for permission, not worked around
- Imported listings link back to their source, and are marked as imported
- Anything requiring a partner agreement stays behind an unset key until that
  agreement exists

If you operate a feed here and want it removed, open an issue.

**A worked example.** parkrun publishes an open event list and the adapter for
it works fine — ~2,900 events across 21 countries, and it would have been the
whole `running` layer, which no other curated feed covers. On reading their
terms, that use isn't permitted. So the config was renamed to
`parkrun_sources.json.pending-permission`, the workflow step now guards on the
file's absence and skips, and the adapter sits disabled until someone at
parkrun says yes in writing. The events would have been good; the terms said
no. That is what the rules above mean in practice.

## Running it

```bash
pip install -r requirements.txt
python mapsee_ingest_ics.py --config ics_sources.json --store events.json
python mapsee_supabase_sync.py --store events.json --only-new
```

CI runs on a daily schedule (`.github/workflows/aggregate-events.yml`). Secrets
are optional per adapter; see the workflow header for the list.

## Adding a source

Most additions are **config, not code** — append an entry to `ics_sources.json`,
`localist_sources.json`, `opendata_sources.json`, or `ods_sources.json`, verify
it with `catalog_curate.py verify`, and open a PR.

### Adding a new platform (a new adapter)

New *platforms* need a small adapter. Every adapter in this repo follows the
same contract, so copy the closest one — `mapsee_ingest_localist.py` is the
smallest complete example, `mapsee_ingest_ics.py` the most thorough:

- **CLI.** `--config <sources.json> --store <store.json>` — universal across all
  adapters. The workflow relies on it; nothing else is assumed.
- **Read the config through `_entries()`**, never a bare `json.load`. Some
  configs nest their list under a `"sources"` key, and iterating the raw object
  walks the dict *keys* instead. This has bitten two readers already.
- **Append to the store, don't overwrite.** Several adapters write to the same
  `feeds_events.json` in one job; the last one must not erase the others.
- **No-op silently when the key is unset.** Print a one-line skip and return 0.
  A fork with no credentials must still run green.
- **Identify honestly** — reuse the shared `MapseeAggregator/1.0 (+https://mapsee.me)`
  User-Agent, and respect `robots.txt` and rate limits. See [Conduct](#conduct).
- **Normalize to the common event shape** so `mapsee_supabase_sync.py` can upsert
  it: stable `external_id`, `external_source`, title, start/end, lat/lon, a
  source URL to link back to, and a category.
- **Add a step to `aggregate-events.yml`** in the job matching your source's
  cost profile (see the run-order note below), guarded so a missing config file
  skips rather than fails.

### What runs when

Execution order lives in `.github/workflows/aggregate-events.yml`, whose header
comment explains the job split in detail. The short version:

| Job | Cadence | What |
|---|---|---|
| `ticketmaster` | daily 06:17 UTC | The one Ticketmaster sweep, US then international. Serial by nature — the key is rate-limited to ~2 req/s. |
| `feeds` | daily 06:17 UTC | ICS, open data, Localist, JSON-LD, markets. Carries no coords, so each venue is geocoded (~1.1s); isolated so a slow feed can't drag the TM sweep. |
| `meetup` | Mon & Thu 06:40 | One Meetup sweep. Separate key from Ticketmaster, so it runs in parallel. |
| `extra_sources` | Mon & Thu 06:40 | SeatGeek/DICE/AXS metro sweep, Eventbrite, NPS/Localist/Sports. Skipped wholesale when none of those keys are set. |
| `indexnow` | after every run | Pushes the URLs of events that just landed to IndexNow (Bing/Yandex/Seznam/Naver). `if: always()`, so a run where one leg failed still announces what the others ingested. |
| `source-health` | daily 08:40 | Reads what landed; opens an issue if a source went quiet. See [Monitoring](#monitoring-how-you-find-out-something-broke). |
| `audit` | Sun 04:10 | Deep re-probe of every curated feed; commits the refreshed ledger. |

**Why `indexnow` exists.** An event page's crawlable life runs from the moment it
is ingested to the moment the event starts — `sitemapEvents()` drops it from the
sitemap the second it is in the past. Ingest adds ~6,500 indexable events a day
into a sitemap index that already announces 50,000 URLs, so relying on a crawler
to work down that list means a share of every day's events expire before anyone
fetches them. IndexNow is the push half: *these specific URLs are new, now.*
Google doesn't participate and is still served by the sitemaps; the reason to
care about Bing is that its index is what backs Copilot and ChatGPT search.

`mapsee_indexnow.py` reads with the **public anon key**, not the service role —
so it is structurally incapable of announcing a private or hidden event, whatever
its query says. It needs no secret.

> ⚠️ The submission is authenticated by a key file served at
> `https://mapsee.me/<key>.txt`, which lives in the **sibling repo** at
> `mapsee/site/afea88b0d7114a5188694ff0f3580849.txt`. Delete that file and every
> submission starts failing with a 403 — the job says so loudly, but the file
> looks like junk, so it is worth knowing what it is.

Adapters within a job run sequentially and all append to one store, which is
synced once at the end of the job. That last part matters: a job cancelled at
its timeout mid-loop throws away everything it had ingested, which has happened
(~42k events lost). Keep new steps cheap, or give them their own job.

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

---

## Part of the Conbinience suite

This pipeline feeds **Mapsee**, a product of **Conbinience LLC** (Seattle). It is
the only public repository of the four:

| Repo | Serves |
| --- | --- |
| [`conbinience`](https://github.com/conbinience/conbinience) | www.conbinience.com — company site, and the suite map |
| [`fishsie`](https://github.com/conbinience/fishsie) | www.fishsie.com |
| [`mapsee`](https://github.com/conbinience/mapsee) | mapsee.me + six front doors — the app this pipeline writes into |
| **`mapsee-aggregator`** *(this one)* | no site — runs on GitHub Actions |

Events ingested here surface on all seven Mapsee doors; `derive_categories` in
`mapsee_supabase_sync.py` is what routes an event to the doors it belongs on.
