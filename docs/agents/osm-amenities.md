# OSM amenities: which civic places earn a pin, a sheet, or nothing

> Part of mapsee-aggregator's agent notes — see `AGENTS.md` for the map. Read this file when the bug is about: toilets, food banks, artwork, deny-lists, facts vs names, the cached element list, `--only-new` on a rewrite-every-run adapter.
> Every note below was measured before it was written; keep the numbers when you edit.

- **ONE SELECTOR IS NOT ALWAYS A CATEGORY.** Every OSM amenity Kind except one
  describes something civic by definition - there is no private drinking
  fountain. `leisure=swimming_pool` is the opposite: most pools on earth are
  behind a house or a hotel. So `Kind` grew `extra` (an Overpass filter on
  `access`) AND `public_only` (the same test again in Python), because a query
  filter is a request and the answer is somebody else's, and the cost of being
  wrong is a walking route to a stranger's garden. It carries `always_open=False`
  and `always_list=True` for the food bank's reasons: an untagged pool is not
  open, and WHERE ONE IS is the actionable fact even with no hours. Measured
  2026-09-03: 26 public pools in a 25-mile Seattle box and 30 in London, ONE of
  which publishes readable hours - which is the argument for `always_list`, not
  against the Kind.

- **MOST CIVIC PLACES HAVE NOTHING WORTH READING, and pretending otherwise is
  how you ruin a map.** `mapsee_ingest_osm_amenities.py` imports eight OSM
  selectors — playgrounds (1,006,477 worldwide), public artwork (366,557),
  drinking water (365,393), outdoor gyms (100,841), little free libraries
  (46,908), bike repair stands (23,216), food banks (4,938), give boxes (1,478).
  A drinking fountain is a drinking fountain: its pin already carries the whole
  of what the row knows, so a sheet costs a tap, the bottom of the screen and a
  history entry to answer a question nobody asked. So the adapter sorts its own
  output. A row carrying a fact somebody could ACT on — hours, an operator, a
  fee or access rule, an accessibility note, a website, a real description, or
  for a sculpture its artist — is an ordinary standing row. Everything else is
  `pin_only`: ../mapsee 0194 draws it and does nothing else. **A NAME IS NOT A
  FACT** — "Sarah's Book Box" tells you nothing a book icon on that corner did
  not. At OSM density any rule that merely DEMOTES these is not enough:
  `events_near`'s pool is capped at 800, so four hundred playgrounds would bury
  the gig three streets away. They are not in that pool at all. The verdict is
  a PREFILTER rather than the last word: ../mapsee 0195 hands the client the
  description and the image and it decides again, the way the product
  re-validates every order link this repo writes. An OSM `image` or
  `wikimedia_commons` file is content on its own — for a sculpture it is the
  best content there is — and promotes a row out of furniture with no other
  fact required.

- **A DENY-LIST WHOSE COMMENT DESCRIBES AN ALLOW-LIST WILL BE WRONG FOR EVERY
  ADAPTER ADDED AFTER IT.** `to_row` appends "🔎 More on this show: <google
  search>" and says in its own comment that this is for the big-venue
  aggregators — but the test is `not _src.startswith(("opendata:", "venue:",
  "ics:", "program:"))`, so all three OpenStreetMap PLACE adapters inherited it
  by default. A charity shop and a drinking fountain do not have support acts.
  On food and second-hand that was merely odd; on civic amenities it was
  load-bearing, because ../mapsee 0195 decides whether a pin OPENS by asking
  whether anything survives stripping the row's boilerplate — and a Google
  search link is not a fact about a fountain, so every furniture pin on earth
  would have become clickable. `osm-` is excluded now. It was found by
  generating the REAL stored description for a bare fountain and reading it,
  which is the only way it could have been: the adapter's own output was
  correct, and the line was added two files later.

