# Finding sources: catalogs, discovery, verification, refusals and licences

> Part of mapsee-aggregator's agent notes — see `AGENTS.md` for the map. Read this file when the bug is about: `catalog_curate`, the ledger and its statuses, `_not_included`, sitemaps and robots, bot challenges, site builders vs calendars, calendar plugins, licences.
> Every note below was measured before it was written; keep the numbers when you edit.

- **The coverage report invented 26 of the 60 countries it claimed.** It is not
  a status page: its thin-ground ranker decides where the next curation run
  spends its budget, and `coverage --json` is the only record of whether the
  catalog grows. Four defects fed it at once, found 2026-09-06 by reading one
  report. (1) `ALL_TYPES = sorted(CONFIG) + sorted(EXTRA_CONFIG)` listed jsonld,
  mylisting and venuepilot twice — they are declared in BOTH tables over the
  same file — and `_coverage_rows` walked both paths: 107 sources counted twice,
  1702 reported against 1595 real, a 6.7% inflation carried by every
  `total_sources` in `coverage_history.jsonl`. (2) `_rows_parkrun` iterated
  `countries`, a MAP of parkrun's numeric country id to an ISO code, as a list —
  all 20 countries filed under names like "97", "3", "85", each tripping the
  "0-2 sources" FLAG, while the 20 real ones read as having no running supply at
  all against ~2,965 weekly events. (3) `default_country` was used raw and the
  configs use two conventions (gancio writes "DE", mobilizon writes "Germany"):
  15 sources split into six phantom rows, and "CA" with 1 source was FLAGged as
  needing feeds three lines under "Canada" with 36. (4) Nothing consulted
  `_url_country`, which has been in the file since the ccTLD work, so 539 of
  1702 rows (32%) sat under "?" — indistinguishable to the ranker from a country
  with no feeds. Fixing all four: `?` 539 -> 178, countries 60 -> 37, Sweden 14
  -> 51, Denmark 22 -> 48, New Zealand 16 -> 53, Australia 46 -> 84. That fix
  then sat uncommitted until 2026-09-14, and main fixed (2) and (3) on its own
  meanwhile (`_iso_countries`), so (1) and (4) were still live when it landed:
  on a4bb0cf's configs the report counted 2,975 rows where one walk gives
  2,809 (jsonld 328 for 164 entries) and filed 734 of them (24.7%) under `?`.
  After, with four spam hosts also retired from `mobilizon_sources.json`: 2,805
  rows, 200 under `?` (7.1%), Belgium 28 -> 66, Sweden 16 -> 53, New Zealand 17
  -> 53, Canada 38 -> 73. None of it ever failed a run; the arithmetic of a
  growth metric is exactly what a green run cannot check, which is why
  `test_coverage_rows.py` pins the invariants (no type counted twice, no country
  that is a number or a bare ISO code) rather than today's totals.

- **The report read the whole world as the United States, 115 sources of it.**
  Measured 2026-09-19 over 3,131 live rows. `_parse_place` knew two spellings of
  a place: a two-letter US STATE code, and a country in `_COUNTRY_ALIASES` — a
  17-entry hand-written table. Everything else fell through to "the last comma
  part is the metro", and `_locate` then applied the bare-`(City)`-is-US
  convention on top, so the country's own name or code BECAME the metro and the
  country became the US. 103 rows ended in a foreign ISO code (`, Zurich, CH` —
  20, then Stockholm 10, Brussels 10, Sydney 7, Paris 6, Oslo 6, Copenhagen 5,
  Warsaw 5, Madrid 5, Brno 3, Auckland 2, Vienna 2, London, Winnipeg, Dublin)
  and 12 more in a country name the alias table happened to lack (Sweden 3,
  Italy 2, Hong Kong 2, Norway 2, Spain, Finland, Denmark). That is where the
  `*CH 20`, `*SE 10`, `*BE 10` and `*Wien 9` rows in the metro table came from.
  Fixing it: US 1,882 -> 1,784, Switzerland 80 -> 100, Sweden 53 -> 66, Belgium
  73 -> 83, Norway 15 -> 23, Denmark 48 -> 54, Spain 22 -> 28, Czechia 21 -> 24,
  France 52 -> 58, Australia 84 -> 92, Hong Kong 3 -> 5. The direction is the
  point: it inflated the one country the ticketing APIs already blanket and hid
  real supply in eleven that read as thin ground. `_country_named` resolves all
  three spellings and `_ISO_COUNTRY` — which already knew every one of these —
  is read both ways round. The US-state branch still runs FIRST and must: `CA`
  in a geocode suffix is California, `DE` Delaware, `IN` Indiana.

