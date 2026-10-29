# CI: the workflows, budgets, timeouts, job order and what a guarded job needs

> Part of mapsee-aggregator's agent notes — see `AGENTS.md` for the map. Read this file when the bug is about: a job that worked for an hour and lost its work, `timeout-minutes`, `always()`, `--only-new` in CI, the order of the daily jobs, secrets, a config file a job needs.
> Every note below was measured before it was written; keep the numbers when you edit.

- **A STEP BUDGET MUST ADVANCE THROUGH THE CATALOG, INCLUDING A PARTLY READ
  SOURCE.** Run 36131926531 on 2026-09-25 hit the ICS import's 250-minute
  step cap and the JSON-LD import's 60-minute cap (11:57:43–12:57:56 for the
  latter). The current configs held **1,576 ICS feeds** and **273 JSON-LD
  sites**. ICS already saved the event store after each feed and its later sync
  succeeded; JSON-LD saved only after its entire site loop, so its timed-out
  pass lost that adapter's rows. Both now stop under their step caps (220 and
  45 minutes), save progress, and resume at the next source on the following
  run. A deadline reached inside one source records the next VEVENT or detail
  page, so a slow source cannot pin the cursor ahead of the catalog tail. The
  cursor is cached with the feed caches; an unbounded manual run starts at the
  beginning and leaves that cursor alone. `test_feed_budgets.py` checks rotation,
  partial-source resume, a removed source, and manual full sweeps.

