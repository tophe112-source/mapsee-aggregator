# CI: the workflows, budgets, timeouts, job order and what a guarded job needs

> Part of mapsee-aggregator's agent notes — see `AGENTS.md` for the map. Read this file when the bug is about: a job that worked for an hour and lost its work, `timeout-minutes`, `always()`, `--only-new` in CI, the order of the daily jobs, secrets, a config file a job needs.
> Every note below was measured before it was written; keep the numbers when you edit.

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
  `test_ingest_parkrun.py` now asserts every guarded source config is present.

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
  the ends that make a past row permanent) → 08:23 cleanup (deletes what has
  finished, which now includes everything the purge un-dated an hour earlier).
  Most of what the purge touches is therefore removed by the EXISTING janitor
  rather than by a delete of its own. Move the purge after cleanup and every
  un-dated row waits a full extra day.

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
