# The adapters and what individual sources taught

> Part of mapsee-aggregator's agent notes — see `AGENTS.md` for the map. Read this file when the bug is about: Luma, parkrun, businesses vs events, a malformed record, schema.org by accident, webcal, JSON-LD, Overpass slots, seattlecenter, a helper's inherited contract.
> Every note below was measured before it was written; keep the numbers when you edit.

- **Optional search fields need source facts, including negative admission facts.**
  The 2026-10-02 GSC audit checked 40 public URLs / 39 distinct listings. Meetup
  group names were being emitted as people with no public URL; retain the native
  `group.urlname` as an actual Organization URL, with bounded names/slugs. The
  native complete prose now retains restricted admission before truncation:
  first 100 tickets, FREE/Lady, a required themed outfit, purchase and free-until
  entry cannot assert universal free admission. Generic JSON-LD retains its
  named performer/organizer fields alongside conservative ticket facts.
  `validFrom` survives only if every same-price offer publishes the same valid
  zoned ISO datetime; import/event dates are never substitutes. 94 admission
  checks and all 59 test scripts pass; 2 Windows stdout failures needed
  `python -X utf8`, not a source repair. No extra per-event HTTP read was added.
  Ponce's allowed 26,330-byte ICS response had 30 VEVENTs, including 4 City
  Winery occasions tagged UTC at 15:00 despite official pages publishing
  3–5pm Atlanta. Source-configured exact LOCATION overrides now retain the
  official 650 North Avenue NE address / entrance coordinates / organizer;
  the explicit title+LOCATION+TZID rule alone corrects this wall clock to
  America/New_York. Other sources, UTC-Z timestamps and all-day dates remain
  unchanged. Original location/date fingerprints and UID/fallback IDs survive.
  The Mapsee backfill repairs the 2 reported occasions immediately; normal
  source-detail refresh reaches future occurrences. RAM's fresh source page
  still publishes no availability or on-sale date; do not invent them. Its
  published registration cutoff/call notice now survives the 800-character
  prose cap on refresh: 10 event-detail checks retain it without inferring stock.

- **MUSEUM ADMISSION WINDOWS ARE VISITS, NOT ORGANIZED EVENTS.** Google's
  Event guidance excludes business hours. The 2026-10-01 dry run of 14 curated
  museum/fort/gallery programs generated 2,854 records: 2,853 dated windows and
  1 standing VMFA row. All now explicitly carry
  `source_details.listing_type = "visit_window"`, which the Mapsee Worker uses
  for Place metadata; a monthly free-admission offer is also a visit. The
  opt-in changes no fingerprints, dates, coordinates, admission restrictions
  or row cardinality. All 2,854 markers survived EventStore and `build_rows`
  with 0 geocoding calls. Only the 10 programs with existing numeric admission
  facts assert zero prices; the 4 established monthly programs gain no invented
  price facts. Summer meals, the gallery tour and 2 performances retain Event
  defaults. Unsupported opt-ins fail closed. `test_programs.py`: 45/45;
  `test_ingest_programs.py`: 49/49. Deploy the compatible Worker before this
  metadata; 0229 already projects the JSON, so no database migration is needed.

