# mapsee-aggregator — the map for agents

The event-ingest pipeline behind mapsee.me. Public repo (MIT). It has no server:
GitHub Actions runs a set of Python scripts on a schedule, and they write into
the same Supabase the product reads.

**This file is deliberately small, because every model loads it on every
session.** The measured notes about what bites — 154 of them — live in
[`docs/agents/`](docs/agents/), one file per topic. `docs/agents/INDEX.md` lists
every note's headline: grep it for the symptom, then open ONE file. Nothing in a
note is a guess; each records a measurement, and the number is the point.

Sibling products, same author, same shape of notes: `../Mapsee` (the product and
the Worker this pipeline feeds), `../Fish/fishsie-repo` (the game),
`../Conbinience` (the company site). `../AGENTS.md` is the suite map.

## The shape of it

```
41 adapters              -> a JSON store -> mapsee_supabase_sync.py -> Supabase
mapsee_ingest_*.py          *_events.json   (classify + geocode + upsert)
```

Every adapter normalises one source into `NormalizedEvent` and appends to a
store file. The sync is the only thing that talks to the database, and it is
where an event gets its **category** — which decides which of the seven mapsee
front doors it reaches.

| Looking for | Go to |
|---|---|
| Which category an event ends up in | `map_category` / `derive_categories` in `mapsee_supabase_sync.py` |
| The promotion rules (`_VOLUNTEER_RX`, `_PARTY_RX`, `_FITNESS_RX`, …) | same file, above `map_category` |
| Adding/verifying feed sources | `catalog_curate.py` — `discover`, `verify`, `merge`, `audit`, `coverage` |
| Where new sources come FROM | `discover <socrata\|ckan\|mobilizon\|osm>`; `curation_cursor.json` is how far each catalog query has been read |
| Finding a VENUE's own calendar, anywhere on earth | `catalog_discover_osm.py` — OSM venues with a `website`, probed for a calendar and fingerprinted to an adapter |
| US state fairs, as the fair itself | `mapsee_ingest_fairs.py` + `fair_sources.json` — 48 fairs, one multi-day event each. Identity and coordinates are curated; the DATES are scraped every run and never stored |
| Finding a whole TOWN's calendar | `catalog_discover_civic.py` — Wikidata cities with an official website (5,770 in the US), probed the same way. The city's own calendar, plus its tourism board where Wikipedia names one |
| Which categories curation targets | `curated_categories()` in `catalog_curate.py` — read live from `mapsee.me/api/lenses` |
| Whether a source has gone quiet | `mapsee_health_check.py` |
| Whether the catalog is actually growing | `coverage_history.jsonl`, one line per curation run |
| Deleting past events | `mapsee_cleanup.py` |
| North American public library programmes | `mapsee_ingest_bibliocommons.py` + `bibliocommons_sources.json` - six systems, 28,314 upcoming measured 2026-09-03, every row carrying its branch's surveyed coordinate |
| Public swimming pools | the `leisure=swimming_pool` Kind in `mapsee_ingest_osm_amenities.py`. The only Kind with an extra Overpass filter, and the reason is that most pools on earth are in back gardens |
| Whether a platform has already been probed and refused | `curation_ledger.json` - `verify` skips a `fail` for 90 days without a network call. The table under "Platforms probed" below is the durable half |
| Whether a row is an advertisement rather than an event | `mapsee_spam.py` — one predicate, wired into `EventStore.upsert` so all 41 adapters get it; `test_spam.py` is mostly about what it must NOT refuse |
| How much of a source is advertising | `mapsee_spam_audit.py` — measures the rate per instance, so `_not_included` is a number and not an impression |
| Removing spam that got in before the gate did | `mapsee_spam_purge.py` — reports by default, `--apply` to write; runs daily from `spam-purge.yml` at 07:40, BETWEEN the import and the janitor |
| Real "order pickup" links for food venues | `mapsee_menu_links.py` — writes a `🛒 Order:` line the product turns into the button |
| Removing an order/booking link whose destination has died | `mapsee_prune_links.py` — dry run by default; only a 404 or an empty page counts, never a 403 |
| Re-running the classifier over rows already in the table | `mapsee_reclassify.py` — dry run by default; `--apply` refuses without `--allow` |
| Takeaway places (not events) from OpenStreetMap | `mapsee_ingest_osm_food.py` + `osm_food_sources.json` — only places with a real order link AND readable hours |
| Second-hand / charity / vintage shops (not events) from OpenStreetMap | `mapsee_ingest_osm_secondhand.py` + `osm_secondhand_sources.json` — the food adapter's sibling, feeding `market` (fleabop). Bar is readable hours, not an order link; read its header for why that differs. Fetches no third-party websites |
| UK leisure-centre, community-sport and volunteering sessions | `mapsee_ingest_openactive.py` + `openactive_sources.json` — RPDE feeds, all CC-BY 4.0, all carrying their own coordinates |
| Playgrounds, drinking fountains, outdoor gyms, little free libraries, food banks, public art | `mapsee_ingest_osm_amenities.py` + `osm_amenity_sources.json` — the third OSM PLACES adapter. Most of what it writes is `pin_only` FURNITURE: drawn on the map and nothing else |
| Public transit bundles (bus/metro suggestions on a walk) | **not here** — `../mapsee/tools/transit_build.py` + `transit_sources.json`. It is Python and it is a scheduled pipeline, so this is where you would look; it lives in the product repo because its output is a static site asset and mapsee deploys on push, which a cross-repo push would only complicate |
| Chaining a repeating listing into one `series_id` | `mapsee_link_series.py` |
| Brazilian cultural events (state/municipal registers) | `mapsee_ingest_mapasculturais.py` + `mapasculturais_sources.json` — the only source that puts anything on the map in Brazil |
| The two HTML scrapers (no feed exists at either source) | `mapsee_ingest_pioneersquare.py`, `mapsee_ingest_seattlecenter.py` — both place events from a venue book in their config, never from the page |
| What runs when | `.github/workflows/aggregate-events.yml` header — the best doc in the repo |

