#!/usr/bin/env bash
# Daily live-data refresh for Backboard on Linux/macOS: schedule, results and
# box scores from ESPN's public scoreboard, the injury report, a feature
# rebuild and the day's odds snapshot, into data/database/nba.db.
#
# Run by hand:   scripts/refresh_live_data.sh
# Run every day: crontab -e, then (06:00 every day)
#   0 6 * * * /path/to/Backboard/scripts/refresh_live_data.sh
#
# The database is local and untracked (about 1 GB), so this has to run on the
# machine (or volume) that holds data/. A running API server picks up the new
# data by itself; no restart is needed.
set -uo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"
python="$root/.venv/bin/python"
[ -x "$python" ] || python="python3"
log="$root/data/refresh.log"
echo "[$(date -Iseconds)] refresh start" >> "$log"
"$python" -m src.live_data refresh >> "$log" 2>&1
code=$?
echo "[$(date -Iseconds)] refresh exit $code" >> "$log"
exit $code