- **NATIVE ZERO PRICES NEED NO PER-EVENT FETCH, BUT MUST SURVIVE REFRESHES.**
  On 2026-10-01 Tribe `cost` and JSON-LD raw Offer/list/AggregateOffer fields
  gain conservative admission normalization: **80 helper checks**, bounded
  to **64 nodes / 6 levels / 32 price digits**. Every offer needs a price for
  universal zero; known positive prices veto global free even with incomplete
  siblings or bounds, while zero-only incomplete offers remain unknown.
  Audience-restricted offers do not invent a paid price. Admission
  markers precede the sync's **600-character** prose cap and the existing 0227
  text tagger. No detail-page request, guessed currency or database schema is
  added. Tribe's opt-in `free_only` filters before venue normalization; a spy
  sees **1 converter call for 4 rows**, dropping unknown/paid/member-only rows.
  - The native reader's transient `admission_checked` flag is not serialized.
    The local EventStore remembers its pricing owner; a successful same-owner
    unknown read clears obsolete prices and injected free prose. Unrelated thin
    readers preserve facts, and native pricing updates retain performer metadata.
    A known paid/restricted source vetoes another source's zero, independently
    of ingestion order; the same owner's paid -> zero correction still applies.
    **11 persisted EventStore -> to_row lifecycle checks** cover these transitions.
    New blank-price rows omit details and do not force daily refreshes; explicit
    clears of previously enriched rows retain the existing detail-sync behavior.
    Fresh thin feeds follow the existing only-new/Wednesday refresh rule.
  - Tribe IDs are local to a publisher. Two different hosts with numeric ID
    **541** previously became **1 persisted record / 1 rekey**; scoped internal
    keys now preserve **2 records / 0 rekeys**, including after save/reload.
    Public source IDs and fingerprints are unchanged; source refs carry publisher
    provenance, so an external ticket link cannot hide the owning feed. Unbound
    legacy refs do not steal another host's event. **64 Tribe checks** cover
    free-only refreshes from zero to paid/unknown, cross-host collisions, legacy
    upgrades and rekeying into an existing destination. New unknown/paid rows
    still skip conversion; the one site lookup avoids rescanning per event.
  - `standing:true` is opt-in for programs with verified year-round weekly hours:
    fixed coordinates, explicit weekday hours and an IANA zone that agrees with
    the sync are required; seasonal/monthly/closure rules are rejected. **29
    standing checks** and the existing **49 program checks** pass. VMFA's weekday
    hours use **1 stable fingerprint**, retaining it on the next day, with no
    geocoder requests. The weekly roller has no holiday exceptions: use dated
    schedules when closures matter. Verified dated site points can separately
    opt into `coords_exact:true` to avoid redundant Census refinement.

- **A VEVENT URL can be the subscription export, not its event page.** Measured
  2026-10-01 on Santa Rosa's category 31 CivicPlus feed: all **5** retained
  volunteer shifts had relative URL
  `/common/modules/iCalendar/iCalendar.aspx?feed=calendar&catID=31`, while their
  descriptions supplied exact `/calendar.aspx?EID=` pages. The old importer
  put the relative export in `sources[].url` and the displayed Tickets / info
  line. `_event_url` now resolves explicit relative URLs with stdlib `urljoin`,
  preserves third-party signup URLs, and replaces a CivicPlus subscription
  only with an already-published same-host event-page URL whose numeric EID
  matches this VEVENT's UID. Missing, wrong-event or foreign description links
  leave the configured calendar home as the info fallback, or no link when
  there is no home. No URL is invented and no extra page is fetched. A home
  fallback never triggers opt-in per-event detail enrichment; malformed URLs
  cannot lose the feed. **10 regression checks** exercise the real ingest and
  final store-to-row link, including these failure paths; RamArt's existing
  **9/9** source-detail checks still pass. Fingerprint/time/location inputs are
  untouched. Existing rows follow the normal only-new/Wednesday refresh rule.

- **An empty iCal DESCRIPTION can hide a useful source page.** The 3 museum
  events in Unsie's 2026-10-01 Search Console notice had full street addresses
  inside LOCATION but no structured address columns, price/status or artists.
  Racine Art Museum's own allowed event pages supply those: `mapsee_event_details`
  reads only their overview/ticket blocks and the matching JSON-LD occasion.
  The opt-in `details: ramart` on 1 ICS source keeps its fingerprint/time/coords.
  A successful `source_details` read refreshes unclaimed rows even in only-new;
  failed reads omit the key and preserve known facts. Skip-unchanged still
  compares these existing rows. Requires Mapsee **0229 applied before shipping**.
  Current facts: 3 exact addresses, 2 named artists, USD 130 public vs 104 member
  price on Potter's Wheel, sold-out/no current price on Pet Portraits, and a free
  family festival with no named performer. Its source's `performer: Organization`
  is a placeholder, not a participant. **9/9** source/ingest/sync checks pass in
  `test_event_details.py`; fake network calls, wrong dates, member-only prices,
  claimed rows and held rekeys are covered. The generic free classifier keeps
  its conservative exclusion for the words "free fall".

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