- **A BARE ONE OF THESE IS NOT ALWAYS WORTH A DOT, and `tourism=artwork` is
  the selector that proves it.** Seven of the nine are here BECAUSE existence is
  the answer — "there is a drinking fountain on that corner" needs no words.
  OSM's artwork tag takes in every tagged wall, and "there is art here" tells
  nobody anything. Measured in one Seattle box: **404 artworks, 146 unnamed, and
  55 tagged `artwork_type=graffiti` of which ZERO carried a name** — along
  Eastlake they draw a solid line of 🎨 down the side of I-5, burying ten
  drinking fountains and nine playgrounds in the same view. `Kind.bare_is_enough`
  is false for artwork alone: it has to arrive with a name, an artist, a
  description, an inscription or a photograph. The TYPE does not count and that
  is the load-bearing half — `🗿 Type: graffiti` is a restatement of the
  category, the same nothing as "a name is not a fact", and it was the entire
  content of the row that prompted this. Refused at INGEST rather than hidden at
  render, because a row that will never be drawn and can never be opened is one
  more row for every `events_near` scan to walk past.

- **AND `--only-new` WAS NEVER RIGHT FOR THIS ADAPTER, only unexamined.** Its
  own comment had the reason and drew the wrong conclusion — "pin_only is
  written on EVERY row, so an OSM mapper adding opening hours to a playground
  only takes that pin out of furniture on a full refresh" — treating as a
  backfill footnote what is a permanent staleness bug. EVERY column this
  adapter writes is derived from OpenStreetMap, so with `--only-new` a row is
  frozen as of the day it was first seen and no edit anybody makes upstream ever
  reaches it. The playground that gained hours stays furniture for ever; 0205's
  `icon` is the same shape and is what made it visible. `osm-amenities.yml` now
  rewrites on every run including the scheduled one, which is safe and bounded
  for three specific reasons — the sync's Claimed-guard drops a claimed row
  before writing, `--max-places` bounds the window however often it runs, and
  the cursor advances so successive runs walk the catalogue instead of redoing
  one window. The cost taken deliberately is blast radius: a regression in
  `to_row` now reaches rows that already exist.

- **A CACHED ELEMENT LIST CANNOT SEE A NEW SELECTOR, and the workflow comment
  that says so is not a mechanism.** `osm-amenities.yml` caches each area's
  Overpass result under `osm-amenity-vN-<area>-`, with its own note: "the
  ingest self-heals a changed bbox but cannot see a changed SELECTOR, so bump
  it whenever KINDS gains or loses an entry." `amenity=toilets` was added and
  the key was not bumped, so the next run restored a one-day-old list fetched
  by the OLD query, printed "7211 element(s) from cache", and imported not one
  toilet. A silent no-op that reads exactly like a successful sweep — the same
  shape as the parkrun config that was never committed while the job printed a
  friendly skip every night.

- **`amenity=toilets` is 519,045 uses and was the obvious omission.** Denser
  than drinking water (365,559) and the thing people actually expect a map of
  civic amenities to know. Same shape as a fountain — untagged means the pin is
  the information, real hours make a listing that can be shut — plus the three
  facts that decide whether to walk over: a baby changing table, drinking water,
  showers.

- **AND AN UPSERT CANNOT DELETE, so refusing them at ingest left 120 already on
  the map.** `mapsee_retire_thin_artwork.py` is the other half — `hidden_at`
  rather than DELETE, dry by default, `--unhide` to reverse, never touching a
  claimed row. It judges from the row's STORED DESCRIPTION, which is the same
  evidence `to_event` used to write it, so there is no OSM round trip and no
  second opinion to drift. Its own first version found ZERO against live data
  where 120 were sitting, because it matched the opener on `— public artwork`
  and an UNNAMED row has no dash in it — which is precisely the shape it exists
  to find. `test_retire_thin_artwork.py` pins both openers.

- **AND THE THIRD FAILURE WAS ON THE LINE THAT PRINTS THE RESULT.** That script
  has now failed live three times — twice on its own query, once on its report
  — and not once on its judgement, which is the only part 20 unit cases were
  covering. The third was `print(f"  {past} {hidden}")` over a `past` nobody had
  defined, left behind by the edit that removed an `f"{verb}d"` producing
  "hided". It ran the whole sweep first: 9,781 pins walked, 54 correctly hidden
  and WRITTEN, then a NameError on the summary, exit 1 and a red job over work
  that had entirely succeeded. Unreachable from every case in the file, because
  all of them call `is_thin()` or `patch_paths()` directly — **the bug was in
  `main()`, and nothing ran `main()`**, the same gap that cost
  `mapsee_ingest_osm_amenities` two production runs. The test drives the real
  `main()` against a stubbed transport on all three argv shapes now.