- **A CONCURRENCY GROUP QUEUES ONE RUN, NOT A LINE — A THIRD ARRIVAL CANCELS
  THE SECOND.** `cancel-in-progress: false` means a run that finds the group
  busy waits, but GitHub keeps exactly ONE pending run per group and cancels
  any earlier pending one when another arrives. Two things were sized on that
  2026-09-22. The 09:30 run (`curate-catalog.yml`, daily **09:30 UTC**; the
  community walk `discover osm --kinds community_centre,library --metros 40`
  until 2026-10-24, a second full osm pass on the shared cursor since) sits six hours
  after the 03:20 daily and the Wednesday 03:50 gap sweep, which together take
  up to 3 h and already share the queue — a cron in that window would not wait
  behind the Wednesday sweep, it would cancel it. And a whole-catalogue amenity
  refresh (`osm-amenities.yml`, new `shard` input, `shard=1,2,3` then `4,5` —
  **156 and 104 areas**, 260/5 = 52 a shard) is TWO dispatches, because one is
  260 matrix jobs against the 256 cap and three or more would cancel each
  other in the queue. Both crons resolve their pin off `github.event.schedule`
  (the Wednesday sweep's pattern), because a `schedule` event has no inputs.

- **The international sweep has no cursor, so the countries at the bottom of
  the file are the ones a slow run always drops — and they are the thinnest.**
  `mapsee_sweep_global.py` walks `metros_global.json` from the top every run and
  stops at `--deadline`; the Meetup job's international leg stops starting
  metros at 320 minutes of a 350-minute cap, and the job itself measured 288-326
  minutes across 2026-09-04..09-13. The file's order is the order countries were
  ADDED, which puts Brazil, Hong Kong, the UAE, South Korea, Singapore and South
  Africa last: six of the eight countries the coverage report FLAGs as having
  0-2 curated sources. A drop that always falls in the same place is not a
  budget, it is a country that is never swept. `rotate_countries` starts the
  walk at a different country each day (`_day_ordinal`, `MAPSEE_TODAY` fixes it
  as everywhere), so 29 countries take 29 days to turn the wheel and every one
  of them leads a run within it. By COUNTRY, not by metro: a country's metros
  are written largest-first and cutting one in half would give its first six
  metros a permanent seat and the rest none. NOT thinnest-first, which is how
  `catalog_discover_osm.metros()` sorts and is right for a CURATION sweep with a
  budget for empty ground — here it would only move the permanent drop onto
  London, Toronto and Sydney.

- **A five-hour feed job still dropped seven imports.** Run `34220367595`
  (2026-09-08) spent 223m34s in ICS, then hit its 300m limit in Tribe. Six later
  adapters and the final sync were skipped. `feeds` now has three independent
  groups: 1 ICS adapter, 18 local adapters, 9 civic adapters. Each has its own
  cache, sync, retained store and step-outcome report. Failed adapter processes
  continue to the sync but fail the job at the end. `feed_group` dispatches a
  single group for recovery without rerunning the global sweeps. ICS checkpoints
  its store, both caches and then its cursor at most every 120 s (after each
  attempted source until 2026-10-24); explicit cache/save
  runs even after failures. IndexNow and database-wide maintenance wait for all
  groups, including the formerly omitted OpenActive job.

- **A SWEEP THAT SAVES ONCE, AT THE END, SAVES NOTHING THE DAY THE CAP ENDS
  IT — and `markets_osm` was ended on 8 of its 9 sweep days.** From 2026-08-21
  to 2026-09-11 every Monday and Friday sweep was cancelled at the job's
  330-minute cap (08-21, 08-24, 08-31, 09-04 twice, 09-07, 09-11) with its sync
  skipped; the only finish, 08-28, spent 312 minutes on 259 bboxes (~73s each,
  47 retries) and synced 22,780 rows in 2.0 minutes. `store.save()` runs once,
  after the loop, so each cancellation wrote zero rows while the layer's 42-day
  horizon ran down. The bboxes are now split across the two run days by index
  (`shard_by_weekday`: even Monday, odd Friday, ~157 min each), `--max-minutes
  285` is checked before every bbox and every second-pass retry and returns
  what it has, and the sync is `if: always()` on the store file existing. The
  same sync waited for Wednesday to drop `--only-new`, a day this sweep never
  runs, so no market row was ever refreshed: it now refreshes on both days
  (`--skip-unchanged` kept), because a shard is only in the store on its own
  day. The config's note said Mon+Thu for `[0, 4]`, which is Mon+Fri. Stdout
  was block-buffered too — the 08-28 step began 18:45 and printed its first
  bbox line at 20:24. `test_markets_shard_budget.py` pins shards and budget.

- **A cache warmer can delay every job without warming one area.** Food run
  `34210805826` spent its entire 45m warm step on 29 of Portland's 30 tiles.
  Area runners then cold-fetched again. Food now uses independent area caches,
  retaining the four-runner concurrency limit. Its existing wall-clock budget
  also bounds Overpass requests/retries. Incomplete area lists remain uncached.
  Rio completed in 104m48s and synced; only artifact finalization failed (403).
  Its cursor wrapped to 0, so no progress was lost that time. All three OSM
  workflows now retry failed cursor uploads under a distinct artifact name and
  fail if the retry fails. Weekly health audits also reapply their ledger onto
  the current branch tip, retrying competing pushes five times like curation.

- **Refresh day must be a day the job actually runs.** The extra-source job has
  two weekly slots (Monday/Thursday) but checked Wednesday before dropping
  `--only-new`: zero scheduled refreshes per week. It now refreshes Thursday.
  OSM food and second-hand now match amenities: `--skip-unchanged` refreshes
  existing rows during ordinary cursor rotation, while the sync's claimed-owner
  guard preserves owner edits. `full_refresh` still restarts the cursor; normal
  runs keep their progress. OSM's 30-day place-cache TTL still bounds how soon
  changes at OpenStreetMap itself can be observed.

- **`--only-new` is the default in CI.** It skips events already in the table, so
  a scheduled run can only ADD. Wednesday's daily run drops the flag and does a
  real refresh — that is the only time changes at the source reach the map.

- **Every ingest job is deliberately failure-tolerant** (`set +e`, `|| true`,
  bare `exit 0`) so one dead feed cannot abort a sweep of forty. The consequence
  is that a green run proves nothing; `source-health.yml` is the actual signal.

- **A CONFIG FILE A GUARDED JOB NEEDS IS PART OF THE JOB.** Every ingest step is
  written `if [ -f x_sources.json ]; then ... else echo "no x_sources.json —
  skipping"; fi`, which is right for a source deliberately not configured and
  indistinguishable from one nobody committed. `mapsee_ingest_parkrun.py` was
  written, wired into `aggregate-events.yml`, and `parkrun_sources.json` never
  landed — so every scheduled run printed a friendly skip and the entire
  `running` layer stayed empty in all 20 countries, with no red tick anywhere.
  That is ~2,965 free, weekly, volunteer-run events, 17,790 dated occurrences
  over the 42-day horizon, every one with surveyed coordinates. Audited the rest
  when it was found: the other absent files are runtime STORES (`feeds_events`
  and friends, produced mid-run) and `ckan_sources.json`, which `merge` creates
  when something finally verifies — so parkrun was the only real one, and
  `test_ingest_parkrun.py` now asserts every guarded source config is present,
  except the ones parked on purpose (next note).

- **A CONFIG THAT IS ABSENT ON PURPOSE LOOKS EXACTLY LIKE ONE NOBODY COMMITTED,
  AND PARKRUN WAS "FIXED" BACK ON.** The note above took `parkrun_sources.json`
  for forgotten. It had been parked: parkrun's terms reserve all rights in site
  content and reference an anti-scraping policy, and the README's Conduct
  section says publicly that the adapter sits disabled until parkrun says yes in
  writing. Re-creating the file switched it back on, and it imported the
  worldwide list twice a week. On 2026-09-25, 30 of the 91 running, sports and
  fitness rows within 35 km of Ottawa were parkrun events. The config is parked
  again as `parkrun_sources.json.pending-permission`, in the working shape so
  that re-enabling is one rename. `test_ingest_parkrun.py` now FAILS while the
  live file exists, and lists it in `KNOWN_EMPTY`. `mapsee_retire_parkrun.py`
  (dispatched from `retire-parkrun.yml`) hid what had been imported: 17,870
  rows on 2026-09-25, after four dry runs to get a complete walk
  (`sync-eventstore-and-paging.md` says why). Afterwards the running, sports
  and fitness rows within 35 km of Ottawa held 0 parkrun of 61, and wegosie's
  Ottawa, London, Dublin and Sydney pages held 0 each. Before creating a
  guarded config that is missing, look for a `.pending-permission` twin and
  read the README's Conduct section.
  **Then the owner decided to show parkrun, the same day**, since it wants its
  events shared and attended. The config is live again with a `_decision`
  note (what is shown, and how to stop), the test now fails if the live file
  goes MISSING or a parked twin reappears, and the README's Conduct section
  says what a parkrun listing is and how any organizer gets one taken down.
  `retire-parkrun.yml` run 7 (`apply` + `unhide`) put back exactly **17,870**
  rows in 33.5 minutes: the same number run 5 hid. Measured on the app's own
  public read, `events_near_public` within 35 km, running, that weekend:
  London 187 parkrun rows of 269, Sydney 33 of 38, Dublin 20 of 24, Ottawa 5
  of 5. A `/c/` page can lag by up to its `s-maxage` of 6 h (London's still
  held 0 an hour later).

- **A FAILED READ IS NOT A FAILED RUN, and `sys.exit(1)` cost seven domains
  their daily push.** That same job reads the events for mapsee.me and then
  submits /c/ landing pages for mapsee.me and the six other doors — which do
  not need that query at all. One 57014 deep in the walk exited the process
  before a single landing page went out. The events are the time-critical half
  and the landing pages are the independent half; losing one must not lose the
  other. It still exits non-zero, because a job that has silently stopped and a
  job that reported what it could must not look the same.

- **A STEP CANCELLED BY `timeout-minutes` SKIPS EVERY STEP AFTER IT, INCLUDING
  THE ONE THAT SAVES THE WORK.** Three live instances, all found together on
  2026-08-26 and all reading as "cancelled" rather than "failed":
  `feeds` at exactly 4h00m lost `mapsee_link_series` nightly (above);
  `curate-catalog`'s discover step ran 05:36:58 to 07:07:00 — its ninety-minute
  cap to the second, and the run before it 04:06:37 to 05:36:41, the same
  ninety — and skipped **Coverage after** and **Commit new sources**, so every
  source it had verified went in the bin, under a comment on that very cap
  claiming "a run that hits it still commits everything it proved before
  stopping". (That job's runtime is WILDLY variable rather than uniformly
  over: 2, 13, 41, 12, 52 minutes on the five days before, then two 90s. So
  the cap is not hit every run — it is hit whenever the `osm` cursor lands on
  dense metros or Overpass is slow, which no amount of tuning predicts. That
  is the argument for a budget rather than a bigger number.) And
  `osm-food`'s Paris job ran 5h30m against a
  330-minute cap and skipped its sync, its cursor slice and its hand-up, so the
  sweep was discarded AND the cursor never moved — next week it would start in
  the same place and do it again, while the other seven metros finished in one
  to three hours. **The job cap is a CEILING, not a plan**: end the work
  yourself with time left to save it (`--max-minutes` on the ingest, a shell
  `DEADLINE` between backends in curate), and put `always()` on the steps that
  save. A wall-clock budget is needed where a per-item cap is not enough,
  because the cost is one fetch per venue against servers we do not control and
  is not predictable from the candidate count.