- **894 more rows named a STATE where the metro belongs.** Same defect, other
  half. The civic backend writes `geocode_suffix` as `, City, Region` and
  Wikidata's region label is the full name, so `, Eau Claire, Wisconsin` parsed
  as metro "Wisconsin" — and `_US_STATES` is codes only, so the full names were
  not states to it. The country came out right by accident, via the bare-token
  US default. That was the entire top of the metro table (`*Texas 173`,
  `*Florida 125`, `*California 76`, `*Minnesota 71`), a report claiming 173
  sources in one "metro" that is a state with a hundred towns in it. With
  `_US_STATE_NAMES` in the same branch, 1,209 metro rows now name the actual
  city (Shawnee, Mansfield, Lakeville, Idaho Falls, Royal Oak, Poway). "New
  York" is the one name that is legitimately both, and 11 rows carry it as a
  metro correctly.

- **A two/three-letter token in a source's name is an acronym, not a city.**
  `_locate` preferred the name's `(...)` over the geocode suffix, which is right
  for "(DC metro)" and wrong for "Eau Claire — Redevelopment Authority (RDA)",
  "Centre Franco-Iranien (CFI)" and a gancio entry spelling its parens
  "(US, IL)". Each became a US metro with one source in it — the exact shape the
  thin-ground ranker chases and sends a run at. `_looks_like_a_code` refuses
  both that and the metro slot generally, so `, Winnipeg, MB` and `, SA` fall to
  the ccTLD rescue instead and come back Canada and Australia.

- **Both generators now SPELL THE COUNTRY OUT in the suffix they ship, so the
  rescue above stops being needed.** The venue backend built its metro label as
  `f"{name}, {ISO}"` and that label is the geocode_suffix floor for every ics
  candidate a metro proposes — `, Zurich, CH` was written into
  `ics_sources.json` 20 times, and it is not just unreadable by the report, it
  is a suffix a geocoder has to guess at. `metros()` carries `country_name` from
  `metros_global.json`, which has held it all along. The civic backend wrote no
  country at all, which was true while it swept one: `, Issaquah, Washington`
  means the US to a US-default geocoder. `, Bern` does not mean Switzerland to
  one — Bern, Kansas, Bern, Indiana and Bern, Idaho are all real. US towns keep
  the two-part form 900 shipped sources already use; everywhere else gets the
  country. The cursor still keys on the ISO code via `metro_key`, so none of
  this moves the walk.

- **An ISO code with no name in `_ISO_COUNTRY` becomes a country.** `IS` and
  `JM` reached the report as two countries called "IS" and "JM", each with one
  source and each FLAGged as needing feeds — a gap invented by a lookup miss.
  They are the only two codes any config declares that the table lacked;
  `test_coverage_rows.py` now walks every `*_sources.json` and fails on the next
  one.

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

- **A curated city list is only as complete as its last audit, and Seattle has
  TWO market operators.** Neighborhood Farmers Markets and the Seattle Farmers
  Market Association both run markets; `market_sources.json` was built from the
  first and silently missed three of the second's five (Central District, Madison
  Park, South Lake Union) until 2026-08-12. National coverage does not save you
  here — the USDA directory carries Central District under its former name,
  Madrona. Enumerate every operator's own sitemap when auditing a city.

- **A search endpoint that ignores every paging parameter still answers 200.**
  `bikereg.com/api/search` returns exactly 100 events and ignores `page`,
  `offset`, `count`, `MaxResults`, `startdate`/`enddate`, `state` and `radius`
  alike — and reports `ResultCount: 100`, so nothing in the envelope reveals the
  1,267 events withheld. An adapter built on it would look complete for ever.
  The GraphQL gateway BikeReg's own docs recommend is cursor-paged with a real
  `hasNextPage` and a `totalCount`, which is the whole reason
  `mapsee_ingest_bikereg.py` is on it. Same lesson as RunSignup's silent
  1,000-row cap, one step worse: there the tail was detectable by counting.

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

- **A CivicPlus city's calendar names its own category, and every one of them
  was filed `community` anyway.** The platform has no whole-calendar export, so
  a town is one source per PROGRAMME, and the link that carries each feed URL
  carries the programme's name beside it. `CIVIC_DENY_RX` already reads that
  name to refuse a tax or a zoning calendar — and then `civicplus_candidates`
  threw it away and stamped `DEFAULT_CATEGORY` on the survivors. Measured
  2026-09-13 over all 363 civic-discovered ics sources: 363 of 363 `community`,
  including 34 named "Library", 8 named for a library's children's or teen
  programme, 2 "Farmers Market" and one "Volunteer Opportunities". The fattest
  category in the catalog (`community`, 1,010) was being fed by the calendars of
  the thinnest (`kids` 25, `volunteer` 10). `category_for_feed` reads the name;
  re-filing the 45 that state one moved `kids` 25 -> 33 and `learning` 123 -> 157
  with no new source at all — the same "check whether the supply is missing or
  merely unlabelled" that fleabop's classifier note records.

- **"Parks & Recreation" is the one that must STAY `community`, and it is the
  biggest thing the rule leaves out.** 51 of the 52 civic sources whose name
  matches anything outdoorsy are a parks DEPARTMENT's whole calendar, which is
  mixed by construction: it is where a town puts its summer concert series, its
  movies in the park and its block party alongside the trail walks. Filing them
  `outdoors` would have moved 51 sources of exactly that supply off awaresie,
  the neighbourhood door, to win the one genuinely-pure nature centre in the
  set. AGENTS.md's pure-vs-mixed rule, applied to the category that would most
  have flattered the coverage report.

