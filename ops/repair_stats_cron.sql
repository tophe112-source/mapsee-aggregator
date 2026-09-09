-- Run as postgres in the Mapsee Supabase SQL editor after inspecting the job.
-- Applied 2026-09-09: job 4 was active every six hours but its latest three
-- executions each failed at 120 seconds. Core snapshots were 313 hours old.
-- This changes only the existing job's command; schedule and owner stay intact.
-- PostgreSQL starts a statement's timer before invoking a function. SET must
-- precede the refresh statement, not be performed inside refresh_stats().
select j.jobid, j.jobname, j.schedule, j.active, j.command,
       d.status, d.return_message, d.start_time, d.end_time
from cron.job j
left join lateral (
    select status, return_message, start_time, end_time
    from cron.job_run_details d where d.jobid = j.jobid
    order by start_time desc limit 3
) d on true
where j.jobname = 'mapsee-refresh-stats';

-- Guard against overwriting a command somebody has independently changed.
select cron.alter_job(jobid, command := $cmd$
set statement_timeout = '10min'; select public.refresh_stats(30);
$cmd$)
from cron.job
where jobname = 'mapsee-refresh-stats' and active
  and regexp_replace(command, '\s+', '', 'g') = 'selectpublic.refresh_stats(30);';

-- Optional one-time catch-up: run these two statements together. A dashboard
-- gateway error is not proof of rollback; inspect computed_at before retrying.
-- set statement_timeout = '10min'; select public.refresh_stats(30);

select jobid, jobname, schedule, active, command
from cron.job where jobname = 'mapsee-refresh-stats';
select kind, computed_at from public.stats_snapshot
where kind in ('overview', 'sources', 'categories');

-- The related venue sitemap job also failed at 120s on 2026-09-09. Inspect it
-- before applying the same bounded fix; preserve its daily 04:41 schedule.
select jobid, jobname, schedule, active, command from cron.job
where jobname = 'mapsee-refresh-venue-sitemap';
select cron.alter_job(jobid, command := $cmd$
set statement_timeout = '10min'; select public.refresh_venue_sitemap();
$cmd$)
from cron.job
where jobname = 'mapsee-refresh-venue-sitemap' and active
  and regexp_replace(command, '\s+', '', 'g') = 'selectpublic.refresh_venue_sitemap();';
-- Optional catch-up, followed by readback:
-- set statement_timeout = '10min'; select public.refresh_venue_sitemap();
select kind, computed_at from public.stats_snapshot where kind = 'cron:venue_sitemap';