Source lists are the `*_sources.json` files; `CONFIG` at the top of
`catalog_curate.py` maps each type to its file and its URL key.

## How this repo wants to be worked

- **`--only-new` is the default in CI, so a fix reaches the FUTURE only.** A
  classifier, glyph or coordinate fix changes nothing already in the table until
  `mapsee_reclassify.py` (dry run by default) or a rewrite-every-run adapter
  revisits it. An upsert cannot delete.
- **A 403 or a bot challenge is a refusal, not an obstacle.** Do not retry with a
  browser User-Agent; the way in is to ask the operator (`docs/agents/platforms-probed.md`).
- **The three daily jobs run in a load-bearing order**, every ingest step is
  deliberately failure-tolerant, and a step cancelled by `timeout-minutes` skips
  every step after it unless `always()` saves the work (`docs/agents/ci-and-jobs.md`).
- **The 30 `test_*.py` scripts are the CI gate.** They print one line per case
  and exit non-zero; no runner. `MAPSEE_TODAY=YYYYMMDD` fixes "today".
- **Never add `pull_request:` to a workflow that reads secrets.**
  `SUPABASE_SERVICE_ROLE_KEY` bypasses RLS; nothing in the repo holds a real key.
- **Other agents work in worktrees off this checkout.** Stage your own paths;
  never `git add -A`.
- **Measure before you write it down.** Add your note to the topic file with the
  number that proved it, then `python agent_notes.py --index`.

## Where to look, by symptom

