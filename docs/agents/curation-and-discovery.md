# Finding sources: catalogs, discovery, verification, refusals and licences

> Part of mapsee-aggregator's agent notes — see `AGENTS.md` for the map. Read this file when the bug is about: `catalog_curate`, the ledger and its statuses, `_not_included`, sitemaps and robots, bot challenges, site builders vs calendars, calendar plugins, licences.
> Every note below was measured before it was written; keep the numbers when you edit.

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
