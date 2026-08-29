# mapsee-aggregator — notes for agents

The event-ingest pipeline behind mapsee.me. Public repo (MIT). It has no server:
GitHub Actions runs a set of Python scripts on a schedule, and they write into
the same Supabase the product reads. Sibling repos: `../mapsee` (the product and
the Worker), `../conbinience`, `../fishsie`. `../SUITE-AUDIT.md` covers all four.

## The shape of it

```
34 adapters              -> a JSON store -> mapsee_supabase_sync.py -> Supabase
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
| Which categories curation targets | `curated_categories()` in `catalog_curate.py` — read live from `mapsee.me/api/lenses` |
| Whether a source has gone quiet | `mapsee_health_check.py` |
| Whether the catalog is actually growing | `coverage_history.jsonl`, one line per curation run |
| Deleting past events | `mapsee_cleanup.py` |
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
  config's `venue` block — which is now the ONLY way a Squarespace event gets
  placed, because the adapter reads the page rather than `?format=json` and the
  page carries no coordinates at all. Luma reports a US postal code only inside
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
- **A widget key on somebody's page is not a feed you may read.**
  theveraproject.org embeds a DICE widget whose `partnerId` and `apiKey` are in
  plain sight, and using them would reach `api.dice.fm` — the ONE path
  dice.fm/robots.txt disallows, with somebody else's credential. What is allowed
  is the venue's own DICE PAGE (`Allow: /`, listed in DICE's published sitemap),
  whose `__NEXT_DATA__` already carries the events server-rendered, so no API
  call is needed at all. `mapsee_ingest_dice_venue.py` reads that and nothing
  else. If you ever need more than the page carries, that is the signal to get a
  real `DICE_API_KEY`, not to borrow one.
- **The stock Squarespace robots.txt disallows `?format=json`, on every site on
  the platform.** It is not a per-site choice a friendly organiser could waive —
  the same file ships with volunteerparktrust.org and sfmamarkets.com alike, and
  `User-agent: *` is the group that applies to us. `mapsee_ingest_squarespace.py`
  read it for months before anyone checked. It now reads the bare collection
  page, which is allowed, and which turns out to carry the exact UTC instant in
  the Google Calendar export link the template renders for humans. What the page
  does NOT carry is any coordinate, so the config's `venue` block went from
  fallback to requirement. The address it does offer glues street to city
  ("1247 15th Avenue East Seattle, WA, 98112"), and `_split_maplink` refuses to
  guess the boundary unless the config's own `city` confirms it — a wrong street
  is a pin on a real road that nobody is standing in.
- **When a listing carries two dates, the one the template shows a human is the
  wrong one.** A MyListing card ships its occurrence on the wrapper and the
  LISTING'S NEXT DATE on the inner span the visitor actually reads — stamped
  identically into every card that listing produces, because it is the badge on
  the tile. 55 of 80 live cards on bainbridgeisland.com disagreed between the
  two. Take the span and a fifty-night music residency becomes the same Friday,
  fifty times, each row well-formed and individually plausible. This is the same
  shape as SLU's `date_time`-on-the-series-start and Squarespace's default pin,
  and the tell is the same: when a source offers two spellings of one fact, find
  a record where they DIFFER before choosing, rather than after.
  `mapsee_ingest_mylisting.py`, pinned in `test_ingest_mylisting.py`.
- **A recurring event with no end date projects pins forever, and nothing
  downstream will ever remove them.** `mapsee_cleanup.py` deletes the PAST; a
  2050 Fourth of July is not past and never will be within anyone's planning
  horizon. Live on bainbridgeisland.com: 423 occurrences in 2026, 184 in 2027,
  then a one-or-two-a-year tail — Hometown Halloween, the Polar Bear Plunge —
  running to 2050. So the horizon belongs at INGEST, where the count that was
  dropped can still be printed. Any adapter reading an expanded recurrence
  needs one; `horizon_days` (400) is the MyListing config's.
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
- **"Order pickup" must be earned by the URL, never by the category.** The
  product used to show that button on any food event with a link; measured, 400
  of 400 upcoming food events pointed somewhere you could not order (352 at
  meetup.com), including a yoga class the classifier had filed under food. The
  second attempt matched a bare `/menu` path, and a dry run over 144 live venues
  pulled in a town website, an events platform and a tourism board — all of which
  have a nav item called Menu — plus real restaurant menus you cannot order from.
  Only known ordering HOSTS and unambiguous `/order*` paths qualify.
  `looks_like_ordering` here and `looksLikeOrdering` in
  `../mapsee/site/js/app.js` must agree; they are verified behaviourally, not
  textually, because a JS regex literal escapes slashes and Python does not. The
  product re-validates whatever this writes, so a disagreement fails safe as a
  line that never renders — and `test_menu_links.py` pins both regressions.
- **A classifier fix reaches the FUTURE only.** `--only-new` means a scheduled
  run can only add, and Wednesday's full run re-reads the SOURCES — neither
  re-applies our own rules to rows already stored. `mapsee_reclassify.py` is the
  backfill, and it carries two guards learned the hard way. It paginates INSIDE
  each time window: without that a busy window silently sampled its first 500
  rows, and two dry runs disagreed (92 changes vs 4) purely because of what got
  cut. And `--apply` refuses to write without `--allow food->fitness`, because
  re-running the classifier replays EVERY rule ever added against rows that
  predate all of them — the first full pass wanted to move a block party to
  fitness and a fitness class to volunteer, on description prose, neither of
  which had anything to do with the fix being backfilled.
- **One adapter ingests BUSINESSES, not events, and that is a different promise.**
  `mapsee_ingest_osm_food.py` takes takeaway places from OpenStreetMap. Every
  other adapter imports something a venue PUBLISHED; a restaurant existing is
  not a listing, which is why venue outreach can honestly say "nobody at your
  end put them there". So it is deliberately narrow and the narrowness is the
  design: no order link, no import; no readable `opening_hours`, no import; and
  it never creates menu items, because we are not the till (which is also what
  keeps `has_storefront` false so a claimed restaurant still gets offered 0%
  pickup). Two live lessons are pinned in `test_osm_food.py`: an unreadable
  `off` rule crashed the first real run, and refusing it matters most of all
  rules because ignoring "shut" advertises a place as open. And of the first 13
  order links found in Seattle, SEVEN were gift-card pages on genuine ordering
  hosts — `NOT_ORDER_PATH` is why that is now six.
- **A failed fetch is not a dead page, and conflating them is expensive.**
  `fetch()` returns `None` for every failure — 403, timeout, non-HTML — and the
  first version of the destination check read that as "gone". The big ordering
  hosts all block scrapers, so `order.toasttab.com`, `www.toasttab.com` and
  `ubereats.com` came back as zero bytes and would every one have been discarded,
  including a Toast URL `test_menu_links.py` itself asserts is valid. Measured:
  the Seattle backfill found 41 order links across 317 candidates with the bug
  and 96 without it. `destination_verdict()` keeps `unknown` apart from `dead`
  and only `dead` — a real 404/410, or HTML we actually retrieved with an empty
  `<title>` — may drop a link. The default everywhere is to keep, because the
  behaviour it replaced (no check at all) was already fail-open.
- **An upsert cannot delete, so a fix to what we WRITE never reaches what is
  already written.** When a link dies the ingest skips the place, no row is
  produced, and the row from when it worked survives with its dead button —
  `--only-new` means the sync would skip it even if one were produced. Two
  levers: `--ignore-cursor` + `full_refresh` re-examines and rewrites existing
  rows, and `mapsee_prune_links.py` cuts a line whose destination is provably
  gone. Neither is automatic; both are dry by default.
- **National CKAN portals publish SPREADSHEETS ABOUT events, not event feeds.**
  Measured 2026-08-22 over all 14 portals: 4,781 datasets examined, 4,332 skipped
  `no_datastore`, **0 candidates**. Sampling the resources says why — 1,634 of
  1,653 are plain files against 19 datastore_active, and the formats run 1,335
  XLSX to 42 CSV. What matches "events" is a register, an attendance count or a
  funding line; `mapsee_ingest_ckan` reads the DataStore API, and teaching it CSV
  would not rescue much because the bulk is spreadsheets. Keep the backend, the
  cursor is cheap and the 19 are real — but do not spend a curation run there
  expecting a country to be filled. Three portals were ADDED that day after
  probing (Slovenia, Latvia, Greece answer `/api/3/action` 200/success); Czechia,
  Spain, Norway, Estonia, Poland and Slovakia were probed and rejected, and that
  is recorded in `CKAN_PORTALS` so nobody repeats it. Unaddressed and known:
  `CKAN_QUERIES` is English only, so a Greek portal is searched for "events".
- **A CONFIG FILE A GUARDED JOB NEEDS IS PART OF THE JOB.** Every ingest step is
  written `if [ -f x_sources.json ]; then ... else echo "no x_sources.json —
  skipping"; fi`, which is right for a source deliberately not configured and
  indistinguishable from one nobody committed. `mapsee_ingest_parkrun.py` was
  written, wired into `aggregate-events.yml`, and `parkrun_sources.json` never
  landed — so every scheduled run printed a friendly skip and the entire
  `running` layer stayed empty in all 20 countries, with no red tick anywhere.
  That is ~2,965 free, weekly, volunteer-run events, 17,790 dated occurrences
  over the 42-day horizon, every one with surveyed coordinates. Audited the rest
  when it was found: the other absent files are runtime STORES (`feeds_events`
  and friends, produced mid-run) and `ckan_sources.json`, which `merge` creates
  when something finally verifies — so parkrun was the only real one, and
  `test_ingest_parkrun.py` now asserts every guarded source config is present.
- **parkrun start times are NOT in the feed, and they must not be guessed.** They
  vary by country and by SEASON — a UK 9am is an Australian 7am in summer, and
  some UK events start at 09:30. The adapter emits an all-day event and says
  "Start time on the event page." rather than inventing one; `start_times` in the
  config is per-country and deliberately empty until somebody checks a country.
  Its `countries` map is the same discipline one step over: parkrun's country
  codes are opaque integers and the feed carries only a domain, so `97 -> GB`
  is written down as data instead of parsed out of `parkrun.org.uk` by a regex
  that would have to know org.uk is not UK.
