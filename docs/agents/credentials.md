# Credentials

> Part of mapsee-aggregator's agent notes — see `AGENTS.md` for the map. Read this file when the bug is about: a job needs a secret.
> Every note below was measured before it was written; keep the numbers when you edit.

`.env.example` lists all 38 variables. Nothing in this repo should ever hold a
real key; CI supplies them as secrets. `SUPABASE_SERVICE_ROLE_KEY` bypasses RLS —
treat any workflow that reads it as privileged.

`GOOGLE_CALENDAR_API_KEY` (added 2026-09-30) is the one credential here that
changes what the pipeline is ALLOWED to read, not only what it can: with it,
the Google calendars in `ics_sources.json` are read through the Calendar API,
whose host serves no robots.txt, instead of the iCal export that
calendar.google.com/robots.txt refuses. Restrict the key to the Google Calendar
API in the Cloud console. An application restriction by IP cannot work, because
Actions runners have no fixed address. `mapsee_gcal.py` sends it in the
`X-Goog-Api-Key` header, so it never appears in a URL, an error or a log.
`smoke-gcal.yml` (dispatch) proves a new key in about a minute.
