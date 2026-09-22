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

- **The best location data in the catalog was invisible to the report: 824 of
  840 surveyed venues read as metro "?".** `_locate` reads a source's NAME and
  its `geocode_suffix`, and that is all an ics-shaped config carries — but 840
  entries place themselves with a `venue` block instead, a surveyed OSM point
  with an address. Measured 2026-09-20: lat/lon on 836 of them, a city on 399,
  a country on 132, and the report could read none of it. That is 26% of the
  catalog, and it is every source the venue walk has ever proposed: "Nectar
  Lounge, Seattle WA" read as metro ?, country ?, which the thin-ground ranker
  cannot tell apart from a country with no feeds. `_locate_entry` now falls
  back, in order of how directly the thing was observed — `_metro` (868 of
  them), the venue's stated city/region/country, then the surveyed point — and
  metro "?" goes 1,004 -> 133 while country "?" goes 276 -> 219, with ZERO rows
  moving from one named country to another.

- **A METRO BBOX IS A SWEEP RADIUS, NOT A BORDER, and letting geometry name the
  country got 24 rows wrong.** The first version of the fallback above took the
  country from whichever swept metro's bbox contained the venue's point. It
  located 820 of 824 — and moved Musee de l'Impression sur Etoffes (Mulhouse,
  `.fr`) into Switzerland via Basel, De Muziekgieterij (Maastricht, `.nl`) into
  Belgium via Liege, Hans-Peter Porsche TraumWerk (Bavaria, `.de`) into Austria
  via Salzburg, and three Geneva-adjacent French venues into Switzerland. Every
  one was a border case the ccTLD already had RIGHT. `_metro` has the same flaw
  for the same reason: it records which sweep found the venue, not where the
  venue is. So the rule is split — geometry and provenance may name the METRO,
  and only a statement ABOUT THE VENUE (its own `venue.country`, or its city and
  region together) may name the COUNTRY. Everything else still falls through to
  the ccTLD rescue, which is where it belongs.

- **`_rows_jsonld` must not start calling `_locate`.** Adding the venue fallback
  to the generic path was safe; wiring the same call into the jsonld expander
  was not. `_locate` reads a name's parenthetical as a city and defaults it to
  the United States, so "i45 (industrie 45)" — a club in Zug — became a US
  metro called "industrie 45". The expander keeps its original country rule
  (ccTLD, then the US only when the NAME scan found a US metro) and takes only
  the metro from the new fallback. The US default has to stay keyed to the name
  scan rather than to the metro, because the metro can now come from a Swiss
  venue block and "metro is known, therefore American" would then be false.

- **Three registries that are not ccTLDs and still name one country.** `.cat` is
  Catalonia's sponsored TLD and every holder is in Spain — 14 rows, and it is
  why the metro table read "Barcelona ?" beside "Barcelona Spain". `.gov` and
  `.edu` are US-restricted registries; everywhere else uses gov.uk, gov.au,
  edu.au, ac.uk, which the table already resolves through their real ccTLD. 50
  rows between them. The surrounding rule still holds for `.org`, `.com`,
  `.net`, `.eu` and `.social`, which say nothing: those are the 217 rows that
  remain "?" and they are honest.

- **Six countries the report FLAGged as thin were reachable by no backend at
  all, so the flag could never be closed.** Measured 2026-09-20 by crossing the
  coverage rows against `metros_global.json` and `CITY_CLASSES`: Iceland,
  Jamaica, Lithuania, Malaysia, Puerto Rico and Slovenia each had exactly ONE
  source, and in every case it came from a global feed (parkrun, bikereg, a
  Mobilizon instance) rather than from anything that went looking. The ranker's
  advice for them is "broaden metros and categories", and there was no metro
  list and no city class to broaden. Five now have a civic walk — Iceland 77
  cities (reykjavik.is), Lithuania 112 (vilnius.lt), Slovenia 40 (Ljubljana),
  Malaysia 40 (Kuala Lumpur), Jamaica 2 from 8 rows (ksac.gov.jm) — plus South
  Korea (Seoul) and the UAE (Dubai, dm.gov.ae), which the OSM venue walk swept
  but the town walk never did. All seven sort to the FRONT of the thinnest-first
  rotation, so they are the next seven civic runs.

