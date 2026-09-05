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