- **AND THE BUDGET GOES WHERE THE TIME GOES, WHICH IS NOT WHERE THE LOOP IS.
  Twice, in the same fix.** The first attempt at the curate budget put a
  deadline between BACKENDS in the shell loop, which reads as obviously right
  and would never once have fired: from that run's own log, socrata took 6
  SECONDS, ckan 101, mobilizon 3, and `osm` the remaining 88 — and osm is
  deliberately LAST, so nothing follows it to check anything. Moved inside
  `_discover_osm`'s METRO loop it still did not fire: the next run printed
  nothing whatsoever from that backend before its 90-minute cancellation,
  because one metro is an Overpass call plus a LIVE FETCH PER VENUE and a dense
  metro is hundreds of them — a single iteration of the loop you can see
  outlasts the whole budget. It is checked in the per-VENUE loop now.
  **Bounding the loop is not the same as bounding the work**: find the
  iteration that spends the time, and if that iteration is itself a loop, keep
  going down. Its cursor half matches: a metro abandoned part-way is UNREAD and
  the cursor stays before it, exactly as for one Overpass never answered for,
  and re-probing is cheap because the ledger already holds the dead ends that
  pass found. `test_discover_osm.py` pins both.

- **`always()` IS THE HALF THAT ACTUALLY SAVES THE WORK, and it is worth having
  even when the budget is right.** Proved in production the same day: run
  33012688812 was cancelled at its 90-minute cap with the budget still
  mis-sized — and committed `70501d9`, 266 lines of advanced cursors plus 24 of
  fresh ledger, where the two runs before it had committed nothing at all. A
  budget reduces how often you rely on that; it is not a substitute for it.

- **A JOB THAT WORKS FOR AN HOUR AND PUSHES ONCE IS A JOB THAT LOSES A RACE IT
  NEVER KNEW IT WAS IN.** `curate-catalog` verified its sources, wrote its
  ledger, advanced its cursor, appended its coverage line — and died on
  `git push` with `! [rejected] main -> main (fetch first)`, at the last step,
  throwing all of it away. **Three times: 2026-08-20, 2026-08-27, 2026-09-02.**
  Nothing about it reads as a race, because the log above the error is a
  perfect run.
  **The two causes are both structural.** On 2026-08-27 the job checked out
  `1ad207e` at 14:19 and pushed at 15:19; SIX commits had landed inside that
  hour, one of them `osm-amenities: advance the cursor`. Four workflows here
  write to main (the three osm cursor jobs and `source-health`, which writes
  `curation_ledger.json` — the same file), plus whatever a person pushes, so an
  hour-long checkout losing the race is the expected case and not bad luck.
  On 2026-09-02 it was this workflow racing ITSELF, which reads as impossible
  because `concurrency: curate-catalog` is right there. Concurrency serialises
  the two RUNS and cannot un-stale a SHA: **`actions/checkout` defaults to
  `github.sha`, the tip as it was when the run was CREATED.** Both crons were
  delivered ~4.5h late and 9 minutes apart (03:20 and 03:50 arriving at 07:59
  and 08:08, which is also why the "deliberately 30 minutes after the daily run,
  which by then has finished" comment was never true — the job takes 40-90
  minutes), so the gap sweep was created against `ab5ee01`, waited in the queue,
  started at 08:40:12, and checked out `ab5ee01` **seven seconds after** the
  daily run superseded it with `7f421c0`. Thirty-one minutes of discovery,
  rejected.
  **Both halves are fixed and they are different fixes.** `ref:
  ${{ github.ref_name }}` on checkout resolves the ref when checkout RUNS, so a
  queued run computes from the newest base — which matters beyond the push,
  because the cursor is the whole reason this job is continual rather than
  convergent and a run starting from a superseded one re-walks ground. And the
  commit step is now osm-food's loop: fetch, `reset --hard`, RE-APPLY, push,
  five times. Re-reading beats rebasing for the reason that file already
  records — recomputing against whatever main says NOW cannot conflict — and it
  needs no history, so the shallow clone is fine where a rebase would not be.
  **Re-applying is only correct because each of the four files has a merge rule,
  and each rule is wrong in a silent way.** `merge` already dedups by canonical
  URL so re-running it is idempotent; `catalog_curate.py reapply` unions the
  ledger (newer `checked` wins, because the audit writes it too), merges the
  cursor ONE LEVEL DOWN (a top-level `update` replaces a whole backend and
  resets every other one to whenever main last wrote it — valid file, months of
  walking gone) and appends the coverage line once. `test_curate_reapply.py`
  grades all of it, and its own harness had to learn the `check-ssr-rpc` lesson
  first: the first regression planted raised a `KeyError` and ended the file in
  a traceback with every later rule ungraded, so anything that dereferences a
  result is passed as a lambda and a throw is reported as the assertion it is.

- **A `timeout-minutes` EQUAL TO GITHUB'S 6-HOUR LIMIT IS NOT A TIMEOUT.** At
  360 the Meetup job is terminated by GitHub, not cancelled by its own cap, so
  no `always()` step can run. It ran 269-293 minutes 08-14..08-21 and 288-326
  minutes 09-04..09-13; on 09-04 that was US sweep 93.2, US sync 18.8,
  international sweep 191.0, final sync 22.6 (which had no `always()`). Each
  metro had a 1800s subprocess timeout and the sweep no overall budget, yet
  log timestamps on 09-04 and 09-13 put the slowest metro at 88s international
  (p50 65s), 80s US and 7s for Ticketmaster. Now: the job's first step writes
  `MEETUP_DEADLINE` (start + 320 min) to `GITHUB_ENV`, `mapsee_sweep_global.py
  --deadline` starts no metro after it and counts only metros it started, the
  per-metro timeout is 600s, the cap 350 and the final sync `always()`. Do not
  parallelise the legs: one token, no 429 backoff. `test_sweep_global.py`.

