# Source health, baselines and quiet sources

> Part of mapsee-aggregator's agent notes — see `AGENTS.md` for the map. Read this file when the bug is about: the health check and `stats_snapshot_all`, `external_source`, a green run with no baseline, a source with no retry.
> Every note below was measured before it was written; keep the numbers when you edit.

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