- **OFFSET paging against our own database gets dearer every page, and it fails
  the day the catalog outgrows it.** `mapsee_indexnow` walked new events with
  `limit=1000&offset=N`, which asks Postgres to produce and DISCARD N rows before
  returning the next thousand — so a full walk costs quadratically in pages. It
  worked for months and died on 2026-08-22 at exactly `offset=6000`, once a
  26-hour window held more than 6,000 new events (the sweeps of the previous days
  are what put it over). The fix is a KEYSET: ask `created_at > the last one I
  saw`, which costs the same on page seven as on page one. The cursor must be
  `(created_at, id)` and not `created_at` alone — a merge lands hundreds of rows
  on one timestamp, and a keyset on a non-unique column either serves that
  timestamp for ever or skips its tail. `test_indexnow.py`. The other `offset=`
  callers in this repo are third-party APIs (Socrata, ODS, Recreation.gov) and
  bounded, so they are not the same bug.
- **A KEYSET FIXED THE COST PER PAGE AND LEFT THE NUMBER OF PAGES UNBOUNDED,
  which is the half that broke next.** `mapsee_indexnow` walks every indexable
  event created in the last 26 hours; measured 2026-08-26 that window held
  ~261,600 rows, so the walk was ~262 requests against the ANON role's ~3s
  ceiling and one slow page ended it. Three rules fell out. **The walk is
  CAPPED and runs NEWEST-FIRST** — IndexNow is a freshness hint with a 10,000
  URL protocol ceiling and everything in the window is in the sitemap anyway,
  so announcing the newest ten thousand is the honest submission; ascending, a
  capped walk would have kept the STALEST rows and dropped everything that had
  just landed. **A timeout re-asks the SAME page before shrinking one** —
  halving is the right answer for OFFSET paging, where the cost IS the
  discarded prefix, and with a keyset (measured flat at 0.4s from page 1 to
  page 60) going 1000 to 125 only buys eight times the requests and eight times
  the exposure; `PAGE_MIN`'s own comment already said this and had no other
  lever. **And out of levers, it announces what it has** rather than raising.
- **"A SUBSET OF THE SITEMAP" IS AN INVARIANT TWO FILES HAVE TO KEEP, AND ONE
  OF THEM MOVED.** `fetch_new_event_ids`' own docstring says its predicates are
  "lifted verbatim from sitemapEvents()" and that IndexNow must announce a
  subset, "never a superset". Then ../mapsee 0194 added `pin_only: is.false` to
  both sitemap queries and nothing added it here, so this job spent months
  announcing `/e/` pages for drinking fountains and playgrounds that the
  sitemap deliberately withholds. Nothing could report it: both ends answer
  200 and every URL is real. It got materially worse the day "a thing that
  never shuts is not a listing" moved several hundred rows per metro into
  furniture. A comment saying two things must agree is not a mechanism;
  `test_indexnow.py` now asserts the predicate set.
- **A FAILED READ IS NOT A FAILED RUN, and `sys.exit(1)` cost seven domains
  their daily push.** That same job reads the events for mapsee.me and then
  submits /c/ landing pages for mapsee.me and the six other doors — which do
  not need that query at all. One 57014 deep in the walk exited the process
  before a single landing page went out. The events are the time-critical half
  and the landing pages are the independent half; losing one must not lose the
  other. It still exits non-zero, because a job that has silently stopped and a
  job that reported what it could must not look the same.
- **A STEP CANCELLED BY `timeout-minutes` SKIPS EVERY STEP AFTER IT, INCLUDING
  THE ONE THAT SAVES THE WORK.** Three live instances, all found together on
  2026-08-26 and all reading as "cancelled" rather than "failed":
  `feeds` at exactly 4h00m lost `mapsee_link_series` nightly (above);
  `curate-catalog`'s discover step ran 05:36:58 to 07:07:00 — its ninety-minute
  cap to the second, and the run before it 04:06:37 to 05:36:41, the same
  ninety — and skipped **Coverage after** and **Commit new sources**, so every
  source it had verified went in the bin, under a comment on that very cap
  claiming "a run that hits it still commits everything it proved before
  stopping". (That job's runtime is WILDLY variable rather than uniformly
  over: 2, 13, 41, 12, 52 minutes on the five days before, then two 90s. So
  the cap is not hit every run — it is hit whenever the `osm` cursor lands on
  dense metros or Overpass is slow, which no amount of tuning predicts. That
  is the argument for a budget rather than a bigger number.) And
  `osm-food`'s Paris job ran 5h30m against a
  330-minute cap and skipped its sync, its cursor slice and its hand-up, so the
  sweep was discarded AND the cursor never moved — next week it would start in
  the same place and do it again, while the other seven metros finished in one
  to three hours. **The job cap is a CEILING, not a plan**: end the work
  yourself with time left to save it (`--max-minutes` on the ingest, a shell
  `DEADLINE` between backends in curate), and put `always()` on the steps that
  save. A wall-clock budget is needed where a per-item cap is not enough,
  because the cost is one fetch per venue against servers we do not control and
  is not predictable from the candidate count.
- **AND THE BUDGET GOES WHERE THE TIME GOES, WHICH IS NOT WHERE THE LOOP IS.
  Twice, in the same fix.** The first attempt at the curate budget put a
  deadline between BACKENDS in the shell loop, which reads as obviously right
  and would never once have fired: from that run's own log, socrata took 6
  SECONDS, ckan 101, mobilizon 3, and `osm` the remaining 88 — and osm is
  deliberately LAST, so nothing follows it to check anything. Moved inside
  `_discover_osm`'s METRO loop it still did not fire: the next run printed
  nothing whatsoever from that backend before its 90-minute cancellation,
  because one metro is an Overpass call plus a LIVE FETCH PER VENUE and a dense
  metro is hundreds of them — a single iteration of the loop you can see
  outlasts the whole budget. It is checked in the per-VENUE loop now.
  **Bounding the loop is not the same as bounding the work**: find the
  iteration that spends the time, and if that iteration is itself a loop, keep
  going down. Its cursor half matches: a metro abandoned part-way is UNREAD and
  the cursor stays before it, exactly as for one Overpass never answered for,
  and re-probing is cheap because the ledger already holds the dead ends that
  pass found. `test_discover_osm.py` pins both.
- **`always()` IS THE HALF THAT ACTUALLY SAVES THE WORK, and it is worth having
  even when the budget is right.** Proved in production the same day: run
  33012688812 was cancelled at its 90-minute cap with the budget still
  mis-sized — and committed `70501d9`, 266 lines of advanced cursors plus 24 of
  fresh ledger, where the two runs before it had committed nothing at all. A
  budget reduces how often you rely on that; it is not a substitute for it.
- **A CURSOR MUST ADVANCE BY WHAT WAS EXAMINED, and "the window's length" stops
  being that the moment anything can stop early.** `osm-food` set
  `cursor = start + len(window)` BEFORE the loop, which was right while the only
  exit was finishing it. With a budget it would march past venues nobody looked
  at, so it moves after the loop and counts. Pinned in `test_osm_food.py`
  through the REAL `main()`, because that is where it lives — the same gap that
  cost `mapsee_ingest_osm_amenities` two production runs this month. Writing
  that case found one more of the family: `m.CURSOR_PATH = <tmp>` does NOT
  redirect `load_cursor`, whose signature is `path=CURSOR_PATH` — a DEFAULT
  ARGUMENT bound once at def time — so the test read the repo's committed
  cursor and reported Paris=178 for a run that examined nothing.
- **`raise_for_status()` throws the diagnosis away.** That failure reported
  `500 Server Error: Internal Server Error for url: ...` and nothing else,
  because requests never looks at the body — so the one thing that says WHICH
  5xx it is, and therefore whether to take a smaller bite or wait for the edge,
  was discarded at the moment it mattered. Exactly what `mapsee_health_check`
  learned when it reported a 57014 as "one or more sources have gone quiet".
  Quote the server back: `_explain()` here, and the same rule everywhere.
- **Two different 5xx come back from PostgREST and they want opposite things.**
  A statement timeout (`57014`) means we asked for too much, and the answer is a
  SMALLER bite — `mapsee_cleanup.py` halves its batch, and retrying the identical
  request just burns the run's time budget doing what already failed. An upstream
  503 — `upstream connect error or disconnect/reset before headers`, Envoy unable
  to reach Postgres — means the request never happened, and the same one works
  once the edge recovers. Both are `>= 500`, so anything talking to Supabase has
  to tell them apart; getting it backwards is silent in either direction. On
  2026-08-14 a roughly hour-long Supabase outage took out three scheduled jobs,
  and only `mapsee_health_check._rpc` (which has always retried 5xx, and reports
  "could not run" as distinct from "a source has gone quiet") failed in a way
  that said what had happened. `mapsee_menu_links.sb` died on a nine-frame urllib
  traceback ending in `HTTP Error 503`, which reads like a bug in that file;
  `mapsee_cleanup` gave up on the first one. Both retry now, `test_cleanup.py`
  and `test_menu_links.py` pin the distinction, and a sustained outage still
  FAILS — it just fails in one greppable sentence that says the work is safe to
  retry. Do not convert these to `exit 0`: a provider being down for an hour and
  a job that has silently stopped must not look the same.
- **A minus sign is an option, not a number.** The international sweep spawns
  each ingester with `--latlong=VALUE`, fused, because argparse exempts only
  `^-\d+$|^-\d*\.\d+$` from option parsing and a latlong has a comma in it. Sent
  as two tokens, `--latlong -33.8688,151.2093` died with "expected one argument"
  for every metro south of the equator — all of Australia, New Zealand and South
  Africa, 22 of 165 metros, on BOTH sources, twice a week. `run()` prints the
  non-zero exit and continues by design, so the sweep still closed with "swept
  165 metros" and a green tick. `test_sweep_global.py` pins the argv form.
  That exemption is the INTERPRETER'S rule and it changed: Python 3.14 made the
  matcher a prefix match (`-\.?\d`), so the split form parses there and the
  test's own premise went false — red on a dev box, green on CI, with the code
  correct. `tests.yml` pins 3.12, where the split form still dies, so the fusing
  stays load-bearing; the test now REPORTS the running argparse's behaviour and
  asserts only what holds on every version. A test that hard-codes a standard
  library behaviour is a test with an expiry date on it.