- **A minus sign is an option, not a number.** The international sweep spawns
  each ingester with `--latlong=VALUE`, fused, because argparse exempts only
  `^-\d+$|^-\d*\.\d+$` from option parsing and a latlong has a comma in it. Sent
  as two tokens, `--latlong -33.8688,151.2093` died with "expected one argument"
  for every metro south of the equator — all of Australia, New Zealand and South
  Africa, 22 of 165 metros, on BOTH sources, twice a week. `run()` prints the
  non-zero exit and continues by design, so the sweep still closed with "swept
  165 metros" and a green tick. `test_sweep_global.py` pins the argv form.
  That exemption is the INTERPRETER'S rule and it changed: Python 3.14 made the
  matcher a prefix match (`-\.?\d`), so the split form parses there and the
  test's own premise went false — red on a dev box, green on CI, with the code
  correct. `tests.yml` pins 3.12, where the split form still dies, so the fusing
  stays load-bearing; the test now REPORTS the running argparse's behaviour and
  asserts only what holds on every version. A test that hard-codes a standard
  library behaviour is a test with an expiry date on it.

- **Never add `pull_request:` to a workflow that reads secrets.** `tests.yml` is
  the one workflow safe on forks, because it reads none.

- **THE ORDER OF THE THREE DAILY JOBS IS LOAD-BEARING.** 06:17 aggregate (the
  gate refuses new spam at the door) → 07:40 purge (deletes old spam, and clears
  the ends that make a past row permanent — once the repository variable
  `SPAM_PURGE_APPLY` is `true`; until then its schedule is skipped) → 08:23 cleanup
  (deletes what has finished, which then includes everything the purge un-dated
  an hour earlier). Most of what the purge touches is therefore removed by the
  EXISTING janitor rather than by a delete of its own. Move the purge after
  cleanup and every un-dated row waits a full extra day.

- **A JOB WITH NO MEMORY THAT RUNS DAILY ASKS THE SAME QUESTION DAILY.**
  `menu-links.yml` examined the websites behind the 400 soonest food events
  every morning and kept nothing from the day before: 11 consecutive runs
  (2026-09-03..09-13) examined 34-78 venues, fetched 54-104 third-party pages
  each, and found 0 order links; 09-01 and 09-02 found 1 each. It runs weekly
  now, Wednesday 07:40 — after osm-food's Tuesday 05:10 sweep and the 06:17
  ingest, still before cleanup at 08:23, so the ordering above holds.
  `workflow_dispatch` stays for a run on demand.

- **A MIGRATION'S CODE MERGES BEFORE THE MIGRATION RUNS, and nothing sequences
  the two.** `to_row` writes every column for every adapter, so a `pin_only`
  that ../mapsee has merged but not yet applied used to cost not one feature but
  the WHOLE NIGHT: PostgREST answers `PGRST204 Could not find the 'pin_only'
  column`, a 400 is not retryable, so every batch of 50 fell straight through to
  the row-by-row isolation, every one of those rows failed identically, and all
  thirty-seven adapters wrote ZERO having made fifty times the requests to do
  it. `upsert` now reads the column name out of PostgREST's own message, drops
  it once with a `::warning::`, and carries on — 4 requests and 120 rows where
  it used to be 123 requests and nothing. `test_sync_unknown_column.py` pins
  both halves, including that a genuinely poisoned row is still isolated and not
  mistaken for a missing column. The ONE place this tolerance is wrong is
  `osm-amenities.yml`, which refuses to start without the column: there
  `pin_only` is not a nice-to-have but the entire point, and importing furniture
  without it puts every drinking fountain in the metro into the Nearby list.

- **A STEP ADDED TO A LONG JOB IS ADDED TO ITS CRITICAL PATH, and `feeds` was
  already at 3h29m against a 240-minute cap.** OpenActive walks twelve RPDE
  feeds to their end, which is honest work and costs 31 minutes. Dropped into
  `feeds` it tipped that job to EXACTLY 4h00m on its first real run, and the
  runner cancelled it mid-step — losing `mapsee_link_series` for the day, every
  day, silently, because everything before it had already synced and the run
  reports "cancelled" rather than "failed". It has its own job now, beside
  `races` and `markets_osm`, which is what those two are for. Before adding a
  step to `feeds`, look at what that job's last run actually took: the cap is
  four hours and the headroom is not what it looks like.

- **A HELPER THAT WRITES A REPO FILE AS A SIDE EFFECT WILL EVENTUALLY BE
  CALLED BY A TEST.** `_discover_osm` ends with `_save_ledger(led)`, so calling
  it from a throwaway verification with `{}` REPLACED `curation_ledger.json` —
  5,861 entries, 41,030 lines — with an empty file, and `git add -A` then
  committed and pushed it. Both halves are already written down as things not
  to do (../mapsee: "stage your own paths explicitly, never `-A`"), and both
  were done anyway inside one command. Recovered from `29fe5de`; the next
  scheduled run had already rebuilt it to 3 rows and would have re-probed
  thousands of known-dead hosts for weeks, slowly and silently, because the
  ledger is the only thing that makes a sweep get cheaper. `test_discover_osm`
  stubs `_save_ledger` AND hashes the file either side, because the stub is the
  kind of line a later edit removes without noticing. A test that drives real
  machinery has to intercept every write that machinery does, not only the ones
  it asserts on.

- **THE EVENTS CALENDAR STEP NEVER READ ITS LAST 65 SITES, because every run
  started at site one.** Run 36317891716 (2026-09-27) stopped at its deadline
  "before Autodromo Nazionale di Monza: 65 of 673 sites not started" after
  reading 608 in 80m19s, and the run before it stopped at the same place, so
  the tail of `tribe_sources.json` was configured and never imported. With
  `--deadline`, `mapsee_ingest_tribe.py` now starts at the site the last run
  stopped before (`tribe_cursor.json`, restored and saved by the feeds job's
  `actions/cache` beside the ics and jsonld cursors) and walks the list round;
  without `--deadline` it reads the whole list from the top as before. The file
  holds 820 sites after the 2026-09-27 batch, so at 608 per budget a site is
  now read every run or the one after. `test_workflow_contracts.py` pins the
  flag and both cache paths, because dropping either one silently restores the
  starvation.

