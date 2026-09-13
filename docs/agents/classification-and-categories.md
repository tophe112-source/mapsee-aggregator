# Classification: which front door an event reaches

> Part of mapsee-aggregator's agent notes — see `AGENTS.md` for the map. Read this file when the bug is about: a category key no lens opens, the kids/food/market regexes, category defaults, order pickup, a lens starved by its classifier.
> Every note below was measured before it was written; keep the numbers when you edit.

- **A category key that no lens opens onto reaches only mapsee.me.** The
  vocabulary is `MAPSEE_CATEGORY_KEYS` in `mapsee_supabase_sync.py` (mirrored as
  `VALID_CATEGORIES` in `mapsee_ingest.py`) and it must match `CATEGORIES` in
  `../mapsee/site/js/app.js`. `test_ingest_categories.py` asserts the first two
  agree; nothing checks the third, so check it by hand when you touch it.

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

- **A specific-but-wrong category DEFAULT is worse than a vague right one.**
  The rule below about pure vs mixed calendars has a second edge: on Seattle
  Center's campus, giving each ROOM its own key looked more precise and made
  the classifier worse. "Summer Fitness: Workout Wednesdays: Yoga" on the
  Exhibition Hall lawn stopped reaching fitness once the lawn declared
  `outdoors`, because `outdoors` is not promotable and `community` is; the
  Armory declaring `food` filed two cultural festivals as food. Only the three
  dedicated performing-arts houses (McCaw Hall, the Bagley Wright, Cornish
  Playhouse) keep a room-level key, because the classifier genuinely cannot
  recover a play from its title. Everything else states `community` and lets
  the promotions run.

- **AN ADULT AGE BRACKET IS NOT A CHILDREN'S AGE RANGE, and the rule that could
  not tell them apart put speed dating on the kids layer.** `_KIDS_RX` accepts
  an explicit age range because that is how a library says "for children"
  without using any of the words — "ages 4-18", "grades K-2". Written for a
  TITLE that is true; used as a SECONDARY it also reads DESCRIPTIONS, and an
  adults' listing states its brackets there far more often than a library states
  one anywhere. Measured 2026-09-13 over 2,000 live event pages sampled from
  mapsee.me's own sitemaps: `_KIDS_RX` fired on 42, the age range was what fired
  on 18, and **18 of those 18 had a low end of 18 or more** — every one a
  speed-dating listing enumerating "Ages 18-32", "Ages 28-40", none of them a
  children's programme. Bounding the low end below 18 removed all 18 and cost
  **zero** real children's ranges, because a library writes "ages 4-18" and
  never "ages 30-46". The reported row was "Seattle Gay Online Speed Dating", on
  plansie's kids layer, from a title with nothing childlike in it at all.

- **THE KIDS GUARD WAS ONLY EVER ASKED ABOUT THE PRIMARY, and the primary is the
  path that reads titles.** `_NOT_FOR_KIDS_RX` exists so "Adult LEGO® Club" does
  not reach the kids layer, and the SECONDARY — the path that reads
  descriptions, and therefore the path an adults' listing actually arrives
  down — never consulted it. A guard that only covers the safer of two paths is
  the same shape of bug as the metaphor guard being wired into the primary
  fitness rule only. Both paths check now, but NOT with the same regex, because
  a bare "adult" means different things in the two places: in a title it says
  who the event is for, in a description it usually says who else is in the
  room. Applying the title guard to descriptions would have withheld the layer
  from three of the 42 live hits, one of which — "Homeschool Days" — is
  unambiguously a children's event whose blurb prices "Students (4 – 18): $15"
  beside "Adults (19 & older): $22.50". A price table is not an age policy. So
  the description half is `_ADULTS_ONLY_RX`: only phrases that can mean nothing
  except an adults' event, each carrying its own age or its own format. It fired
  0 times across those 42.

