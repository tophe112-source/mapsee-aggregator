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
  events, 594 at 93 municipal community places), Barcelona (4,141, 1,305 within
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
