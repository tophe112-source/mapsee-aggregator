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
python test_ingest_markets.py       # a metro that loses its Overpass slot, and city-vs-street
python test_cleanup.py              # a statement timeout and an outage want opposite things
python test_retire_perday.py        # collapsing per-day rows never empties a venue
python catalog_curate.py coverage   # where the catalog is thin, per lens category
python mapsee_health_check.py       # needs SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY
```

The 18 test scripts are the CI gate (`tests.yml`). They print one line per
case and exit non-zero on failure — no runner needed. `timezonefinder` has no Windows
wheel above 6.0.1, but it is a lazy optional import with a fallback, so the tests
run without it.

`MAPSEE_TODAY=YYYYMMDD` fixes "today" for reproducible curation runs.

## Credentials

`.env.example` lists all 36 variables. Nothing in this repo should ever hold a
real key; CI supplies them as secrets. `SUPABASE_SERVICE_ROLE_KEY` bypasses RLS —
treat any workflow that reads it as privileged.