- **A date-dependent assertion is not flaky, it is wrong one day in seven.**
  `test_osm_food.py` checked that a Saturday-only venue resolves LATER than a
  seven-day-a-week venue — a proxy for "not today" that is false on Saturdays,
  when both resolve to today and `>` fails on equal dates. It went red every
  Saturday and green again on Sunday, which reads as flakiness and gets
  re-run rather than fixed. Assert the property (the row lands on a Saturday),
  never a comparison that happens to hold on the day you wrote it.
- **A curated city list is only as complete as its last audit, and Seattle has
  TWO market operators.** Neighborhood Farmers Markets and the Seattle Farmers
  Market Association both run markets; `market_sources.json` was built from the
  first and silently missed three of the second's five (Central District, Madison
  Park, South Lake Union) until 2026-08-12. National coverage does not save you
  here — the USDA directory carries Central District under its former name,
  Madrona. Enumerate every operator's own sitemap when auditing a city.
- **Never add `pull_request:` to a workflow that reads secrets.** `tests.yml` is
  the one workflow safe on forks, because it reads none.
- **A search endpoint that ignores every paging parameter still answers 200.**
  `bikereg.com/api/search` returns exactly 100 events and ignores `page`,
  `offset`, `count`, `MaxResults`, `startdate`/`enddate`, `state` and `radius`
  alike — and reports `ResultCount: 100`, so nothing in the envelope reveals the
  1,267 events withheld. An adapter built on it would look complete for ever.
  The GraphQL gateway BikeReg's own docs recommend is cursor-paged with a real
  `hasNextPage` and a `totalCount`, which is the whole reason
  `mapsee_ingest_bikereg.py` is on it. Same lesson as RunSignup's silent
  1,000-row cap, one step worse: there the tail was detectable by counting.
- **A timestamp's offset can belong to the SERVER, not the event.** BikeReg
  stamps every `startDate` `00:00:00` with `-04:00` or `-05:00` — including a
  Kailua-Kona race whose own `eventTimeZone` says "Hawaiian". Read as an instant
  that race lands at 18:00 the day BEFORE. Only the date is true. The tell was
  the same as MyListing's two dates and SLU's series start: find the record
  where the two spellings DISAGREE before choosing.
- **One source id can mean many events, and EventStore deletes on the
  collision.** 39 of BikeReg's 1,246 ids come back once per occurrence date.
  `upsert` keys on `(source, source_id)` and POPS the stored record when the
  fingerprint moves, so a bare id makes each occurrence delete the one before
  it: 1,269 ingested, 1,148 surviving, every casualty an earlier date of a
  series. Put the occurrence date in `source_id`. This is the Localist duplicate
  bug running backwards, and the `rekeyed` counter is what makes it visible.
- **One malformed record cost an entire site, and the log said "FAILED".** The
  Events Calendar returns `venue` as a dict, as `[]`, and as `[{...}]` from the
  SAME site (105/4 of 109 on bicyclecolorado.org), and `image` as a dict or the
  bare boolean `false`. `or {}` covers the empty list — which is why it read as
  correct — and a POPULATED list raises on the first `.get`. There was no
  per-record try, so the exception unwound to the per-site handler and Bicycle
  Colorado ingested 0 of 105 placeable events while printing something that
  looks like a network fault. `_obj()` normalises the shapes;
  `ingest_site` now skips and COUNTS a bad record. `test_ingest_tribe.py`.
- **Reading a source correctly BY ACCIDENT is not reading it.** schema.org spells
  it `location`; WP Event Manager writes `Location`, on every page it renders.
  `mapsee_ingest_jsonld.py` asked for the lowercase key, got None, and fell back
  to the config's `venue` block — which produced the right pin, so nothing looked
  wrong. What was actually there is `{"name": "-", "address": "-"}`, the
  placeholder that CMS renders for a location nobody filled in, on all 95 of The
  Royal Room's event pages. Read the key without the second rule and it gets
  worse, not better: `"-"` is TRUTHY, so it survives the `if not parts.get(k)`
  gap-fill test, the venue block stops filling, and `"-"` reaches the geocoder as
  a street. The rule is the Squarespace one — a location with no address TEXT is
  not a location — and the fix is the config's venue, never a coordinate
  blocklist. `_ld_get` and `_meaningful`, pinned in `test_ingest_jsonld.py`.
- **A single-venue calendar's `startDate` may carry no offset at all, and that is
  the venue block's real job.** WP Event Manager emits `2026-08-19 19:30:00` —
  naive local, space-separated, no zone, on every event. It becomes an instant
  only because `venue` supplies coordinates the sync turns into a timezone; read
  as UTC, a 7:30pm show is served at 12:30pm. So on a site like this the venue
  block is not a fallback for a missing pin, it is what makes the TIME true, and
  the two halves live in different files with no single place either can be seen
  to be wrong. The round trip is asserted end to end.
- **A venue calendar carries entries that are not events, and they are the
  best-formed rows in the feed.** The Royal Room posts "CLOSED FOR MAINTENANCE"
  and "Closed for Private Event" as event_listing posts with real dates, because
  a notice is the only thing that CMS can put on a calendar — 5 of its 95. They
  classify as music and pin at the venue like everything else, so they would tell
  somebody a shut venue is open. `skip_title` is matched on the NAME and anchored:
  the other available tell, a 00:00 start, is shared by every one of them and
  would also throw away a New Year's Eve show. Same family as Traders Village's
  car show — the feed works, and it is not what it looks like.
- **The catalogs cannot see the long tail, and the map can.** Socrata, CKAN and
  joinmobilizon list DATASETS and INSTANCES, so discovery could only ever find
  what somebody had published to a data portal — never a gallery, a zendo or a
  brewery with a Tuesday quiz. Measured against the 1,181 hand-curated Seattle
  sources uncouchme.com publishes: 745 distinct hosts, **648 appearing exactly
  once**. That tail is the bulk of a city and no query would have reached it.
  It is, however, ON THE MAP — OSM tags what programmes things and a third of
  them carry a `website`. 1,656 programme-venues in the Seattle bbox, 599 with a
  site; 98 of those hosts were on the hand-built list too (so the method finds
  the same real places a human found) and 387 were not, of which 114 had a
  calendar on a platform this repo already reads. `discover osm`. What it
  CANNOT find is a Meetup group, an Eventbrite organiser, a blog or a listings
  column — 638 of that 745. It complements hand curation; it does not replace
  it, and a metro it has swept is not a metro finished.
- **Detecting a site BUILDER is not detecting a calendar.** `tribe`,
  `wp-event-manager` and `my-calendar` are calendar PLUGINS: finding one means
  the site has an events system and a feed follows. Squarespace and Wix are how
  the whole site is BUILT, so they match on every page of every site using them,
  including a hand-written "What's On" with nothing behind it. On the first
  London sweep 4 of the 5 candidates that failed verification were Wix sites
  found that way, and White Bear Theatre's turned out not to run Wix Events at
  all. A builder now has to show its events app — Squarespace names the
  collection in the body class, Wix routes through `/event-info/` — and the two
  need different config shapes, because a WordPress `/event/<slug>/` pattern
  matches nothing on Wix.
- **A bot challenge does not answer 403, and the 200 is the dangerous one.**
  dmhsus.org (SiteGround) answers **202** with an `sgcaptcha` body on every path
  including `/robots.txt`, so permission cannot even be established.
  theblackaltar.org's WAF answers a clean **200** with a spinner saying "One
  moment, please…" — on `/wp-json/…/events?from=…`, while the SAME endpoint
  without a query string returns real JSON. A 200 is worst because HTML arrives
  where JSON was expected, and the honest readings of that are "broken feed" and
  "calendar with nothing on", neither of which happened. Match the words, not
  the status. Same policy as sfbike.org: a challenge is a NO, and we do not
  impersonate a browser to get round one.
- **"We cannot read it" is two findings, and only a SECOND NETWORK tells them
  apart.** A publisher who turned bot management on is a NO to honour; a WAF
  scoring the caller's address is not a decision anybody made about mapsee, and
  this pipeline runs from GitHub's addresses rather than from wherever somebody
  happened to probe. `catalog_probe.py` reports what a URL serves AND the egress
  IP it was seen from; `probe-url.yml` runs it from a runner. Both sites that
  prompted it came out differently: dmhsus.org answers 202+sgcaptcha here and
  **403 from GitHub**, on every path including /robots.txt — closed, and an
  earlier note in `jsonld_sources.json` guessing "IP reputation" from the
  caller's IP appearing in the challenge URL was WRONG and is corrected.
  theblackaltar.org answers this sandbox with a spinner and a runner with a real
  200 — a working calendar that would have been retired as dead. Do not record
  either verdict from one vantage point.
- **A metro Overpass never answered for is UNREAD, and the cursor must not move
  past it.** `overpass_venues` returned `[]` for "the endpoint refused" and for
  "this bbox genuinely has nothing", which is the same conflation as `fetch()`
  returning None for every failure. On 2026-08-19 nine of ten metros hit
  connection resets after the day's earlier sweeps had used the public
  endpoint's patience; every one printed "0 venue(s) publish a website" and the
  cursor advanced past all nine — losing Adelaide, Canberra, Dublin and six more
  for **78 runs**, about eleven weeks at three a day. It returns None now, the
  cursor advances only past metros actually READ, and a metro refused three runs
  running is skipped LOUDLY rather than wedging the sweep on one bbox for ever.
  Rate-limiting is the normal failure here: back off between big sweeps rather
  than assuming a quiet endpoint.
- **`webcal://` is `https://` wearing a hat, and `requests` has never heard of
  it.** It is the standard "subscribe to this calendar" scheme and what parish
  and club sites publish, so discovery proposes it verbatim — and then
  verification dies on `InvalidSchema: No connection adapters were found`, which
  reads as a broken feed rather than as a URL nobody normalised. Eight of one
  sweep's candidates were lost that way and all eight passed once the scheme was
  swapped: **833 future events**. Normalised in `catalog_discover_osm._https` and
  again in `mapsee_ingest_ics._fetch_ics`, because a config edited by hand can
  carry one too.
- **A feed can pass verification and still put nothing on the map.** "Returns
  future events" is what `verify` asks; `mapsee_ingest_ics` separately DROPS a
  VEVENT carrying neither GEO nor LOCATION, and that count only appears once the
  source is configured and running — Seattle Parks Foundation was 20 of 30
  unplaceable and merely looked two-thirds empty. `catalog_probe.py --verify`
  reports the placeable fraction before the merge, which matters most for a feed
  nobody can open locally.