| Symptom | Open | Run |
|---|---|---|
| An event reaches the wrong front door, or none; a lens is thin | `docs/agents/classification-and-categories.md` | `python test_categories.py`, `python catalog_curate.py coverage` |
| A date, time, offset or all-day event is wrong | `docs/agents/dates-and-timezones.md` | `python test_sync_all_day.py`, `python test_ingest_bikereg.py` |
| A pin lands in the wrong place, or on `0,0` | `docs/agents/geocoding-and-addresses.md` | `python test_ingest_places.py` |
| A source went quiet; the health report | `docs/agents/health-and-monitoring.md` | `python mapsee_health_check.py` (needs the service key) |
| Adding or verifying a source, a refusal, a calendar plugin, a licence | `docs/agents/curation-and-discovery.md`, `docs/agents/platforms-probed.md` | `python catalog_curate.py verify`, `python test_discover_osm.py` |
| Duplicates, rows that never die, paging, a cursor, a fingerprint, `series_id` | `docs/agents/sync-eventstore-and-paging.md` | `python test_indexnow.py`, `python test_skip_unchanged.py`, `python test_link_series.py` |
| A job timed out, lost an hour's work, ran in the wrong order | `docs/agents/ci-and-jobs.md` | read `.github/workflows/aggregate-events.yml`'s header |
| Advertisements on the map | `docs/agents/spam-and-content.md` | `python mapsee_spam_audit.py`, `python mapsee_spam_purge.py` (report first) |
| A civic place (toilets, food banks, artwork) drawn wrong or unopenable | `docs/agents/osm-amenities.md` | `python test_ingest_osm_amenities.py` |
| OpenActive sessions, booking grids, forty thousand standing rows | `docs/agents/openactive-and-standing-rows.md` | `python test_ingest_openactive.py`, `python test_retire_openactive_slots.py` |
| Library programmes | `docs/agents/libraries-bibliocommons.md` | `python test_ingest_bibliocommons.py` |
| A state fair's dates | `docs/agents/state-fairs.md` | `python test_ingest_seattlecenter.py` (yearless dates share the lesson) |
| Brazil is empty, or a Mapas Culturais row is odd | `docs/agents/brazil-mapasculturais.md` | `python test_ingest_mapasculturais.py` |
| One specific adapter or source misbehaves | `docs/agents/adapters-and-sources.md` | its `test_ingest_<name>.py` |
| A secret is missing | `docs/agents/credentials.md` | `.env.example` lists all 36 |

## The notes

| File | Notes | Covers |
|---|---|---|
| `docs/agents/curation-and-discovery.md` | 24 | the ledger, statuses, `_not_included`, sitemaps, robots, bot challenges, site builders, plugins, deep pages, licences |
| `docs/agents/osm-amenities.md` | 23 | which civic places earn a pin or a sheet, deny-lists, facts vs names, the cached element list |
| `docs/agents/openactive-and-standing-rows.md` | 15 | RPDE paging, `ScheduledSession`, booking grids, collapse, standing rows, retirements |
| `docs/agents/ci-and-jobs.md` | 14 | timeouts, `always()`, budgets, job order, secrets, configs a guarded job needs |
| `docs/agents/adapters-and-sources.md` | 13 | Luma, parkrun, businesses vs events, malformed records, webcal, JSON-LD, Overpass, seattlecenter |
| `docs/agents/classification-and-categories.md` | 11 | lens keys, promotion regexes, kids/food/market, category defaults, order pickup |
| `docs/agents/sync-eventstore-and-paging.md` | 11 | upserts, OFFSET vs keyset, PostgREST errors, fingerprints, `series_id`, cursors |
| `docs/agents/dates-and-timezones.md` | 9 | server offsets, bare dates, sentinels, `starts_at`, years on the wrong side |
| `docs/agents/brazil-mapasculturais.md` | 7 | an accepted filter that never ran, `0,0`, placeholders, `Etc/UTC`, measured negatives |
| `docs/agents/geocoding-and-addresses.md` | 6 | Census, Photon, wrong coordinates, the city in the address |
| `docs/agents/spam-and-content.md` | 6 | the predicate, the purge, implausible end dates, safe scheduled deletes |
| `docs/agents/state-fairs.md` | 5 | marketing-copy dates, towns vs venues |
| `docs/agents/health-and-monitoring.md` | 4 | `stats_snapshot_all`, baselines, a source with no retry |
| `docs/agents/libraries-bibliocommons.md` | 4 | geocoding cost, ignored date filters, stock tiles |
| `docs/agents/running.md`, `credentials.md`, `platforms-probed.md` | — | the operational sections, verbatim |
| `docs/agents/INDEX.md` | all | every headline, generated — grep it first |

## Running things, the short version

```bash
pip install -r requirements.txt
python test_categories.py            # one of the 30 gate scripts; the full list is in docs/agents/running.md
python catalog_curate.py coverage    # where the catalog is thin, per lens category
python agent_notes.py                # the notes map is under budget and the index is fresh
```

`timezonefinder` has no Windows wheel above 6.0.1; it is a lazy optional import
with a fallback, so the tests run without it.