- **`18\+` AND `21\+` IN THE GUARD COULD NEVER FIRE, from the day they were
  written.** Both sat inside a trailing `\b`, and `+` is not a word character,
  so the boundary demanded a letter or digit immediately after the plus sign —
  which is exactly what an age gate never has. Measured 2026-09-13: "Ages 18+",
  "21+ only" and "18+ event" all returned no match. A `\b` after a punctuation
  character is almost always a bug; check any alternative in this file that
  ends in one.

- **A WORD PEOPLE USE *ABOUT* SOMEBODY IS NOT A STATEMENT ABOUT WHO AN EVENT IS
  FOR, and `infant` is the proof.** Bare `infants?|newborn` matched 3 times
  across the same 2,000 live pages and **all 3 were wrong**: a Red Cross
  certification class ("recognize and respond to adult, infant and child
  victims") and a book group on *Beloved* ("an enslaved woman who killed her
  infant daughter"), both landing on the kids layer. Zero were real. Scoped to
  the way a library actually names the session — Infant Massage, Newborn Group,
  Infants & Toddlers — exactly as `baby` was already scoped one line above.
  Every ambiguous single word in `_KIDS_RX` and `_FITNESS_RX` is qualified for
  this reason; an unqualified one is a bug waiting for a big enough sample.

- **`_PARTY_RX` owns "block party", and one title is not a reason to take it
  back.** mapsee.me's own create placeholder is "Block Party Potluck" and
  awaresie is the neighbourhood door, so a family block party landing on
  bar.ventures ("Nights Out, Nightlife and People to Meet") reads like a
  miscast — `_PROMOTABLE_TO_PARTY` includes `community`, so a town calendar's
  block party is promoted straight out of it. Measured before touching it, and
  the measurement says leave it alone: across 280 live event pages sampled from
  mapsee.me's own sitemaps, **zero** mention a block party anywhere in title or
  description, and `_PARTY_RX` fires on three — a speed-dating night, a second
  speed-dating night and a happy hour, all correctly nightlife. The one real
  instance found all day ("NIU Homecoming Block Party", from DeKalb's new civic
  feed) is a university homecoming, which is a party; and it keeps `community`
  as a SECONDARY, so it still reaches plansie and awaresie either way. The
  primary only decides the pin's colour and glyph. Retuning a deliberate,
  documented rule on a sample of one is how the kids regex acquired its
  unqualified words.

- **vivosie was starved by the word "series".** `_SECONDARY_RX["music"]` asked
  for the exact phrase `concert series`, so "Summer Concert Series" reached the
  live-music lens and "Concert in the Park - Radio Replay" reached nothing.
  Measured 2026-09-13 over 3,160 DISTINCT live titles from 335 civic community
  calendars: 63 are about music and the rule caught **12**. The other 51 were
  symphonies, community concert bands, mariachi in the park, Oktoberfest
  concerts and tribute acts — the free outdoor town concert, which is the most
  characteristic thing "Live Music Near You Tonight" could open onto. Widening
  it takes 105. Same shape as fleabop: the supply was not missing, it was
  unlabelled, and no amount of curation would have found it.

- **Every word in that rule was counted before it went in, and two had to be
  qualified.** Bare `concerts?` scored 69 hits and all 69 were genuine — worth
  reading every one rather than assuming, because the neighbouring words were
  not so clean. Bare `jazz` scored 10 and **6 were Jr. Jazz youth BASKETBALL**,
  so it is scoped to `live jazz`, `jazz night/series/band/trio/orchestra`, `jazz
  at`. Bare `music` took "Bill & Ted Face the Music", "Music Together" (a
  toddler class), "Music and Movement" and "USSSA Music City Fall Nationals -
  Softball", so it is scoped to `music series/festival/night/of` and `music
  in/on the`. `bands?` scored 25 with one miss, the FILM "Trolls Band Together",
  hence the `(?!\s+together)` lookahead. `choir` is deliberately absent —
  half its hits are "Choir Practice", and a rehearsal is not a gig — and
  `bandshell` is absent because it scored zero, not because it is wrong.

- **`kayak` and `canoe` could never match the word anybody writes.** Both sat
  inside the secondary group's trailing `\b`, so the boundary demanded a
  non-word character straight after "canoe" — and a listing says "Canoeing" or
  "Kayaking", never the bare stem. `hik(?:e|ing)` one line above shows the
  suffix was meant to be there; these two and `snowshoe` were simply missed.
  Same family as the `18+` age gate whose `+` could never end a `\b`. The first
  patch for it REINTRODUCED the bug one line down — `swim\s+lesson` cannot match
  "Swim Lessons" either — which is why every plural in there is now spelled out.
  If you add a word to one of these groups, test it against the plural.

- **wegosie was missing the sports a parks department actually runs.** `outdoors`
  is deliberately not one of wegosie's keys: a hike keeps its outdoors PIN and
  reaches the movement lens through the fitness SECONDARY. Measured 2026-09-13
  over 6,000 DISTINCT live titles from 490 civic and park feeds, counting only
  titles that reached NEITHER the outdoors nor the fitness layer: **pickleball
  25, skating 17, birding/bird walk 16, swim lessons 3, archery 3, disc golf 2,
  kayaking/canoeing 2**. pickleball is the single biggest miss in the corpus and
  the word is unambiguous — there is no other pickleball. Net effect: 181 of the
  6,000 reached wegosie from a `community` base before, 239 after.
  END TO END, against the 3,657 live events the 42 newly-merged park and
  conservation sources actually carry: **1,573 reached wegosie before the fix
  and 1,792 after** — +219 events over 18 distinct titles, among them four
  Seacoast Chapter beginner bird walks, "Family Canoeing", "Intro to Archery",
  "Adult Skate Camp" and every drop-in "Pickleball". The rest of that 1,792 is
  worth knowing too: 1,471 arrive because the SOURCE states `fitness` (a town's
  pool and fitness timetable) and only 321 because the classifier found them.
  The config category does the heavy lifting; the regex reaches the free
  outdoor sessions a config default cannot know about.

- **A bare `walks?` is the obvious win and the wrong call.** It scores 77 hits in
  that corpus and **74 of them correctly reach neither layer** — "Art & Wine
  Walk", "13th Annual Historic Cemetery Walk", "Luminary Walk", "A Walk in Their
  Shoes - Dementia Simulation Workshop". On a town calendar a "walk" is an art
  crawl or a fundraiser far more often than it is exercise, so it stays
  qualified (`nature walk`, `guided walk`, `trail walk`, `bird walk`). Bare
  `paddle` is the same trap one size down: 5 hits, of which "Paddle Battle",
  "Battle of the Paddle" and "Doggie Paddle Day" are 4. And `swim\s+meet` was
  written and then removed: 3 of the 6 `swim …` hits are CLOSURE notices ("Swim
  Meet - Swim Center Closed"), and a closed pool on a movement lens is worse
  than missing the one real meet.

- **`category_for_feed` grew `outdoors` and `fitness` once the park agencies made
  them worth having.** Measured over all 587 civic+parks ics sources: 6
  sub-calendars state outdoors ("Open Space", "Open Space Master Calendar",
  "Remington Nature Center") and 16 state fitness — and the fitness ones are
  nearly all AQUATICS: "Aquatic Center", "Aquatic Fitness", "Aquatics Public
  Swim", "Aquatics Jr High Swim Team", "Fitness Center", "Masters Swimming",
  "Doling Fitness Schedule", "Senior Fitness", "Yoga". A town's pool timetable
  is the one part of a parks department that is purely movement, and it is
  exactly what wegosie opens onto. Six is a small number and `outdoors` is the
  thinnest curated category on the map, so it still earns the rule.

- **`wellness` was written into that rule and taken straight back out.** Its only
  two hits in the corpus are "Department of Health, Wellness and Animal
  Services" and "Community - Wellness and Recovery" — a county department and a
  recovery support group, neither of them exercise. `parks & rec` stays absent
  for the reason recorded above: 81 sub-calendars carry it and every one is
  mixed by construction.
