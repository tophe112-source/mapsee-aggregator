# The sync, EventStore, upserts, paging, cursors, fingerprints and series

> Part of mapsee-aggregator's agent notes — see `AGENTS.md` for the map. Read this file when the bug is about: an upsert that cannot delete, OFFSET vs keyset, PostgREST 5xx, `series_id`, `make_fingerprint`, `--ignore-cursor`, a toggle overtaken by a new kind of row.
> Every note below was measured before it was written; keep the numbers when you edit.

- **SCOPING A SOURCE ID PREVENTS NEW COLLISIONS, BUT DOES NOT REPAIR OLD ROWS.**
  On 2026-10-02 the map showed 3 Improv Happy Hour rows for one Seattle show.
  Unexpected Productions, Blessington Parish (an Irish Mass) and Lockleaze
  (a Bristol Baby Hub) all published Tribe ID 10011650. Their live normalized
  fingerprints exactly matched the 3 stored external IDs; only `425e0db3...`
  was the Seattle event. Before 294b34f the global `(tribe, numeric_id)` lookup
  rekeyed a populated record to another publisher's fingerprint while keeping
  its Seattle fields, then replaced its source URL. Supabase upserts on the
  new fingerprint cannot remove the old row. Publisher-scoped lookup now
  prevents that merge, but existing corrupt rows require a separate repair.
  `ops/2026-10-02_improv_happy_hour_duplicates.sql` records the guarded,
  reversible hide of 2 unclaimed copies with 0 attendees; the verified current
  Seattle fingerprint stays visible with the publisher's Eventbrite link.
  The latest automated dedupe step also timed out after 12 minutes (run
  37008068654, job 110957722472); a successful overall job is not evidence
  that its continue-on-error cleanup finished. The persisted Tribe regression
  now checks 3 publishers through `build_rows`, including each external ticket
  URL. Do not identify the publisher from a third-party checkout hostname.

- **A feed's ownership guard must read that feed's IDs, not the global catalog.**
  `fetch_import_state` combines existence and claimed-state reads in batches
  of at most 100 IDs / 6 KB URL, before any writes. In `test_sync_lookup.py`,
  three incoming IDs require one request and return two rows at both 1,000 and
  1,000,000 synthetic catalog rows. A server cap of two still finds a claimed
  row on page four. Fifteen checks cover bounded URLs, both sync modes,
  duplicate IDs, missing ownership, incomplete/changed Content-Range and
  no writes after failure. A successful HTTP response alone is insufficient:
  verify exact count and every page before treating a missing ID as new.
  Lookup failure stops the import; unchanged-content comparison may still
  fail toward writing only AFTER ownership is verified. This is a request
  cost model, not a live query benchmark or an atomic claim/write lock.

- **`--only-new` PAID TO PREPARE EVERY ROW IT THEN DROPPED.** `main()` ran
  `build_rows` — Spotify lookups, the Census batch, `to_row` — on the whole
  store, and only then read the import state and dropped existing ids. On
  2026-09-12 the 13 syncs checked 280,223 ids to upsert 15,549 rows; Meetup's
  final sync alone geocoded 22,276 of 53,871 rows (12,832 unique addresses
  matched) to write 2,480. Under `--only-new` the same single
  `fetch_import_state` read now runs first, on the store's fingerprints
  (`to_row`'s `external_id`), via `build_rows(keep=...)`, so only new records
  are enriched, geocoded and built. The read now covers placeable RECORDS, not
  built rows, so it also checks the coordless ones the geocoder would have
  dropped: 286,641 ids instead of 280,223 on that day's stores (+2.3%; Races
  alone 14,131 against 8,209). Refresh days are unchanged (build, then read),
  and a failed read still stops the sync before any write — and now before any
  geocoding. Geocode, import-state and upsert phases print their seconds.
  `test_sync_lookup.py` pins both orders and the failed read, and is now in
  `tests.yml`, which never ran it.

- **An upsert cannot delete, so a fix to what we WRITE never reaches what is
  already written.** When a link dies the ingest skips the place, no row is
  produced, and the row from when it worked survives with its dead button —
  `--only-new` means the sync would skip it even if one were produced. Two
  levers: `--ignore-cursor` + `full_refresh` re-examines and rewrites existing
  rows, and `mapsee_prune_links.py` cuts a line whose destination is provably
  gone. Neither is automatic; both are dry by default.

- **OFFSET paging against our own database gets dearer every page, and it fails
  the day the catalog outgrows it.** `mapsee_indexnow` walked new events with
  `limit=1000&offset=N`, which asks Postgres to produce and DISCARD N rows before
  returning the next thousand — so a full walk costs quadratically in pages. It
  worked for months and died on 2026-08-22 at exactly `offset=6000`, once a
  26-hour window held more than 6,000 new events (the sweeps of the previous days
  are what put it over). The fix is a KEYSET: ask `created_at > the last one I
  saw`, which costs the same on page seven as on page one. The cursor must be
  `(created_at, id)` and not `created_at` alone — a merge lands hundreds of rows
  on one timestamp, and a keyset on a non-unique column either serves that
  timestamp for ever or skips its tail. `test_indexnow.py`. The other `offset=`
  callers in this repo are third-party APIs (Socrata, ODS, Recreation.gov) and
  bounded, so they are not the same bug.

