# Daily live-data refresh for Backboard: pulls the NBA schedule and final
# scores from ESPN's public scoreboard into data/database/nba.db.
#
# Run by hand:      powershell -File scripts\refresh_live_data.ps1
# Run every day:    see "Daily refresh" in readme.md (Windows Task Scheduler).
#
# The database is local and untracked (about 1 GB), so this cannot run in
# GitHub Actions; it has to run on the machine that holds data/.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
$log = Join-Path $root "data\refresh.log"
"[$(Get-Date -Format s)] refresh start" | Add-Content $log
& $python -m src.live_data refresh 2>&1 | Add-Content $log
$code = $LASTEXITCODE
"[$(Get-Date -Format s)] refresh exit $code" | Add-Content $log
exit $code
