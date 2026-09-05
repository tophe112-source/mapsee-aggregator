# The sync, EventStore, upserts, paging, cursors, fingerprints and series

> Part of mapsee-aggregator's agent notes — see `AGENTS.md` for the map. Read this file when the bug is about: an upsert that cannot delete, OFFSET vs keyset, PostgREST 5xx, `series_id`, `make_fingerprint`, `--ignore-cursor`, a toggle overtaken by a new kind of row.
> Every note below was measured before it was written; keep the numbers when you edit.

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