- **A KEYSET FIXED THE COST PER PAGE AND LEFT THE NUMBER OF PAGES UNBOUNDED,
  which is the half that broke next.** `mapsee_indexnow` walks every indexable
  event created in the last 26 hours; measured 2026-08-26 that window held
  ~261,600 rows, so the walk was ~262 requests against the ANON role's ~3s
  ceiling and one slow page ended it. Three rules fell out. **The walk is
  CAPPED and runs NEWEST-FIRST** — IndexNow is a freshness hint with a 10,000
  URL protocol ceiling and everything in the window is in the sitemap anyway,
  so announcing the newest ten thousand is the honest submission; ascending, a
  capped walk would have kept the STALEST rows and dropped everything that had
  just landed. **A timeout re-asks the SAME page before shrinking one** —
  halving is the right answer for OFFSET paging, where the cost IS the
  discarded prefix, and with a keyset (measured flat at 0.4s from page 1 to
  page 60) going 1000 to 125 only buys eight times the requests and eight times
  the exposure; `PAGE_MIN`'s own comment already said this and had no other
  lever. **And out of levers, it announces what it has** rather than raising.

- **"A SUBSET OF THE SITEMAP" IS AN INVARIANT TWO FILES HAVE TO KEEP, AND ONE
  OF THEM MOVED.** `fetch_new_event_ids`' own docstring says its predicates are
  "lifted verbatim from sitemapEvents()" and that IndexNow must announce a
  subset, "never a superset". Then ../mapsee 0194 added `pin_only: is.false` to
  both sitemap queries and nothing added it here, so this job spent months
  announcing `/e/` pages for drinking fountains and playgrounds that the
  sitemap deliberately withholds. Nothing could report it: both ends answer
  200 and every URL is real. It got materially worse the day "a thing that
  never shuts is not a listing" moved several hundred rows per metro into
  furniture. A comment saying two things must agree is not a mechanism;
  `test_indexnow.py` now asserts the predicate set.

- **`raise_for_status()` throws the diagnosis away.** That failure reported
  `500 Server Error: Internal Server Error for url: ...` and nothing else,
  because requests never looks at the body — so the one thing that says WHICH
  5xx it is, and therefore whether to take a smaller bite or wait for the edge,
  was discarded at the moment it mattered. Exactly what `mapsee_health_check`
  learned when it reported a 57014 as "one or more sources have gone quiet".
  Quote the server back: `_explain()` here, and the same rule everywhere.

- **Two different 5xx come back from PostgREST and they want opposite things.**
  A statement timeout (`57014`) means we asked for too much, and the answer is a
  SMALLER bite — `mapsee_cleanup.py` halves its batch, and retrying the identical
  request just burns the run's time budget doing what already failed. An upstream
  503 — `upstream connect error or disconnect/reset before headers`, Envoy unable
  to reach Postgres — means the request never happened, and the same one works
  once the edge recovers. Both are `>= 500`, so anything talking to Supabase has
  to tell them apart; getting it backwards is silent in either direction. On
  2026-08-14 a roughly hour-long Supabase outage took out three scheduled jobs,
  and only `mapsee_health_check._rpc` (which has always retried 5xx, and reports
  "could not run" as distinct from "a source has gone quiet") failed in a way
  that said what had happened. `mapsee_menu_links.sb` died on a nine-frame urllib
  traceback ending in `HTTP Error 503`, which reads like a bug in that file;
  `mapsee_cleanup` gave up on the first one. Both retry now, `test_cleanup.py`
  and `test_menu_links.py` pin the distinction, and a sustained outage still
  FAILS — it just fails in one greppable sentence that says the work is safe to
  retry. Do not convert these to `exit 0`: a provider being down for an hour and
  a job that has silently stopped must not look the same.

