# Credentials

> Part of mapsee-aggregator's agent notes — see `AGENTS.md` for the map. Read this file when the bug is about: a job needs a secret.
> Every note below was measured before it was written; keep the numbers when you edit.

`.env.example` lists all 36 variables. Nothing in this repo should ever hold a
real key; CI supplies them as secrets. `SUPABASE_SERVICE_ROLE_KEY` bypasses RLS —
treat any workflow that reads it as privileged.
