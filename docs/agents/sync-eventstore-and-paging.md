# The sync, EventStore, upserts, paging, cursors, fingerprints and series

> Part of mapsee-aggregator's agent notes — see `AGENTS.md` for the map. Read this file when the bug is about: an upsert that cannot delete, OFFSET vs keyset, PostgREST 5xx, `series_id`, `make_fingerprint`, `--ignore-cursor`, a toggle overtaken by a new kind of row.
> Every note below was measured before it was written; keep the numbers when you edit.

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

