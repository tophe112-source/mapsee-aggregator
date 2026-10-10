# Community-centre schedules

> Part of mapsee-aggregator's agent notes — see `AGENTS.md` for the map. Read this file when the bug is about: what is on at a community centre, a drop-in timetable (lane swim, pickleball, open gym, seniors' socials, family drop-ins), a recreation-booking platform, Toronto / PerfectMind / Linked Events rows, or where a centre's schedule lives
> Every note below was measured before it was written; keep the numbers when you edit.

- **A DROP-IN WITH A FEE IS A SCHEDULE; A COURSE IS A CATALOGUE.** The owner's
  request of 2026-10-03 was "what is going on at the local community place", and
  most of it costs a few dollars: of Toronto's 28,155 drop-in sessions (facility
  slots aside) 50.0% are free and 41.4% charge $4.39-$10.64 at the door; of
  51,960 PerfectMind drop-ins over 15 cities, 115 (0.2%) cost over $20. The 2026-09-27 review
  that refused "paid registration catalogues" was refusing COURSES - numbered
  sessions you sign up for weeks ahead (Cranberry 131 of 134, Amilia 77%,
  Calgary's registered programmes 98% paid of 15,432). So the line is
  registration, not price: a session anyone can turn up to is in, with its fee
  stated; a registration-only course, a private booking or rental, childminding,
  a card-holder-only session and a closure are out. "Free" is written only when
  the source says so, in words 0227's strict `free` reads ("Admission: free",
  "Free drop-in"); a bare "Free." does not match it.

- **THE OSM WALK FINDS A CENTRE ONLY WHEN OSM KNOWS ITS WEBSITE, AND IN NORTH
  AMERICA THE TIMETABLE IS NOT ON THE WEBSITE.** Audit of 2026-10-03: 676 of
  3,748 configured sources were about community centres, but the US had 330 of
  them and only 2 were OSM centres - the rest are town category calendars. 143
  of the 261 swept metros had no community-centre source at all (12 of 14 UK, 11
  of 13 French, 39 of 82 US). A sample of 117 centres the ledger calls
  no-calendar found ActiveNet linked on 14 (Canada 9 of 25), Xplor 2, RecDesk 1,
  WebTrac 1, and 99 of 117 with a plain HTML programme page. Those links say
  "Register" or "Drop-in schedule", which CAL_LINK_RX never read, so
  `REC_BOOKING_HOSTS` in `catalog_discover_osm.py` makes the host the signal
  and files them `offsite:<host>`. The platforms themselves are measured in
  `platforms-probed.md`: PerfectMind is the one that is permitted and drop-in.

- **CITY OPEN DATA ALMOST NEVER PUBLISHES A CENTRE'S TIMETABLE, AND THE FEW THAT
  DO ARE THE BIGGEST SINGLE SOURCES.** Swept 2026-10-03: 65 terms over the US
  Socrata catalogue (3,067 datasets), every dataset on 50+ US Socrata domains
  (17,838), 13 CKAN portals, 38 ArcGIS hubs, and the Canadian, European, APAC
  and Latin American portals by hand. Dated community-centre schedules exist in
  Toronto (31,734 drop-in sessions in 90 days at 176 locations), Helsinki/Espoo
  (Linked Events), Montreal (13,873 occurrences, 70.3% needing registration),
  Philadelphia (CARTO, 7,077 weekly occurrences at 55 sites), Madrid (1,388
  events, 594 at 93 municipal community places; parked 2026-10-05: robots.txt), Barcelona (4,141, 1,305 within
  40 m of a centre civic, every clock time a 03:00/12:00 placeholder) and Hong
  Kong (LCSD: 336 walk-in programmes, 895 cultural events). Everything else is a
  facility list, an archive (Ottawa and Mississauga end in 2022) or a course
  catalogue (Calgary 2.0% free of 15,432; Winnipeg 72% lessons). NYC's
  "program" datasets are past-only attendance logs (18,821 aquatics rows, 0
  after 2026-10-03).

- **CHICAGO PARK DISTRICT WAS 16 DAYS OF A 90-DAY WINDOW AND HALF COURSES.** The
  configured `tn7v-6rnw` read 500 rows ordered by start, which reached
  2026-10-19 of a window published to 2026-12-31, and took every activity type:
  795 Instruction and 155 Camp rows in 90 days. Since 2026-10-04 it keeps
  `activity_type` Open (drop-in, 1,068) and the untyped Events (408): 1,464 rows,
  read whole at limit 3,000. Its Programme rows have no description worth the
  name ("October 3, 2026 to October 3, 2026"), so the opendata adapter's new
  `map.fee` writes the price: 288 of 1,186 stored rows say "Admission: free.".
  277 same-day repeats (two freestyle-ice hours at McFetridge) still merge on
  title|date|venue and keep one time - the opendata adapter does not collapse.

- **A SEED LIST OF CENTRES THE MAP CANNOT SEE YIELDS ABOUT ONE FEED IN TEN, AND
  JCCs ARE THE BEST OF IT.** 2026-10-03, find_calendar under the production UA:
  US 153 probed (JCC Association directory, settlement houses, 27 thin metros) ->
  52 with a calendar platform -> 19 with rows -> 12 kept, 6 of them JCCs (12 of
  39 JCCs run The Events Calendar); senior centres 0 of 16, settlement houses 2 of
  47, LGBTQ centres 2 of 23. Canada 155 of 455 directory hosts (Calgary
  associations, Edmonton leagues, Winnipeg centres) -> 53 calendars, but 15
  Google embeds (robots.txt refuses the export; the Calendar API needs
  GOOGLE_CALENDAR_API_KEY), 14 Wix sites with 0 event blocks and 28 empty -> 5
  kept. Elsewhere 157 probed -> 30 platforms -> 6 kept; none of 37 Melbourne
  neighbourhood houses publishes more than a term PDF. 302 Canadian hosts and
  153 JCCs were left unprobed (`seeds_unprobed.json`, `jcc_unprobed.json` in the
  research scratch).