- **A refusal is not a fact about the site, so it must not be written down as
  one.** theblackaltar.org served one probe and challenged the next, seconds
  apart from the same IP, then blocked steadily once probed repeatedly. "This
  site has no events page" is stable and worth parking in the ledger for the
  90-day TTL; a challenge, a timeout or a 5xx is how the site felt about us for
  one request. Parking those would retire a working calendar over a bad moment —
  and with the metro cursor, nobody would look again for months. They cost one
  request to re-probe, so `_discover_osm` simply does not record them.
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
- **A regex that FINDS a JSON-LD block is not a parser that can READ it, and
  discovery must use the parser.** The Royal Lyceum's programme is 40 well-formed
  `Event` blocks, and `json.loads` refused every one of them: a raw control
  character in a description, which strict JSON rejects and `strict=False`
  accepts. The fingerprint asked by regex and said yes; `mapsee_ingest_jsonld`
  asked by parsing and got nothing — so discovery proposed the page, verification
  reported "no schema.org Event blocks found", and neither end could see the
  other was right. `_parse_ld` now retries non-strict (worth it on its own: 40
  events on one page), and `_has_event_block` parses with the adapter's own
  helpers so the two cannot drift again. Same family as `looks_like_ordering`
  having to agree with `looksLikeOrdering`.
- **Detecting a calendar plugin tells you it HAS events, not what it will hand
  you.** Events Manager was routed to the JSON-LD adapter because it is a
  WordPress events plugin; the Bongo Club's page carries `WebPage` and `WebSite`
  and no `Event` at all, so every candidate found through it failed for a reason
  that had nothing to do with the site. What it does have is an iCal export on
  any calendar page — `/events-main/?ical=1` is 3.1MB where the site root's is
  27KB, which is why `FEED_TEMPLATES` interpolates `{cal}` and not just
  `{origin}`. Check what a platform EXPORTS before assigning it an adapter.
- **A deep event page can be a better crawl seed than the index, and only
  measurement says which.** Landing the fingerprint on `/events/guys-dolls`
  makes a config that dies silently the day that show closes, so `_prefer_listing`
  walks up to `/events/`. But the Lyceum's index is JS-rendered: it yields **0**
  links matching the crawl pattern where the single show's page yields 4. The
  guard only takes the parent once it has been fetched and shown to link at least
  two siblings — a parent that is not an index is a source that ingests nothing,
  which is the same rule as never merging a constructed feed URL unproven.
- **A platform can imply a feed URL the page never links.** My Calendar (100k+
  WordPress installs, and what small arts orgs and congregations actually reach
  for) publishes iCal at `/?feed=my-calendar-ics` and links it from nowhere, so
  scraping for an `.ics` href finds nothing and a perfectly readable site looks
  unreadable. `FEED_TEMPLATES` constructs it — and then FETCHES it, because a
  constructed URL merged unproven is a source that ingests zero.
- **`cmd_merge` rewrote whole files to add one line.** It dumped at `indent=2`
  into files stored at `indent=1`, so adding a single source to
  `ics_sources.json` produced a 3,653-line diff — every line of a 260-entry file
  re-indented around it. A merge nobody can read is a merge nobody checks, which
  is the opposite of what the ledger and the verify step are for. `_file_indent`
  reads the file's own shape and writes it back that way.
- **A calendar PAGE is often not the whole calendar; the sitemap is.** The Royal
  Room's `/events/` renders 12 cards and loads the rest over admin-ajax, so an
  adapter pointed at it imports 12 of 95 and looks complete. The site's own
  event-post sitemap — declared in its robots.txt — is all 95 with no paging at
  all. `link_pattern` is a regex over whatever the listing URL returns, so a
  sitemap is a valid `listing`, and usually the better one. Same shape as
  BikeReg's search endpoint that answers 200 with a silent hundred-row ceiling.
- **Cloudflare's bot challenge is a NO, and curl getting through is not a
  second opinion.** sfbike.org's robots.txt allows everything (`User-agent: *`,
  `Allow: /`, Content-Signal `search=yes`), and the endpoint still answers
  python-requests with a 403 "Just a moment..." while answering curl with a 200
  — identical URL, UA, headers and pacing. That is a client-FINGERPRINT block,
  not a rate limit, and beating it means impersonating a browser to defeat bot
  management somebody deliberately turned on. Declined in
  `tribe_sources.json._not_included`, same line as the borrowed DICE key.
- **A lens can be starved by its CLASSIFIER rather than by its sources.**
  fleabop is "Flea Markets, Clothing Swaps and Vintage Near You" and held 3,469
  upcoming events of which 2 named a flea market and 0 named thrift, vintage,
  swap or antique — 46.6% were farmers markets, and `market_sources.json` is a
  farmers-market file end to end (85 uses of "farmers", 0 of "flea"). But ~230
  events DID name second-hand retail; they were sitting on community, music,
  other and arts because `_SECONDARY_RX["market"]` asked only for English and
  only for the shopping words. Widening it moved 123 of them onto the lens with
  no new source at all. Before curating for a thin lens, check whether the
  supply is missing or merely unlabelled. Non-English matters here more than
  anywhere: Flohmarkt, brocante and vide-grenier are the bulk of it, and
  Flohmarkt COMPOUNDS (Garagenflohmarkt, Frauenflohmarkt) so it must match as a
  suffix, without a leading `\b`.
- **A venue's event calendar is often not the thing the venue IS.** Traders
  Village is one of the largest flea markets in the US; all 10 entries on its
  Events Calendar are a car show, a pet adoption, a corn maze and a Halloween
  trail. The market itself is never listed because it just happens every
  weekend. Filing that calendar under `market` would put a corn maze on fleabop
  and call it a flea market. Declined on the same grounds as Marin's venue-less
  rides: the feed works, and it is not what it looks like.
- **A source that runs one day a week has no retry, and nothing notices.** The
  OSM Overpass sweep is 245 calls, so it carries `run_weekdays` — and with `[0]`
  that was a single window per week, inside the longest job in the repo. Monday
  2026-08-10 the `feeds` job hit its 120-minute timeout, and the entire
  international market catalogue was absent for the week: Berlin's 11 OSM
  Flohmärkte sat in OSM with parseable `opening_hours` inside a configured
  bbox and never reached the table. Nothing reported it and nothing could — the
  health check sees `external_source='mapsee'` as one bucket, so it can say the
  pipeline stopped but never that ONE source did. Now `[0, 4]`, so a lost run
  costs three days rather than seven.
- **A fuzzy search's keyword is not a classification, and `market` is the word
  that proves it.** Meetup's `eventSearch` is not a phrase match, and the
  adapter files whatever a keyword returns under that keyword's category. For
  "farmers market"/"night market" that meant every `market` event in Berlin was
  a Meetup row and NONE was a market — three stand-up nights, a Magic: the
  Gathering league, a run club, an e-commerce breakfast, a meditation. "Market"
  is a business word before it is a shopping one. The demotion in
  `map_category` is gated on PROVENANCE (`_from_keyword_sweep`), not just text,
  and that is the load-bearing part: "Randolph Street Market" fails a market
  regex too, so a text-only rule would have thrown away the real supply to fix
  the fake. Same shape as `_WEAK_KEY_FOR_FITNESS`, one level up.
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
- **A config's `category` is a DEFAULT, so the right value depends on whether
  the calendar is pure or mixed.** Measured over live titles from nine cycling
  clubs. A PURE ride calendar must state `fitness`, because the classifier
  cannot recover a ride from its name — with a `community` base, all 50 of
  Bicycle Colorado's distinct titles stay on community, since `_FITNESS_RX` has
  never heard of "Velo", "Gear Hub" or "TNT Tuesday Night Thunder". A MIXED
  advocacy calendar must state `community`, because `fitness` is not in
  `_PROMOTABLE_TO_VOLUNTEER` and nothing downstream can rescue what lands there:
  with a `fitness` base, Bike East Bay puts a stadium valet shift and a
  phone-banking session on wegosie. From `community` the promotion rules sort
  it — rides to fitness, volunteer shifts to volunteer, the rest honestly
  community. Fewer events reach the movement lens and none of them is a lie.
  Read a source's actual titles both ways before choosing.
- **The `kids` layer is fed by a REGEX, not by sources, and it was missing a
  third of its supply.** All 58 library feeds in `ics_sources.json` are filed
  `learning` — correctly, because that is what a library calendar is as a whole
  — so `_KIDS_RX` is the only thing that gives plansie's kids layer anything at
  all. Measured over 1,347 distinct live titles from eight public library
  feeds: 124 promoted and **132 more were plainly children's or teen events
  that did not**. The gaps were systematic — teen/tween absent altogether,
  `lego\s+(?:club|build)` missing "LEGO in the Library" (the programme is named
  for the brick alone), `baby\s+(?:time|rhyme|song)` missing "Baby Lap Sit",
  and "Read to the Dog" matching nothing despite being a staple. An explicit
  age range ("ages 4-18", "grades K-2") is now a signal too, because it is how
  a library says "for children" without using any of the words. Before adding
  sources for a thin category, check whether the supply is already arriving
  under another key — the same lesson `market` taught, one layer down.
- **Widening a kids rule catches the adults' version of the same programme.**
  "Adult LEGO® Club" is a real listing on a real library calendar: libraries run
  the identical session for grown-ups and say so in the title. `_NOT_FOR_KIDS_RX`
  withholds the promotion, never moves anything, and the volunteer rule still
  runs first so "Teen Volunteer Corps" lands on volunteer rather than kids.
- **`brunch` was the commonest food word on the map and the food rule did not
  have it.** 615 upcoming events with "brunch" in the title, 158 reaching
  oneday.cafe, 457 sitting on community (235), theater (78) and music (37) —
  and oneday is the second-thinnest lens with `food` as its ONLY category, so
  that was a third of its potential supply. A brunch is a meal whatever else is
  happening at it: "Golden Girls Drag Brunch" stays THEATER and reaches oneday
  too, which is the case the secondaries column exists for. `taproom` and
  `distillery` were the same omission one size down — `brewery` was there and
  its siblings were not. The check that matters when widening a secondary is
  that no PRIMARY moves: measured over 473 live titles, 0 did.