- **A green sweep can hide a changed Socrata schema.** On 2026-09-09,
  civic run 34358778922 succeeded while NYC Parks returned HTTP 400 because
  `startdate`/`enddate` had become combined floating `starttime`/`endtime`
  timestamps. Ordering/filtering the current fields recovered 1,142 placeable
  rows from 1,153; Chicago Park District supplied another 498 of 500 rows.
  Their configured New York/Chicago timezones apply to naive timestamps and
  query clocks. The targeted sync upserted 1,549 changed events, skipped 29
  unchanged rows and 12 virtual events; public readback matched six sampled
  titles and start instants. Socrata now retries transient failures at most
  three times, leaves 400/403 alone, saves other successful sources and exits
  nonzero after a source failure. Five tests in `test_ingest_opendata.py` pin
  these contracts; changes to its source config also trigger CI.

- **"HAS COORDINATES" WAS THE WHOLE ONLINE TEST, AND AN ORGANISER CAN WALK
  THROUGH IT.** Every adapter that refuses a virtual event refuses it by asking
  whether it has a latitude — `mapsee_ingest_meetup` says so in as many words
  ("online / no venue -> can't map it"). That holds only while the venue box is
  empty. Meetup lets a Zoom event be filed as `eventType: PHYSICAL` with
  anything at all typed into the venue, and then it has coordinates and sails
  past. The reported row did exactly that: "Seattle Gay Virtual Speed Dating on
  Zoom … Join from home", status ACTIVE, venue AND address both the Plus Code
  "JP7Q+33 Mercer Island", city "Seattle" — a street corner on the map for a
  video call, and the sync's Census pass then moved the pin several km by
  geocoding the fabricated address. Measured 2026-09-13 over 2,000 live event
  pages from mapsee.me's own sitemaps: **40 rows say in their own words that
  they are on Zoom and every one is pinned to a street; 35 are online-only and
  ALL 35 came through Meetup** — one commercial speed-dating network reposting
  the same template city by city, into groups that have nothing to do with it (a
  taekwondo group, a calligraphy club, a vegan cookery crew). `looks_online_only`
  in `mapsee_ingest.py` is the predicate; the adapter refuses on it, and
  `mapsee_retire_online_events.py` is the other half for rows already written.

- **THE EXPENSIVE DIRECTION IS THE HYBRID ONE.** The other 5 of those 40 are a
  sangha, a church and a meditation group that genuinely run a room as well as a
  stream, and "Online and In-Person" is a real event at a real address. Refusing
  those would take working congregations off the map to remove a spam network,
  and nothing downstream would ever say so. `_HYBRID_RX` withholds on one clear
  phrase and reads the TITLE as well as the blurb, because one of the five says
  it only in its title.

- **TWO PHRASES WERE DELIBERATELY TAKEN BACK OUT of the online rule, and both
  were in the first draft.** `virtual class`/`virtual session`: a Les Mills
  VIRTUAL class is held in a real studio with the instructor on a screen, and
  OpenActive leisure centres are the biggest single block of rows on the map
  (393 of the 2,000 sampled are `book.everyoneactive.com` alone, with titles
  like "R P M Virtual"). Every other `virtual …` noun fired 0 times across the
  sample, so nothing was lost by dropping the ambiguous two. And `Zoom link`,
  which measured **0 for 2** — both rows carrying it are hybrid. A listing that
  publishes a dial-in usually has somewhere to dial in FROM.

- **A PLUS CODE IS A DROPPED PIN, NOT THE NAME OF A PLACE.** Google Maps offers
  an Open Location Code when you pin somewhere with no address, so it is what
  lands in a venue box filled in by a machine or by somebody with nothing to put
  there — and it renders to the reader as the name of the venue. 6 rows in the
  sample carry one; 5 have it in BOTH the name and the address and all 5 are the
  speed-dating network, and the sixth is a real hike whose venue is "2800 Torrey
  Pines Scenic Dr". `venue_is_only_a_plus_code` therefore demands BOTH halves,
  and that is load-bearing: large parts of the world have no street addressing
  and a Plus Code is the honest ADDRESS there — but the venue is still called
  something. The base-20 alphabet excludes vowels, so the pattern cannot match
  an ordinary word with a plus in it ("Cafe 8+8", "Studio A+").