- **A FACT BUYS A SHEET, NOT A LISTING — "can it be SHUT" is the Nearby test.**
  `pin_only` began as "carries nothing worth reading", so one operator tag or
  one surface promoted a playground into `events_near`. Measured 2026-08-26 in
  one Seattle box: **752 rows from this adapter were in the Nearby list and 745
  were open 24 hours a day** — 58% of everything under `kids`, 67% under
  `arts`, several titled simply "Playground". Nearby is a list of what is ON,
  and a thing that is always there is not on however much is written about it.
  So `to_event` asks whether there is a time it is SHUT (`days != ALWAYS`,
  compared against the window rather than the verdict, because a rule parsing
  cleanly to `Mo-Su 00:00-24:00` is 24/7 written the long way), plus the food
  bank exemption. Everything else is a pin, and what it carries decides what
  the pin DOES — ../mapsee's `amenityHasContent` reads the description written
  here and gives a pin with something to say a hover and a tap. The two
  judgements are no longer one question asked twice.

- **AN OPERATOR THAT RESTATES THE THING IS NOT A FACT — "a name is not a fact",
  one tag over.** `operator=Little Free Library` on a little free library says
  exactly what the title and the book glyph already said. Live in Seattle: 47
  of 571 openable pins carried that line and **35 had nothing else**, so the
  entire content of their sheet was the row's own kind read back at them — a
  tap spent to be told what the map already showed. Suppressed on an EXACT
  match against the row's name or its kind's noun, so `Seattle Parks` on a
  playground and a superstring like `Little Free Library Ltd` both still print.
  Found by reading the hover labels the 24/7 rule had just made visible.

- **OSM WRAPS SOME DESCRIPTIONS IN QUOTES, AND THE HOVER LABEL IS WHERE IT
  SHOWS.** 34 of 1,000 live Seattle pins read "Catfish — 'The ceramic tiles…"
  or "…band type head saw.'" the moment a description reached a tooltip —
  invisible for as long as those rows were unopenable. A MATCHING pair is
  stripped, and so is an unbalanced straggler when it is the only quote in the
  string; anything with a partner is somebody's punctuation and is left alone.
  Underneath it was the older trap: one sculpture's entire description is a
  single apostrophe, and punctuation is TRUTHY, so it would have made a pin
  openable on nothing — the WP Event Manager `"-"` lesson in another costume.
  `_clean` now needs at least one letter or digit.

- **A VALUE THAT MATCHES THE ASSUMPTION IS NOT A FACT — "a name is not a fact",
  one level down, and missed on the first pass.** `access=yes` is the commonest
  tag on a playground and `fee=no` on a drinking fountain, so between them they
  were promoting a large share of the two densest selectors out of furniture —
  into sheets whose ENTIRE content was `🚪 Access: Open to everyone`. That is
  precisely the tap-for-nothing the split exists to prevent, and it survived
  review because "an access rule" and "a fee rule" read like facts in the
  abstract. Free and public is what a civic amenity IS; only the DEVIATION is
  worth a sheet, so `access=private` and a real charge still count and still
  print, and the assumed values are not printed at all. Deliberately NOT
  extended to `wheelchair`: nobody may assume accessibility either way, so all
  three of its values are real facts. Found by rendering the sheet a
  `access=yes`-only playground would actually produce and reading it.

- **`social_facility=food_bank` is 4,938 uses; `amenity=food_bank` is 16.**
  Reaching for the obvious key produces an adapter that runs clean, reports
  success and imports essentially nothing — the same silence as the parkrun
  config that was never committed while the job printed a friendly skip nightly.

- **"No hours tagged" means opposite things for a playground and for a food
  bank.** A playground is untagged because it never closes; writing it open all
  week is true. A food bank written the same way sends somebody with an empty
  bag to a locked door — the food adapter's worst failure wearing a different
  hat. `Kind.always_open` is the flag, and a food bank with no readable hours
  gets NO weekly pattern and makes no claim whatsoever about being open. An
  UNREADABLE hours string is treated as absent everywhere, never as an open
  sign.