- **Counting records is not checking dates.** wisconsinbikefed.org's iCal export
  parses beautifully — 50 VEVENTs, 46 with LOCATION, 41 with a GEO line, real
  riding in the titles — and every event in it is in the PAST: 2025-11-02 to
  2026-04-18, read on 2026-08-16. It was configured on the strength of those
  counts, ingested 0 of 50, and came straight back out. A feed's shape says
  nothing about its horizon. `empty` is not `fail`, so it is recorded in
  `_not_included` as a stale export to re-probe in a season, not as a dead feed.
- **AN RPDE FEED PAGES FROM THE OLDEST END, so reading page one tells you
  nothing — and it lies in BOTH directions.** OpenActive publishes over RPDE:
  items ordered by modification time ASCENDING, followed via `next` until the
  page repeats. Measured 2026-08-26: Our Parks reports **0 future sessions on
  page one and 854 when walked to the end**; England Netball reports 0 either
  way and has been dead since **2019** — 14,798 records, still answering 200,
  still listed in the official catalog seven years later. A verifier that stops
  at page one retires a working feed and configures a dead one. Same family as
  BikeReg's search endpoint answering 200 with a silent hundred-row ceiling, and
  as "counting records is not checking dates". `walk()` in
  `mapsee_ingest_openactive.py`; `test_ingest_openactive.py` pins it, along with
  the `state: "deleted"` tombstone that must REMOVE a cancelled session rather
  than be dropped on the floor.
- **A whole national catalogue can sit on one licence, and it is worth
  checking.** All 127 OpenActive dataset pages that parse — across five
  independent catalogs and 175 landing pages — are CC-BY 4.0. The attribution
  that licence requires is written into every row's description by the adapter;
  it is the term we hold the data on, not a footer. What it also bought: these
  feeds carry their own coordinates (499/500 with `geo`, 500/500 with a
  structured `PostalAddress` on one publisher; 91% across sixteen), and outside
  the US that is the difference between a source and nothing at all — the only
  geocoder here is US Census, so `mapsee_ingest_tribe` reported "kept 43 events"
  for Calgary and placed zero of them.
- **A `ScheduledSession` does not know its own name, and 93 of 127 publishers
  are not publishing events at all.** An OpenActive occurrence carries a date
  and a `superEvent` pointer; the title, place, price and activity live on the
  `SessionSeries` it names, so that shape needs both feeds read and joined —
  and the occurrence must win on the DATE, or every week of a fifty-week block
  inherits the series' own `startDate` and lands on one Monday (MyListing,
  exactly). Of the 127, only 34 publish dated sessions; the other 93 publish
  `FacilityUse` and `Slot` — bookable courts and halls. A bookable badminton
  court at 19:00 is an empty room somebody may or may not take, and mapping it
  is the Traders Village mistake. Also unread and worth ~30,000 records: ~13
  Legend-platform publishers ship a `SessionSeries` feed and NO occurrence feed,
  so their sessions exist only as an `eventSchedule` PATTERN. Expanding that is
  the same job `mapsee_ingest_markets` and `mapsee_ingest_parkrun` already do,
  and it is the highest-value thing to add here next; `_not_included` in
  `openactive_sources.json` has the measurements.
- **A source can hand you a SENTINEL date, and `mapsee_cleanup.py` can never
  remove it.** British Cycling's Let's Ride returns 172 rides dated in the
  **year 2500**. Cleanup deletes the PAST; 2500 is not past and will not be
  within anyone's planning horizon. It is the "recurring event with no end date
  pins forever" trap arriving pre-made from the publisher rather than built by
  us, and the defence is the same one MyListing needed: a horizon at INGEST,
  where the count that was dropped can still be printed.
- **MOST CIVIC PLACES HAVE NOTHING WORTH READING, and pretending otherwise is
  how you ruin a map.** `mapsee_ingest_osm_amenities.py` imports eight OSM
  selectors — playgrounds (1,006,477 worldwide), public artwork (366,557),
  drinking water (365,393), outdoor gyms (100,841), little free libraries
  (46,908), bike repair stands (23,216), food banks (4,938), give boxes (1,478).
  A drinking fountain is a drinking fountain: its pin already carries the whole
  of what the row knows, so a sheet costs a tap, the bottom of the screen and a
  history entry to answer a question nobody asked. So the adapter sorts its own
  output. A row carrying a fact somebody could ACT on — hours, an operator, a
  fee or access rule, an accessibility note, a website, a real description, or
  for a sculpture its artist — is an ordinary standing row. Everything else is
  `pin_only`: ../mapsee 0194 draws it and does nothing else. **A NAME IS NOT A
  FACT** — "Sarah's Book Box" tells you nothing a book icon on that corner did
  not. At OSM density any rule that merely DEMOTES these is not enough:
  `events_near`'s pool is capped at 800, so four hundred playgrounds would bury
  the gig three streets away. They are not in that pool at all. The verdict is
  a PREFILTER rather than the last word: ../mapsee 0195 hands the client the
  description and the image and it decides again, the way the product
  re-validates every order link this repo writes. An OSM `image` or
  `wikimedia_commons` file is content on its own — for a sculpture it is the
  best content there is — and promotes a row out of furniture with no other
  fact required.
- **A DENY-LIST WHOSE COMMENT DESCRIBES AN ALLOW-LIST WILL BE WRONG FOR EVERY
  ADAPTER ADDED AFTER IT.** `to_row` appends "🔎 More on this show: <google
  search>" and says in its own comment that this is for the big-venue
  aggregators — but the test is `not _src.startswith(("opendata:", "venue:",
  "ics:", "program:"))`, so all three OpenStreetMap PLACE adapters inherited it
  by default. A charity shop and a drinking fountain do not have support acts.
  On food and second-hand that was merely odd; on civic amenities it was
  load-bearing, because ../mapsee 0195 decides whether a pin OPENS by asking
  whether anything survives stripping the row's boilerplate — and a Google
  search link is not a fact about a fountain, so every furniture pin on earth
  would have become clickable. `osm-` is excluded now. It was found by
  generating the REAL stored description for a bare fountain and reading it,
  which is the only way it could have been: the adapter's own output was
  correct, and the line was added two files later.
- **A MIGRATION'S CODE MERGES BEFORE THE MIGRATION RUNS, and nothing sequences
  the two.** `to_row` writes every column for every adapter, so a `pin_only`
  that ../mapsee has merged but not yet applied used to cost not one feature but
  the WHOLE NIGHT: PostgREST answers `PGRST204 Could not find the 'pin_only'
  column`, a 400 is not retryable, so every batch of 50 fell straight through to
  the row-by-row isolation, every one of those rows failed identically, and all
  thirty-seven adapters wrote ZERO having made fifty times the requests to do
  it. `upsert` now reads the column name out of PostgREST's own message, drops
  it once with a `::warning::`, and carries on — 4 requests and 120 rows where
  it used to be 123 requests and nothing. `test_sync_unknown_column.py` pins
  both halves, including that a genuinely poisoned row is still isolated and not
  mistaken for a missing column. The ONE place this tolerance is wrong is
  `osm-amenities.yml`, which refuses to start without the column: there
  `pin_only` is not a nice-to-have but the entire point, and importing furniture
  without it puts every drinking fountain in the metro into the Nearby list.
- **A STEP ADDED TO A LONG JOB IS ADDED TO ITS CRITICAL PATH, and `feeds` was
  already at 3h29m against a 240-minute cap.** OpenActive walks twelve RPDE
  feeds to their end, which is honest work and costs 31 minutes. Dropped into
  `feeds` it tipped that job to EXACTLY 4h00m on its first real run, and the
  runner cancelled it mid-step — losing `mapsee_link_series` for the day, every
  day, silently, because everything before it had already synced and the run
  reports "cancelled" rather than "failed". It has its own job now, beside
  `races` and `markets_osm`, which is what those two are for. Before adding a
  step to `feeds`, look at what that job's last run actually took: the cap is
  four hours and the headroom is not what it looks like.
- **A HELPER IMPORTED IS A CONTRACT INHERITED, AND BOTH OF THIS ADAPTER'S
  PRODUCTION FAILURES WERE ONE GUESSED RATHER THAN READ.**
  `mapsee_ingest_osm_amenities` imports nine helpers from
  `mapsee_ingest_osm_food`, which is exactly right — `parse_opening_hours` is
  eighty lines of refusals each bought with a live failure. What it also
  inherits is nine signatures, and two were assumed:
  `area_bbox` reads `area["center"]` as a PAIR (not `lat`/`lon` keys), and
  `window_at` returns a LIST, with the CALLER owning the next cursor
  (`(start + len(window)) % n`, as `osm_secondhand` does). Unpacking it as a
  pair is a `ValueError` nothing static catches.
  Each cost a runner to find, the second after pulling 7,210 Seattle elements
  from Overpass. Neither was reachable from any of the 41 unit cases, because
  every one of them called `to_event` or a pure helper directly — **the bugs
  were both in `main()`, and nothing ran `main()`**. It now does, against a
  stubbed `sweep_tiles`, and that is the case that would have caught both.
  Read the source of anything you import from a sibling adapter; the docstrings
  are there and both of these were one `inspect.signature` away.
- **A CONFIG THAT PARSES AS JSON IS NOT A CONFIG THAT LOADS.**
  `osm_amenity_sources.json` shipped with `"lat"` and `"lon"` as separate keys
  where `area_bbox` reads a `"center"` PAIR. Valid JSON, reviewed, and all 41
  cases green — because every one of them built a `NormalizedEvent` directly and
  none went near the config. The first real run died on
  `KeyError: 'center'` at the first line of `main()`, after a runner had been
  spent and an Overpass fetch queued. This is the parkrun config that was never
  committed wearing a different hat: a config a job needs is part of the job,
  and nothing that tests only the pure functions can see it.
  `test_ingest_osm_amenities.py` now runs EVERY area through `area_bbox` and
  `tiles`, so the file has to be loadable by the code that will load it.
