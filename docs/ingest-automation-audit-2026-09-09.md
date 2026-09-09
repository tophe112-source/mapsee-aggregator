# Mapsee ingest automation audit — 2026-09-09

Reviewed all 20 active GitHub workflows, adapter wiring (44 entry-point files),
recent scheduled runs, failure logs, and the database statistics scheduler.
Pure workflow/adapter tests run without production credentials.

## Verified

- [Festival run 34356898437](https://github.com/tophe112-source/mapsee-aggregator/actions/runs/34356898437): successful extraction and database readback, 1 festival, 24 agenda items, 12 stages, 0 configured-source failures. Public readback retained agenda revision 2. Worldwide discovery advanced to 53 candidates; unsupported schedules remain pending review. This was a manual verification; the twice-daily schedule remains enabled.
- [Tests 34359289528](https://github.com/tophe112-source/mapsee-aggregator/actions/runs/34359289528): full Linux integration gate passed after the workflow and health fixes.
- [OSM food 34358786392](https://github.com/tophe112-source/mapsee-aggregator/actions/runs/34358786392): targeted Seattle run completed planning, import/sync, area-cache saving, cursor artifact handoff and committed cursor update.
- [Source health 34359346785](https://github.com/tophe112-source/mapsee-aggregator/actions/runs/34359346785): passed; fresh statistics report 373,906 upcoming events. `community` remains visible but no longer triggers an ingest alarm when people have not posted.

## Repairs

- Split the serial feed job into ICS/local/civic groups. Yesterday's job hit 300 minutes after spending 223m34s in ICS, leaving six later adapters and final sync skipped.
- Persist ICS progress after every attempted source; save group caches and recovery artifacts even after failure. Report tolerated process failures and fail the job after retaining successful work.
- Remove the OSM food warm step that spent 45 minutes without completing Portland. Each area owns a rolling cache; requests/retries obey the wall-clock budget. Retry failed cursor artifact uploads.
- Reapply weekly audit ledger updates onto the current branch and retry competing pushes.
- Run the extra-source refresh on Thursday, matching its actual schedule. Refresh OSM food/second-hand rows during cursor rotation, preserving claimed-owner edits.
- Repair the existing statistics cron command to establish a bounded 10-minute timeout before invoking its refresh. Previous runs failed at exactly 120 seconds and left the snapshot 313 hours stale. One-time catch-up advanced all core snapshots to September 9, verified through the public RPC.

## Operational limits and follow-up

- [Civic recovery 34358778922](https://github.com/tophe112-source/mapsee-aggregator/actions/runs/34358778922) is a full live recovery run; inspect its final outcome and attached per-step report. Its new matrix dispatch/routing was verified, but a whole scheduled cycle has not yet elapsed since release.
- The health snapshot groups all imported adapters under `mapsee`; it cannot individually prove every feed is healthy. Weekly feed probes and the new per-process reports provide the finer-grained evidence. A source returning zero with exit code 0 still requires those probes.
- Venue sitemap refresh had the same 120-second timeout (last successful snapshot August 30). Its existing daily cron command was repaired with the same bounded timeout; verify its catch-up and next scheduled run.
- A separate product notification job, `mapsee-follow-announce`, still contains a `<PROJECT_REF>` placeholder and fails hourly. It is outside event ingestion and was not enabled or changed as part of this audit.
- OSM place caches retain their 30-day TTL. Worldwide festival discovery does not mean every arbitrary HTML or PDF timetable is supported.

The guarded database repair and readback queries are in `ops/repair_stats_cron.sql`.