- **`upsert` WAS THE ONE PLACE THAT STILL TREATED EVERY FAILED BATCH ALIKE,
  and it multiplied an outage by fifty.** A batch whose retries ended on any
  status fell through to row-by-row isolation, so a final 503 cost 3 + 50x3 =
  153 POSTs per 50-row batch against a database already failing (2026-08-29
  13:36:49, OpenActive, `503 PGRST002 Could not query the database for the
  schema cache`). The final answer now decides: a 4xx (not 408/429) isolates;
  a 500 (57014, or a trigger's non-P0001 raise) halves 50 -> 25 -> 12 and
  isolates only what still fails at 12; a 502/503/504/408/429 is the request
  never happening, so the batch is LOST in 3 POSTs, counts toward
  `GIVE_UP_AFTER`, and the next batch waits ~8s (retries back off ~2s/~4s,
  jittered). A 503 mid-isolation stops the isolation. And "the next run will
  re-send them" was half true: a lost UPDATE on a refresh day is not re-sent
  until the next one, because the `--only-new` runs between skip existing ids.
  `test_sync_transport_loss.py` pins all three paths.

- **A read-back that only warns at 99% says nothing on the day it matters.**
  The 2026-09-09 Wednesday refresh compared 221,000 stored rows and rewrote
  76,946 of them, 95% from two adapters: OpenActive 46,798 of 63,228 and
  Meetup 16,258 + 10,075. `unchanged_ids` names a column only when it differs
  on 99% of a sync's rows, which none did, so the log never said which columns
  made those rows differ. It now prints `Skip-unchanged: N of M stored rows
  differ; most-blamed columns: a n, b n, c n` on every read-back, so the next
  refresh day names the cause. Diagnostics only — the comparison is unchanged.
  `test_skip_unchanged.py` pins the line.

- **One source id can mean many events, and EventStore deletes on the
  collision.** 39 of BikeReg's 1,246 ids come back once per occurrence date.
  `upsert` keys on `(source, source_id)` and POPS the stored record when the
  fingerprint moves, so a bare id makes each occurrence delete the one before
  it: 1,269 ingested, 1,148 surviving, every casualty an earlier date of a
  series. Put the occurrence date in `source_id`. This is the Localist duplicate
  bug running backwards, and the `rekeyed` counter is what makes it visible.

- **`--ignore-cursor` MAKES A RUN UN-REPEATABLE, so fusing it with "rewrite
  existing rows" made a backfill impossible to finish.** The ingest reads
  `cursor = {} if a.ignore_cursor else load_cursor(...)` and then refuses to
  SAVE one — every such run starts at candidate 0 and leaves the cursor where it
  was. `osm-amenities.yml` had ONE input, `full_refresh`, passing both
  `--ignore-cursor` AND dropping `--only-new`, so dispatching it ten times
  re-swept the same first window ten times. Measured after the run that was
  meant to backfill 0205's `icon`: **278 of 1,000 sampled Seattle furniture rows
  still NULL, London 689, Paris 618** — and no number of repeats would have
  moved them. A backfill wants ADVANCE + REWRITE, which is precisely the
  combination the fused input could not express. They are two inputs now
  (rewriting is now what every run does; `restart_cursor` restarts), and the
  general shape is worth the name: when one flag sets two independent knobs, the
  combination it cannot reach is the one somebody will eventually need.

- **`make_fingerprint` is date-keyed on purpose, and a matinee is not the
  evening show.** It truncates its date argument to `YYYY-MM-DD` because it is
  the CROSS-SOURCE key and two feeds describing one gig disagree about the
  minute — right for all 34 adapters that came before, because their sources do
  not run the same event twice in a day. Seattle Rep does: Freak the Mighty
  plays 2:00 p.m. and 7:30 p.m. on the same Saturday, filed as separate
  listings because they are separate performances people hold separate tickets
  to. `name|date|place` is byte-identical for the pair and `EventStore` dedupes
  on the fingerprint PRIMARY, so one simply vanished — 59 events in, 57
  fingerprints out, both casualties a matinee. `mapsee_dedupe_events.py` already
  draws this line the same way and says why; if you add a source with same-day
  repeats, widen the key by the clock as `occurrence_fingerprint` does, and
  leave all-day rows hashing to exactly what the shared helper returns.

- **`series_id` is assigned after the fact, not at ingest.** A repeating listing
  publishes each occurrence separately and the store is rebuilt every run, so the
  occurrences never meet in memory — the table is the only place a series is
  visible whole. `mapsee_link_series.py` runs after the sync and stamps them.
  Its failure mode is silent and bad: chain two unrelated events and one of them
  disappears from the map behind the other (`collapseSeries` in
  `../mapsee/site/js/app.js` folds a series to its next occurrence), with nothing
  in any log to say so. That is why it has a test, refuses implausibly large
  groups, and never touches a claimed row.

- **A TOGGLE'S MEANING CAN BE OVERTAKEN BY A NEW KIND OF ROW.** ../mapsee's
  "Hide open shops" sent `p_hide_standing`, which drops every row with
  `recurring_hours` — the same set as "shops" for exactly as long as imported
  restaurants and second-hand shops were the only standing rows there were.
  Folding leisure timetables into standing rows would have made one toggle
  labelled for shops hide every pool and gym in the country. The category
  decides now (`food`, `market`), client-side, because teaching `events_near`
  about shop-ish categories is a migration and 0204-0208 is a recent enough
  lesson in what an unverifiable change to that function costs.

- **A PAGE HIDDEN WHOLE MOVES THE NEXT ONE BY A FULL PAGE, AND `max(1, PAGE -
  decided)` SKIPPED ITS FIRST ROW.** A retire tool filters on `hidden_at`, so
  under `--apply` every row it hides leaves the result set, and the offset for
  the next page must shrink by exactly that many. `mapsee_retire_online_events`
  advanced by `max(1, PAGE - len(page_ids))`. When a whole page matched, that
  stepped one row too far. It also counted rows DECIDED rather than WRITTEN, so a
  failed PATCH left rows in the set that the cursor then treated as gone.
  Simulated with a page of 3 over an in-memory table (2026-09-25), rows 4 and 8
  of 9 were never examined. It now advances by `PAGE - rows actually written`,
  which always makes progress, because either rows leave the set or the offset
  moves. `mapsee_retire_parkrun` went further, to a keyset on
  `(starts_at, id)`. Its first dry run walked 284,624 rows, and two 4-day
  windows answered 500 at offsets of about 54,000. A keyset costs the same on
  every page and needs no correction at all. `test_ingest_parkrun.py` runs that
  walk, including four rows on one timestamp across a page boundary: a keyset
  on `starts_at` alone loses them, and that mutant fails the test. **The keyset
  still stuck at the same two places**, because `events` has no index on
  `(starts_at, id)`. One page inside a run of rows that share a `starts_at` must
  fetch and sort the whole run, and a cluster of standing rows is tens of
  thousands of them. The second dry run failed after `2026-09-24T22:00Z` and
  `2026-09-30T06:00Z`, with the same 14,891 hits. The walk now steps strictly
  past an instant it cannot page through, finding the next instant with an
  `order=starts_at` probe, which needs no sort. It then judges every skipped
  instant against the weekly schedule of the parkrun rows it did find (seven
  days, plus or minus an hour for clock changes). Only an instant that could
  hold one makes the run INCOMPLETE. That check was not academic. The third dry
  run walked 436,667 rows and stepped past four instants. One of them,
  `2026-09-26T00:00Z`, did hold a parkrun row ("Plantation Forest parkrun"),
  and equality on that instant plus `category=eq.running` found it in 67 ms,
  where no page of the instant could be read. So every stepped-past instant is
  now swept through category filters; the weekly check only judges one that
  cannot be swept.

- **AN UNHIDE WALK IS NOT THE HIDE WALK RUN BACKWARDS, AND ITS DRY RUN
  OVERCOUNTS.** `mapsee_retire_parkrun.py --unhide` walks the same window over
  `hidden_at=not.is.null`. The hide took 12.5 minutes; the unhide took **34**
  (dry, run 6) and **33.5** (apply, run 7). Hidden rows are sparse (35,767
  among the ~450k the hide walked), so a page scans far to fill 500, and **60**
  instants on the first day timed out and were stepped past at ~29 s each
  (three ~8 s attempts), which is most of the run. `retire-parkrun.yml`'s limit
  went from 40 to 90 minutes before the apply for that reason. The dry run
  counted **17,923** rows to restore; the apply restored **17,870**, the exact
  number hidden. The 53 were counted twice: a page reads part of an instant,
  the next page times out, and the sweep of the stepped-past instant reads all
  of it again. In an apply the first write takes a row out of the hidden set,
  so the sweep cannot see it twice. When instants were stepped past, trust an
  apply's written count over a dry run's hits.

- **READING A SOURCE BETTER CHANGES EVERY ROW'S IDENTITY, so the sync moves
  the rows instead of inserting new ones.** `external_id` is a fingerprint of
  the title and the venue text, and the upsert keys on it and cannot delete:
  the day the ics adapter stopped reading OpenAgenda as ISO-8859-1, every one
  of its rows would have been written a second time, clean, beside the garbled
  one. An adapter that knows an event's old identity now says so
  (`legacy_fingerprints`), and `rekey_legacy` runs before anything reads
  existence. A row still under an old key is PATCHed to the new one, keeping
  its id, its /e/ link and any claim. If the new key already has a row, the
  old one is hidden, and left alone if claimed. A record whose old row cannot
  be looked up or moved is held back for that run, never inserted, and a moved
  row is written even under `--only-new`, since it still carries the garbled
  text. Measured end to end on 13 real feeds, with the old reader's keys as
  the table: 2,738 rows under an old key, 2,737 moved, 1 hidden (the same
  event twice, once with a no-break space before "!"), 0 held, and the table's
  row count unchanged. The only old row left is the cancellation the fixed
  reader refuses. The adapter stops emitting old keys after `LEGACY_KEYS_UNTIL`
  (2026-11-30), when every such row has moved or is past.

- **ONE FAILED CHUNK OF THE STATE READ USED TO COST THE NIGHT; IT IS RETRIED NOW (2026-10-09).** `fetch_import_state` failed closed on the first non-200 among its chunks of 100 ids, and OpenActive alone has about 690 of them.
  - In run 120, 6 jobs' syncs stopped with "Could not verify imported event ownership": Races at 13:24, Ticketmaster at 13:27, OpenActive at 13:54, then Meetup, local and civic. `rec` (14:09) and `ics` (17:03) synced fine after the blip.
  - Each chunk now gets `STATE_READ_TRIES` = 3 attempts, backing off 2 s then 4 s. A retry re-reads the chunk from offset 0 and keeps nothing from a failed try.
  - A persistent failure still fails closed before any write. Its message now names the reason (our own check's words, or an exception's class, never its text, which can carry the URL).

- **CLEANUP MUST NOT COUNT THE CATALOG BEFORE EVERY BOUNDED DELETE RUN.**
  On 2026-10-09, run 37951179234 hit 57014 while counting, then deleted only
  **46** rows before even a **10**-ID fetch timed out. October 10 live EXPLAIN
  already used the valid `events_aggregator_cleanup_idx`; adding
  `ORDER BY starts_at,id` only added Incremental Sort (limit cost 1,436.65 to
  1,456.77), so no sort or duplicate index was shipped. Apply now goes directly
  to bounded candidate fetches; dry-run alone requests `count=planned`, since
  `count=estimated` can still perform an exact count. The source/start/end
  safety predicates remain on every delete. The behavioral fixture makes
  **3** requests to fetch/delete two rows and observe exhaustion, with **0**
  preliminary count requests; a failed dry-run count remains a visible error.
  All **73** CI test scripts passed. This removes wasted work; it does not prove
  the Micro database's separate memory/I/O pressure has been resolved.

- **RUNSIGNUP'S 1,000 CAP CUTS BY NAME, SO AN OVER-CAP STATE LOSES THE END OF
  THE ALPHABET (2026-10-11).** Florida's 120 days answered exactly 1,000 and
  page 4 ended at "Volusia Flagler HS XC". Two date halves held 701 and 431,
  1,042 unique races: the lost 42 ran W to Z and 31 were keepable ("Zonta ABC
  Run/Walk"). A capped state is now halved by date until each half fits (10
  requests instead of 5), a race that straddles the cut is kept once by
  race_id, and only a single day still over the cap prints the NOTE. Texas was
  at 910. `python test_ingest_runsignup.py`.

- **THE JANITOR NEVER DELETES A LIVE STANDING ROW (2026-10-12).** mapsee_cleanup
  deleted every imported row whose window ended 7 days ago, standing rows too,
  and a delete cascades to the row's chat and RSVPs. A centre, library or pool
  is one standing row the database's roller moves to today, and the roller was
  doing 5,000 rolls an hour against ~20,800 due (../mapsee 0234): its queue head
  sat at 2026-10-08T21:59Z, four days old, so from about 10-15 the janitor would
  have started deleting centres. The filter now carries
  `and=(or(recurring_hours.is.null,hidden_at.not.is.null))`: a hidden (retired)
  standing row still goes, a live one is the roller's to bring back. PostgREST
  accepts the nested form (anon GET, 200; Polish Home's standing row falls
  outside it). `python test_cleanup.py` (2 checks fail with the guard removed).

- **THE COLLAPSER FAILED FOR WEEKS WHILE ITS STEP SHOWED GREEN (2026-10-13).**
  `mapsee_dedupe_events.py` listed every imported row with no `starts_at` floor
  (about 1.49M planned rows; it was sized for 193k) and its --max-seconds bounded
  only the deletes. The step ran 12m12s, its timeout, on most runs 09-22..10-07
  and 4-5 minutes from 10-08, and annotated exit 1 on all 25 runs 09-19..10-12
  (ci-and-jobs.md); its log was never read (gh refuses the blob redirect), so
  why it failed from 10-08 is inferred, not seen. A census of 25,369 live
  imported rows in 11 metros then found 1,153 redundant rows (4.5%; Chicago
  11.1%), among them the owner's report: "Halloween Pet Portraits" and "...
  Fundraiser" at 9th & Thomas, one Discover SLU page renamed, so two
  fingerprints. It now lists one UTC day of live rows at a time under a budget
  that covers the listing, the reads and the writes, and it hides rather than
  deletes (cancelled-events.md). `python test_dedupe_events.py`.

- **A KEYSET NEEDS A LOWER BOUND THE INDEX CAN USE, NOT JUST AN or() (2026-10-13).**
  Paging one day of live rows by `or(starts_at.gt.X,and(starts_at.eq.X,id.gt.Y))`
  alone climbed 0.81, 0.45, 0.85, 1.52, 2.03 s over five pages on the anon key;
  with `starts_at.gte.X` beside the or(), 0.77, 0.45, 0.52, 0.48, 0.49, 1.01 s.
  One sample each, and the faster ran second over the same pages (a warm
  cache can explain part of it); the job's own listing of 10-14 (28,759 rows in
  29 pages, median 1.09 s, max 1.95 s) is the better evidence. 1.26 s and 3.23 s,
  quoted here before, were over all 35 list requests of that run, 6 of them
  57014s at 3.13-3.23 s on the started days. `count=exact` hit
  57014 at 3.2-3.6 s on three of four days. The collapser's cursor is
  `and=(starts_at.gte.S,or(starts_at.gt.S,id.gt.I),starts_at.lt.H)` and it never
  sends offset= or count=; `test_dedupe_events.py` pins both.