- **A REFUSAL AT THE BOUNDARY LEAVES EVERYTHING ALREADY WRITTEN EXACTLY WHERE IT
  IS**, and this repo now has three scripts that exist only to say so —
  `mapsee_retire_thin_artwork`, `mapsee_retire_perday_osm` and
  `mapsee_retire_online_events`. Ship the retire pass WITH the ingest rule, share
  one predicate between them so they cannot drift, and hide rather than delete so
  `--unhide` can take it back. The one wrinkle: `hidden_at` is a single column
  several tools set, so `--unhide` here can restore a row that
  `mapsee_prune_cancelled` hid for being cancelled — most of these listings are
  both. It is self-healing (the pruner re-hides within the day), but run
  `--unhide` because you think THIS rule was wrong, not as a general undo.

- **A VENUE BLOCK PINS EVERY COORDLESS EVENT AT ITS VENUE, including the ones
  held somewhere else, and no automatic rule can tell which those are.**
  Measured 2026-09-27 over the Events Calendar sites that carry a `venue`
  block: 183 of them, 7,633 events. 7,042 had no coordinates of their own and
  all took the block's. 2,865 named no venue, 1,974 gave the block's own
  address, 1,374 named only a room or an alias, and the rest gave another
  address. The obvious rule, "not the block's city, so not the block", would
  have been wrong about almost every one of the 336 that differed: Den Haag vs
  's-Gravenhage (91), Etobicoke vs Toronto (87), Vanier vs Ottawa (73), Lurup vs
  Hamburg (30). Outside the US a row the block does not place is DROPPED, since
  Census is the only geocoder at the sync, so that rule would have deleted
  them. The strays that could be identified were a few dozen, most of them
  in two sites: UMFA's Land Art Week trips ("Rozel Point", "Powder Mountain",
  no address) beside 18 rows that say only "UMFA", and a Tampere centre's
  events in Oulu, Kemi and Rovaniemi. How many of the 1,374 room-or-alias rows
  are strays cannot be read from the data. A US stray WITH a street is fixed by the sync anyway, because the
  Census pass re-geocodes every row that has an address. But the block also
  lent its street to events without one, which the Census pass geocoded
  straight back to the block. So `names` on a block is an opt-in: it then
  fills only an event that names no venue or one of those names, and lends
  neither its coordinates nor its street to anything else. Set on UMFA and
  kuurosokeat.fi. Squarespace and JSON-LD have the same fallback and were not
  measured.

- **COMMUNICO PUTS "ONLINE" IN THE BRANCH HALF OF THE LOCATION, and the sync's
  online check never saw it.** A row-level review of 46 library calendars
  (2026-09-28, 36 of them Communico `*.libnet.info` feeds) found online
  sessions written as "Online - Virtual Room", "Virtual - Zoom", "Virtual
  Branch - Virtual Room 3" and "Virtual Library - ...". 101 carried GEO:0;0,
  which the adapter correctly ignores, so it sent the LOCATION to Photon and
  pinned whatever came back: OCLS's 59 went to the Orange County centroid and
  a shoe shop, PGCMLS's 34 to a schools office, HEPL's 4 to a bank. 114 more
  carried the owning branch's GEO and were pinned at the branch (Miami-Dade
  38, Jacksonville 27). `is_virtual` in the sync reads a venue made ONLY of
  placeholder words, so "Virtual - Virtual Room - Adult Programming" passed
  it. `ONLINE_LOC_RX` in `mapsee_ingest_ics.py` now skips such a row before
  GEO is read, and counts it on the feed's line. It matches the room half too
  ("Westlake Porter Public Library - Online"). Look-alikes stay:
  "Virtual Reality Lab - Room 2", "Online Learning Center, 5 Elm St". Over the
  837-feed corpus of 2026-09-27 it matches 315 of 70,904 ics rows: 28
  distinct strings, every one of them plainly online. The same
  review found Kent County's LOCATION reaching Photon with a literal `&nbsp;`,
  because only `&amp;` was unescaped. It found "Offsite - Offsite" and "In the
  Community -" guessed onto a post office and a community centre: they are
  now placeholders, and "In the Community - Lincoln Park" still geocodes.