- **A BARE ONE OF THESE IS NOT ALWAYS WORTH A DOT, and `tourism=artwork` is
  the selector that proves it.** Seven of the nine are here BECAUSE existence is
  the answer — "there is a drinking fountain on that corner" needs no words.
  OSM's artwork tag takes in every tagged wall, and "there is art here" tells
  nobody anything. Measured in one Seattle box: **404 artworks, 146 unnamed, and
  55 tagged `artwork_type=graffiti` of which ZERO carried a name** — along
  Eastlake they draw a solid line of 🎨 down the side of I-5, burying ten
  drinking fountains and nine playgrounds in the same view. `Kind.bare_is_enough`
  is false for artwork alone: it has to arrive with a name, an artist, a
  description, an inscription or a photograph. The TYPE does not count and that
  is the load-bearing half — `🗿 Type: graffiti` is a restatement of the
  category, the same nothing as "a name is not a fact", and it was the entire
  content of the row that prompted this. Refused at INGEST rather than hidden at
  render, because a row that will never be drawn and can never be opened is one
  more row for every `events_near` scan to walk past.
- **`--ignore-cursor` MAKES A RUN UN-REPEATABLE, so fusing it with "rewrite
  existing rows" made a backfill impossible to finish.** The ingest reads
  `cursor = {} if a.ignore_cursor else load_cursor(...)` and then refuses to
  SAVE one — every such run starts at candidate 0 and leaves the cursor where it
  was. `osm-amenities.yml` had ONE input, `full_refresh`, passing both
  `--ignore-cursor` AND dropping `--only-new`, so dispatching it ten times
  re-swept the same first window ten times. Measured after the run that was
  meant to backfill 0205's `icon`: **278 of 1,000 sampled Seattle furniture rows
  still NULL, London 689, Paris 618** — and no number of repeats would have
  moved them. A backfill wants ADVANCE + REWRITE, which is precisely the
  combination the fused input could not express. They are two inputs now
  (rewriting is now what every run does; `restart_cursor` restarts), and the
  general shape is worth the name: when one flag sets two independent knobs, the
  combination it cannot reach is the one somebody will eventually need.
- **AND `--only-new` WAS NEVER RIGHT FOR THIS ADAPTER, only unexamined.** Its
  own comment had the reason and drew the wrong conclusion — "pin_only is
  written on EVERY row, so an OSM mapper adding opening hours to a playground
  only takes that pin out of furniture on a full refresh" — treating as a
  backfill footnote what is a permanent staleness bug. EVERY column this
  adapter writes is derived from OpenStreetMap, so with `--only-new` a row is
  frozen as of the day it was first seen and no edit anybody makes upstream ever
  reaches it. The playground that gained hours stays furniture for ever; 0205's
  `icon` is the same shape and is what made it visible. `osm-amenities.yml` now
  rewrites on every run including the scheduled one, which is safe and bounded
  for three specific reasons — the sync's Claimed-guard drops a claimed row
  before writing, `--max-places` bounds the window however often it runs, and
  the cursor advances so successive runs walk the catalogue instead of redoing
  one window. The cost taken deliberately is blast radius: a regression in
  `to_row` now reaches rows that already exist.
- **A CACHED ELEMENT LIST CANNOT SEE A NEW SELECTOR, and the workflow comment
  that says so is not a mechanism.** `osm-amenities.yml` caches each area's
  Overpass result under `osm-amenity-vN-<area>-`, with its own note: "the
  ingest self-heals a changed bbox but cannot see a changed SELECTOR, so bump
  it whenever KINDS gains or loses an entry." `amenity=toilets` was added and
  the key was not bumped, so the next run restored a one-day-old list fetched
  by the OLD query, printed "7211 element(s) from cache", and imported not one
  toilet. A silent no-op that reads exactly like a successful sweep — the same
  shape as the parkrun config that was never committed while the job printed a
  friendly skip every night.
- **`amenity=toilets` is 519,045 uses and was the obvious omission.** Denser
  than drinking water (365,559) and the thing people actually expect a map of
  civic amenities to know. Same shape as a fountain — untagged means the pin is
  the information, real hours make a listing that can be shut — plus the three
  facts that decide whether to walk over: a baby changing table, drinking water,
  showers.
- **AND AN UPSERT CANNOT DELETE, so refusing them at ingest left 120 already on
  the map.** `mapsee_retire_thin_artwork.py` is the other half — `hidden_at`
  rather than DELETE, dry by default, `--unhide` to reverse, never touching a
  claimed row. It judges from the row's STORED DESCRIPTION, which is the same
  evidence `to_event` used to write it, so there is no OSM round trip and no
  second opinion to drift. Its own first version found ZERO against live data
  where 120 were sitting, because it matched the opener on `— public artwork`
  and an UNNAMED row has no dash in it — which is precisely the shape it exists
  to find. `test_retire_thin_artwork.py` pins both openers.
- **AND THE THIRD FAILURE WAS ON THE LINE THAT PRINTS THE RESULT.** That script
  has now failed live three times — twice on its own query, once on its report
  — and not once on its judgement, which is the only part 20 unit cases were
  covering. The third was `print(f"  {past} {hidden}")` over a `past` nobody had
  defined, left behind by the edit that removed an `f"{verb}d"` producing
  "hided". It ran the whole sweep first: 9,781 pins walked, 54 correctly hidden
  and WRITTEN, then a NameError on the summary, exit 1 and a red job over work
  that had entirely succeeded. Unreachable from every case in the file, because
  all of them call `is_thin()` or `patch_paths()` directly — **the bug was in
  `main()`, and nothing ran `main()`**, the same gap that cost
  `mapsee_ingest_osm_amenities` two production runs. The test drives the real
  `main()` against a stubbed transport on all three argv shapes now.
- **A FACT BUYS A SHEET, NOT A LISTING — "can it be SHUT" is the Nearby test.**
  `pin_only` began as "carries nothing worth reading", so one operator tag or
  one surface promoted a playground into `events_near`. Measured 2026-08-26 in
  one Seattle box: **752 rows from this adapter were in the Nearby list and 745
  were open 24 hours a day** — 58% of everything under `kids`, 67% under
  `arts`, several titled simply "Playground". Nearby is a list of what is ON,
  and a thing that is always there is not on however much is written about it.
  So `to_event` asks whether there is a time it is SHUT (`days != ALWAYS`,
  compared against the window rather than the verdict, because a rule parsing
  cleanly to `Mo-Su 00:00-24:00` is 24/7 written the long way), plus the food
  bank exemption. Everything else is a pin, and what it carries decides what
  the pin DOES — ../mapsee's `amenityHasContent` reads the description written
  here and gives a pin with something to say a hover and a tap. The two
  judgements are no longer one question asked twice.
- **AN OPERATOR THAT RESTATES THE THING IS NOT A FACT — "a name is not a fact",
  one tag over.** `operator=Little Free Library` on a little free library says
  exactly what the title and the book glyph already said. Live in Seattle: 47
  of 571 openable pins carried that line and **35 had nothing else**, so the
  entire content of their sheet was the row's own kind read back at them — a
  tap spent to be told what the map already showed. Suppressed on an EXACT
  match against the row's name or its kind's noun, so `Seattle Parks` on a
  playground and a superstring like `Little Free Library Ltd` both still print.
  Found by reading the hover labels the 24/7 rule had just made visible.
- **OSM WRAPS SOME DESCRIPTIONS IN QUOTES, AND THE HOVER LABEL IS WHERE IT
  SHOWS.** 34 of 1,000 live Seattle pins read "Catfish — 'The ceramic tiles…"
  or "…band type head saw.'" the moment a description reached a tooltip —
  invisible for as long as those rows were unopenable. A MATCHING pair is
  stripped, and so is an unbalanced straggler when it is the only quote in the
  string; anything with a partner is somebody's punctuation and is left alone.
  Underneath it was the older trap: one sculpture's entire description is a
  single apostrophe, and punctuation is TRUTHY, so it would have made a pin
  openable on nothing — the WP Event Manager `"-"` lesson in another costume.
  `_clean` now needs at least one letter or digit.
- **A VALUE THAT MATCHES THE ASSUMPTION IS NOT A FACT — "a name is not a fact",
  one level down, and missed on the first pass.** `access=yes` is the commonest
  tag on a playground and `fee=no` on a drinking fountain, so between them they
  were promoting a large share of the two densest selectors out of furniture —
  into sheets whose ENTIRE content was `🚪 Access: Open to everyone`. That is
  precisely the tap-for-nothing the split exists to prevent, and it survived
  review because "an access rule" and "a fee rule" read like facts in the
  abstract. Free and public is what a civic amenity IS; only the DEVIATION is
  worth a sheet, so `access=private` and a real charge still count and still
  print, and the assumed values are not printed at all. Deliberately NOT
  extended to `wheelchair`: nobody may assume accessibility either way, so all
  three of its values are real facts. Found by rendering the sheet a
  `access=yes`-only playground would actually produce and reading it.
- **`social_facility=food_bank` is 4,938 uses; `amenity=food_bank` is 16.**
  Reaching for the obvious key produces an adapter that runs clean, reports
  success and imports essentially nothing — the same silence as the parkrun
  config that was never committed while the job printed a friendly skip nightly.
- **"No hours tagged" means opposite things for a playground and for a food
  bank.** A playground is untagged because it never closes; writing it open all
  week is true. A food bank written the same way sends somebody with an empty
  bag to a locked door — the food adapter's worst failure wearing a different
  hat. `Kind.always_open` is the flag, and a food bank with no readable hours
  gets NO weekly pattern and makes no claim whatsoever about being open. An
  UNREADABLE hours string is treated as absent everywhere, never as an open
  sign.
- **A FOOD BANK IS THE ONE PIN WHERE "YOU CANNOT CLICK IT" IS THE WRONG
  ANSWER, and getting there took making it furniture first.** The bar for a
  sheet is "a fact you cannot already see from the map", which is right for the
  other seven selectors and backwards for this one: WHERE A FOOD BANK IS *is*
  the fact somebody came looking for, and whether they can open it, read its
  name, route to it or send it to somebody must not depend on whether a mapper
  filled in a phone number. Measured in Seattle: 24 food banks, and only 6 were
  openable — Ballard and Wallingford had hours, the other 18 (ACRS, St Mary's,
  Salvation Army Renton, the Little Free Pantries) were silently inert.
  `Kind.always_list`, and it is deliberately ONE selector: the argument is the
  stakes, and widening it to give boxes and bike stands would undo the
  furniture split by degrees. What it must never do is invent a time —
  `hours_unknown_line` says which of the three silences it is (`24/7`, a string
  our parser refused, quoted verbatim and attributed because unparseable is not
  unreadable, or nothing at all), which is parkrun's all-day event and its
  "Start time on the event page." one selector over.