- **A DAY THAT HAS STARTED SCANS EVERY ROW THAT STARTED, TO RETURN THE FEW STILL
  ON (2026-10-13).** Live rows (`ends_at >= now`) by a `starts_at` range cost the
  rows the range scans. Anon key, 3 s ceiling: today and yesterday as whole days
  hit 57014 on page 1, as did five of six 3-hour slices of today, while
  tomorrow (all upcoming) listed 28,759 rows in 29 pages (median 1.09 s). So
  `list_window` splits a day at now: the started part goes in 6-hour slices
  that HALVE on a timeout down to 15 minutes (re-asking the same range only
  repeats the scan), the rest is the plain keyset. Run that way, today listed
  13,324 rows in 70 pages with 9 statement timeouts (the run had 16: the other
  7 were yesterday's 28 requests for 111 rows), and four quarter-hours still
  failed (04:00, 05:00, 07:00, 15:00Z: thousands of rows on one instant, which
  no slice splits). A failed part is reported, exits 1, and the rest of the day
  is used: a row missing from a window can only lose a match. The service role
  (8 s, no RLS) is not measured. Older running days go LAST: 3 of the census's
  854 reference hides involve a row started 2+ days before (Chicago Park
  District season programmes, 21-29 days back).

- **ONE EVENT, TWO CATEGORIES (2026-10-13).** 305 of 1,012 matched pairs carry
  two different categories: all-day copies 127, same key drifted 54, one source
  with similar titles 41, cross-source 36, one source listing twice 23, renames
  18, loose cross-source 6. One "Halloween Bar Crawl" in two Meetup groups is
  party in one and fitness in the other. The old key (title, category, start)
  lost every one of them; the collapser no longer compares category.

- **1.5 KM SINGLE-LINK MERGED BRANCHES AND CENTRES (2026-10-13).** The old
  `find_duplicates` over the 25,369 census rows: 613 clusters, 728 drops, 287 of
  them more than 150 m from the kept row (EarlyON drop-ins at other centres 123,
  Starbucks 10, Dunkin' 8, Chipotle 7); single-link chains reached 7 Starbucks
  in central Paris. Now 150 m, standing rows are not listed, and a member is
  hidden only if it matched the survivor itself (a star, not a chain; 10 of 864
  census members were left by that rule).

- **TWO ROWS ON ONE PAGE MADE IN ONE READ ARE TWO RECORDS (2026-10-13).** NYC
  Parks "Tennis" x21 on one dataset link, "Status Mic" / "Flopsauce Mic" on one
  Instagram link, Toronto "Pickleball, ages 60+" / "Cards, ages 60+" on one
  centre page: same page, same start, created together. Requiring rows on one
  page to be created at least 12 h apart left 0 false positives in the census
  and gave up 18 true pairs.

- **ONE PUBLISHER'S TWO PLACES ON ONE PIN ARE TWO EVENTS, AND A BOOKING
  PLATFORM'S TWO PAGES AT ONE SLOT ARE TWO SESSIONS (2026-10-13).** fiz-leipzig,
  bona-peiser and a YMCA (operations.daxko.com) geocode several sites to one
  coordinate: 7 of the 1,069 hides the build planned on production (10-12..14)
  were one programme at two centres, 0.0 m apart, made in one read. Now one
  publisher (not a multi-tenant host: Meetup, OpenAgenda, Eventbrite, Facebook,
  Luma), made under 12 h apart, with place names that each carry a word the
  other lacks, is two venues; room words do not count ("The Yard at Chicago
  Shakespeare" is "Chicago Shakespeare Theater": 5 census c1 pairs). On a
  booking platform (Everyone Active, Better, ActiveCommunities, PerfectMind,
  Daxko, Places Leisure, SFPL) two pages at one slot never match: 55 of the
  1,069 were such pairs, 52 Everyone Active ("Public Swimming Tue 19:00"
  ...AM190096 / ...AT190096), unverified at the source but distinct by code.
  The census labels "one domain, equal title, same start" true by rule (c1 has
  no booking exclusion; c1? did), so its "0 of 11,871 sessions" was partly
  circular: 4 c1 pairs are given up, 3 Everyone Active pools and McFetridge's
  session beside its season.

- **A LONG EVENT AND A SHORT ONE THAT START TOGETHER ARE TWO (2026-10-13).**
  1,098 of 1,100 census true same-start pairs end under 6 h apart, 0 at 6-48 h.
  The build hid a 59-day exhibition behind a 2 h row of it, a 33-day one behind
  its vernissage, a 49 h run behind its first 1.2 h show, and a 25 h pop-up
  behind its 1 h slot: their ends are exactly 24 h apart, so "more than 24 h"
  (the review prototype) missed it. END_GAP_H is 12: 26 of the 1,069 planned
  hides refused (12 OpenAgenda / Ticketmaster.fr runs, 12 Places Leisure day
  rows, the pop-up, 1 Everyone Active). On the census, 2 true-labelled
  same-start pairs end 48 h+ apart; the one the build matched (McFetridge's
  session beside its season, c1 by rule) is refused, and 12 rather than 24
  costs nothing there.

- **A LINK THAT NAMES NO EVENT IS A LISTING PAGE, HOWEVER FEW TITLES THE RUN HAS
  SEEN ON IT (2026-10-13).** The title count per link grows only from rows read
  so far: in a replay of the census 9 hides sat on links where it had seen 2
  titles or fewer and the census 3 to 65 (placesleisure.org/centres/latchmere
  12, grownyc.org/greenmarket 65). `names_an_event` now asks the link itself:
  an id in its last segment (5+ digits, 12+ hex, Luma's "zzaa4x39", /m/4480) or
  one or two up (/evento/1285675556092/comunicacion), 3+ digits in the query,
  or a title word in the last two segments, and never a page under /centres/,
  /branch/, /facilities/. 0 census true pairs lost; 20 of the 1,069 refused (16
  Places Leisure centre pages). A day row and a timed row made in one read on
  one page must carry one title (GrowNYC's page for every Greenmarket; a
  festival and its parade): CPL's 45 such copies do, 1 does not ("Adult Basic
  Computer Skills Help").

- **THE COLLAPSER'S RULE, MEASURED (2026-10-13, revised after two reviews).**
  Over the census's 15,632 labelled candidate pairs, with each link's titles
  counted census-wide: the build matched 999 true and 0 labelled not-a-duplicate,
  the revision 994 and 0 (the 5 given up are in the notes above). Through the
  job's own main() over the census rows (replay, now = 10-13T15:00Z): 866
  hidden (same-slot 422, same-page 325, all-day copy 119), 1,040 of 1,373 true
  pairs resolved (75.7%), redundancy 1,153 -> 308 rows (4.5% -> 1.21%;
  Chicago 0.8%); 4 hides on links the census labelled listing pages (One Story
  Time x2, which the census read as a rename; Select Budget Committee x2,
  ambiguous) and 17 on pairs it never labelled (the coverage review re-read
  them with production links). The build: 875, 1,045, 304, 9 and 17. Its
  1,069 production report hides (anon key, report only), re-decided: 914
  unchanged, 107 no longer hidden, 47 keep the other row, 1 left for a person.
  Left out on purpose: re-timed (24) and re-dated (18) copies, cross-posts whose
  titles name different things, standing rows (154 + 17), 6 cross-source pairs
  with another start, and two bars 78 m apart both running "Trivia Night" from
  two publishers (a place-name test there would refuse 9 census true pairs:
  "Garcias" / "Garcia's Chicago"). Read 50 pairs per rule from the first nightly
  dedupe-report before DEDUPE_APPLY goes on. The third version (below: add-ons,
  clockless classes, undated wrong-site links, the categories union): labelled
  pairs 991 true and 0 not; the replay 805 hidden (same-slot 417, same-page
  275, all-day copy 113), 973 of 1,373 true pairs, redundancy 369 (1.45%;
  Chicago 81, 2.5%), because 58 copies whose categories do not fit stay
  visible, 56 of them CPL's two adapters; without that rule 863, 1,037 and 311.

- **"NEWEST WINS" MEANS "THE KEY THE SOURCE STILL EMITS", WHILE ITS SOURCE IS
  STILL CONFIGURED (2026-10-13).** For a rename it does: 1 of 1 rename page
  checked (Discover SLU) shows the newer title, and 6 of 7 re-dated or re-timed
  Meetup pages show the newer row's value (Randonnée 316422086's says 10-11,
  neither row's: 10-18 newer, 10-24 older). For an all-day copy both keys are
  still emitted, by two adapters, and in 193 of 219 the clockless row is the
  newer one, so newest-wins would keep the row without a time 88% of the time.
  It fails when the newer row's source is retired: 131 of 137 Chicago Public
  Library same-page hides in the replay keep a newer row made 1.18-3.24 days
  (median 1.99) before its start, the open-data shape this branch retires, and
  once that feed is gone nothing updates or cancels the kept row
  (libraries-bibliocommons.md says when to switch on). The order now: claimed,
  people on it (RSVP, chat, promotion), a row with a clock, a row whose link is
  its own page (no other date), the whole event over '<a part> <sep> <it>' or
  '<it> <a package>' on another page at the slot and over a row whose undated
  link names another event, newest import; on an exact creation tie not a 'Copy
  of', then the fewer title words, then id.

- **WHO SURVIVES: THE VENUE'S OWN LINK, THE WHOLE EVENT (2026-10-13).** Of the
  build's 1,069 production hides, 24 kept a newer row whose "Tickets / info"
  named another site's event on another date (a yacht club's "Musical Bingo"
  linking a Minnesota council meeting on 12-22) and hid the venue's own; a
  booth two months newer hid "Children's Carnival"; ties inside one read went to
  the id, keeping "Nourish Moves at the Market Square Farmers Market" over the
  market and a club-seating upsell over the concert. Re-decided: 24 keep the
  venue's row (`link_ok`), 12 the whole event (`part_of`, same-slot edges only:
  on one page the newer title is the one the source emits), 11 the shorter title
  of an exact tie. The report lists every row whose link names another date
  (`link_names_another_date`): that is an adapter's bug to find, not the
  collapser's to fix.

- **THE SECOND SAFETY READ: ADD-ONS, CLOCKLESS CLASSES, UNDATED WRONG-SITE LINKS
  (2026-10-13).** A report run of the revised rules over a day nobody had seen
  (2026-10-15, anon, 558 planned hides, all re-read by id and read by hand)
  found 13 wrong (2.3%): 3 distinct events hidden ('Hazlett' behind 'The
  Pre-Show Speakeasy with Hazlett - NOT A SHOW TICKET'; 'Recreation: Zumba
  Toning' behind 'Recreation: Zumba', two 24 h rows made 0 s apart; a France
  Travail workshop behind another agency's webinar on one pin) and 10 wrong
  survivors (9 rows whose undated link is another site's event; the Spurs game
  behind its 'Courtside Experience'). Now: NOT_AN_EVENT takes 'not a <1-2
  words> ticket'; '<event> <package word>' (courtside, VIP, access, experience,
  post-game ...) ranks with the parts; two clockless day rows made in one read
  under two titles are two; a link whose last slug spells 2+ words, none in the
  row's title or place, ranks below a neighbour whose own link names it; the
  booking-platform test looks at both links (match(a, b) was not match(b, a));
  'Entfällt', 'Fällt aus', 'verschoben' are cancel marks; across publishers a
  1-2 word title beside one that adds an audience word ('Yoga' / 'Yoga for
  Seniors') is two; an all-day row and '<its title>: <part>' are two; a
  listing link with a date in its query or a month token ('october2026') names
  no event. Cost on the census's labelled pairs: 994 -> 991 true (Bona Peiser's
  two clockless courses, Lenny Pearce's 'Toddler Rave'), 0 not-true. That day
  re-planned cluster by cluster: 541 unchanged, 8 keep the other row, 2 behind
  another row, 3 no longer matched, 4 left visible (categories); 4 of 553
  still wrong (the France Travail pair; 'Weekly Specials', 'Potters Wheel
  Taster' and 'AA Rush Hour' keep a wrong-site row, whose slug shares a word
  with the title or has only one: counting a single word fixed 'AA Rush Hour'
  but flipped 3 Ticketmaster rows whose ids read as words). The build's 1,069: 1,054 as the revision
  decided, 13 other survivors, 1 more no longer hidden. Pinned as KNOWN in
  test_dedupe_events.py, each with what refusing it costs: the France Travail
  pair, Thekla/Mockau made days apart (3 census true pairs), two operators'
  centres 70 m apart (1 + 1), no-link 'Storytime' 111 m apart (5 production
  hides), and an RSVP that lands between a batch's re-read and its PATCH
  (events has no column that says 'has an RSVP'; the re-read is now per batch
  of 100 copies, so the window is one batch, not the day). The RSVP read is a
  keyset on event_id: a server that ignored offset= kept it going 28 reads.

- **A HIDE TOOK THE EVENT OFF THE COPY'S DOORS: THE KEPT ROW CARRIES ITS COPIES'
  CATEGORIES (2026-10-13).** A door and its category chips match `category =
  any(keys) OR categories && keys`. On the 2026-10-15 plan (categories re-read
  for all 1,093 rows, 11 anon GETs), 127 copies carried a category the kept row
  lacked and 93 lost a whole door (plansie 31, unsie 26, bar 21, vivosie 20;
  theater lost Broadway shows kept as Ticketmaster 'other' rows). One function
  (`merged_categories` in mapsee_supabase_sync) is applied in three places:
  the collapser PATCHes the kept row's categories before hiding (only if the
  row still holds what was read, unclaimed and visible); the sync merges the
  copies' categories into every stored kept row it writes
  (`merge_copy_categories`: the row's id rides the state read, the copies are
  one keyset in duplicate_of order, which 0236's partial index serves; its plan
  is unmeasured until 0236 is applied), BEFORE skip-unchanged, so a refresh
  neither drops the union nor rewrites the row every day; and each collapser
  run starts with `kept_rows_pass`, which gives a kept row that lost it the
  union back and un-hides a copy that no longer fits. The window the sync
  merge closes, measured on 8 runs of aggregate-events (67 sync steps): a sync
  step finished a median 4.8 h, at most 5.9 h, before the dedupe job could
  start, and the exact-detail adapters (Meetup, JSON-LD, Tribe, OpenActive, ICS
  organizers) rewrite their stored rows daily, every adapter on Wednesday; the
  17:23 festival sync would wait for the next run. A product projection
  (events_map_src) was not needed. 0108 allows 2 secondaries: the row's own
  stay, copies' primaries fill first; a copy that does not fit stays visible
  (4 of 557 copies on 10-15; 58 of 863 in the census replay, 56 of them CPL's
  two adapters). Re-planned, 0 of 553 hides lose a door or a category, and of
  the re-review's 122 (primary differs, a category lost) 118 stay hidden with
  every door, 2 are no longer hidden ('Hazlett'; the Spurs game, now kept) and
  2 stay visible for want of room. A claimed kept row is never written; its
  copies that need it stay visible. mapsee_reclassify.py rewrites categories
  without the merge: the next collapser run restores it.

- **TURNING THE COLLAPSER ON: A SWITCH WITH THREE POSITIONS AND SIX STEPS
  (2026-10-13).** One switch ('true') used to arm both the bounded first write
  and the 9 scheduled runs a week (21 days ahead, 60 back). DEDUPE_APPLY is now
  unset (report), 'bounded' (the nightly job reports and runs `--apply --days 0
  --lookback 0`, which only keeps what was hidden; dedupe-events.yml may apply
  at most 7 days each way) or 'true' (the nightly job hides).
  test_workflow_contracts.py runs both workflows' shell for each value. The
  steps (also in ../mapsee 0236's ORDER): 1. land this branch (CPL's open-data
  feed goes with it); 2. read the first nightly report, 50 pairs per rule, at
  most 1 wrong in 50; 3. apply 0236 and VERIFY; 4. dispatch mode report: it
  prints "duplicate_of present" and "Hidden copies: 0"; 5. no sooner than 5 days
  after the first daily run without vsdy-d8k7, and once 0236's CPL check returns
  0, set 'bounded' and dispatch apply with days 3, lookback 3, then open 20 kept
  rows' /e/ pages (copy gone from the place sheet, kept row on each door the
  copy was on); 6. set 'true'. Undo a run with unhide-report then unhide and its
  stamp; unsetting the variable stops new hides only.
