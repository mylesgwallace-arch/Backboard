# One-command setup (and start) for Backboard on Windows.
#
#   Double-click setup.bat in the repo folder, or from PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1 [options]
#
# Every step is safe to repeat; a second run only does what is missing:
#   1. update the code (git pull), keeping your local edits (they are stashed
#      and put back; if they clash, they stay in the stash and it says so)
#   2. create .venv and install requirements.txt (again only when it changes)
#   3. check the database (data\database\nba.db); -TestData builds a made-up
#      league instead, so the site runs without the real download
#   4. build the model inputs (data\processed\game_features.csv) if missing
#      or older than the database
#   5. fit the injury overlay and the player-forecast ranges if missing
#   6. register the daily data refresh in Task Scheduler (06:00)
#   7. start the server and open http://127.0.0.1:8000 (Ctrl+C stops it)
#
# Options: -NoUpdate -NoSchedule -NoStart -TestData -Port 8000

param(
    [switch]$NoUpdate,
    [switch]$NoSchedule,
    [switch]$NoStart,
    [switch]$TestData,
    [int]$Port = 8000
)

# "Continue", not "Stop": Windows PowerShell 5.1 turns a native command's
# redirected stderr into a terminating error under "Stop". Every step checks
# $LASTEXITCODE itself instead.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Step($text) { Write-Host "`n== $text" -ForegroundColor Yellow }
function Ok($text) { Write-Host "   $text" -ForegroundColor Green }
function Note($text) { Write-Host "   $text" }
function Fail($text) { Write-Host "   $text" -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------------------
Step "1/7 Code"
$git = Get-Command git -ErrorAction SilentlyContinue
if ($NoUpdate) {
    Note "Skipped (-NoUpdate)."
} elseif (-not $git -or -not (Test-Path (Join-Path $root ".git"))) {
    Note "Not a git checkout (or git is not installed); skipping the update."
} else {
    $branch = (git rev-parse --abbrev-ref HEAD).Trim()
    $upstream = git rev-parse --abbrev-ref --symbolic-full-name "@{u}" 2>$null
    if (-not $upstream) {
        Note "Branch '$branch' has no upstream; skipping the update."
    } else {
        $dirty = git status --porcelain --untracked-files=no
        $stashed = $false
        if ($dirty) {
            git stash push -m "backboard-setup $(Get-Date -Format s)" | Out-Null
            $stashed = $true
            Note "Set aside your local edits: $($dirty -join ', ')"
        }
        git pull --ff-only
        if ($LASTEXITCODE -ne 0) {
            if ($stashed) { git stash pop | Out-Null }
            Fail "git pull failed (see above; usually your branch has its own commits). Merge by hand, then run setup again."
        }
        if ($stashed) {
            git stash pop
            if ($LASTEXITCODE -ne 0) {
                Write-Host "   Your edits clash with the update. The update is in; your edits are kept in the stash." -ForegroundColor Red
                Write-Host "   To look at your edits:                           git stash show -p"
                Write-Host "   To keep the updated files and drop your edits:   git reset --hard ; git stash drop"
                exit 1
            }
            Ok "Updated, and your local edits are back."
        } else {
            Ok "Up to date with $upstream."
        }
    }
}

# ---------------------------------------------------------------------------
Step "2/7 Python and packages"
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    $base = $null
    $candidates = @(@{ Exe = "py"; Pre = @("-3") }, @{ Exe = "python"; Pre = @() })
    foreach ($candidate in $candidates) {
        if (-not (Get-Command $candidate.Exe -ErrorAction SilentlyContinue)) { continue }
        $pyArgs = $candidate.Pre + @("-c", "import sys; print(sys.version_info >= (3, 11))")
        $answer = & $candidate.Exe @pyArgs 2>$null
        if ("$answer".Trim() -eq "True") { $base = $candidate; break }
    }
    if (-not $base) {
        Fail "Python 3.11 or newer was not found. Install it (winget install Python.Python.3.13, or python.org), then run setup again."
    }
    Note "Creating .venv with $($base.Exe) $($base.Pre -join ' ')..."
    $pyArgs = $base.Pre + @("-m", "venv", ".venv")
    & $base.Exe @pyArgs
    if ($LASTEXITCODE -ne 0) { Fail "Could not create .venv." }
}
$requirements = Join-Path $root "requirements.txt"
$stamp = Join-Path $root ".venv\requirements.sha256"
$hash = (Get-FileHash $requirements -Algorithm SHA256).Hash
if (-not (Test-Path $stamp) -or (Get-Content $stamp -Raw).Trim() -ne $hash) {
    Note "Installing packages (a few minutes the first time)..."
    & $venvPython -m pip install --disable-pip-version-check -q -r $requirements
    if ($LASTEXITCODE -ne 0) { Fail "pip install failed (see above)." }
    Set-Content $stamp $hash
}
Ok (& $venvPython -c "import sys; print('Python', sys.version.split()[0], 'with the project packages')")

