# Source health, baselines and quiet sources

> Part of mapsee-aggregator's agent notes — see `AGENTS.md` for the map. Read this file when the bug is about: the health check and `stats_snapshot_all`, `external_source`, a green run with no baseline, a source with no retry.
> Every note below was measured before it was written; keep the numbers when you edit.

- **An active pg_cron job can still inherit the wrong timeout.** On 2026-09-09,
  `mapsee-refresh-stats` (job 4) was active at `23 */6 * * *`, but its last three
  runs each failed after 120s inside the category aggregate. The source snapshot
  was 313h old, and the latest 15 health runs could not evaluate ingestion.
  `ops/repair_stats_cron.sql` records the inspected command and guarded repair:
  set a bounded 10m statement timeout BEFORE calling `refresh_stats(30)` in the
  cron command, preserving schedule/owner. The one-time catch-up's SQL-editor
  request eventually showed a gateway error, but the public snapshot RPC proved
  all three core rows advanced to `2026-09-09T13:35:31Z`. Check database state
  before retrying an ambiguous UI response. The next health run evaluated
  sources instead of failing on stale statistics.
  The venue sitemap's daily job 2 had the same 120s failure; its guarded command
  repair and catch-up advanced `cron:venue_sitemap` from August 30 to September 9
  at `13:48:28Z`, also verified through the public snapshot RPC.

- **Community posting activity is not an ingest schedule.** After the snapshot
  repair, run `34358782959` had one finding: `community` had no new event for
  11 days against an 8-day ingest budget. SQL defines that bucket as NULL
  `external_source`, so it measures people posting, not a scheduled importer.
  The exact bucket stays visible in totals/the full table but is excluded from
  baseline SILENT/MISSING/DRAINED alarms. Imported sources still fail those
  checks. The 57 health assertions cover both cases without resetting baseline.

- **The health check reads `stats_snapshot_all()`, NOT `source_stats()`.** The
  latter aggregates `public.events` on read and cannot finish inside the API
  role's ~3s statement timeout — ../mapsee migration 0112 retired it for exactly
  that and moved the product onto a snapshot a cron computes. The aggregator kept
  calling it and failed its first eight runs, reporting a Postgres 57014 as
  "one or more sources have gone quiet". Two rules fell out and both are now
  tested (`test_health_check.py`): read what the product reads, and put the
  server's own error body in the report — a status code alone diagnoses nothing.

- **A source in the health report is `external_source`, which is `'mapsee'` for
  everything this repo writes.** So it sees the aggregator as ONE bucket: it can
  tell you the pipeline stopped, never that the Meetup adapter did. No per-
  adapter provenance is persisted (`external_id` is a bare fingerprint hash).
  `FEED_DOWN`, from the curator audit, is the per-feed signal.

- **A green source-health run with no baseline means "no opinion", not
  "healthy".** SILENT / MISSING / DRAINED all compare against
  `source_health_baseline.json`; without the file the job passes having evaluated
  nothing, which is what it did on every run where the RPC answered. CI now
  passes `--seed-baseline` and commits the result.

- **A source that runs one day a week has no retry, and nothing notices.** The
  OSM Overpass sweep is 245 calls, so it carries `run_weekdays` — and with `[0]`
  that was a single window per week, inside the longest job in the repo. Monday
  2026-08-10 the `feeds` job hit its 120-minute timeout, and the entire
  international market catalogue was absent for the week: Berlin's 11 OSM
  Flohmärkte sat in OSM with parseable `opening_hours` inside a configured
  bbox and never reached the table. Nothing reported it and nothing could — the
  health check sees `external_source='mapsee'` as one bucket, so it can say the
  pipeline stopped but never that ONE source did. Now `[0, 4]`, so a lost run
  costs three days rather than seven.
