# The adapters and what individual sources taught

> Part of mapsee-aggregator's agent notes — see `AGENTS.md` for the map. Read this file when the bug is about: Luma, parkrun, businesses vs events, a malformed record, schema.org by accident, webcal, JSON-LD, Overpass slots, seattlecenter, a helper's inherited contract.
> Every note below was measured before it was written; keep the numbers when you edit.

- **Luma's Discover feed takes `discover_place_api_id`.** The obvious
  `place_api_id` — which is what the id is called everywhere else in Luma's own
  payloads — is accepted, ignored, and answered with a 200 and a full page of
  events for whatever city the RUNNER's IP is in. Asking for Seattle from a
  GitHub runner returns Columbus, Ohio, silently. `expect_region` in
  `luma_sources.json` turns that into a refusal instead of wrong data; set it on
  every place.

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

- **parkrun start times are NOT in the feed, and they must not be guessed.** They
  vary by country and by SEASON — a UK 9am is an Australian 7am in summer, and
  some UK events start at 09:30. The adapter emits an all-day event and says
  "Start time on the event page." rather than inventing one; `start_times` in the
  config is per-country and deliberately empty until somebody checks a country.
  Its `countries` map is the same discipline one step over: parkrun's country
  codes are opaque integers and the feed carries only a domain, so `97 -> GB`
  is written down as data instead of parsed out of `parkrun.org.uk` by a regex
  that would have to know org.uk is not UK.

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

- **A venue calendar carries entries that are not events, and they are the
  best-formed rows in the feed.** The Royal Room posts "CLOSED FOR MAINTENANCE"
  and "Closed for Private Event" as event_listing posts with real dates, because
  a notice is the only thing that CMS can put on a calendar — 5 of its 95. They
  classify as music and pin at the venue like everything else, so they would tell
  somebody a shut venue is open. `skip_title` is matched on the NAME and anchored:
  the other available tell, a 00:00 start, is shared by every one of them and
  would also throw away a New Year's Eve show. Same family as Traders Village's
  car show — the feed works, and it is not what it looks like.

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

- **VERIFYING IS NOT INGESTING, AND A CITY PUBLISHES ITS SHUT DAYS.** A
  CivicPlus city is many calendars — Gloucester publishes 60 — and there is no
  whole-calendar export, so each is proposed separately and each has to earn it.
  Three tests, cheapest first: the category NAME (free, a DENY list because
  governance vocabulary is small and stable while "Concerts on the Green" is
  not — written as a keep list first, it threw away 4th of July, Juneteenth,
  Halloween and Pickering Barn while keeping "Waste Collection Events"); then
  the feed's own ENTRIES, because 30 of two cities' 47 categories are valid
  iCalendar holding nothing, and several more are meeting schedules or 95
  repetitions of "Juneteenth Day Holiday" that pass any name test; then a cap of
  six per city, ranked by upcoming volume, because 5,770 cities times twenty
  categories is not a file anybody can read.

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

- **The obvious copy of a fact is the wrong one, and seattlecenter.com offers
  three at once.** Its listing groups cards under a date heading with NO YEAR on
  a calendar that runs seven months ahead, so inheriting it stamps every January
  show eleven months in the past, where the horizon filter drops it in silence —
  the date is only stated in full on the individual event page, which is why
  that adapter pays one request per event. Its locations are Google Maps links
  carrying TWO coordinate pairs, and the first one a regex finds (`@lat,lon`,
  the viewport centre) is a constant 165m west of the place (`!3d!4d`) on every
  link; worse, on a DETAIL page that link usually belongs to a different event,
  because of the related-events rail. Same tell as MyListing's two dates, SLU's
  series start and BikeReg's server offset: when a source spells one fact twice,
  find a record where the two DISAGREE before choosing.
  `mapsee_ingest_seattlecenter.py`, pinned in `test_ingest_seattlecenter.py`.
