# mapsee-aggregator — notes for agents

The event-ingest pipeline behind mapsee.me. Public repo (MIT). It has no server:
GitHub Actions runs a set of Python scripts on a schedule, and they write into
the same Supabase the product reads. Sibling repos: `../mapsee` (the product and
the Worker), `../conbinience`, `../fishsie`. `../SUITE-AUDIT.md` covers all four.

## The shape of it

```
31 adapters              -> a JSON store -> mapsee_supabase_sync.py -> Supabase
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
| Where new sources come FROM | `discover <socrata\|ckan\|mobilizon>`; `curation_cursor.json` is how far each catalog query has been read |
| Which categories curation targets | `curated_categories()` in `catalog_curate.py` — read live from `mapsee.me/api/lenses` |
| Whether a source has gone quiet | `mapsee_health_check.py` |
| Whether the catalog is actually growing | `coverage_history.jsonl`, one line per curation run |
| Deleting past events | `mapsee_cleanup.py` |
| Chaining a repeating listing into one `series_id` | `mapsee_link_series.py` |
| What runs when | `.github/workflows/aggregate-events.yml` header — the best doc in the repo |

Source lists are the `*_sources.json` files; `CONFIG` at the top of
`catalog_curate.py` maps each type to its file and its URL key.

## Things that will bite you

- **A category key that no lens opens onto reaches only mapsee.me.** The
  vocabulary is `MAPSEE_CATEGORY_KEYS` in `mapsee_supabase_sync.py` (mirrored as
  `VALID_CATEGORIES` in `mapsee_ingest.py`) and it must match `CATEGORIES` in
  `../mapsee/site/js/app.js`. `test_ingest_categories.py` asserts the first two
  agree; nothing checks the third, so check it by hand when you touch it.
- **`--only-new` is the default in CI.** It skips events already in the table, so
  a scheduled run can only ADD. Wednesday's daily run drops the flag and does a
  real refresh — that is the only time changes at the source reach the map.
- **Every ingest job is deliberately failure-tolerant** (`set +e`, `|| true`,
  bare `exit 0`) so one dead feed cannot abort a sweep of forty. The consequence
  is that a green run proves nothing; `source-health.yml` is the actual signal.
- **The health check reads `stats_snapshot_all()`, NOT `source_stats()`.** The
  latter aggregates `public.events` on read and cannot finish inside the API
  role's ~3s statement timeout — ../mapsee migration 0112 retired it for exactly
  that and moved the product onto a snapshot a cron computes. The aggregator kept
  calling it and failed its first eight runs, reporting a Postgres 57014 as
  "one or more sources have gone quiet". Two rules fell out and both are now
  tested (`test_health_check.py`): read what the product reads, and put the
  server's own error body in the report — a status code alone diagnoses nothing.
- **A source in the health report is `external_source`, which is `'mapsee'` for
  everything this repo writes.** So it sees the aggregator as ONE bucket: it can
  tell you the pipeline stopped, never that the Meetup adapter did. No per-
  adapter provenance is persisted (`external_id` is a bare fingerprint hash).
  `FEED_DOWN`, from the curator audit, is the per-feed signal.
- **A green source-health run with no baseline means "no opinion", not
  "healthy".** SILENT / MISSING / DRAINED all compare against
  `source_health_baseline.json`; without the file the job passes having evaluated
  nothing, which is what it did on every run where the RPC answered. CI now
  passes `--seed-baseline` and commits the result.
- **The curation ledger records candidates too.** Most `"status": "fail"` rows
  are URLs that were probed and rejected — that is the process working. Only the
  ones still present in a `*_sources.json` are regressions; `configured_dead()`
  in `mapsee_health_check.py` is what separates them. The schema key is
  `"status"`, not `"ok"` — discovery filtered on the latter for months and so
  never filtered at all.
- **`status` has THREE values, and `empty` is not `fail`.** A feed that parses
  fine with nothing upcoming is a venue between seasons, not a broken feed;
  recording both as `fail` is why FEED_DOWN reported 15 configured regressions
  when six were genuinely broken. `_status_for()` decides. Discovery skips both
  (proposing a source that ingests zero is the same waste either way); only
  `fail` is a regression, and only `fail` is worth retiring from a config.
- **The audit retries; candidate verification does not.** Re-probing the 15
  feeds the audit had marked dead-while-configured found four answering
  normally, all four having failed on a ReadTimeout or a 403. A `fail` on a
  CONFIGURED feed parks it for 90 days and reports it as a regression, so it is
  worth a second look; a fresh candidate that times out costs nothing to skip
  and comes round again next week.
- **`_not_included` in a sources file is an editorial NO, and discovery reads
  it.** `mobilizon_sources.json` declines an instance for spam and another for an
  unclear licence. Both verify fine, because verification proves a feed works,
  not that we want it. Anything that proposes sources must consult
  `_not_included()` or it will re-propose them every week.
- **A source that HANDS you coordinates can hand you the wrong ones, and nothing
  downstream will catch it.** Two live examples, both now covered by
  `test_ingest_places.py`. Squarespace ships a default map pin at
  `40.7207559,-74.0007613` — lower Manhattan — for every event whose location
  was never filled in; that was 17 of Volunteer Park Trust's 22 upcoming events,
  all of them in Seattle. The defence is not a coordinate blocklist but the rule
  that *a location with no address text is not a location*, falling back to the
  config's `venue` block. Luma reports a US postal code only inside
  `full_address`, where a bare five-digit search finds the STREET NUMBER
  ("15600 NE 8th St, Bellevue, WA 98007" → 15600, a real ZIP in Pennsylvania).
  Well-formed, plausible, wrong is the worst failure this pipeline produces.
- **Luma's Discover feed takes `discover_place_api_id`.** The obvious
  `place_api_id` — which is what the id is called everywhere else in Luma's own
  payloads — is accepted, ignored, and answered with a 200 and a full page of
  events for whatever city the RUNNER's IP is in. Asking for Seattle from a
  GitHub runner returns Columbus, Ohio, silently. `expect_region` in
  `luma_sources.json` turns that into a refusal instead of wrong data; set it on
  every place.
- **Never add `pull_request:` to a workflow that reads secrets.** `tests.yml` is
  the one workflow safe on forks, because it reads none.
- **`series_id` is assigned after the fact, not at ingest.** A repeating listing
  publishes each occurrence separately and the store is rebuilt every run, so the
  occurrences never meet in memory — the table is the only place a series is
  visible whole. `mapsee_link_series.py` runs after the sync and stamps them.
  Its failure mode is silent and bad: chain two unrelated events and one of them
  disappears from the map behind the other (`collapseSeries` in
  `../mapsee/site/js/app.js` folds a series to its next occurrence), with nothing
  in any log to say so. That is why it has a test, refuses implausibly large
  groups, and never touches a claimed row.

## Running things

```bash
pip install -r requirements.txt

python test_categories.py           # the classifier: which lens an event reaches
python test_ingest_categories.py    # adapters + the shared vocabulary
python test_link_series.py          # which repeats count as one thing
python test_health_check.py         # the alarm itself, against a faked transport
python test_ingest_places.py        # coordinates: default pins, address parsing
python catalog_curate.py coverage   # where the catalog is thin, per lens category
python mapsee_health_check.py       # needs SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY
```

The five test scripts are the CI gate (`tests.yml`). They print one line per
case and exit non-zero on failure — no runner needed. `timezonefinder` has no Windows
wheel above 6.0.1, but it is a lazy optional import with a fallback, so the tests
run without it.

`MAPSEE_TODAY=YYYYMMDD` fixes "today" for reproducible curation runs.

## Credentials

`.env.example` lists all 36 variables. Nothing in this repo should ever hold a
real key; CI supplies them as secrets. `SUPABASE_SERVICE_ROLE_KEY` bypasses RLS —
treat any workflow that reads it as privileged.