- **continue-on-error TURNED 25 RED RUNS GREEN (2026-10-13).** "Collapse
  duplicate imported events" (a step inside indexnow, timeout-minutes 12,
  continue-on-error) annotated "Process completed with exit code 1" on every
  non-skipped run 2026-09-19..10-12, 25 runs, while the jobs API reported the
  step as success; it ran 12m12s, its timeout, on most runs 09-22..10-07. The
  collapser is now its own `dedupe` job: it needs every ingest job, has no
  continue-on-error, gets 25 minutes for 1,200 s of `--max-seconds` across its
  calls in every position of the switch ('bounded': 240 for the kept pass, 960
  for the report; `test_workflow_contracts.py` adds every call up), shares one
  concurrency group with the manual dedupe-events.yml, runs its own tests first,
  and hides only when the repository variable DEDUPE_APPLY is 'true' ('bounded'
  lets only the manual dedupe-events.yml hide, at most 7 days each way, while
  this job reports and keeps what was hidden: sync-eventstore-and-paging.md).
  indexnow needs it, so nothing it hides is announced. It runs after BOTH
  schedules and an extra_only dispatch: the Mon/Thu run syncs SeatGeek and
  Eventbrite, and 246 of the 804 hides in a replay of the census involve a
  SeatGeek row (on the daily schedule alone they waited a day). The 17:23 UTC festival sync waits
  for the next 06:17 run. `test_workflow_contracts.py` pins all of it.

- **A SYNC WAITS OUT A DATABASE THAT IS NOT ANSWERING, WITHIN A BUDGET
  (2026-10-19).** During the two-hour outage the races (13:25Z), Ticketmaster
  (13:27Z), OpenActive (13:53Z) and Meetup (15:05Z) syncs each gave up after
  three reads of the ownership state - about **two minutes** - and the day's
  rows were lost; the instance was back at 15:44Z, **36 minutes** after the last
  of them. `wait_for_database` (`mapsee_supabase_sync.py`, called in `main()`
  before `rekey_legacy`) probes one row of `events` until it answers or
  `MAPSEE_DB_WAIT_SECONDS` (default **900**, 0 = off) elapses; probe time and
  sleeps share that monotonic budget, with each request timeout and sleep capped
  to the time remaining. A 4xx is an answer (ours to fix); only a 5xx or no
  answer is waited out. The requests timeout bounds connect/read operations but
  is not a strict total-call cancellation deadline. The recital sync has a
  **30 s** wait inside its 3-minute ingest step; facility hours has **120 s**
  inside its 10-minute step, preserving room for the sync itself. A spent
  budget proceeds to fail closed exactly as before. `test_sync_db_wait.py`
  simulates 15 s requests: a 60 s budget permits two probes plus 30 s of sleeps,
  not the former three probes plus 45 s of sleeps (90 s elapsed).

- **THE ICS STEP SPENT UP TO 40 OF ITS 220 MINUTES SAVING A 199 MB STORE, SO
  IT SAVES EVERY TWO MINUTES.** The ICS store is 198.6 MB and 131,065 events,
  and one save takes 3.1-3.5 s (2026-10-23). Saving it and both caches after
  every source cost 40.2 min of the 220-min budget on 10-23, 19.0 min on 10-22
  and 0.6 min on 10-21. main() now saves at most every SAVE_EVERY_SECONDS =
  120 (PerfectMind's interval), plus at the budget, at the end of the
  catalogue and on KeyboardInterrupt. The cursor is written only in that
  checkpoint, after the store, so a kill loses at most 120 s of feeds and the
  next run re-reads them.

- **Meetup's two legs have two stores, and the US one wins at the border
  (2026-10-24).** In the old shared meetup_events.json, the always() final
  sync re-read the state of 51,996 ids in 116.9 s (us_sync: 54.4 s). It also
  sent 29,890 addresses to Census (us_sync: 14,471) on 2026-10-23, rebuilding
  ~24.7k US rows us_sync had already written, and each of the 179
  international metros loaded and rewrote them too (6-13 min a day,
  10-21..23). The international sweep now writes meetup_global_events.json.
  The final step syncs the US store again only when `steps.us_sync.outcome !=
  'success'`. SPLIT STORES DUPLICATE BORDER EVENTS. Three international
  circles overlap a US one: Tijuana is 15.6 mi from San Diego; Victoria (74.2
  mi) and Vancouver (121.3 mi) fall within Seattle's 100 mi radius. An event
  both legs find has one fingerprint but can differ in category and ends_at,
  because each process keeps the category of the first keyword whose results
  held it; a reproduced San Diego event came out community/21:00 in one store
  and party/23:00 in the other. Every Meetup record carries source_details, so
  needs_detail_sync keeps the stored row on --only-new days too, and
  skip-unchanged sees a difference. Two copies would therefore BOTH be written
  EVERY day, not only on Wednesday, and the row's door would switch between
  the two syncs. The final step therefore drops from the international store
  every fingerprint the US store holds, live or tombstoned, before syncing it,
  and keeps its own tombstones. Its log line `Border overlap: N of M` is the
  measured overlap.

- **The ics group's final sync is skipped after a checkpoint that succeeded
  (2026-10-24).** Nothing imports after the checkpoint in that group. The
  final pass wrote 0 rows after a 239.2 s state read of 130,979 ids on
  2026-10-23 (163.5 s, 0 rows on 10-22). The guard reads
  `steps.checkpoint.outcome`, not `.conclusion`: under continue-on-error the
  conclusion is always success, so a failed checkpoint must still be followed
  by the sync. The feeds job sets PYTHONUNBUFFERED=1. Block-buffered, the ics
  step printed its first line ~33 minutes in on 10-23.

- **THE DAILY OSM SWEEP STOPPED ON A 3-METRO CAP WITH HALF ITS BUDGET LEFT,
  AND CKAN COST 2 MINUTES A DAY FOR NOTHING (2026-10-23).** Run 116 read
  Dublin, Cork and Galway in 25.0 of its 55 osm minutes (08:50:38-09:15:39)
  and stopped on `OSM_METROS_PER_RUN=3`. Run 114 stopped the same way with
  about 4.7 minutes left. The other 4 of 6 daily runs ran out of time inside a
  metro. At 1.29 verified per osm-minute, the roughly 35 unused minutes over 6
  days were about 45 verified. The 03:20 cron now defaults `--metros` to 40,
  so the budget decides; dispatches keep 3. ckan took 2.1, 2.1, 2.2, 1.8, 2.5
  and 1.9 minutes in those 6 daily runs and proposed 0 each time (3,324-4,641
  datasets no_datastore), the 2026-08-22 finding again. The daily run now
  reads ckan on Wednesdays only (`date -u +%u` = 3). The Wednesday gap sweep
  shares the `all)` case and keeps ckan, and so do dispatches. Taking ckan out
  of `all)` outright would have left unpinned ckan on no schedule, so its
  per-query cursors would never move. `test_workflow_contracts.py` drives the
  step's shell for each schedule.

- **THE EVENTS CALENDAR STEP READ 586 OF 1,408 SITES A RUN, ONE AT A TIME, AND
  A SITE CAME ROUND EVERY ~2.4 DAYS (2026-10-23).** "[tribe] done: +33721
  events from 586 of 1408 sites (stopped at the step deadline)": the step
  stopped at its 80-minute deadline in 15 of 15 runs (80.0-86.9 min), at 8.2 s
  a site. The 1,408 sites are on 1,408 hosts, and most of a site's time is its
  own Crawl-delay (1,328 at 2 s, 54 at 10 s, 9 at 30 s or more).
  `mapsee_ingest_tribe.py` now reads `--workers` sites at once (TRIBE_WORKERS,
  default 3 as in PerfectMind), never two on one base_url host, each worker
  with its own requests.Session. `ingest_site` returns its events and only the
  main thread upserts; the free_only admission ids are read from the store
  before a site starts. The deadline is checked before every page after the
  first, without sleeping toward a page that could not start in time: Haydon
  Wick's 15 pages at 30 s are ~7 min, which is how an 80-minute deadline
  became an 86.9-minute step. The store (written to .tmp and moved into place)
  and the cursor are saved together at most every 120 s, at the end, and on
  SIGINT (exit 130). The cursor is the first site in list order that has not
  finished. Local smoke (8 sites on 4 hosts, 3 pages at 1 s): 21.1 s with one
  worker, 8.0 s with three, never two requests on one host at once, never less
  than 1.00 s between a site's pages. The expected effect, about 1,408 sites
  in ~65 min, is an estimate until the next civic log's done line (now "(3 at
  once, N min)") measures it. It brings events in fresher; it adds no
  calendars. `test_ingest_tribe.py` pins out-of-order completion and the
  cursor, the page-level stop, host exclusivity and interrupts.