- **Municipal open data does not publish local music, festivals or block
  parties, and the Socrata catalog says so plainly.** Measured 2026-09-13
  against the live federated catalog, page 1 of each: "block party" matches 87
  datasets, "street fair" 44, "community festivals" 49, "concerts" 15, "live
  music" 16, "parades" 18 — and every one of those six yields **zero** datasets
  that are both event-shaped (`_infer_map`) and not already configured or
  known-dead. "concerts in the park" and "summer concert series" match 6 and 0
  datasets in total. What the words do match is permit tables, which
  `_DISCOVER_REJECT` refuses for good reason. Do not add these queries; the same
  shape as the fitness/running measurement in curate-catalog.yml's header. The
  supply for this kind of event is the `civic` backend — a town's OWN calendar.
  Two 40-city batches run the same day returned 64 verified sources, and across
  the 45 that stayed `community`: 44 local-music events (Apache Junction alone
  runs a "Concert in the Park" series), 57 festivals and parades, 40
  block-party-shaped events (movies in the park, food-truck nights) and 58
  market days. The second batch was far richer than the first, so do not read
  one batch as the rate.

- **A PARK DISTRICT IS NOT A CITY, so `civic` discovery can never propose it.**
  `catalog_discover_civic` generates candidates from Wikidata's "city in the
  United States" class, and the agencies that run free naturalist-led hikes —
  county forest preserves, regional open-space districts, Audubon chapters,
  nature centres — are not in it. Probed 64 of them by hand 2026-09-13 with the
  same `find_calendar` the other backends use: **18 carry a machine-readable
  calendar** (tribe, CivicPlus, Trumba, LibCal, Squarespace), 5 are behind a bot
  challenge, 2 unreachable, the rest publish a calendar no fingerprint matches.
  17 verified and merged, and they took `outdoors` from 26 sources to 33 — the
  one starved category that no amount of civic or Socrata discovery had moved.
  A hand-curated seed list is the right shape here, the same as
  `fair_sources.json`: there is no catalog to walk.

- **Verification proves the feed, and the NAMES still have to be read.** Six of
  the 28 park candidates were dropped before verifying and two more after, all
  on what their titles turned out to be: a county's Public Health, Workforce
  and Emergency Management calendars riding along on the same CivicPlus site as
  its open space; a "Calendar of Observances" that is a list of dates rather
  than events at a place; an "SMSD Aquatic Center - Lap Lane Availability" feed,
  which is the bookable-badminton-court mistake exactly; a mansion publishing
  "Wedding Site Tours", "Open for Visitors" and "Closed for Private Event" — an
  ANTI-event; and a "City Park" feed whose 365 vevents are 365 copies of "Public
  Historical Tour", one standing row per day. Every one of those feeds parses
  perfectly and returns future events. The second batch added two more of the
  same family, and they are the ones to watch for: "Douglas County Open Space"
  and "Frederick County — Energy and Environment" turn out to be a Planning
  Commission, a Land Use Public Hearing, a Historic Preservation Board and a
  Sustainability Commission Meeting. A governance calendar wearing an
  open-space name gets past `CIVIC_DENY_RX` because the denied word is in the
  DEPARTMENT, not in the calendar's own label.

- **The big free-walk organisations are closed to us, and that is their
  decision.** Probed 2026-09-13 with the production User-Agent: ramblers.org.uk
  answers **403** on /robots.txt itself, sierraclub.org returns a bot-management
  interstitial, wildlifetrusts.org a Cloudflare challenge, and Milwaukee County,
  St. Louis County, Marin County, Santa Clara County and Riverside County Parks
  the same. Do not retry with a browser UA. mountaineers.org is the one that
  says yes in writing — `User-agent: * / Allow: /` with
  `Content-Signal: search=yes,ai-train=no,use=reference` — and it names
  ClaudeBot, GPTBot and CCBot in individual Disallow groups, so anything reading
  it must be honest about which UA it sends.

- **Open data has trail INVENTORIES, not led walks, and the catalog says so as
  plainly as it did for block parties.** Measured 2026-09-13 against the live
  Socrata federated catalog, page 1 of each: "guided hikes" matches 5 datasets
  in total, "ranger programs" 5, "naturalist programs" 3, "hiking trails
  events" 1, "bird walks" 11. The big ones are big and empty — "nature
  programs" 600, "park programs" 514, "recreation programs" 476 — and across
  all eleven new terms, plus the two `parks events` / `recreation programs`
  queries already in DISCOVER_QUERIES, **zero** datasets are both event-shaped
  (`_infer_map`) and not already configured or known-dead. A city publishes the
  GEOMETRY of its trails and the boundary of its parks; the Tuesday morning
  bird walk along that trail is on the nature centre's own calendar. So do not
  add these queries — the same answer as for local music and festivals, and the
  reason the park-agency seed list above is a hand-curated file rather than a
  discovery backend.