- **A ROW NOBODY COULD OPEN WAS A ROW NOBODY HAD READ.** "Food bank — food
  bank." is what naming a thing after its own kind produces, and it sat in
  every unnamed row's description for as long as the adapter existed without
  anyone noticing, because every unnamed row was furniture and furniture's
  description is never rendered. It only became copy the moment food banks
  started listing. Whenever a rule stops hiding a class of row, READ what that
  class has been writing.
- **A HAND-WRITTEN FIXTURE CANNOT SEE A BUG THAT LIVES BETWEEN TWO FILES, so
  `gen_amenity_fixtures.py` generates them.** ../mapsee's `amenityHasContent`
  is a second opinion on `pin_only`, and what it actually reads is a string
  this repo assembles in two places — the adapter writes the description, the
  sync appends to it. That is how the 🔎 Google link reached every drinking
  fountain past a check that passed. Run it after touching `to_event` or
  `to_row` and commit what changes; build the rec with
  `NormalizedEvent.as_record` and never `vars()`, because `primary_url` reads
  `rec["sources"]`, which is not a dataclass field, so a hand-shaped rec
  silently drops the "Tickets / info:" line the client also has to strip.
- **A CATEGORY IS NOT A KIND, AND THE MAP DREW THREE THINGS AS ONE DOT.**
  Reported: drinking fountains, public toilets and bike repair stands were all
  🚰. All three are category `outdoors`, and ../mapsee looked the pin's glyph up
  BY CATEGORY — so the layer could say there was something civic on a corner and
  never which. `Kind.glyph` had carried 🚰/🚻/🔧 since this adapter was written
  and NOTHING HAD EVER READ IT: `NormalizedEvent` had no icon field, so the value
  was assigned and dropped on the floor for the file's whole life, and the sync
  hard-coded `"icon": None` under the note "let the app render the category's
  emoji". That note was right when it was written and stops being right the
  moment one category holds several KINDS of thing. `NormalizedEvent.icon` is
  the fix, ../mapsee 0205 returns the column and the client prefers it — which
  is the rule `eventGlyph` (`ev.icon || catEmoji(ev.category)`) had followed for
  ordinary events all along. Two things the 127 existing cases could not see,
  because every one of them read a description or a `pin_only` and none had ever
  asked what the pin is DRAWN with: the glyph reaching the row at all, and no
  two kinds sharing one. Both are asserted now, per kind.
- **AND A GLYPH FIX REACHES THE FUTURE ONLY.** `--only-new` means a scheduled
  run cannot rewrite a row it already wrote, and 1,000 of 1,000 sampled
  furniture rows had `icon` NULL. So every pin already on the map keeps drawing
  its category's emoji until `osm-amenities.yml` runs with `full_refresh`. The
  client's fallback to the category glyph is therefore load-bearing rather than
  defensive — it IS the map until that backfill lands — and it is checked.
- **`series_id` is assigned after the fact, not at ingest.** A repeating listing
  publishes each occurrence separately and the store is rebuilt every run, so the
  occurrences never meet in memory — the table is the only place a series is
  visible whole. `mapsee_link_series.py` runs after the sync and stamps them.
  Its failure mode is silent and bad: chain two unrelated events and one of them
  disappears from the map behind the other (`collapseSeries` in
  `../mapsee/site/js/app.js` folds a series to its next occurrence), with nothing
  in any log to say so. That is why it has a test, refuses implausibly large
  groups, and never touches a claimed row.

- **A HELPER THAT WRITES A REPO FILE AS A SIDE EFFECT WILL EVENTUALLY BE
  CALLED BY A TEST.** `_discover_osm` ends with `_save_ledger(led)`, so calling
  it from a throwaway verification with `{}` REPLACED `curation_ledger.json` —
  5,861 entries, 41,030 lines — with an empty file, and `git add -A` then
  committed and pushed it. Both halves are already written down as things not
  to do (../mapsee: "stage your own paths explicitly, never `-A`"), and both
  were done anyway inside one command. Recovered from `29fe5de`; the next
  scheduled run had already rebuilt it to 3 rows and would have re-probed
  thousands of known-dead hosts for weeks, slowly and silently, because the
  ledger is the only thing that makes a sweep get cheaper. `test_discover_osm`
  stubs `_save_ledger` AND hashes the file either side, because the stub is the
  kind of line a later edit removes without noticing. A test that drives real
  machinery has to intercept every write that machinery does, not only the ones
  it asserts on.

- **A FILTER CAN BE ACCEPTED AND NEVER RUN, and the count it returns is then the
  WHOLE ARCHIVE.** Mapas Culturais (Brazil's state and municipal cultural
  registers, and the only reason this repo has anything in the country) takes
  `_startsOn=GTE(today)` on `/api/eventOccurrence/find`. Ceará applies it —
  16,587 occurrences, 329 returned. Espírito Santo ACCEPTS AND IGNORES it and
  returned 1,575: its entire history, of which ELEVEN were future and the oldest
  was dated **1911**. João Pessoa answered 34, of which zero were future. All
  three answer 200, and on a small instance an unfiltered archive is
  indistinguishable from a filtered result — which is how "1,575 upcoming
  events" became the headline number of the first audit and was wrong by two
  orders of magnitude. The tell is free: ask `@count=1` WITH the filter and
  again WITHOUT, and equal counts mean it did nothing. That check is an
  ACCELERATOR, never the guarantee — every row is re-checked client-side
  against `rule.startsOn`, because that is the only thing that cannot be
  silently wrong. Same family as BikeReg's search endpoint answering 200 with a
  hidden hundred-row ceiling, and as wisconsinbikefed's beautifully-formed iCal
  in which every event was past. `test_ingest_mapasculturais.py`.
- **AND THE DATE IS IN THE BLOB, NOT THE COLUMN.** `_startsOn`/`_startsAt` are
  real columns and they are NULL on whole instances — all 1,575 of Espírito
  Santo's, all 34 of João Pessoa's — while the `rule` JSON carries the value on
  every row of every instance seen. That is WHY the server filter can pass a
  2023 event: it tests an empty column. Two spellings of one fact, chosen only
  after finding the records where they disagree, which is the rule MyListing and
  SLU already paid for.
- **`{"latitude": "0", "longitude": "0"}` IS THE COMMONEST COORDINATE IN THAT
  SOURCE AND IT IS A STRING, so it passes every presence check.** 55 of the 345
  genuinely-future occurrences are at null island — a space whose registrant
  never dragged the pin. `"0"` is truthy, so an audit asking
  `if loc.get("latitude")` counts them all as placed: the first pass reported
  Ceará at 328 of 329 placeable when the truth is 281, and reported two
  instances as live supply when neither can draw a single row. Sergipe and Pará
  are declined in `_not_included` on the PLACEABLE count, not the event count —
  the Seattle Parks Foundation rule, which is that "returns future events" and
  "puts anything on the map" are different questions. It is the WP Event Manager
  `"-"` and this platform's own `undefined` in another costume: a falsy-LOOKING
  value that is truthy.
- **A PLATFORM CAN INTERPOLATE ITS OWN MISSING-VALUE PLACEHOLDER INTO AN
  ADDRESS.** `endereco` arrives as "Rua Dragão do Mar, , Praia de Iracema,
  Fortaleza, undefined, CE, 60060-390" — 167 of Ceará's 329 live rows — and
  `value` is the second one it writes. Both are truthy and would reach the row
  as street text. The address itself comes in TWO layouts on the same instance
  (UF last, and UF second-last with the CEP after it), so it is read outward
  from the UF token, whose shape is unmistakable, rather than by counting
  commas. Same discipline as MyListing's "Bainbridge Island, Washington 98110".
- **`timezoneName` SAID `Etc/UTC` ON ALL 1,938 OCCURRENCES AND IT IS NOT TRUE.**
  The times in `rule` are naive local clock times — a Fortaleza cinema session
  whose own `_startsOn` was serialised `America/Fortaleza` still reports
  `Etc/UTC` in the field named for the timezone. Read the field and a 19:40 show
  is served at 16:40. The adapter emits `start_local` and lets the sync turn the
  venue's coordinates into a zone, which is what WP Event Manager's naive stamps
  already needed; Brazil spans four zones, so a country-wide constant is not
  available either. A field named for a fact is not the fact.
- **BRAZIL WAS EMPTY BECAUSE NOTHING EVER ASKED.** Not a bug and not a dead
  feed: `metros_global.json` held 165 metros across 28 countries with **none in
  South America**, and no `*_sources.json` contained a single Brazilian entry —
  zero textual matches, zero coordinate pairs inside the country's bbox. So the
  international sweep never queried it, the OSM place adapters had no area
  there, and `discover osm` walks the same metro list, which means curation
  could not have found it either. The country is densely mapped: 90
  `amenity=marketplace` in the Rio box and 94 in Brasília's, comparable with the
  densest European cities already configured, plus 615 playgrounds, 355
  artworks, 268 toilets and 241 outdoor gyms in Rio alone. A gap that looks like
  a coverage failure can be a config file that was never given a row.
- **PARKRUN AND MOBILIZON ARE MEASURED NEGATIVES IN BRAZIL, written down so
  nobody re-derives them.** parkrun's live feed is 2,965 events across exactly
  20 country codes and Brazil is not one of them. `mobilizon.com.br` is the only
  Brazilian instance in the joinmobilizon directory and it holds ONE future
  event, carrying no coordinates. `dados.gov.br` answers 401 — the national CKAN
  needs a key. None of these is a bug to fix; all three cost a probe to
  re-check, which is why the numbers are in the configs.

- **A SESSION PUBLISHED EVERY TEN MINUTES IS A BOOKING GRID, and it walks past
  the refusal written for exactly that.** This repo already declines OpenActive's
  `FacilityUse` and `Slot` — "a bookable badminton court at 19:00 is an empty
  room somebody may or may not take" — and the same thing arrives through the
  front door as a `ScheduledSession`, which that rule never sees. Measured
  2026-08-28 in a ±0.03 box on central London: one pool published **"Swim For
  Fitness" 255 times in a week, 110 of them on a single day** at ten-minute
  spacing from 05:40, and three title/venue pairs like it were about **half of
  the 800 rows `events_near` will return for that viewport**. Since that RPC
  sorts every candidate in the box to take its top N, the grid is most of why
  central London exceeds the API role's ~3s statement timeout while Seattle and
  New York do not. `collapse_booking_grids` keeps ONE row per venue per day,
  opening when the first slot opens and closing when the last one closes, saying
  in its own description how many slots it stands for — which is a truer listing
  than any ten-minute slice of it. The threshold is per DAY at ONE venue for ONE
  title because that is the shape a grid has and a programme does not: 308 of
  321 distinct title/venue pairs in that box occurred exactly once, and the
  busiest genuine one ran four times in a WEEK.