- **AN iCALENDAR FEED THAT NAMES NO CHARSET IS UTF-8, and `resp.text` read it
  as ISO-8859-1.** requests gives a `text/*` body without a charset HTTP/1.1's
  default, and RFC 5545 says an iCalendar stream's default is UTF-8.
  OpenAgenda serves a bare `text/calendar`, so every accented character of its
  57 feeds became two (the "é" of "Numériques" arrived as U+00C3 U+00A9), on
  the map and in the fingerprint. In the 2026-09-28 corpus that was 3,692
  listings in 58 feeds; the 58th, wina-gent.be, garbled only descriptions.
  `_decode_ics` now decodes the octets as UTF-8 after unfolding them (a fold
  may split a character) and keeps `resp.text` for a declared charset or a
  body that is not UTF-8. Replayed over 13 of the feeds, fetched once each: of
  3,031 records the old reader built, 2,708 had a garbled title or venue and
  none do now. Changing the text changes the identity, so the adapter also
  hashes each event as the old reader did (the same parser over the Latin-1
  reading) into `legacy_fingerprints`, and 3,030 of the 3,031 old identities
  are recovered that way. The one it misses is "Point Information Nantes
  Solidaire : ANNULÉ", which the fixed reader recognises as a cancellation and
  refuses. None of these feeds sends an ETag or Last-Modified, so none was ever
  in `ics_feed_cache.json`; any feed that was is fetched afresh once
  (`FEED_CACHE_VERSION`).