- **A BOM IN FRONT OF `Disallow: /` READ AS ALLOW-ALL, AND TODAY IT CHANGES NO
  CONFIGURED VERDICT.** `robots_txt.parse` kept U+FEFF glued to the first field
  name (`str.strip()` does not remove it), so FrontDesk Suite's BOM + `User-agent:
  *` + `Disallow: /` parsed as no groups. Fixed 2026-10-04. Fetched every
  configured origin's robots.txt once to size it: 2 of 2,759 serve a BOM
  (cal.laget.se, ville-hoenheim.fr) and neither file refuses any of their 3
  configured requests, so nothing configured moves; the fix is for the next one.
  The same day showed the other way robots.txt was being misread: Cloudflare's
  BLOCK page ("Sorry, you have been blocked", HTTP 403) is not a challenge, so
  a 4xx on /robots.txt carrying it read as RFC 9309's "unavailable", allow-all.
  Every DC-area WebTrac tenant checked serves exactly that. CHALLENGE_RX names it
  since e9775d4; of 2,816 configured origins, 1 moved (Biblioteca Central PUCRS,
  Porto Alegre, a tribe source, retired into `_not_included` with the rule).

- **THE PINNED COMMUNITY-CENTRE WALK IS HALF DONE.** 13 runs (2026-09-22..10-03)
  moved its cursor 0 -> 121 of 261 and read 11,797 centre and library venues:
  5,086 known-dead, 3,610 no-calendar, 1,235 unreachable, 1,496 HTTP errors, 323
  bot challenges, 25 offsite; 396 candidates, 203 merged (median 12 future
  rows). Throughput fell from 31 metros a run to 3-7. 140 metros remain, 82 of
  them in the US.

- **AROUND WASHINGTON, DC THE TIMETABLES SIT BEHIND ONE VENDOR'S ROBOTS.TXT;
  AROUND SEATTLE BEHIND ACTIVENET'S TERMS.** Swept 2026-10-04 at the pipeline's
  R=100 km. DC: 31 government bodies. Vermont Systems' WebTrac holds 11 of them
  (DC DPR, Arlington, Alexandria, Reston's classes, Vienna, Montgomery County
  Recreation, Loudoun, Falls Church, Prince George's Parks, Greenbelt, Prince
  William), and every tenant's robots.txt answers 403; ActiveNet holds McLean's
  courses, Howard County and Takoma Park; Gaithersburg, Leesburg, Herndon and the
  Columbia Association answer 403 outright; calendar.dc.gov and the Department of
  Aging's calendars are empty; no PerfectMind tenant. What the cities DO publish
  as feeds is their events, not their timetables: 8 sources, about 507 rows in 90
  days (Reston Community Center 262, Montgomery County's Trumba calendars 118,
  Alexandria 77 after its meetings are removed by title). Community-run: 99 hosts
  probed (49 from OSM, 50 seeds) -> 8 feeds, about 202 rows. The configured
  Prince George's Parks Events Calendar carries 509 rows in 90 days but 199 of
  200 sampled have no venue, so with no street they are never geocoded and never
  reach the map: lost supply, not misplaced pins. Seattle: 41 government bodies.
  ActiveNet holds 7 (Seattle, Shoreline, Auburn, Edmonds, Mill Creek, Marysville,
  Snohomish County), Rec1 or Amilia course catalogues 4 (Kirkland, Bellevue,
  Puyallup, Redmond), and 6 sites answer 403 (Shoreline's, Kenmore's, Kent's and
  SeaTac's city sites, Parks Tacoma, Bainbridge parks). PerfectMind holds the
  drop-ins of Tukwila (386 in 90 days), Renton (459, after 521 private bookings
  refused) and Maple Valley (6); Lynnwood, Burien, Mercer Island and Bothell are
  tenants with no drop-in calendar. Seattle's two configured Trumba feeds hold
  one-off events: 12 of the city-wide feed's 500 rows are at a centre. The
  community-run centres are where Seattle's supply is: 103 hosts probed -> 14
  feeds, about 1,150 rows (the Center for Active Living in West Seattle 709,
  CISC 200). Five more centres publish Google calendars, which need
  GOOGLE_CALENDAR_API_KEY; 9 answered with a bot challenge. Consent letters:
  ../mapsee/outreach/partners/webtrac-permission.md and activenet-permission.md.
  Overpass reset every TLS handshake from the cloud sandbox on all 5 mirrors;
  Geofabrik's Postpass (OSM over SQL, robots.txt allows /) ran the same selector.

- **A CENTRE'S DAY IS ONE ROW PER STRETCH, NEVER ONE ROW FROM THE FIRST START TO THE LAST END.** Toronto's Drop-in table (CKAN, read 2026-10-03/04) runs Lane Swim up to six times a day at one pool. The first `mapsee_ingest_toronto_rec.py` folded a title's day into one row and listed the times in the text. 2,506 of its 23,232 rows then spanned hours when nothing runs (11,346 gap-hours; Antibes "Lane Swim" 11:30-21:00 for three short swims), plus 251 of 1,603 EarlyON rows, and ../mapsee pulses a pin "Happening now" from starts_at to the end, through every gap. The adapter now folds exact repeats (359), joins back-to-back or overlapping sessions, and ends a row at every gap: 2,401 title-days and 251 centre-days split live on 2026-10-04, 27,654 rows in 7 requests and 17 s, 0 spanning a gap. Each row names the day's other stretches. Identity is the stretch's first start time, as bibliocommons and perfectmind key theirs: a shuffled re-read is identical, and a second upsert adds 0 and rekeys 0.
- **AN ALL-DAY TABLE IS A ROOM UNDER ANY TITLE, AND A STAFF EMAIL HIDES IN A WEBSITE FIELD.** Beyond the three facility titles (3,630 sessions), 713 kept Toronto sessions ran 6 h or more: Table Tennis 9:00-20:00, Squash 40 of 40, Billiards 28 of 28, Snooker 112 of 146, FitnessTO "Walking" 11:00-19:00. `facility_use_shapes` refuses those titles from 6 h, judged on the joined stretch (563 more sessions). Youth Zone and Club: Social at 6 h+ are staffed and kept, so the cut is not bare duration. Matching Free Centres on street number + name also caught 9 parks at a centre's address (47 locations for 38 centres); they now match on Location ID. One of 231 EarlyON centres had a staff email in `website`, and adding "https://" put it on 11 rows' "Tickets / info" line. `_website` now refuses '@', userinfo and values without a dotted host, and those rows fall back to the City's finder page.

- **A CITY'S EVENT API MARKS A SIGN-UP IN FIELDS, BUT A THIRD OF WHAT IS NOT A DROP-IN IS ONLY IN THE WORDS.** `mapsee_ingest_linkedevents.py` read Helsinki's and Espoo's Linked Events on 2026-10-03: 12,575 + 6,149 leaf rows, 10,529 kept on fields alone. The fields catch 216 Helsinki enrolment windows, 3,691 Espoo hobby-catalogue rows and 277 sign-up links. A review replay of the same pull on 2026-10-04 found what fields cannot say:
  - 242 private appointments. One-to-one digital help "ajanvarauksella" and a sewing machine "varattavissa" say it in the title; reading dogs' 15-minute slots say "Varaa oma aikasi" in the text.
  - 41 rows titled as courses.
  - 19 rows with kulke's stock "Maksuton, vaatii ilmoittautumisen" (free, requires registration).
  - 10 card-only rows in Swedish ("servicecentralskort").
  - 19 weekly sessions written as ONE row from the first date to the last ("Vauva-aamu", Mondays 10-11, stored 09-21 to 12-14).
  - 10 rows on a place that is the town itself.
  - 20 Zoom/Teams language cafés pinned at a library.

  The traps run the other way too. 158 of 200 registration phrases sit inside a negation that contains them ("ingen förhandsanmälan krävs", "no prior registration required"). "Ei ajanvarausta" (no appointment) is common on drop-in digital help. And 89 of 3,852 Espoo catalogue rows say "ei tarvitse ilmoittautua" and are drop-ins. So each phrase is checked against the words right before it. A live 7-day Helsinki run kept 1,033 of 1,768 rows with none of these on the map.
- **AN IMAGE LICENCE CAN FORBID WHAT THE PRODUCT DOES WITH A POSTER.** Linked Events marks 6,426 of the 10,648 replayed rows' images `event_only`: "can only be used for information and communications connected to the event ... The source and photographer must absolutely be mentioned". 1,333 of them name no photographer at all, and 112 more say "-". ../mapsee/src/index.js (~4968) takes the first poster among a venue's events as the VENUE page's og:image, and list thumbnails carry no credit. So `event_only_images` is false on both instances, and only the 4,127 cc_by images are used, each credited in the attribution paragraph.

- **A PERFECTMIND "NO FEE" IS THE WIDGET'S CHARGE, NOT THE SESSION'S PRICE.** I replayed the 2026-10-03 capture (78,339 rows) and scored the stored rows with 0227's twin. The adapter had written "Free drop-in" on 8,392 rows and 0227 tagged 8,692 free. 3,190 of those sat behind a paid membership named in the Details: Surrey's "Seniors Services Membership required." (1,792) and Markham's "Fitness membership required" (1,313). Another 181 had a stated fee but prose that read as free: Coquitlam's $9.00 skate, "Coaches are free of charge" (95), and Moose Jaw's $0.00-$18.25 gym, "free of charge with membership" (86). Since 2026-10-04 the adapter does three things. It refuses a row whose Details require a membership or pass the source does not call free: 5,253 rows, including Brampton's Flower City Seniors Centre 2,095, Markham 1,345 and NVRC's Parkgate Society 21. It writes no price where the widget does not sell the row (Markham's 749 drop-in aquafit, $7.58 at the desk) or hides prices (NVRC, Westside). And it ends every stated fee with "(not free)", which 0227's FREE_NEG reads as a veto on the whole row. After: 65,163 rows kept and 4,498 tagged free, with 0 behind a paid membership and 0 with a stated fee. Live on 2026-10-04 (Markham, Coquitlam and Moose Jaw, 148 requests), the same answers went from 491 tagged free to 104.

- **THE `rec` GROUP'S FIRST SYNC IS 103,600 ROWS, AND PREPARING THEM, NOT
  POSTING THEM, IS WHAT TAKES THE TIME.** Verified on main 2026-10-04 by two
  `feed_group=rec` dispatches. Run 37216641420: Toronto 27,654 rows in 18 s,
  Linked Events 10,204 kept in 7 min, PerfectMind 65,760 sessions in 2,410
  requests and 17.1 min, 0 rows geocoded (all carry the publisher's point) - and
  the sync ran out its then 20-minute cap with 78,770 rows stored. Run
  37219810868 (cap 60 for `rec` only, 4e4fa55): "78,770 of 103,604 id(s) already
  in Supabase, read in 135.4 s", 24,834 left, 5 dropped by the moderation
  pre-filter, preparing the rows 4.5 min, "Upsert phase: 24,829 row(s) in
  218.5 s"; the whole sync 8 min and the job green. So a first sync or a
  Wednesday refresh of the full group is about 4 + 19 + 15 minutes, which the
  60-minute cap holds and 20 never could; a --only-new day is the new sessions
  only.

- **A COUNTY CALENDAR NAMES ITS CLASSES IN THE BLURB, NOT THE TITLE.** On Fairfax County's events calendar (Drupal fullcalendar_view, 195 events in 90 days on 2026-10-04), a title-only class rule refused 6 rows ("Cider Making (Class)"). It kept 9 more whose own blurb or title says class or workshop, all behind a ParkTakes form: "Little Hands on the Farm" ("Classes held during the same week will cover repeat topics"), "Foraging for Wild Edibles" ("This class will focus on"), "Nature as a Canvas" ("In this hands-on workshop"), "Paper Quilling" ("Students learn how to"), and a "Teacup Topiary Workshop". `mapsee_ingest_drupal_fullcalendar.py` now reads the blurb with phrases that need a determiner (CLASS_BODY_RX), so "a resource fair, workshops, and lunch" is still kept. With that rule, 15 refused and 123 written. The same run showed that a page with no visible Date & Time carries a placeholder clock: Animal Services' "Reading Tails" (2 rows) had only a hidden startDate at 18:00 and said "check the exact date and time in the registration link", and it was also "open for group registrations only". Both rows are refused (`require_visible_date` in the config).

- **A PRICE LINE AT THE END OF A DESCRIPTION IS CUT BY THE SYNC, "(not free)" WITH IT.** The sync's `_cap_prose` (800 characters) cuts the end of the text and keeps only a final paragraph of 200 characters or fewer. Fairfax's adapter put "🎟 Price: $X (not free)." second-to-last, so after to_row 9 of 133 rows lost the line (a 1,022-character Halloween party among them) and 11 lost "Registration required.". 0227's twin then tagged 7 free, not the 9 the store said. With the price and registration lines first and the blurb cut to fit, 0 of 122 lose them, and 0227's twin on the to_row output tags 7 free and 0 rows with a fee. Perfectmind already puts its price first for this reason.

- **THE CENSUS BATCH CANNOT PLACE A THIRD OF A COUNTY'S BARE STREETS.** Of the 15 Fairfax rows whose page gives a street but no point, 5 (3 of 13 streets) got no match from `batch_geocode`: 4621 Legato Road (Fire Station 40), 3001 Vaden Drive (Jim Scott Community Center) and 6500 Franconia-Springfield Pkwy (Springfield Town Center). The one-line geocoder put the last one on 6500 Franconia Rd, the wrong road, and the sync drops unmatched rows. The config's `street_pins` holds OSM's pins for those 3 streets, read with one Postpass query on 2026-10-05, and the 10 streets left all matched the same day: 122 of 122 rows reach the map.

- **Glen Echo Park's `<time>` field is wrong where its time line is not: 3 of 184 rows, and the time line wins.** glenechopark.org's events calendar (Drupal 8, no iCal, no JSON-LD) gives each event a written time line on its card ("2:45pm - 6:00pm", "2:45pm: Lesson | 3:30pm - 6:00pm: Social Dance") and a structured `<time>`. On 2026-10-04 the two disagreed on 3 of 184 rows. The Junior Ranger swearing-in (7685, 7686) says "11:00am" on its card, on its page and in "the first Saturday of each month at 11:00am", but its `<time>` says 10:00, copied from the tour it follows. The Viennese Ball's (7638) `<time>` 8:00pm is none of its line's times (7:30 lesson, 8:40 dance). The first build took the `<time>`, which put the swearing-in on the map an hour early, and its 68 checks did not notice. `mapsee_ingest_glenecho.py` now uses the `<time>` only when a card has no time line, and prints and counts every disagreement; `test_ingest_glenecho.py` fails if the precedence flips. Two Google-Docs `<p dir="ltr">` time lines (7638, 9658) were also missed by a bare-`<p>` pattern. A lesson-then-dance schedule is read as one span only when its parts meet end to start.

- **Glen Echo Park: 184 dances, theatre and tours in 90 days from 190 requests; 240 of 430 cards are opening hours, not events.** On 2026-10-04 the month pages listed 430 cards from Oct 4 to Jan 2. 240 (55.8%) were the daily hours of 9 resident studios and galleries. These, 5 "CANCELLED TODAY-" dances and 1 "Virtual" talk are refused from the listing without a detail request. The 184 kept are all placed from a venue book of OSM buildings: 176 exact, 8 on the park pin. 28 say 'Admission: free.' and ../mapsee's 0227 twin tags exactly those 28; every fee row ends '(not free)'. Primary after derive_categories: community 129, kids 41, fitness 7, arts 6, music 1. A network error costs one page; five failures in a row end the run.

- **A TOWN'S COMMUNITY-CENTRE TIMETABLE CAN BE ALL RRULE, AND ITS HOLIDAYS ARE FILED ON ANOTHER CALENDAR.** The City of Manassas, VA's Revize calendar has 915 rows (2026-10-05). Only 49 are dated within 90 days, and every Community Center session there is an RRULE (`mapsee_ingest_revize.py` expands them; on 323 live rules and 5,209 occurrences it gives the same answer as python-dateutil). The city's closures are rows on its Community Events calendar ("Christmas Holidays ... all City offices closed", plus New Year's and Thanksgiving). They are refused as events. Applied to the calendar the city runs (`host_calendars`), they also skip 6 sessions in the window that the rule's own EXDATEs miss: Toddler Drop-In on 12/24, 12/25 and 1/1, and Open Gym on 11/27, 12/25 and 1/1. Toddler Drop-In's EXDATEs already skip every other city holiday in its rule. An upsert cannot delete a session written 90 days ahead, so this has to happen at import. A club the city merely lists (Run Club, manassasrunclub@gmail.com) is neither closed by those days nor hosted by the city.

- **MONTREAL'S `est_inscription_obligatoire` IS NECESSARY, NOT SUFFICIENT.** The Loisirs Montréal dataset was read 2026-10-05: 10,759 session rows, 1,791 of them in the 90 days from 2026-10-04.
  - **Vrai on 1,315 (73.4%).** This includes 146 free-play titles ("Patinage libre") that need a reserved place.
  - **Faux does not mean drop-in.** Of the 468 Faux rows, only 133 are kept, each on the source's own words: 39 say "aucune réservation nécessaire" or similar, 73 have a "libre" title, 21 use the description's own "venez" / "ouvert à tous".
  - **The rest are club seasons and courses:** 166 course or league words, 128 empty club listings, 29 "l'inscription à ce cours", 7 court rentals, 4 loan counters and 1 lifeguard course.
  - **Description dates bind the weekday on their own line.** 897448 says "Dimanches : du 17 mai au 11 octobre … Jeudis : 28 mai au 3 septembre". When those ranges were pooled, a Thursday and a Friday were written on dates the text had ended in September.
  - **The price is in the first paragraph, and the row fits the sync's 800 characters.** With the price placed after the source text, `_cap_prose` cut it from 12 rows of 837-873 characters. A synthetic fee row lost its "(pas gratuit)" and 0227 read it as free.
  - **Result:** 920 rows at 18 sites. 63 are free in their own words; 0227 agrees with the adapter on all 920 stored rows.

- **A FINDER PROGRAMME THAT SAYS "OPEN" CAN STILL BE A SIGN-UP; ITS OWN LINK AND ITS OWN "REGISTRATION REQUIRED" ARE THE VETOES.** In Philadelphia's Parks & Recreation Finder (phl.carto.com, 2,246 active, public, approved programmes on 2026-10-05), keeping a programme on drop-in or open words alone kept 80. 27 of those were sign-ups.
  - `registration_form_link`, which the Finder prints as "To sign up visit:", held a URL, an e-mail or a bare host on 172 programmes. One was Pickup Volleyball Games, live, $3.00 a week, signing up on opensports.net.
  - Own-text cases: "Registration required by emailing ... for a time slot" sat beside "All are welcome" (Maple Sugaring Open House); "Please complete a one time 2-page registration form" (adult volleyball).
  - "walk in" was the verb ("a morning walk in search of birds").
  - "open to all ages / skill levels" is about who may sign up, not who may turn up.
  - "free play" is camp copy (Summer Camp, $500.00 a season).

  With the link and the text as vetoes read BEFORE the words, camps and care refused by type or name, and "Price: free" (not "drop-in") for a programme let in only by "all are welcome", the rules keep 53. Of those, 6 programmes / 144 rows fall in the 90-day window. `test_ingest_phl_parks.py` catches all 16 mutations.

- **A SECOND SEED ROUND OVER 425 JCCs AND CANADIAN COMMUNITY LEAGUES KEPT 16, AND THE BIG JCC CALENDARS ARE REGISTRATION CATALOGUES.** 2026-10-04/05: 425 seeds, 141 calendars found (59 refused by robots.txt at the home page, 13 refused outright), 123 measured, 56 with rows in the next 90 days, 46 past the automatic gate, 16 kept after reading the rows and a dry run with the real adapters (1,097 rows in 90 days, 82% with coordinates). The four largest JCC feeds were sign-up grids: Commonpoint Queens 3,174 rows, Samson 1,369, JCA Jacksonville 1,088 and Mayerson 1,081, with 85-100% of the rows read in the Class category (swim levels, reformer Pilates, archery). A JCC with more than 1,000 rows in 90 days is a catalogue until its categories say otherwise; filtering on The Events Calendar's categories (include_categories) is what saved PJCC (1,310 group-exercise rows left out) and MARJCC. Washington DC and Seattle gained nothing: DCJCC and Stroum are already configured; Bender JCC's 'Propel' theme prints 20 'tribe-events' class strings with no Events Calendar REST route or iCal export behind them (both 404); Pozez JCC's robots.txt answers with a bot challenge. Merged 2026-10-05 after the two adapter fixes in adapters-and-sources.md, with JCC of Windsor's 4 'WJCC Closed' notices refused by tribe's skip_title (`^\W*WJCC\s+closed\b`): tribe +11, squarespace +3, jsonld +2.

- **AN EVENTS CALENDAR SITE'S 'FREE' CAN BE A TICKETED TALK.** JCCSF's REST cost field says 'Free' on 22 of 28 rows; 3 of 3 event pages read 2026-10-05 say 'GET TICKETS $20 - $42', 'Register $36 - $150' and 'GET TICKETS $45 - $250' (sold on Eventbrite). mapsee_ingest_tribe writes 'Free to attend.' from that field, so every row would read free. Before keeping a centre that sells through Eventbrite, open two pages whose cost says Free. PJCC's 'Cost: Free' agreed with its pages.

- **Madrid's municipal agenda is parked: datos.madrid.es's robots.txt names the datastore.** Checked 2026-10-05 (the file's Last-Modified is 2026-09-29). Under `User-agent: *` it says "# bloquear endpoints concretos del datastore" / `Disallow: /api/3/action/datastore_search`, beside `/api/`, `/*?` and `/dataset/*/resource/*/download/*`. The JSON files the 2026-10-03 survey read (`/egob/catalogo/300107-0-...json`) now answer 302 into that download path. The portal's own API spec (`/swagger/openapi.yaml`, 25 endpoints) lists no `datastore_*` call, so the ckan exemption in `DOCUMENTED_API_TYPES` does not reach it. Montreal is no precedent: its CKAN has only the stock `/api/` line, and its CSV download path is allowed. `mapsee_ingest_madrid.py` asks robots.txt about its exact first request. It stops (1 request, 0 rows, exit 0, live 2026-10-05) unless the config's `permission` records the Ayuntamiento's written yes; the config ships as `madrid_sources.json.pending-permission`. Replaying the one 2026-10-05 pull offline, the rules would write 975 rows at 147 venues, 616 of them at community places. 735 are free, by the City's own flag and by 0227's twin alike.

- **On Madrid's agenda an all-day twin is not always a copy, and "every day" with no clock is a placeholder.** An all-day item and a timed one with the same title, venue and date are one show only when their texts agree. 'Fiestas del Pilar 2026 en Salamanca' on 10 Oct is a 21:00 concert (50438944) and an all-day programme from 10:30 (50438921). Folded, the 21:00 row carried the morning's programme. Germinal, Paula Comitre and Juan Berlanga do fold: their texts are empty or contained in the other, 5 of the 6 all-day twins on the pull. A weekly L..D recurrence with no clock and no named dates gives no days: 6 such items wrote 44 all-day rows, 38 with no text. 'Andrea Jiménez. Contra Antígona' was ten rows, Monday 30 Nov included, at a theatre dark on Mondays. Such runs are refused from four calendar days. A placeholder day that another id times at the same venue is dropped (1: 'Compañía Juan Berlanga', Friday 4 Dec). `python test_ingest_madrid.py`.

- **ONE ADDRESS CAN BE TWO BUILDINGS: NAME A ROW BY THE FACILITY AT ITS OWN POINT.** Barcelona's facility register files a library and a children's casal under Carrer de Vallcivera 3, 86 m apart. Ranked by kind, the casal named all 14 of the library's rows, a seniors' talk among them, and put them on the kids door. `choose_venue` in mapsee_ingest_barcelona.py takes the facility nearest the row's point. Kind rank only breaks ties within 20 m. On 2026-10-05, across 39 addresses holding several facilities, co-located points differed by 0–4.8 m and Pati Llimona's single building by 18.7 m; the next separate door is 30.6 m away. 15 rows were renamed.

- **A WEEKDAY IN FRONT OF A DATE IS ITS LABEL, AND THREE DATES ARE THREE SESSIONS.** "Dissabte 24 d'octubre" read as a weekday plus a date wrote a musical on every Saturday of its range: 4 performances the source never lists. "Mostra de cinema emergent", with Dies "28 octubre, 25 novembre i 16 desembre", was one date-only run 'happening now' for 7 weeks because its title reads as an exhibition. Strip a weekday label (refuse one that is not its date's weekday). Treat a row as a run only when it is an exhibition and its Dies is not a list of dates.

- **OPENING HOURS ARE NOT AN EVENT, WHATEVER THE TITLE CALLS THEM.** At Barcelona's community places, 7 registers whose title names a space ("Trobada 'Punt TIC'", "Espai 12/16", "Sala de joc") and that run on 4+ weekdays were 368 of 1,598 rows (23%). Meanwhile "Espai 'Sala d'Informàtica'" was refused. 9 month-long collection points and swaps (6 h+ on 4+ weekdays) were date-only runs. A space title on 4+ weekdays, or 6 h+ on 4+ weekdays, is now refused unless it is an exhibition: 16 registers, 2026-10-05.

- **A DATES FILE THAT LISTS EVERY DAY FROM THE FIRST TO THE LAST IS NOT A SCHEDULE: 71 of 948 LCSD culture rows were performances that do not exist.** Hong Kong LCSD's eventDates.xml gave "Music at Heart" (8 Wednesday lectures, 07/10-25/11/2026) all 50 days in between. Lending the text's one "19:30" to each wrote 42 lectures that do not exist. Border Town's "24, 29-31/10, 5-7/11/2026 (Thu-Sat) 20:00 / 25/10, 1, 8/11/ 2026 (Sun) 15:00" (a list across two months, and a space before the year) gave every night both clocks: 9 phantom shows. An installation's "10.9-14.10.2026* ... Opening hours of the following days: 22.9.2026 (Tue) 19:00-23:00" lent its exception day's hours to every day: 20 timed rows instead of one run. `mapsee_ingest_lcsd.py` now writes only the dates the event's own text names (42 dropped, 2026-10-05). It reads lists across months, "/ 2026" and dotted dates. Two date phrases share a clock only when nothing but a separator stands between them. `test_ingest_lcsd.py` holds the three live strings, and 17 mutations prove the fixes.

- **THE ROOM IS PART OF THE PLACE: a building-only fingerprint lost a free show.** A fingerprint of title + time + date + BUILDING folded "Cantonese Opera Excerpts" at 14:15 on 1/11 into one record. One show was in Ko Shan Theatre's Theatre ($50, $20, $10); the other was in its New Wing Auditorium (free, another troupe). The store kept the ticketed one. 7 of the 10 pairs it folded on 2026-10-05 were two shows. The LCSD fingerprint now uses the full venue with its room. The 4 true duplicates still fold, each one show listed twice in one room under two category codes.

- **A REFUSED STANDING ROW MUST BE REWRITTEN, NOT SKIPPED, OR ITS OLD WEEK ROLLS ON FOR EVER.** mapsee_ingest_facility_hours takes over 70 OSM community-centre listings, and osm_amenities skips those elements. On 2026-10-05 its first version skipped 8 of them on refusal: Alki ("Closed - Childcare Site Only"), Northgate (only a "Summer" set), 2 Cleveland renovations and 4 Phoenix centres. That left 8 listings with no writer. Any centre written once and refused later would also have kept its last recurring_hours for ever, because an upsert cannot delete and a standing row never expires. Now a taken-over listing is rewritten as osm_amenities' "opening times are not listed" listing (8 rows). An own row that is refused (16 on 2026-10-05) is NOT written: as pin_only scenery it still opened a sheet in ../mapsee, which drew the all-week window as "12:00 AM - 11:59 PM" for centres the city calls closed (integration review), and none of them existed yet. The gap left: an own row written with hours and refused, or gone from the data, on a later day keeps its last week until a retirement exists. An empty answer writes nothing.

- **../mapsee HIDES "☎ Phone:" AND "🏛 Run by:" ON EVERY ROW THAT IS NOT AN OSM OR BUSINESS LISTING.** parseImportedDesc strips both lines and renders them only in the civic card (behind "Public details from OpenStreetMap contributors") or the business card. Measured in node on the stored Laurelhurst and Cypress Hills rows: the phone was parsed and then shown nowhere. A row that takes over an OSM listing now keeps the markers, the OSM line and the "<name> — community centre in <town>" head; node gives civic=true and "Plan an event here"=true. Every other row writes "Run by X." and "Call N." as sentences.

- **SEATTLE'S LN_HOURS CONTRADICTED 2 OF 8 LATE NIGHT SITES' OWN PAGES (2026-10-05).**
  - South Park's open data says "Fri 6:30pm-10:30pm and Sat 3:30pm-8:30pm"; its page says "Late Nite 7:00pm - Midnight" on both nights.
  - Van Asselt's says "(no Saturday)"; its page says "Sat: Closed ( Teen Late Night: 7pm-12am)".
  - All 28 features share one GIS_EDT_DT, so a fresh edit date proves a bulk republish, not a checked cell.
  - The config records each stale cell (`late_night_disagrees`, skipped while unchanged), and evenings are projected 21 days instead of 90 (171 rows became 36), because an upsert cannot remove one.

- **A PARSABLE HOURS CELL BEATS SEATTLE'S DAY_ FLAG.** Garfield's school-year DAY_SATURDAY2 is "No" beside "10am-5pm", and seattle.gov says "Sat: 10 a.m. - 5 p.m." with Saturday drop-ins. The flag is None beside hours on Laurelhurst Mon, Ballard Mon and Garfield Wed. It was the only contradiction in a current season.

- **THE SAME STREET ADDRESS IS THE SAME BUILDING; 40 m IS NOT ENOUGH.** Four NYC older adult centres sat 41-102 m from the box centre of the OSM community centre at their own address: Stanley Isaacs 41 m, the Y at 54 Nagle Ave 43 m, Bensonhurst 44 m, Riverdale Y 102 m. Each drew a second dot. --propose-osm now treats an equal housenumber + street within 150 m as colocated. Sam Field / NNORC WOW (58-20 Little Neck Pkwy, 67 m) was added by hand, because that OSM way carries no address.

- **A DATED ROW ON AN OSM POINT MUST NOT SAY "OpenStreetMap contributors".** mapsee_retire_perday_osm hides rows that have that phrase, no recurring_hours, and a 4dp key equal to a standing row's. 148 of the first 171 Late Night rows matched all three. They now credit "Map point © OpenStreetMap (ODbL)", and 0 match.

- **RESEARCHED 2026-10-03/04 AND NOT BUILT: onePA (SINGAPORE), OTTAWA'S OTTREC, SEOUL, SYDNEY, ZARAGOZA, NANTES, OKAZAKI, EDMONTON'S FULL TABLE, CIVIC AMSTERDAM, VANCOUVER'S MEALS, NYC'S COMPUTER CENTRES - WHAT EACH HELD AND WHAT IT WAITS ON.** The community-centre survey (open-data cells for Canada, the US, Europe and APAC/Latin America, the recreation-platform cell and three seed cells on 2026-10-03; the DC sweep on 2026-10-04) built what AGENTS.md's `rec` row lists and left these, each holding sessions a neighbour could turn up to. Every number is the survey's, read under the production User-Agent on that date. None of these is in `curation_ledger.json` except where said, so nothing stops a second survey paying for them again, and the numbers will have moved: re-measure before building.
  - **onePA, Singapore (People's Association): waits on its terms of use.** `www.onepa.gov.sg/sitemap/sitemap-event.xml` lists 2,175 event pages, each carrying its data in a server-rendered `__NEXT_DATA__`; robots.txt allows them (it disallows only `/search` and `/cart`). A random 45 all answered 200: 44 active, 14 free (32%), 70% free or S$10 or less, 32 with MapLat/MapLng, about 1,840 rows projected to start in 90 days. No data licence, and the terms say no part "shall be reproduced, republished ... without prior permission" (platforms-probed.md). If it is built: StartDate is Singapore wall time labelled +00:00 (an 8.30am class reads 08:30+00:00); every `<lastmod>` is rewritten daily, so key on the id in the URL; the event sitemap answered 502 without the index's `?rev=`; and `sitemap-course.xml` is a paid catalogue (4,149 courses, 8 of 8 sampled S$68-257). The ledger holds 4 onePA outlet pages as no-calendar (2026-09-10), because the calendar is the sitemap, not the outlet page.
  - **ottrec, Ottawa: no licence.** `data.ottrec.ca/export/latest.json` is an unofficial compilation of ottawa.ca's drop-in schedules: 25,396 occurrences in 90 days at 132 facilities with coordinates, 46.7% of them reservation-required. robots.txt allows the export, but the data is "Compiled data (c) Patrick Gaskin" and "schedules (c) City of Ottawa", so it needs both to say yes (platforms-probed.md). The City's own drop-in pools page served a bot challenge, and its open data ends in 2022 (note above).
  - **Seoul's public-service reservations: unmeasured, behind a key.** `openapi.seoul.go.kr:8088/{KEY}/json/ListPublicReservationEducation/1/1000/` (and Culture, Sport, Institution), licence 출처표시 (attribution). Plain HTTP on port 8088 timed out on 4 of 4 GETs from the sandbox, so nothing was counted. The key is SEOUL_OPEN_API_KEY, which mapsee_ingest_seoul.py already reads, so one call from a runner answers it. Every row is a booking by construction; only PAYATNM=무료 culture and education rows could qualify.
  - **City of Sydney What's On: the dates are in `__NEXT_DATA__`, not in the JSON-LD.** Read per venue (`whatson.cityofsydney.nsw.gov.au/venues/<slug>`, then `/events/<slug>`). robots.txt allows both; no open licence is stated. Redfern, Juanita Nielsen and Ultimo community centres had 53 event pages and 501 dated occurrences in 90 days in `eventDates`. 26 pages say `freeEvent=true`, and every JSON-LD Event carries an address and geo. But the JSON-LD startDate/endDate is the season (38 of 53 began before the read, e.g. 2026-01-12 to 2026-12-21), so mapsee_ingest_jsonld would keep only the 15 one-offs. It needs a page's own date list expanded, with the clock from the JSON-LD. Never read the site whole: it carries commercial listings. The council's own centre pages are ledger no-calendar rows (2026-08-19), which is why the walk never got here.
  - **Zaragoza's agenda API: one row per subEvent, and paging repeats rows.** `www.zaragoza.es/sede/servicio/actividades/evento/list.json?srsname=wgs84`. robots.txt allows it; the licence was not verified. 1,007 subEvent occurrences in 90 days, 957 with a point, 815 with a start time; the 3,011 rows read held 2,333 ids. 126 (12.5%) are at 24 community venues (centros cívicos, centros de convivencia para mayores): 54 priced, 47 free, 25 unstated. It needs a new adapter: startDate plus openingHours.startTime, deduped on id, subEvent and date. The civic walk filed zaragoza.es as no-calendar (2026-10-04).
  - **Nantes Métropole (ODS): the ods adapter cannot join a date to its hour columns.** `244400404_agenda-evenements-nantes-metropole_v2`, Licence Ouverte, read through the Explore API (exempt). 3,248 dated rows in 90 days (1,201 `id_manif`), 3,246 placed, 1,310 (40%) gratuit. About 140 (4%) are at maisons de quartier, centres socioculturels and maisons des habitants. `date` is bare and the clock is in `heure_debut`/`heure_fin`, so every row would land all-day. Mapping `id` to `id_manif` would fold the 3,248 rows into 1,201. It carries `annule`/`reporte` flags.
  - **Okazaki (BODIK CKAN 92a4cbd7): slash dates.** CC BY 4.0. 440 active rows, 269 with lat/lon. Ledger `fail` 2026-09-27: "'開始日' is not a date column (sample: '2027/03/12')", and 開始時間 is a bare HH:MM. Its 49 rows at 市民センター are courses at 300 yen a session. The same date fix would admit 東大阪市 0849fcf9 (6 rows, also a ledger fail) and 門真市 (5 rows, under a new resource_id every month).
  - **Edmonton's whole drop-in table (Socrata mdkr-4c27): paid admission and stacked lanes.** Only the free-skate subset is configured (`opendata_sources.json:240`). The table held 3,877 rows in its rolling 7-day window at 39 facilities, all of them drop-in. Only 69 (1.8%) were free: the rest charge admission, and 530-546 were sauna, steam and whirlpool slots (the two cells counted differently). Read as-is, 394 of 1,478 title/date/venue fingerprints carry several start times, and the store merge loses 676 session times. It needs Toronto's per-stretch fold and a facility join that geocodes buc9-mag8's addresses, never its points.
  - **Civic Amsterdam's buurthuis agenda: the recurrence is prose.** `civicamsterdam.nl/amsterdam-oost/agenda/` (Noord too). robots.txt allows it; the licence is not established. The Oost agenda links 119 activity pages (inloop, shared meals, knitting clubs, homework help), and each one's rule is in its title ('3e maandag van de maand', 'in oneven weken'). There is no Event JSON-LD, feed or plugin, and find_calendar fingerprints nothing. The ledger's 2026-09-30 fail is a location page, not this agenda.
  - **Vancouver's free and low-cost food programs (ODS): the clock is prose.** OGL-Vancouver, Explore API. 18 of 77 programmes are free walk-in meals (no signup, no referral), all with coordinates: 71 meal-days a week, about 913 in 90 days. The days are flag columns; the time is only in the description ('Sun 11am', 'Thurs at 12pm'). Last processed 2026-08-31.
  - **NYC Citywide Public Computer Centers (Socrata sejx-2gn3): a lab's hours, not a centre's.** 471 sites open in 2026, 2,635 weekly open slots, 272 at community, older-adult or Parks centres. Times come in at least 6 free-text formats. mapsee_ingest_facility_hours reads NYC Aging's centres (cqc8-am9x), not this.
  - **Montgomery Parks (MD) and the Granicus OpenCities towns: HTML only (2026-10-04).** montgomeryparks.org's events are a custom WordPress post type with no schema.org Event, and `/wp-json` answers 401. Arlington, the City of Fairfax and Vienna run OpenCities with no iCal, RSS or JSON-LD. Fairfax and Vienna show 10 special events each; Arlington's calendar is drawn by JavaScript.
  - **Barcelona's `agenda-cultural` (resource 3abb2414): a workshop catalogue at the centres.** 2,975 rows in 90 days, 1,544 at centres cívics, and 751 of those (49%) are priced quarterly workshops ('Taller ...', 15-43 EUR, prior registration). mapsee_ingest_barcelona reads agenda-diaria (7% priced) instead.
  - **Paris, Bordeaux and Orléans (ODS): city agendas, not centres.** Que Faire à Paris (ODbL): 2,269 rows in 90 days, 4.9% at community places. Bordeaux Métropole `met_agenda` (Licence Ouverte): 1,593, 3.5%. Orléans Métropole (Licence Ouverte): 559, 7%. The ods adapter reads all three today; they were left for a general curation run, not refused. Bordeaux mirrors OpenAgenda: 43 of 60 sampled uids are in the public export.
  - **Santa Clara County Senior Dining (Socrata b5zy-63w9): stale.** 37 meal sites, all with coordinates, 177 weekly meal slots, 19 sites with no reservation. Last updated 2024-07-17.
  - **Fernwood NRG, Victoria (jsonld, `fernwoodnrg.ca/events-directory/`): malformed dates.** 17 rows in 90 days, all free or unpriced (12 are a weekly newcomers' welcoming group), 13 with their own address. startDate is not zero-padded ('2026-10-4T18:00-7:00') and keeps -7:00 after 1 November, so 7 rows would land an hour early. Its candidate entry said "Review before merging", and it is not configured.

- **THE SAME SURVEY'S REFUSALS AT ROW REVIEW, PARKED GOOGLE CALENDARS, UNREACHABLE HOSTS AND DEAD DATASETS, BY NAME, SO THE NEXT SURVEY DOES NOT PAY FOR THEM AGAIN.** 2026-10-03/04, the survey above. Portal-level refusals are in platforms-probed.md's last table; single community-association sites that refused are left to the ledger.
  - **Refused at row review, and not in the ledger, so discovery can propose them again.** Marcus JCC Atlanta (tribe): 12,553 rows in 90 days, and 94 of the first 100 are paid lessons. BrisWest Centre, Brisbane (Squarespace): 68 of 92 rows are instructor-run classes, and 36 state a fee or booking. False Creek CC, Vancouver (WP Event Manager): 37 rows, 0 with a location, and about 44 of 48 pages are paid classes.
  - **Eventbrite-only centres: no route.** CareFirst Community Engagement Center (an Eventbrite /cc/ collection) and Impact Hub Baltimore (organizer 8423440202). The API cannot list a third party's events (mapsee_ingest_eventbrite.py:8).
  - **Booking platforms seen and not assessed.** ezFacility: 3 tenants, robots.txt 404, but the session search needs the whole filter form and answered 'LocationError' on 2; its tenants are mostly commercial gyms and rinks. Gladstone: linked from 3 of 17 sampled UK centres, all Edinburgh Leisure, which is OpenActive's ground. Jonas Club: members' clubs, not probed.
  - **18 Google-calendar embeds, parked unmeasured.** They were found on 2026-10-03 when the export was refused and the key was not set. 15 are Canadian: Chaparral, Hillhurst/Sunnyside, Rosemont*, Alberta Avenue, Britannia Youngstown, Ermineskin, Deer Lodge, Fort Garry (Hobson site), Park City West*, masseyplace.org, dtesnhouse.ca, oanh.ca, dovercourt.org, centre.prospectcommunities.com and esrc.ca (* = the hall-rental availability calendar, not events). 3 are in the US: We Are Family (North Charleston), The Center of Wichita, Hollywood Senior Center (Portland). None is configured or in the ledger. GOOGLE_CALENDAR_API_KEY is set in CI now, so find_calendar on those sites will find them again.
  - **Unreachable from the sandbox, so no verdict.** Amsterdam's open.data.amsterdam.nl Activiteiten.json (CC BY 4.0, dataset last modified 2022-04-07), opendatabilbao.eus, aperto.comune.torino.it, open.regina.ca, Saskatoon's open data, Tucson's CKAN, and Gold Coast, Logan, Moreton Bay and Ipswich. For CDMX's Cartelera and PILARES and Joondalup's Event Espresso feed, robots.txt did not answer, so RFC 9309 says assume Disallow. A connect error is a fact about the probing host: re-probe from CI before writing a verdict.
  - **Leads nobody read.** Queensland's 'Neighbourhood Centres' dataset (data.qld.gov.au) is a ready seed list: 130 centres with lat/lon and 126 websites, on 108 hosts, of which 7 are in the ledger and 1 is configured. Also unread: donneesquebec's sit-quebec-evenements (tourism events, CC BY 4.0) and Melbourne's What's On (robots.txt allows all).
  - **Dead datasets by id; the conclusion is in the open-data note above.** Edmonton courses nr35-h9xv: 1 free of 1,432. Edmonton y7pw-4xyu and 7afh-aawr: 15-minute occupancy grids of mdkr-4c27. Halifax Arena Bookings: 7,543 in 90 days, 72% hockey. Laval and Longueuil: 0 future rows. Montgomery County MD Fall Programs q3jz-uk6h: 383 of 397 paid, and the 4x4 id changes every season. Charlotte CMPD Events: 68 of 108 are HOA or community meetings. Cary's open-gym: an attendance log last modified 2016-08-02. Euskadi Kulturklik: municipality centroids only, 1,834 priced against 33 free. Fáilte Ireland events CSV: 2,978 rows, about 5 at community centres. Málaga agenda-2026: 95 in 90 days, 0 at centres. Minato's events on Tokyo's CKAN: 31 rows with free-text dates and no coordinates. Brisbane's unconfigured ODS event sets: active-and-healthy duplicates active-parks; classes-and-workshops has 34 rows, 26 already in BCC events; 11 more hold 6-122 rows each.
