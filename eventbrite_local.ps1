# Run the Eventbrite API import from THIS machine and sync it to Supabase.
#
# Eventbrite now runs via the OFFICIAL API (organizer feeds), which works fine
# from CI too - so this local script is optional, handy for a quick manual run
# or testing new organizer ids. It reads organizers from eventbrite_organizers.json.
#
# ⚠️ This SYNCS TO PRODUCTION. Step 2 writes to live Supabase with the
# service-role key, which bypasses every row-level-security policy, and anything
# it inserts shows up on mapsee.me and all three lens domains immediately.
# `--only-new` keeps it to inserts (no updates, no deletes). To see what it
# WOULD do first, run just the ingest line by hand and read eventbrite_local.json
# before letting the sync run.
#
# One-time setup:
#   1) pip install requests tzdata timezonefinder
#   2) create .env.local NEXT TO THIS SCRIPT (git-ignored) with:
#        EVENTBRITE_API_TOKEN=<your private Eventbrite token>
#        SUPABASE_URL=https://<ref>.supabase.co
#        SUPABASE_SERVICE_ROLE_KEY=<service role key>
#        MAPSEE_HOST_PROFILE_ID=<the aggregator host profile uuid>
#      (a template with the keys already laid out is created for you the first
#       time this script runs — fill in the values and re-run)
#   3) add organizer ids to eventbrite_organizers.json (or set
#      include_my_organizations: true to pull the orgs your token owns)
#
# Then:  powershell -ExecutionPolicy Bypass -File eventbrite_local.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$Required = @("EVENTBRITE_API_TOKEN", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "MAPSEE_HOST_PROFILE_ID")

# First run: lay down a template rather than just complaining. Values are left
# as <PLACEHOLDER> so the check below still refuses to run - a half-filled
# .env.local must fail HERE, loudly, and not three steps later as a 401 from
# Supabase or a silent no-op ingest (mapsee_ingest_eventbrite.py exits 0 when
# the token is missing, so a bad env would otherwise look like success).
if (-not (Test-Path ".env.local")) {
    @(
        "# Local-run secrets for eventbrite_local.ps1 - GIT-IGNORED, never commit.",
        "# Fill in the three <PLACEHOLDER> values, then re-run the script.",
        "",
        "# Eventbrite -> Account Settings -> Developer Links -> API Keys -> your private token",
        "EVENTBRITE_API_TOKEN=<PLACEHOLDER>",
        "",
        "# Public project URL (same one shipped in site/js/config.js) - already filled in.",
        "SUPABASE_URL=https://sjdcamppswwhwecheran.supabase.co",
        "",
        "# Supabase -> Project Settings -> API -> service_role. NOT the anon key.",
        "# This bypasses ALL row-level security. Treat it like a root password.",
        "SUPABASE_SERVICE_ROLE_KEY=<PLACEHOLDER>",
        "",
        "# profiles.id of the aggregator host account (used as created_by on every",
        "# event this imports). Find it in the Supabase table editor.",
        "MAPSEE_HOST_PROFILE_ID=<PLACEHOLDER>"
    ) | Set-Content ".env.local" -Encoding utf8
    Write-Host "Created a template .env.local next to this script." -ForegroundColor Yellow
    Write-Host "Fill in the <PLACEHOLDER> values, then run this again." -ForegroundColor Yellow
    exit 1
}

Get-Content ".env.local" | ForEach-Object {
    if ($_ -match "^\s*([A-Z0-9_]+)\s*=\s*(.+?)\s*$") {
        [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], "Process")
    }
}

$Unset = $Required | Where-Object {
    $v = [Environment]::GetEnvironmentVariable($_, "Process")
    (-not $v) -or ($v -match "^<.*>$")
}
if ($Unset) {
    Write-Host "These are still unset or left as a placeholder in .env.local:" -ForegroundColor Red
    $Unset | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    Write-Host "Nothing has been read or written. Fill them in and re-run." -ForegroundColor Red
    exit 1
}

python mapsee_ingest_eventbrite.py --config eventbrite_organizers.json --store eventbrite_local.json
if ($LASTEXITCODE -ne 0) { Write-Host "ingest failed" -ForegroundColor Red; exit 1 }

python mapsee_supabase_sync.py --store eventbrite_local.json --only-new
if ($LASTEXITCODE -ne 0) { Write-Host "sync failed" -ForegroundColor Red; exit 1 }

Write-Host "Eventbrite import synced." -ForegroundColor Green