- **Puerto Rico is not missing, it is absent from the data.** Its
  `municipality of Puerto Rico` class answers in 43 seconds with ZERO rows: the
  78 municipios are in Wikidata, but not carrying an official website and
  coordinates and a population together, which is what the walk needs. Hong Kong
  has no settlement class to name at all — it is one city whose subdivisions are
  districts, and the OSM venue walk already sweeps it. Neither is worth another
  attempt without new evidence.

- **Adding a country to CITY_CLASSES and not to COUNTRY_NAMES ships the bug the
  suffix work existed to fix.** Caught on the first live verification of the new
  batch: Jamaica's geocode suffix came out `, Kingston, JM` — an ISO code, the
  exact shape that filed twenty Swiss venue calendars under the United States.
  `cityclass` prints the suffix for this reason, which is how it was seen before
  anything shipped. The two tables are asserted in step at import and pinned by
  a test.

- **The thin-ground ranker counts LABELS, not supply, and the label is
  `community` by construction — so the four starved categories cannot be fed by
  the backends that are actually growing.** Measured 2026-09-20 over all 51 runs
  in `coverage_history.jsonl` (20260810 -> 20260920, 901 -> 3,164 sources).
  Of the +2,263 grown: **community +1,579, seventy per cent of everything**,
  then learning +176, arts +144, fitness +104 — against kids +24, outdoors +26,
  running +21 and **volunteer +4**. The four the ranker points every gap sweep
  at took 3.3% of six weeks of growth, and `running`'s +21 is one jump on
  20260825 that is the parkrun expansion, not a sweep. The cause is structural,
  not a bad query list: 993 of the 1,139 civic-discovered sources (87%) are
  `community`, because `to_candidate` files ics/tribe/mylisting under
  DEFAULT_CATEGORY and the ONLY path that reads a programme's own name —
  `civicplus_candidates` -> `category_for_feed` — exists on CivicPlus, which is
  a US platform. So the twenty countries added to CITY_CLASSES will deliver
  essentially 100% `community`, and `volunteer` will still read as starved.
  **A whole town's calendar IS mixed, so `community` is the honest label** — the
  events inside it reach the right door through the promotion regexes in
  mapsee_supabase_sync, not through the config. Which means the ranker is
  measuring the wrong thing, and no amount of curation will move its numbers.
  The obvious cheap fix DOES NOT WORK, and it was measured rather than assumed.
  The Events Calendar exposes its category list — 18 of 24 civic-discovered town
  calendars answered `/wp-json/tribe/events/v1/categories` — so giving Tribe the
  treatment CivicPlus gets looks like the same trick played worldwide. It is
  not: of 142 distinct category names across those 18, **96 (68%) are local
  vocabulary no word list can hold** ("Route 66", "CAC", "Band Comp", "Healthy
  Point", "Attractions", "Awareness"), 34 are governance the deny list already
  refuses, 7 happen to BE a lens key, and `category_for_feed` recovers exactly
  5 — two learning, two kids, one volunteer. That is the same wall the note
  above `CIVIC_DENY_RX` describes from the other side: governance vocabulary is
  small and stable, programme vocabulary is unbounded and local. A few names are
  worth having anyway ("Arts & Culture" and "Arts/Culture" are arts, "Athletics"
  is fitness, and the ampersand is the only reason the first is dropped today),
  but that is a handful, not a fix. The real one is to rank by what the
  CLASSIFIER produced per category rather than by what the configs declare —
  which needs a per-category count the DB does not currently expose, since
  `stats_snapshot_all` is per SOURCE. Until then, read `volunteer: 13` as "13
  feeds SAY volunteer", never as "the volunteer door is empty".

- **Where the events actually go is not where the config says.** The tribe
  adapter already reads each event's own categories and passes them through
  `norm_categories`, which keeps only names that are ALREADY a lens key and
  drops the rest — so the platform's own labels are lost at ingest and the sync
  never sees them. That is why the 7 of 142 that survive are the ones spelled
  exactly "Arts", "Community", "Food", "Outdoors". Anything richer has to come
  from the promotion regexes reading the event's TITLE, which is what
  `mapsee_supabase_sync` does and is the reason a mixed town calendar still
  fills the right doors.

- **A whole-file ledger write silently loses a concurrent writer.**
  `_save_ledger` dumped the in-memory copy over the file, and every sweep loads
  the ledger once at the start and saves it minutes or hours later — so
  anything another run wrote in between is gone. Measured 2026-09-20: a
  `ledger --offsite --refresh` that had loaded the file dropped ten
  neighbourhood-association probes another session had written while it ran,
  all dated the same day. Nothing errored, nothing was reported, and the next
  sweep would simply re-probe ten sites already answered. It re-reads and
  merges now, with `_merge_ledgers` — the rule `cmd_reapply` has always used
  for the same collision, more recent probe wins a shared URL. Nothing deletes
  from the ledger, so a union cannot resurrect something removed on purpose.
  This matters because the repo's working mode reaches it: more than one agent
  works the same checkout.

- **`offsite:<host>` recorded the host and threw away the LINK, which is the
  only part anything could act on.** 358 venues in the ledger were probed,
  found to have a calendar, and filed as failures naming the platform: facebook
  158, instagram 77, eventbrite 46, ticketmaster 24, humanitix 21, tickettailor
  9, trybooking 6, universe 6, linktr.ee 3, dice 3, meetup 2, axs 1. The note
  above `OFFSITE_HOSTS` already calls this "the only measurement this repo has
  of what venues WORLDWIDE actually use", and it is — but an id lives in the
  URL and nowhere else, so the tally could never become a source. `find_calendar`
  keeps `offsite_url` now, `ledger --offsite` reads it, and `--refresh`
  re-probes the ones recorded before the change (one GET per KNOWN venue, no
  Overpass, no discovery — the ledger's 90-day TTL would otherwise hide them
  for three months over a one-line fix).

- **Almost none of it routes, and that is the finding.** Harvested 37 links on
  2026-09-20 and checked each platform against its own adapter rather than
  against the intuition that "we already ingest that". EVENTBRITE, the biggest
  routable-looking pile at 46, does NOT: `mapsee_ingest_eventbrite`'s header
  states that `/o/<slug>-<id>` profile ids are a different id space from the
  organization ids the API serves and that organizer-scoped fetching 404s, and
  its `organizers` list is documented as "NOT fetched". Four real `/o/` ids were
  harvested (Georges River Libraries 7982128494, Glenorchy Library Tasmania
  6685743883, Pilar 18004751158, Rochester Hills Museum 31205740733) and were
  NOT added, for that reason. Ticketmaster and Meetup are already swept
  nationally by metro, so a venue found this way is covered before it is
  configured; AXS needs partner credentials this project does not hold;
  facebook and instagram are two thirds of the tally and have no API we may
  read; humanitix was assessed in August. What is left is DICE, and only as a
  `/venue/<slug>` link — an `/event/` link names a night, not a room.

- **A plugin's footer credit was being read as "this venue's events are
  elsewhere".** Only visible once the link was recorded: Eventbrite's WordPress
  plugin puts `eventbrite.com/l/wordpress?ref=wpfooter` in the site footer, and
  a footer is on every page. Two of the 24 harvested eventbrite links were that
  or a bare `eventbrite.co.uk/` brand link — about 8%. It is not a cosmetic
  miscount: `offsite:` parks the venue in the ledger as dead for 90 days, so a
  venue whose own calendar the probe simply failed to find is retired on the
  strength of a plugin credit. `_offsite` now refuses a bare host and an `/l/`
  path.

- **A re-probe is also how you learn a site has GROWN a calendar.** One of the
  60 re-probed (Buchhandlung List) now serves its own JSON-LD and no longer
  reads as offsite at all; `--refresh` reports those rather than scraping the
  old page for a link, and leaves them for the next sweep to propose properly.

- **The one backend that can find a whole town's calendar anywhere on earth had
  one country in it, and 20 more were measured in an afternoon.**
  `catalog_discover_civic` says in its own header that it "works the same way in
  every country that has the class", and `CITY_CLASSES` held a single entry
  while the cursor read `{"country": "US", "offset": 2140}` — so every civic run
  since the file was written walked further down the same list of US cities. It
  is also the richest backend here: two 40-city batches returned 64 verified
  sources carrying 44 local-music events, 57 festivals and parades, 40
  block-party-shaped events and 58 market days, against a Socrata sweep that
  measured ZERO event-shaped datasets for "block party", "street fair" or
  "community festivals". Measured 2026-09-19/20, each with the query the file
  actually runs: 40 of 40 cities for every one of CA, GB, IE, AU, NZ, DE, NL,
  BE, CH, AT, SE, NO, DK, FI, ES, PT, MX, JP, ZA and IN, and the site each
  returns is the real town hall (toronto.ca, london.gov.uk, stadt-zuerich.ch,
  berlin.de, lisboa.pt, joburg.org.za). The rotation then orders itself: with
  the live catalog the first eight runs go to Portugal, Japan, South Africa,
  Mexico, India, Norway, the Netherlands and Spain, and the US sorts last with
  its 2,140 cities of walking intact.

- **ONE CLASS IS A US LUXURY, and the seed towns you pick decide what you get.**
  5,770 American cities are all `city in the United States`; nowhere else is
  like that. Canada's settlements-with-a-website split across `municipality`,
  `city or town of Quebec`, `town`, `parish municipality`, `village` and a class
  per province — naming one takes 652 of ~1,500 and misses Toronto. And a
  country's classes cannot be read off a few towns alone: four German seeds
  (Regensburg, Göttingen, Konstanz, Bamberg) proposed `college town` and
  `compact city` and never `municipality of Germany`, which is what ordinary
  German towns are in — and ordinary towns are where the community calendars
  are. Two generators, unioned: the Action API's `wbsearchentities` for the
  phrase ("municipality in Germany" -> Q262166), and `P31` off mid-sized seed
  towns. Mid-sized because a capital is often in a class no other town shares.
  Both halves need filtering: the phrase search matched "City of Canada Bay" —
  an Australian council — for Canada until the label had to END with the country,
  and Mississauga's own `P31` includes `weather station` and `federal electoral
  district`, and an electoral district has a population AND a website, so the
  verification query cannot tell them apart afterwards.

- **Forty rows is not forty cities, and in Switzerland it was two.** A row from
  the civic query is a (city, class, population statement) combination, so a
  country whose classes overlap and whose towns carry a population series
  returns the same town many times. Measured 2026-09-20 over a 40-row page: the
  United States 40 cities, Denmark 37, Portugal 38, Norway 37 — but the United
  Kingdom 29, New Zealand 25, India 23, Canada 20, Austria 19, Mexico 19, Japan
  16, Belgium 12, South Africa 9, Finland 4 and **Switzerland 2**. A civic run
  meant to probe forty Swiss towns probed two, and the rotation only comes back
  to Switzerland once a turn of a 21-country wheel. `cities()` now pages until
  it has `limit` DISTINCT cities (cap `CITY_PAGES_MAX`, three), which is the
  cheap half of the fix; the expensive half — asking SPARQL for one row per city
  — means GROUP BY and SAMPLE around the label service, which is where these
  queries start timing out. Live after: Denmark 109 cities in 120 rows, Belgium
  60 in 120, Sweden 40 in 40 and one page, because it stops as soon as it has
  enough.

- **A cursor over ROWS cannot be advanced by a count of CITIES.** The partial
  read — a batch cut short by the civic step's ten-minute deadline — used
  `offset + read`, and `read` is cities where the offset is rows. With paging
  the two diverge by however many duplicates the batch held, so after three
  Danish cities the cursor moved three rows and the next run re-read the same
  head. Each city now carries `_row`, the row it FIRST appeared at, and a
  partial read advances to the first UNREAD city's own row: proven on a
  synthetic batch whose third city really is 40 rows in, where the old rule said
  3. A batch from an older cursor that has no `_row` falls back to the old
  arithmetic rather than guessing.

- **Five countries time WDQS out, and they have a structure in common.** France,
  Italy, Poland, Czechia and Brazil each answered 500 or 504 to three attempts
  over two days. They are the countries whose settlements are tens of thousands
  of small municipalities — France alone has ~35,000 communes — so `ORDER BY
  DESC(?pop)` has the largest set to sort before `LIMIT` sees any of it. They
  are LEFT OUT rather than listed hopefully, which is CITY_CLASSES' own rule. A
  population floor is the obvious next thing and is UNMEASURED: one attempt at
  `FILTER(?pop >= 3000)` for France answered 500 after 103s, on a day the
  service was also refusing healthy queries — the US query that has always taken
  1.9s answered 504 twice — so it proved nothing either way. Brazil is the one
  worth the most: `mapasculturais` is its only other supply. Re-measure any
  country with `python catalog_curate.py cityclass <ISO>`.

- **The governance filter was English-only, and the civic walk now leaves the
  US.** `CIVIC_SUMMARY_RX` is what `governance_heavy` reads a feed's SUMMARY
  lines with, and it is the ONLY thing standing between a town hall's meeting
  schedule and the map — a council calendar passes the name test, verifies
  perfectly, and lands as twenty `community` events. Its English half was built
  from what live sweeps proposed (Woodbury's Holidays, Mount Vernon's IDA
  Calendar, Mansfield's 70 bare holiday names). The rest is VOCABULARY and is
  labelled as such in the file: nothing has swept a German town yet. What makes
  writing it down safe rather than a guess is `governance_heavy`'s own floor —
  two thirds of a feed's SUMMARY lines must match before anything is refused —
  so only unambiguous COMPOUNDS are listed. `Gemeinderat`, `conseil municipal`,
  `gemeenteraad`, `consiglio comunale`, `kommunfullmäktige`, `reunião de
  câmara`, `sesja rady`, `zastupitelstvo` each name one thing; a bare `Sitzung`,
  `réunion` or `möte` is the word "meeting" and would refuse a reading group.
  `test_discover_civic.py` pins both directions, including the Brazilian book
  club whose entries say `Reunião`. Replace this with a measurement the first
  time a non-US sweep produces one. The same asymmetry the English list
  documents applies: governance vocabulary is small and stable, programme
  vocabulary is unbounded and local, so the list names what we are sure we do
  NOT want.

- **Three curated files were in neither config table, so the report read them
  as empty ground — and each one was a country it was FLAGging.** `coverage`
  walks `CONFIG` and `EXTRA_CONFIG` and nothing else. Measured 2026-09-19:
  Brazil was FLAGged "no arts, community, fitness, kids, learning feeds yet"
  while `mapasculturais_sources.json` holds the two state registers that are, in
  AGENTS.md's own words, the only source that puts anything on the map there
  (one measured at 329 genuinely future occurrences); the United Kingdom was
  FLAGged for `volunteer` while GoodGym, a national volunteering network, is
  entry seven of `openactive_sources.json` — one of the nine curated volunteer
  sources on earth; and `learning` counted 244 without the six BiblioCommons
  systems behind 28,314 upcoming programmes. Three expanders later: 3,131 rows
  -> 3,155, Brazil 14 -> 16 with `community` no longer zero, UK 64 -> 76 with a
  `volunteer` row, Canada 74 -> 76. `coverage_history.jsonl` carries that as a
  step, not growth. None of the three can go in `CONFIG` — that table is what
  `verify` and `merge` PROBE, and these are hand-curated adapters, not
  candidates a sweep can propose. `country` and `city` were added to
  `bibliocommons_sources.json` for this: two of the six systems are Canadian
  (Edmonton, Vancouver) and the adapter reads neither key, so inferring the
  country from the library's NAME would be a guess about exactly the thing the
  report exists to state.

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

- **Verification proves a feed PARSES; only an ingest proves it DELIVERS.** Two
  of four Bend sources passed `verify` and were merged, and a full run through
  their own adapters kept almost nothing. Measured 2026-09-20.
  VOLCANIC THEATRE PUB: `verify` said 25/25 sampled events future; the ingest
  kept ONE. The listing's 25 schema.org Event blocks carry no `url`, so every
  one takes the listing page as its `source_id` and `EventStore`'s
  `(source, source_id)` guard keeps a single row. The real links are tixr.com,
  which answers 403 to the production UA, so following them is a refusal too.
  Not an adapter bug: 239 of the 240 jsonld sites carry a `link_pattern` that
  matches real event pages, and the one that does not — Nectar Lounge — ingests
  all 10 because its Event blocks each carry a `url`.
  FIRST UNITED METHODIST: `verify` said 27 vevents / 27 future, and it is 27
  copies of ONE title, "In-Person Worship Service". A weekly service schedule,
  the same shape as the City Park feed whose 365 vevents were 365 copies of
  "Public Historical Tour". Both are now in `_not_included` with their numbers.
  The general lesson is the cheap one: after `merge`, run the adapter over just
  the new entries and count DISTINCT titles and placed rows, not the verifier's
  "future events".

- **An ics feed with an empty LOCATION is placeable after all, and the option
  already exists.** The church's 27 VEVENTs each carry `LOCATION:` with nothing
  after it, and the adapter kept 0 with "27 unplaceable — more than half this
  feed has no location". `ics_sources.json` takes a `venue` block "used ONLY for
  a VEVENT that carries neither LOCATION nor GEO", added for a neighbourhood
  yard sale that is a few hundred porches; with the church's surveyed OSM point
  it kept 27 of 27, pinned correctly. It came out anyway, for being standing
  rows — but a single-venue calendar with no LOCATION is a `venue` block away
  from working, not a dead end.

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

- **A neighbourhood-wide yard sale has no LOCATION, and it is the one event
  the calendar exists for.** Measured 2026-09-19 while curating for fleabop:
  Montlake Community Club's public Google Calendar (montlake.net/calendar is an
  embed; the `calendar/ical/<id>/public/basic.ics` export is the feed, as it is
  for the 23 Google calendars already in `ics_sources.json`) carried 65 VEVENTs,
  2 upcoming, and the "Montlake Yard Sale" had no LOCATION - a sale that is a
  few hundred porches and a map has no address to put there. Overlook
  Neighborhood Association's calendar was the same: 100 VEVENTs, and its 2023,
  2024 and 2025 yard sales all had no LOCATION while everything else carried a
  street. The ICS adapter dropped every one of them as unplaceable, correctly,
  so the source would have ingested exactly the events nobody curated it for.
  `venue` on an ICS source (same block as Squarespace's) now pins a VEVENT that
  carries NEITHER LOCATION nor GEO to the neighbourhood centroid; a LOCATION
  that Photon cannot place is still dropped, because that is a place we failed
  to find, not an event without one. A first run kept the 2026 yard sale ("1
  pinned to the source's venue") and nothing else changed. Of the neighbourhood
  garage-sale days found the same way, only these two publish a feed at all:
  PhinneyWood, Maple Leaf, Wedgwood and West Seattle Garage Sale Day are a
  static page each (two of them Google Sites), Rose City Park and Wedgwood's
  WordPress have no calendar plugin, Mt Tabor and Multnomah Village are Wix,
  and West Seattle Blog's All-in-One export is Disallowed by name in its
  robots.txt (see `tribe_sources.json._not_included`). The clothing-swap
  organisers were thinner still: SwapDC and Near South verify and have 0
  upcoming, Swapanistas is one day a year on Wix, and A2ZERO (Ann Arbor's
  monthly city-hall swap) is the one on a platform with an adapter - Luma, 4
  upcoming, all with coordinates.

- **Neighbourhood associations live on Squarespace, and a search finds the
  sale before it finds the calendar.** Second pass 2026-09-20, twenty US
  metros searched for "neighborhood association" + garage/yard sale: 30-odd
  association sites, of which six carry a feed. Five are Squarespace
  collections (Bryn Mawr, Linden Hills and Lyndale in Minneapolis, Heart of
  Lincoln Square in Chicago, Hyde Park in Kansas City, Multnomah in Portland -
  2, 5, 6, 2, 9 and 37 upcoming) and NONE of the 240-odd articles across them
  carried a map link, so every one needed a `venue` block at the
  neighbourhood centroid; Lyndale's page also embeds a Google Calendar, which
  is the richer copy (285 VEVENTs, 10 upcoming, 9 with a street) and became the
  source instead. Three WordPress sites answered the Tribe REST route with 0
  events (Lind-Bohanon, Longfellow, Old North End): the plugin is installed and
  the sale is a page, not an event. Everything else was a static page (Site
  Kit, Elementor, Weebly, Google Sites, Wild Apricot). The yield is roughly one
  configurable source per three association sites found, and the search engine
  returns listing aggregators (gsalr, garagesalefinder, yardsalesearch) ahead
  of the associations after the first page, so the second query for a metro
  is worth less than the first.

- **A CivicPlus city almost never has a garage-sale calendar, so do not scan
  for one.** The three garage-sale feeds in `ics_sources.json` (Little Elm,
  Midlothian, Sebastian) all came from `civic` discovery, and the obvious
  follow-up - discovery drops a category with nothing upcoming at probe time
  and caps a city at 6, so the seasonal sale categories it walked past should
  be recoverable - was measured 2026-09-20 over every CivicPlus origin already
  configured: 472 origins, 423 answered `/iCalendar.aspx` (49 were 403s or
  timed out), and among them exactly FOUR categories named a garage, yard,
  rummage, flea or swap sale. Three were the three already configured; the
  fourth (Glenpool, OK) had nothing upcoming. Zero candidates. The civic walk
  is already taking every one of these that exists, and the reason the count
  is tiny is that a city with a garage-sale permit programme publishes it as a
  permit list, not a calendar. Sale-shaped supply for fleabop comes from
  neighbourhood associations and swap organisers, not from city calendars -
  see the two notes above. The Luma search for the same day: eight
  clothing-swap event pages resolved to eight calendars, two US ones with
  anything upcoming (A2ZERO, Home Ec NYC), one in Amsterdam with 33.