- **AND THE HORIZON WAS THE OBVIOUS LEVER AND THE WRONG ONE.** `OpenActive`'s
  `DEFAULT_HORIZON_DAYS` is 120 where every sibling adapter uses 42, which reads
  like the cause of a bloated pool and is not: measured in the same box, every
  WEEK inside 42 days fills the 800-row cap, while 42-120 days holds 82, 87 and
  80 rows and beyond 120 holds one. Cutting it to 42 would have dropped ~250
  rows out of thousands, changed nothing, and lost four months of real
  programming. The density is near-term and genuine. Check which end of the
  distribution the volume is actually at before trimming the tail.
- **A PERFORMANCE MEASUREMENT ON THIS DATABASE IS A MEASUREMENT OF THE CACHE,
  and it will invert under you mid-session.** ../mapsee 0207 baked
  `enable_indexscan = off` into `events_near` on a forced-plan comparison
  showing 17,870ms -> 5,320ms, and 0208 reverted it after measuring 3.2s
  medians warm through the anon key. The same thing happened again while
  verifying the client-side retry for it: London ±0.08 measured 3.28s median
  with 4/6 raising 57014, then six runs later answered on the FIRST try in
  0.67-0.96s, because the earlier attempts had warmed shared_buffers. Both
  readings are true and neither is the answer on its own. Interleave the targets,
  say which cache state a number came from, and treat a single cold sample as
  evidence of nothing.

- **THE OCCURRENCE FEED IS USELESS WITHOUT THE SERIES FEED, so one transient
  500 cost 63,141 sessions.** An OpenActive `ScheduledSession` carries a date
  and a `superEvent` pointer and nothing else — the title, the place and the
  price live on the `SessionSeries` it names. `walk()` gave up on the FIRST
  exception, so when Better (GLL)'s series feed answered HTTP 500 on page four
  only 1,500 of its series were read, and **63,141 occurrences were then
  discarded for naming a series nobody had**. The log said so plainly and it
  reads like the publisher's fault; it was one retry. `_get_retrying` retries
  the transient statuses (408/425/429/5xx, timeouts, resets) and re-raises the
  permanent ones immediately — a 404 is the feed's answer, not a blip, and
  asking again is noise. Same distinction `mapsee_cleanup` draws between a
  statement timeout and an Envoy 503, one layer out.
- **AND THE PAGE CAP IS A CEILING, NOT A PLAN — the same lesson as the job
  timeout, one file over.** `MAX_PAGES = 250` stopped Better at 125,000
  ScheduledSession records and reported, correctly and loudly, that the feed
  was NOT read to the end and its newest sessions were missing. Reporting it is
  not reading it. `max_pages` is per-source now and Better has 700; the walk is
  affordable precisely because `collapse_booking_grids` takes what it returns
  from ~59,700 rows to ~9,200, so the expensive half is the HTTP and the cheap
  half is what survives.
- **EVERY SLOT IN THAT GRID WAS PUBLISHED TWICE, and only counting distinct
  start times showed it.** 255 live "Swim For Fitness" rows held 217 distinct
  instants — two lanes, or one series carried in two feeds. Above the grid
  threshold the collapse absorbs that either way, which is exactly why it went
  unnoticed; below it both rows survive and the list stutters. Same title, same
  point, same INSTANT is one listing, and a minute apart is two.

- **AN UPSERT CANNOT DELETE, so the run that landed the collapse made the map
  WORSE before it made it better.** `collapse_booking_grids` worked exactly as
  designed on 2026-08-29 — Better (GLL) 51,922 slot rows into 2,512 day rows —
  and central London went from 4/6 to **6/6** raising 57014, because the
  collapsed rows carry NEW fingerprints and were INSERTED beside the ~50k they
  replace. 112,408 rows added, none removed. The companion retirement script is
  not a footnote to a collapse, it is half of it:
  `mapsee_retire_openactive_slots.py`, `hidden_at` and dry by default, hiding a
  slot row ONLY where the collapsed day row for that title, venue and date is
  already in the table — because a publisher whose feed died mid-import must not
  have its sessions vanish on the strength of the run that failed.
- **THE GRID WAS THE VISIBLE TENTH, AND THE OTHER 88% WAS ONE PUBLISHER
  PUBLISHING HONESTLY.** Everyone Active wrote **98,871** of that run's 112,408
  rows and is not a grid at all: its per-day collapse found 714 rows in 94
  groups. It runs ~200 leisure centres and publishes ~56,000 distinct classes,
  one occurrence at a time. Sampling what `events_near` RETURNS (its top 800,
  distance-ranked, from a central-London box where Better's pools dominate) and
  inferring what is in the POOL is how that was missed for a whole cycle. The
  returned rows and the scanned rows are different populations; measure the one
  you are trying to shrink.
- **AND "THREE POINTS MAKE A PATTERN" FOLDED NOTHING.** `collapse_weekly_series`
  turns a class that recurs on the same weekday at the same hour into ONE
  standing row with a weekly `recurring_days` — the model 0156 already gives an
  imported restaurant. At a threshold of three it folded ZERO of Everyone
  Active's rows, because that publisher lists only a FORTNIGHT ahead: 38,562 of
  its 55,771 title/venue groups hold exactly two occurrences, 16,990 hold one,
  and no slot repeats three times. At two it takes it to 56,153. Two points is
  thin, so the pair must be CONSECUTIVE weeks — a standing row is rolled forward
  for ever by `roll_recurring_windows` and cleanup only deletes the past, so a
  two-part workshop read as an arrangement is a finished course pinned to the
  map permanently. The threshold that works is a fact about the publisher, not
  about patterns.
- **A SECOND COLLAPSE NEEDS A SECOND RETIREMENT RULE, and the first one cannot
  see the second's orphans.** `mapsee_retire_openactive_slots.py` groups by
  title, venue and DAY and needs six rows in a group, which is the shape a
  booking grid has. A weekly fold's orphans are one occurrence per week, each
  alone in its own day — so every one of them fails that test and is never even
  considered. Run #55 folded 77,346 of Everyone Active's occurrences into 38,227
  standing rows and INSERTED them beside the originals, exactly as the grid
  collapse had a cycle earlier, and the retirement reported nothing to do.
  `weekly_superseded` is the second rule: same safety rule (hide only where the
  replacement is already in the table), keyed on title and venue with no day in
  it, and matched on the row's LOCAL weekday and start time against the standing
  row's own `recurring_hours` — because the fold deliberately leaves the
  bank-holiday special dated and still writes it every run. The two compose: a
  grid day row folded into a standing row is superseded in turn.
- **AND THE ESCAPE HATCH HAD NEVER WORKED.** That script's whole licence to
  write `hidden_at` rather than DELETE is "hiding is reversible", and its scan
  hard-coded `hidden_at=is.null` — so after a successful `--apply` the reverse
  pass could not see a single row it had hidden, reported zero and wrote
  nothing. The test agreed with it, because the stub answered every URL with the
  same fixture and never looked at the query it was asked; a test that drives a
  real query has to ANSWER the query, not the call. The reverse direction needs
  no hidden filter at all rather than the opposite one, because the keepers stay
  VISIBLE while the rows they replaced are hidden and the judgement needs both
  halves; the direction of the write is then decided per row on its own
  `hidden_at`, which is also what stops a second `--apply` restamping.
- **`starts_at`'s FIRST TEN CHARACTERS ARE NOT RELIABLY THE LOCAL DATE.** A
  `timestamptz` normalises to UTC in the column and PostgREST renders it in the
  connection's zone, so a 00:30 class in British Summer Time reads as the
  previous day — and a 19:40 one reads as 18:40, which is a weekly pattern that
  matches nothing. Convert into the zone the sync derived from the venue's own
  coordinates instead. Both retirement rules now do; only the grid rule's
  per-day grouping still reads the string, where an hour's error costs at worst
  one group boundary.
- **A TOGGLE'S MEANING CAN BE OVERTAKEN BY A NEW KIND OF ROW.** ../mapsee's
  "Hide open shops" sent `p_hide_standing`, which drops every row with
  `recurring_hours` — the same set as "shops" for exactly as long as imported
  restaurants and second-hand shops were the only standing rows there were.
  Folding leisure timetables into standing rows would have made one toggle
  labelled for shops hide every pool and gym in the country. The category
  decides now (`food`, `market`), client-side, because teaching `events_near`
  about shop-ish categories is a migration and 0204-0208 is a recent enough
  lesson in what an unverifiable change to that function costs.

## Running things

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
python test_ingest_markets.py       # a metro that loses its Overpass slot, and city-vs-street
python test_ingest_openactive.py    # an RPDE feed's first page is its oldest, and it lies both ways
python test_ingest_osm_amenities.py # which civic pins earn a sheet, and which are just the map
python test_retire_thin_artwork.py  # which already-written artwork rows may be hidden
python test_ingest_mapasculturais.py # an accepted filter that never ran, and a coordinate of "0"
python test_retire_openactive_slots.py # which superseded slot rows may be hidden
python gen_amenity_fixtures.py      # regenerate ../mapsee's amenity fixtures from to_event + to_row
python test_sync_unknown_column.py  # a column the database has not got YET must cost one feature, not the night
python test_cleanup.py              # a statement timeout and an outage want opposite things
python test_retire_perday.py        # collapsing per-day rows never empties a venue
python catalog_curate.py coverage   # where the catalog is thin, per lens category
python mapsee_health_check.py       # needs SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY
```

The 26 test scripts are the CI gate (`tests.yml`). They print one line per
case and exit non-zero on failure — no runner needed. `timezonefinder` has no Windows
wheel above 6.0.1, but it is a lazy optional import with a fallback, so the tests
run without it.

`MAPSEE_TODAY=YYYYMMDD` fixes "today" for reproducible curation runs.

## Credentials

`.env.example` lists all 36 variables. Nothing in this repo should ever hold a
real key; CI supplies them as secrets. `SUPABASE_SERVICE_ROLE_KEY` bypasses RLS —
treat any workflow that reads it as privileged.