- **A SITE THE DEADLINE CUTS SHORT IS NOT FINISHED, OR THE CURSOR STOPS MOVING
  (2026-10-24).** In the first version of the three-worker tribe step a site
  cut between pages counted as finished, so once every site had started before
  the deadline the cursor fell back to where the run began and the same tail
  lost its later pages every run: a stand-in server (6 sites, 3 workers, 4
  pages at 1 s, a 6 s deadline) cut site3-5 "before page 3" in 3 of 3 runs
  with the cursor on site0, and the store stayed at 54 of 72 rows.
  `ingest_site` now returns a SiteEvents list whose `.cut` names the deadline
  or an interrupt (never a publisher's 500 or unreadable page); main writes
  the cut site's rows and leaves it unfinished, so the cursor names the first
  cut site and the next run reads it whole first. The one exception is the
  run's first site, which had the whole step: holding the cursor on a site
  that can never finish would starve the tail as site one did until
  2026-09-27. Same server after the fix: the cursor went site3, site0, site3
  and the store held all 72 rows by run 2. The done line says "(stopped at the
  step deadline) (N site(s) cut short)" whenever a site was cut, even when
  every site started.

- **A DEADLINE CHECKED BEFORE THE PAUSE LETS A REQUEST START PAST
  --max-minutes, AND robots.txt IS AN UNPACED REQUEST.** The Client contract
  that glenecho and natuurwerkdag use checks `clock() >= deadline` and THEN
  sleeps the pause. With a fake clock (pause 1.1 s, 0.2 s a request, deadline
  5.0) the last request went out at 5.7. Robots.check() also fetches
  robots.txt outside the Client's pacing, so the first page followed it 0.2 s
  later. mapsee_ingest_galaxydigital's Client stops when `clock() + wait >=
  deadline`, and counts and paces a fresh robots.txt read.
  test_ingest_galaxydigital asserts both: no request at or after the deadline,
  and at least 1.1 s between any two requests to a host, robots.txt included.
  Mutating either check fails the test.

- **THE JSONLD STEP NOW PAYS EACH HOST'S CRAWL-DELAY (2026-10-25).** That is
  1,202 s more per full pass for the 30 configured hosts asking 2-30 s. It is
  offset by dropping the 1 s sleep after every site's last page, one second
  off each of the 835 site visits. robots.txt is one more request per host per
  run (692 hosts per pass); in a 16-worker audit all 692 took 122.5 s. Askim
  Kulturhus, added, costs about 9 min (52 pages at 10 s) per visit of the
  45-minute step; Tartu (200 pages at 2 s, about 8 min) is held.

- **THE REPORT-ONLY COLLAPSER RAN 9 TIMES A WEEK TO SAY ONE NUMBER, AND WAS
  THE ONLY RED JOB (2026-10-27).** With DEDUPE_APPLY unset, 2026-10-20..10-26
  had 9 dedupe runs (7 daily, 2 Mon/Thu). Together they made 9,428 GETs and
  102 statement timeouts (57014), listed 2,489,674 rows, read 237,434
  descriptions and took 6,138 s. Dedupe was the only failed job in 8 of the 9
  aggregate runs (39416876303, 39774488313, 39778061405, 39948040068,
  40088525568, 40189539295, 40328064439, 40331616791); on 10-21 Meetup and
  indexnow failed too. 'Copies to hide' stayed between 8,110 and 9,065 across
  the 17 runs 10-14..10-26. Now a SCHEDULED run reaches the job only in
  'bounded' or 'true', the last clause of its `if`, and a dispatch still runs
  it. While the switch is unset the report is dedupe-events.yml, mode report:
  dispatch it after the aggregate run has finished (about 19:10 UTC) and
  before 03:00 UTC. One dispatched report costs about 1,176 GETs, about 13 x
  57014, about 311k rows, about 29k descriptions and about 604 s. Actions
  compares `vars.X == 'true'` without regard to case, but the shell's `case`
  is case-sensitive, so 'True' passes the gate on schedule and then only
  reports. test_workflow_contracts evaluates the `if` for every cron and every
  switch value.

- **A GATE SCRIPT THAT CALLS A MODULE'S main() READS THE REAL CLOCK UNLESS
  MAPSEE_TODAY IS PINNED FOR THE WHOLE SCRIPT.** Passing today= to the cases
  that call the function directly does not cover the cases that go through
  main(), which pass today=None. test_meetup_category_cache.py first pinned
  MAPSEE_TODAY only for its last case, and then deleted it. Its 2026-11-05
  fixtures would have been pruned from 2026-11-07 UTC on: with the clock faked
  to 2026-11-20, and with MAPSEE_TODAY=20261107, 10 checks failed, while
  20261027 and 20261106 passed. tests.yml sets no MAPSEE_TODAY, so every push
  touching a .py file would have gone red. Pin it next to the test's TODAY
  constant before anything runs, as test_ingest_barcelona.py and
  test_ingest_drupal_fullcalendar.py do. A case that changes it restores the
  pin, never `del`s it. To prove the pin, fake the module's datetime.now past
  the fixtures' dates and run the script.

- **`--only` WAS A SUBSTRING, SO FIVE MATRIX JOBS PULLED TWO METROS.** The
  five pairs were Reno/GRENOBLE, Portland/Portland ME, Birmingham/Birmingham
  AL, Newcastle/Newcastle upon Tyne and Porto/Porto Alegre.
  - **What it cost.** On 2026-10-25 the Reno job (121138829093) printed
    "Grenoble: 5667 candidate(s) ... from 4000 examined". It started at
    Grenoble's cursor, prepared 4,494 rows for a metro of 718 candidates, and
    handed up only `{'Reno': 0}`. Grenoble was read whole while its committed
    cursor still said 4000.
  - **What changed.** `mapsee_ingest_osm_amenities.py --only` and the plan's
    `only` input are now exact and case-insensitive, and a name matching
    nothing is refused. `test_ingest_osm_amenities.py` (e) and
    `test_workflow_contracts.py` pin this.

- **A CACHE PAIR WHOSE FILE ONLY ONE KIND OF RUN WRITES: KEY IT BY THE FILE'S
  HASH, SAVE ONLY WHEN IT IS NOT THE COPY RESTORED (2026-10-27).**
  prune-cancelled.yml restores prune_url_index.json on every run (key
  prune-url-index-run_id, restore-keys prefix), but only the full sweep writes
  it. Following the repo's run_id idiom, the 12 near runs a week would
  re-upload Sunday's file under fresh keys. Saving a near run's own file would
  be worse: restore takes the NEWEST entry under the prefix, and that file
  would shadow Sunday's. The save key is therefore
  prune-url-index-${hashFiles(file)}, with `if: always() && hashFiles(file) !=
  '' && steps.url-index.outputs.cache-matched-key !=
  format('prune-url-index-{0}', hashFiles(file))`. Only a run that changed the
  file saves, and always() still saves a file written before a probe phase
  that ran into the timeout. test_workflow_contracts pins the pair, the
  prefix's uniqueness and which branch writes.

- **THE STATE CACHE IS ITS OWN ACTIONS/CACHE PAIR PER JOB, AFTER THE OTHERS,
  AND A FEED GROUP'S CHECKPOINT SHARES IT WITH ITS FINAL SYNC (2026-10-27).**
  The pairs are sync-state-ticketmaster-, sync-state-feeds-<group>-,
  sync-state-races- and sync-state-extra-, keyed <run_id>-<attempt>, restored
  by prefix and saved if: always(). Each store has one file, and the flag goes
  after `$ONLY_NEW $ABSENT`. Adding a path to an existing pair would change
  its version and restore none of it. A run over part of a store carries the
  rest of the file forward rather than pruning to its store: civic's
  checkpoint held 3,016 of 165,899 ids on 10-26, so pruning would have made
  the final sync read about 163k. Meetup and OpenActive have no state cache,
  because every record carries source_details and every stored row is
  must-read: on 10-26 they kept 23,665 of 23,684, 25,492 of 25,720 and 76,772
  of 77,729. The Meetup contract test also pins exactly 4 cache steps in that
  job. extra_sources' file is written by Thursday's refresh and read on
  Monday, 4 days later (10-26: 37,837 ids read, 4,745 kept).

- **CHAIN REPEATING LISTINGS LINKED NOTHING FOR AT LEAST 8 DAYS WHILE GREEN,
  AND NOW WAITS FOR vars.LINK_SERIES (2026-10-27).** Its one listing,
  fetch_events, pages every upcoming imported row (~680k) by OFFSET in id
  order, with no hidden, standing or pin filter. Page 1 died at the statement
  timeout (57014) 38-51 s into the step on 10-19, 10-20 and 10-22..10-26 (jobs
  118198402919, 118784501925, 119992354857, 120556942723, 120929673998,
  121228334481, 121758215988). On 10-21 it died at the 120 s ReadTimeout
  (119388758351). continue-on-error kept every run green. The step's `if` now
  starts with `vars.LINK_SERIES == 'true' &&`, for a schedule and a dispatch
  alike, and it stays continue-on-error. Do not set the variable until
  fetch_events is rewritten: a keyset walk on (starts_at, id), a 60-day
  horizon, and hidden_at=is.null&recurring_hours=is.null&pin_only=not.is.true,
  which ../mapsee 0241's events_cleanup_dated_idx can serve. Its docstring
  spells this out. test_workflow_contracts evaluates the step's `if` for every
  cron, for a dispatch and for every variable value.

- **THE MON/THU RUN RE-SENT EVERY LANDING PAGE 49-74 MINUTES AFTER THE DAILY
  RUN, AND NOW SENDS ONLY ITS EVENTS (2026-10-27).** All 11 cached IndexNow
  runs from 10-19..10-26 sent 3,739 /c/ URLs: 1,354 for mapsee.me plus 9 doors
  x 265. landing_urls() returns each host's whole sitemap-pages.xml, so the
  old log line 'whose listings changed this run' was never true. It now says
  'listed in its sitemap-pages.xml'. On 10-19, 10-22 and 10-26 the Mon/Thu
  run's indexnow job started 49, 74 and 52 min after the daily run's and sent
  the same set again. The Submit step now adds --no-pages only when
  github.event.schedule is '40 6 * * 1,4'. jobs.indexnow.if is still always()
  and its needs are unchanged, so the extra run still announces its own /e/
  rows. On a Mon/Thu when GitHub drops the daily run (seen 2026-10-05), the
  landing pages wait a day. This is hygiene, not measured capacity: nothing
  shows an engine fetching the pinged pages. The saving is 0 to ~1.2k SSR RPCs
  a day per engine that does. test_workflow_contracts reads the cron from
  on.schedule (the one extra_sources runs on) and renders the Submit command
  for each cron and for a dispatch.

- **THE WEEKLY AUDIT RAN FOUR HOURS AND KEPT NOTHING, SO FEED_DOWN READ AN
  AUDIT ELEVEN DAYS OLD (2026-10-25).** `source-health.yml`'s audit (run
  40179210416, job 121126419210) probed from 09:05:35 to 13:06:03Z and was
  cancelled at the job's 240 minutes, 311 entries into jsonld's 785: 5,094
  probes (localist 57 in 1.3 min, ics 2,931 in 108.3, tribe 1,501 in 110.2,
  jsonld 16.8). The snapshot copy sat at the end of the probe step, so it never
  ran; the commit step was skipped; the runner killed the python 0.25 s after
  "The operation was canceled." All 130 FEED_DOWN rows on 10-29 dated from
  10-18. The time was not where the probes were: the median took 0.69 s and
  p90 2.93 s, but 67 probes over 60 s (1.3%) took 133.4 of the 240 minutes,
  each a connect or read timeout asked three times at 60 s. The configs held
  6,601 entries on 10-29 (ics 3,372, tribe 1,779, jsonld 1,058). Now
  `audit --max-minutes 200 --workers 4` starts no probe after the deadline;
  goes in order of the ledger row's `checked`, never-checked first, across
  every file, so a cut run is where the next starts; saves every 200 probes
  (1.2-1.7 s a save on the 19.6 MB, 87,234-row ledger), on any way out, and
  aside-then-rename; and prints "audit reached N of M". An entry it does not
  reach keeps its old row. The probe step has `timeout-minutes: 215`, the
  snapshot is its own `always()` step and the commit is `always()`, skipping
  without a snapshot. Four workers, never two on one host (calendar.google.com
  carries 457 entries, montreal.ca 76): live 2026-10-29 on 128 robots-allowed
  entries (Google calendars and jsonld left out), 182.4 s against 459.1 s one
  at a time (2.52x, the parallel run first), the same 114 ok / 10 empty / 4
  fail in both. `test_curate_audit.py`.

- **THE AUDIT NOW WAITS OUT A HOST'S CRAWL-DELAY, SAVES THE MOMENT THE BUDGET
  IS SPENT, AND GIVES A STRAGGLER FIVE MINUTES (2026-10-29).** It asked a host
  as fast as its probes came back (the next probe as one ended, the CivicPlus
  list read right after the feed), though CivicPlus's stock robots.txt asks
  `Crawl-delay: 10` (35 of 35 sampled files read) and LibCal's 10 (leeds,
  smu-sg). Now a host's delay is the larger of its robots.txt Crawl-delay (read
  once per origin per run, before the host's first request; next note) and
  its entries' `crawl_delay`. The scheduler starts no probe on a host before its last
  request plus that delay and works on other hosts meanwhile; inside a probe (a
  retry, the CivicPlus list, The Events Calendar's REST test) the worker waits,
  at most 30 s, and a request that would need longer is not sent. A documented
  API is not asked for robots.txt. Projected from the 10-25 run's timings
  (5,083 of today's 6,581 entries with their own time there, the rest their
  type's mean; 312.8 min of probing): four workers, ideal scheduling, 80.9 min
  with no delay, 86.7 with CivicPlus and LibCal at 10 s plus entries'
  `crawl_delay` (23.1 worker-minutes waiting inside probes, 139 CivicPlus list
  reads and 153 REST tests), 102.1 if every multi-entry host asked 10 s.
  Times 4/2.52 (four workers measured 2.52x, not 4x): about 128, 138 and 162
  min, all inside 200, and what a run does not reach goes first next Sunday.
  The wait on running probes had no timeout, and on 10-25 the runner killed
  python 0.25 s after the cancel. Now the ledger is saved when the budget
  cuts, the running probes get `AUDIT_GRACE_S` = 300 s, a straggler is
  abandoned unrecorded (its old row stays; the workers are daemon threads, so
  it cannot hold python open), and both summary lines print in a `finally`,
  interrupted or not. 200 + 5 minutes sits inside the step's 215.
  `test_curate_audit.py`.

- **THE AUDIT OBEYS ROBOTS.TXT, LEAVES A ROW ALONE WHEN NO SERVER DECIDED IT,
  AND SKIPS A TYPE IT CANNOT VERIFY (2026-10-29).** The final review of the
  dead-feed job found three ways it wrote a verdict nothing measured. It read
  robots.txt for the Crawl-delay alone and fetched what the file disallows,
  filing it ok, empty or fail, never `refused` (round 2 found two by hand:
  OEC Korfbal's `Disallow: /`, Mathallen's `/*?*ical=`). Now every request
  the ingest adapter makes (`_ingest_requests`) is checked before the entry's
  first one: an explicit Disallow is `refused` with the rule, nothing
  fetched; an unreachable or challenged file is probed as before; a
  documented API is not asked; a Google calendar without the Calendar API
  key is not probed and keeps its row (robots.txt refuses its export; 459
  configured), as `verify` skips it. That reads robots.txt for every host:
  4,423 origins in the 6,531 planned entries on the final 10-29 configs, key
  set, where it was read up front only for the 488 hosts with more than one
  entry. At the 0.6 s a read the budget projection assumed, about 44
  worker-minutes, 11-18 minutes of a four-worker run on top of its 128-162;
  an estimate, not measured on a runner. A link
  page `verify_jsonld` would follow is skipped when disallowed and counted in
  the note; alone it fails nothing, and when every page sampled is disallowed
  the entry is `refused` with the rule (nothing was read, and the adapter asks
  robots.txt per page too). Every request is asked, not the first: a jsonld
  entry's second listing disallowed refuses it. The weekly commit counts a
  `[NO ]` line among those probed and the step summary lists them apart from
  the `[XX ]` ones, with the not-decided count; a `[.. ]` probe wrote no row
  and is not counted. A request the pacing will not send (a
  Crawl-delay over 30 s inside a probe) used to end as a `fail` no server
  gave: a CivicPlus list "unreadable" (and kept for the origin), "REST
  unreachable", a retry's "AuditCrawlDelay" in place of the 503. Now that
  probe is "not decided" and the entry keeps its row, like an abandoned one.
  And `mylisting` is in CONFIG with no verifier: its one entry's probe raised
  KeyError inside the worker and was recorded `fail`, so FEED_DOWN would have
  reported Bainbridge Island daily while production kept 556 of 592
  occurrences from it on 10-28. A type with no verifier is now left out of
  the plan, said once. FEED_DOWN reports a configured entry's `refused` row
  (0 on 10-29) as it does `fail`. `test_curate_audit.py`,
  `test_health_check.py`.