# ---------------------------------------------------------------------------
Step "3/7 Database"
$db = Join-Path $root "data\database\nba.db"
if ($TestData) {
    & $venvPython scripts\make_test_league.py --out $db
    if ($LASTEXITCODE -ne 0) { Fail "Could not build the test league (an existing real database is never replaced)." }
    Ok "Built a made-up test league in data\database\nba.db."
} elseif (-not (Test-Path $db)) {
    Write-Host "   data\database\nba.db is missing." -ForegroundColor Red
    Write-Host "   Put the NBA database there (the Kaggle 'NBA Dataset: Box Scores and Stats' export loaded with"
    Write-Host "   src\load_data.py), or run setup with -TestData to try the site on a made-up league."
    exit 1
} else {
    Ok ("Found the database ({0:N0} MB)." -f ((Get-Item $db).Length / 1MB))
}

# ---------------------------------------------------------------------------
Step "4/7 Model inputs"
$features = Join-Path $root "data\processed\game_features.csv"
if (-not (Test-Path $features) -or (Get-Item $features).LastWriteTime -lt (Get-Item $db).LastWriteTime.AddDays(-1)) {
    Note "Building data\processed\game_features.csv (a few minutes)..."
    & $venvPython src\build_features.py | Select-Object -Last 2
    if ($LASTEXITCODE -ne 0) { Fail "Building the features failed." }
}
Ok "Model inputs ready."

# ---------------------------------------------------------------------------
Step "5/7 Injury overlay and player forecasts"
if (-not (Test-Path (Join-Path $root "models\game_context_model.json"))) {
    Note "Fitting the injury and schedule overlay (about a minute)..."
    & $venvPython -m src.game_context fit | Out-Null
    if ($LASTEXITCODE -ne 0) { Note "Could not fit it now; the site fits it on first use." }
}
if (-not (Test-Path (Join-Path $root "models\player_forecast_backtest.json"))) {
    Note "Backtesting the player forecasts (about a minute)..."
    & $venvPython -m src.player_forecast backtest | Out-Null
    if ($LASTEXITCODE -ne 0) { Note "Could not backtest now; forecasts use default ranges until it runs." }
}
Ok "Done (the daily refresh refits both once a week)."

# ---------------------------------------------------------------------------
Step "6/7 Daily refresh"
$taskName = "Backboard live refresh"
if ($NoSchedule) {
    Note "Skipped (-NoSchedule)."
} else {
    schtasks /Query /TN $taskName 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Ok "Already scheduled ('$taskName', daily)."
    } else {
        $script = Join-Path $root "scripts\refresh_live_data.ps1"
        schtasks /Create /SC DAILY /ST 06:00 /TN $taskName /TR "powershell -NoProfile -ExecutionPolicy Bypass -File `"$script`"" /F | Out-Null
        if ($LASTEXITCODE -eq 0) { Ok "Scheduled '$taskName' for 06:00 every day (log: data\refresh.log)." }
        else { Note "Could not create the scheduled task; run scripts\refresh_live_data.ps1 by hand instead." }
    }
}

# ---------------------------------------------------------------------------
Step "7/7 Start"
if ($NoStart) {
    Ok "Setup finished. Start later with: .venv\Scripts\python src\api.py"
    exit 0
}
$url = "http://127.0.0.1:$Port"
Note "Starting the server at $url (Ctrl+C to stop)..."
Start-Job -ScriptBlock {
    param($health, $url)
    for ($i = 0; $i -lt 60; $i++) {
        try { Invoke-WebRequest -UseBasicParsing $health -TimeoutSec 2 | Out-Null; Start-Process $url; return } catch { Start-Sleep 1 }
    }
} -ArgumentList "$url/health", $url | Out-Null
& $venvPython src\api.py --host 127.0.0.1 --port $Port