- **A FOOD BANK IS THE ONE PIN WHERE "YOU CANNOT CLICK IT" IS THE WRONG
  ANSWER, and getting there took making it furniture first.** The bar for a
  sheet is "a fact you cannot already see from the map", which is right for the
  other seven selectors and backwards for this one: WHERE A FOOD BANK IS *is*
  the fact somebody came looking for, and whether they can open it, read its
  name, route to it or send it to somebody must not depend on whether a mapper
  filled in a phone number. Measured in Seattle: 24 food banks, and only 6 were
  openable — Ballard and Wallingford had hours, the other 18 (ACRS, St Mary's,
  Salvation Army Renton, the Little Free Pantries) were silently inert.
  `Kind.always_list`, and it is deliberately ONE selector: the argument is the
  stakes, and widening it to give boxes and bike stands would undo the
  furniture split by degrees. What it must never do is invent a time —
  `hours_unknown_line` says which of the three silences it is (`24/7`, a string
  our parser refused, quoted verbatim and attributed because unparseable is not
  unreadable, or nothing at all), which is parkrun's all-day event and its
  "Start time on the event page." one selector over.

- **A ROW NOBODY COULD OPEN WAS A ROW NOBODY HAD READ.** "Food bank — food
  bank." is what naming a thing after its own kind produces, and it sat in
  every unnamed row's description for as long as the adapter existed without
  anyone noticing, because every unnamed row was furniture and furniture's
  description is never rendered. It only became copy the moment food banks
  started listing. Whenever a rule stops hiding a class of row, READ what that
  class has been writing.

- **A HAND-WRITTEN FIXTURE CANNOT SEE A BUG THAT LIVES BETWEEN TWO FILES, so
  `gen_amenity_fixtures.py` generates them.** ../mapsee's `amenityHasContent`
  is a second opinion on `pin_only`, and what it actually reads is a string
  this repo assembles in two places — the adapter writes the description, the
  sync appends to it. That is how the 🔎 Google link reached every drinking
  fountain past a check that passed. Run it after touching `to_event` or
  `to_row` and commit what changes; build the rec with
  `NormalizedEvent.as_record` and never `vars()`, because `primary_url` reads
  `rec["sources"]`, which is not a dataclass field, so a hand-shaped rec
  silently drops the "Tickets / info:" line the client also has to strip.

- **A CATEGORY IS NOT A KIND, AND THE MAP DREW THREE THINGS AS ONE DOT.**
  Reported: drinking fountains, public toilets and bike repair stands were all
  🚰. All three are category `outdoors`, and ../mapsee looked the pin's glyph up
  BY CATEGORY — so the layer could say there was something civic on a corner and
  never which. `Kind.glyph` had carried 🚰/🚻/🔧 since this adapter was written
  and NOTHING HAD EVER READ IT: `NormalizedEvent` had no icon field, so the value
  was assigned and dropped on the floor for the file's whole life, and the sync
  hard-coded `"icon": None` under the note "let the app render the category's
  emoji". That note was right when it was written and stops being right the
  moment one category holds several KINDS of thing. `NormalizedEvent.icon` is
  the fix, ../mapsee 0205 returns the column and the client prefers it — which
  is the rule `eventGlyph` (`ev.icon || catEmoji(ev.category)`) had followed for
  ordinary events all along. Two things the 127 existing cases could not see,
  because every one of them read a description or a `pin_only` and none had ever
  asked what the pin is DRAWN with: the glyph reaching the row at all, and no
  two kinds sharing one. Both are asserted now, per kind.

- **AND A GLYPH FIX REACHES THE FUTURE ONLY.** `--only-new` means a scheduled
  run cannot rewrite a row it already wrote, and 1,000 of 1,000 sampled
  furniture rows had `icon` NULL. So every pin already on the map keeps drawing
  its category's emoji until `osm-amenities.yml` runs with `full_refresh`. The
  client's fallback to the category glyph is therefore load-bearing rather than
  defensive — it IS the map until that backfill lands — and it is checked.