- **A GOOGLE CALENDAR READ THROUGH THE CALENDAR API HAS TO PARSE LIKE ITS
  EXPORT, OR EVERY ROW MOVES.** calendar.google.com/robots.txt refuses the
  iCal export (`curation-and-discovery.md`), so with GOOGLE_CALENDAR_API_KEY set
  `mapsee_gcal.py` reads a Google calendar through the Calendar API v3.
  www.googleapis.com serves no robots.txt (a 404). It then writes the events
  back as VCALENDAR text for this adapter. Measured 2026-09-30: an unkeyed call
  answers 403 "Method doesn't allow unregistered callers ... Please use API Key
  or other form of API consumer identity". A fake key in the `X-Goog-Api-Key`
  header turns that into 400 "API key not valid", so the header is read and the
  key never has to be in a URL. Identity was the risk. The fingerprint's date
  is whatever `_parse_dt` reads off DTSTART, and for a `Z` time that is the UTC
  date: a 9:30pm show in Edmonton (03:30Z) is filed under the next day. The
  rows the export made from 7 Google calendars in production (2026-09-29) were
  221 of 227 timed in UTC, 2 in a local zone and 4 all-day. Their UIDs were the
  export's (`...@google.com`, or `CSVConvert...` for imported events), which
  the API returns as `iCalUID`. So the text writes a one-off event in UTC with
  its iCalUID, and a series in its own zone. `test_gcal.py` runs the same
  events through `ingest_ics` both ways, and every export row comes back under
  its fingerprint. What changes is series: this parser ignores RRULE, so the
  export gave a series only its first occurrence. The API expands it
  (`singleEvents=true`), and each instance needs a UID of its own
  (`<iCalUID>/<originalStartTime>`), because EventStore re-keys a (source,
  source_id) that returns with a new fingerprint and a shared UID leaves only
  the last instance. 2 of the 30 configured Google calendars (Richmond Yacht
  Club, Edsvikens Tennisklubb) hold a secret `private-...` address. The API
  cannot open a private calendar with a key, so those two log NOT READ.
  Measured with the real key (`smoke-gcal.yml`, 2026-09-30): 28 of 30 read.
  Of the 224 still-upcoming rows the export had made from 7 of them, 223 came
  back under the same fingerprint. The one that did not is a screening no
  longer in the theatre's calendar: Garneau's "NAZA" had four, and the other
  three matched, including the one whose UTC date is the next day. Reading 400
  days ahead kept 949 rows, 616 of them series instances the export could
  never give. That is why the window is now 180 days (`DAYS_AHEAD`), the
  catalog's usual one, with `within_days` per source. Re-run at 180 days: 614
  rows, 315 from a series. 195 of the 196 production rows inside the window
  matched (the same screening missing). The 28 beyond it, yacht races as far
  out as August 2027, are not re-read. An import cannot delete, so they stay
  as they are, and come back under the same fingerprint once inside the
  window. Nine calendars keep
  nothing because their events carry no LOCATION (Betlehem 314 VEVENTs,
  Mercedarias 283, St. Herman's 246, Lasswade Archery 154). A `venue` block
  like Montlake's would pin them. That is a curation decision, not a reader
  bug.

- **MODERN EVENTS CALENDAR'S `"price": "0"` IS AN EMPTY COST FIELD, NOT A FREE EVENT.** MEC copies the event's cost field into `offers.price`. For an empty field the Pro build (7.36) writes "0" and the Lite source (`features/schema.php`) writes "".
  - Measured 2026-10-04 on 26 MEC calendars: 39 of 39 event pages whose block says "0" print no cost line. All 8 with a cost filled in print it.
  - On 20 configured listings, 188 of 388 upcoming events (224 of 455 listed, 11 of 19 sites) carried the zero. `normalize_admission_facts` read every one as free, giving "Free to attend.", then offer:free from 0227 and isAccessibleForFree on /e/<id>. Whitehorn's "$10 drop-ins" yoga was one of them.
  - `_mec_unset_price` drops a bare zero on any page matching `modern-events-calendar|mec-event`. The row then carries `source_details` `{}`, not None, so the sync writes NULL over a stored free claim.
  - Typed text ("Free", "kostenfrei") stays as written, and the admission reader treats it as unknown.
  - MEC's own printed "Free" (`<dd class="mec-events-event-cost">`, from `render_price()` on a zero) is believed, but only on an event page holding a single Event. It appeared on 0 of 60 sampled zero-price pages.
  - `python test_ingest_jsonld.py`.
- **MODERN EVENTS CALENDAR PRINTS THE WALL CLOCK AS IF IT WERE UTC.** MEC stores an occurrence as the local wall clock read as UTC, and its own links show it: Haus Steinstraße's `&time=1791576000` is 20:00Z for a 20:00 show. The JSON-LD prints that instant in the site's offset, here `22:00:00+02:00`.
  - Measured 2026-10-04 on 15 event pages from 9 MEC sites: 14 were late by exactly the site's offset.
    - Whitehorn prints "6:45 pm" next to a block saying 12:45-06:00.
    - Ateliertheater prints 19:30 next to 21:30+02:00 before 25 Oct and 20:30+01:00 after, so the error follows DST.
    - Basler Papiermühle's site is set to UTC, so Basel saw everything 2 h late.
    - The 15th page matched only because the UK was at +00:00.
  - `_mec_wall_clock` hands the sync the naive UTC wall clock before `to_event`, and the sync localises it from the venue. This happens before the date is taken, so the fingerprint uses the corrected date: a block saying `2026-10-24T00:00+02:00` is the 23rd's night, as its `?occurrence=2026-10-23` url says.
  - Lite writes bare dates, which pass through untouched.
  - Live after the fix: 0 of 118 timed candidate rows keep an offset, and every one checked against its page matches.
- **ONE URL WITH SEVERAL DATES WAS ONE ROW.** `EventStore.upsert` looks a row up by source_id before the fingerprint. The Florrie gives every Tuesday's yoga the same url with no `?occurrence=`, so each later date re-keyed the first row and kept the first date's time. Live 2026-10-04: "kept 91 events" into a store holding 21.
  - In the 20-listing sample, 127 of 417 upcoming inline blocks share an id with another date.
  - `_shared_urls` appends `#<date>` only to an id that the same page shows with more than one date, on every JSON-LD site. The fingerprint (external_id) never involved source_id, so DB identity is unchanged.
  - After the fix: 89 kept, 89 stored, 0 re-keyed.
