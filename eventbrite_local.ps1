# Run the Eventbrite API import from THIS machine and sync it to Supabase.
#
# Eventbrite now runs via the OFFICIAL API (organizer feeds), which works fine
# from CI too - so this local script is optional, handy for a quick manual run
# or testing new organizer ids. It reads organizers from eventbrite_organizers.json.
#
# One-time setup:
#   1) pip install requests tzdata timezonefinder
#   2) create backend/aggregator/.env.local (git-ignored) with:
#        EVENTBRITE_API_TOKEN=<your private Eventbrite token>
#        SUPABASE_URL=https://<ref>.supabase.co
#        SUPABASE_SERVICE_ROLE_KEY=<service role key>
#        MAPSEE_HOST_PROFILE_ID=<the aggregator host profile uuid>
#   3) add organizer ids to eventbrite_organizers.json (or set
#      include_my_organizations: true to pull the orgs your token owns)
#
# Then:  powershell -ExecutionPolicy Bypass -File eventbrite_local.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".env.local")) {
    Write-Host "Missing .env.local - see the header of this script for the required lines." -ForegroundColor Red
    exit 1
}
Get-Content ".env.local" | ForEach-Object {
    if ($_ -match "^\s*([A-Z0-9_]+)\s*=\s*(.+?)\s*$") {
        [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], "Process")
    }
}

python mapsee_ingest_eventbrite.py --config eventbrite_organizers.json --store eventbrite_local.json
if ($LASTEXITCODE -ne 0) { Write-Host "ingest failed" -ForegroundColor Red; exit 1 }

python mapsee_supabase_sync.py --store eventbrite_local.json --only-new
if ($LASTEXITCODE -ne 0) { Write-Host "sync failed" -ForegroundColor Red; exit 1 }

Write-Host "Eventbrite import synced." -ForegroundColor Green