- **A REWRITE-EVERY-RUN ADAPTER IS RIGHT AND "REWRITE EVERYTHING" IS NOT, and
  the second half was found on the READ side of another repo.** `--only-new`
  froze every row on the day it first landed, which for the three OSM adapters
  is a permanent staleness bug — every column they write is derived from an
  upstream people edit, so a playground that gained opening hours stays
  furniture for ever. Dropping the flag was correct. What went with it was not:
  an UPDATE that writes identical bytes still costs a dead tuple, a WAL record,
  an index entry and a RELOCATED LIVE ROW, and almost every row is identical,
  because nobody edits a given drinking fountain twice in a month. ../mapsee had
  just measured what that does — `events` is 993 MB of heap against a 256 MB
  shared_buffers, London reads 30,279 rows off 20,597 heap pages (1.47 rows per
  page) and query time is `reads x ~0.9 ms`. Rows arrive in INGEST order, so
  rewriting one moves it to the end of the table and smears a city's set
  further apart; a nightly sweep of tens of thousands is the one thing here that
  makes that worse on purpose. `--skip-unchanged` reads each row back and sends
  only the ones that DIFFER.
  **IT FAILS TOWARD WRITING IN EVERY DIRECTION AND THAT IS THE ENTIRE DESIGN.**
  A refusal, a 5xx, a body that is not a list, a column the server does not
  return, a value it cannot normalise — all read as CHANGED. Being wrong that
  way costs one wasted UPDATE, which is what the code did before it existed;
  being wrong the other way is an OSM edit that silently never lands, which is
  indistinguishable from the `--only-new` staleness the flag was dropped to
  escape. Two consequences worth keeping: the comparison is driven by OUR row's
  own keys, so a column added to `to_row` is compared from that moment rather
  than from whenever somebody remembers a list (the `🔎` link reaching every
  drinking fountain past a passing check is what a hand-kept list produces); and
  the sentinel for "cannot compare" is a class whose `__eq__` returns False,
  never `object()`, because the same instance compares equal to ITSELF and one
  unparseable value on both sides would then read as no change. It is a NO-OP
  under `--only-new`, where every row is new by construction, which is why every
  sync invocation can pass it — and `test_skip_unchanged.py` asserts all
  fourteen do, because one that quietly does not looks exactly like one that
  does.

- **AND THE WINDOW ON A STANDING ROW IS NOT OURS, WHICH WOULD HAVE MADE THAT
  FILTER A NO-OP ON EXACTLY THE ROWS IT WAS WRITTEN FOR.** ../mapsee 0156's
  `roll_recurring_windows` rewrites `starts_at`/`ends_at` on any row carrying
  `recurring_hours` whose window has passed — hourly, at :35 — so what is STORED
  on a standing row is the ROLLED window while `to_row` computes today's. They
  differ almost always, and every OSM amenity, every imported shop and every
  collapsed OpenActive weekly series is a standing row. Comparing that column
  asks what TIME it is, not whether OpenStreetMap changed, and every one of the
  33 cases written before this was noticed still passed. Nothing is lost by
  looking away: a changed PATTERN is a changed `recurring_hours`, which is
  compared, and a row starting or stopping being standing moves that column
  null-to-set — which is why the exemption needs a pattern on BOTH sides.
  Two files, one invariant, neither wrong on its own: the same shape as
  `looks_like_ordering` having to agree with `looksLikeOrdering`, and as the
  attribution line two repos spell differently.

- **A COLUMN FED BY THE CLOCK WOULD UNDO IT ALL WITH ONE LINE IN ANOTHER FILE,
  AND THE LOG WOULD READ AS A BUSY WEEK.** "A standing row never dies" (above)
  already names `last_seen_at` on `to_row` as the way to make "gone from the
  feed" detectable. Add it and every row differs from what is stored on every
  run, for ever: the filter skips nothing and prints "all N rows differ", which
  is indistinguishable from a genuine refresh of a changed catalogue. So a
  column that differs on essentially EVERY row is not an edit anybody made, it
  is a clock, and `unchanged_ids` NAMES it with a `::warning::` rather than
  obeying it — the same rule as reading PostgREST's own message out of a
  `PGRST204` instead of guessing which column is missing. Whoever adds that
  column has to decide what the comparison does with it, in the run that adds
  it, rather than discovering a year later that a lever stopped pulling.
