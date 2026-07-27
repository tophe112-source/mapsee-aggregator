# Scheduled wrapper around eventbrite_local.ps1.
#
# WHY THIS EXISTS: Eventbrite blocks GitHub Actions IP ranges from the public
# discovery pages the adapter reads event ids from (the CI job logs
# "HTTP 405 - discovery blocked from this IP"). The official API hydration works
# fine from anywhere, but without discovery CI can only see events belonging to
# your own token's organizations. So the metro sweep has to run from a normal
# residential connection - i.e. this machine, on a schedule.
#
# Registered as the Scheduled Task "mapsee-eventbrite-sync" (see README).
# Reads .env.local exactly like the manual script, writes a timestamped log,
# and keeps the last 20 runs so a silent failure is still visible days later.
#
# Run by hand:  powershell -ExecutionPolicy Bypass -File eventbrite_scheduled.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$logDir = Join-Path $env:LOCALAPPDATA "mapsee-eventbrite"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ((Get-Date -Format "yyyyMMdd-HHmmss") + ".log")

"=== mapsee eventbrite sync $(Get-Date -Format s) ===" | Tee-Object -FilePath $log

try {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "eventbrite_local.ps1") *>&1 |
        Tee-Object -FilePath $log -Append
    $code = $LASTEXITCODE
} catch {
    "FAILED: $_" | Tee-Object -FilePath $log -Append
    $code = 1
}

"exit code: $code" | Tee-Object -FilePath $log -Append

# keep the last 20 logs, drop the rest
Get-ChildItem $logDir -Filter *.log |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 20 |
    Remove-Item -Force -ErrorAction SilentlyContinue

exit $code
