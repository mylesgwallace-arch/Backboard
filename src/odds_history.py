"""Daily snapshots of every team's playoff and title odds, so they can be charted.

Each snapshot is one ``playoffs.season_playoff_odds`` run (the same
rest-of-season + bracket simulation the Playoffs page shows) from a cutoff
date, stored per team in the ``odds_history`` table. The daily refresh takes
one snapshot after it rebuilds the features; ``backfill`` replays earlier
dates of a season from the data as it stood on each date (results and
strength strictly before the cutoff), so a backfilled point is what the
engine would have said that morning -- not a record of a past run.

Usage::

    python -m src.odds_history snapshot --season 2026
    python -m src.odds_history backfill --season 2025 --every 7
"""

import argparse
import sqlite3
from datetime import datetime, timezone

import pandas as pd

try:
    from src.main import TEAM_DB_PATH
    from src.forward_projection import load_model_inputs, season_schedule
    from src.playoffs import season_playoff_odds
    from src.simulate_season import load_team_names
except ImportError:  # pragma: no cover - direct-script support
    from main import TEAM_DB_PATH
    from forward_projection import load_model_inputs, season_schedule
    from playoffs import season_playoff_odds
    from simulate_season import load_team_names


SNAPSHOT_SIMULATIONS = 1000
SCHEMA = """
CREATE TABLE IF NOT EXISTS odds_history (
    season INTEGER NOT NULL,
    as_of TEXT NOT NULL,
    teamId INTEGER NOT NULL,
    p_made_playoffs REAL,
    p_play_in REAL,
    p_won_conf_finals REAL,
    p_champion REAL,
    mean_wins REAL,
    games_completed INTEGER,
    n_simulations INTEGER NOT NULL,
    model TEXT NOT NULL,
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (season, as_of, teamId)
)
"""


def ensure_schema(conn):
    conn.execute(SCHEMA)


def snapshot(season, as_of, inputs, db_path=TEAM_DB_PATH, n_simulations=SNAPSHOT_SIMULATIONS,
             kind="daily", random_state=42):
    """Simulate odds from ``as_of`` and store (replace) that date's rows. Returns rows written."""
    as_of = str(pd.Timestamp(as_of).date())
    result = season_playoff_odds(season, as_of, inputs, n_simulations=n_simulations,
                                 random_state=random_state, team_names=load_team_names(season, db_path))
    created = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    rows = [
        (int(season), as_of, int(team["teamId"]), team.get("p_made_playoffs"), team.get("p_play_in"),
         team.get("p_won_conf_finals"), team.get("p_champion"), team.get("mean_wins"),
         result.get("games_completed"), int(n_simulations), result.get("model", "elo_boosted_ensemble"),
         kind, created)
        for team in result["teams"]
    ]
    conn = sqlite3.connect(db_path)
    try:
        ensure_schema(conn)
        conn.execute("DELETE FROM odds_history WHERE season = ? AND as_of = ?", (int(season), as_of))
        conn.executemany(
            "INSERT INTO odds_history VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        conn.commit()
    finally:
        conn.close()
    return len(rows)


def backfill(season, inputs, every=7, db_path=TEAM_DB_PATH, n_simulations=SNAPSHOT_SIMULATIONS,
             through=None):
    """Snapshot every ``every`` days from the season's first game to its last (or ``through``)."""
    schedule = season_schedule(inputs, season)
    first = schedule["gameDateTimeEst"].min().normalize()
    last = schedule["gameDateTimeEst"].max().normalize()
    if through is not None:
        last = min(last, pd.Timestamp(through).normalize())
    dates = list(pd.date_range(first, last, freq=f"{int(every)}D"))
    if dates and dates[-1] != last:
        dates.append(last)
    written = 0
    for day in dates:
        written += snapshot(season, day, inputs, db_path, n_simulations, kind="backfill")
    return {"season": int(season), "snapshots": len(dates), "rows": written}


def history(season, db_path=TEAM_DB_PATH, team_id=None):
    """Stored snapshots of a season: one series per team, oldest first."""
    conn = sqlite3.connect(db_path)
    try:
        exists = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='odds_history'").fetchone()[0]
        if not exists:
            return {"season": int(season), "dates": [], "teams": []}
        query = ("SELECT as_of, teamId, p_made_playoffs, p_play_in, p_won_conf_finals, p_champion, "
                 "mean_wins, games_completed, kind FROM odds_history WHERE season = ?")
        params = [int(season)]
        if team_id is not None:
            query += " AND teamId = ?"
            params.append(int(team_id))
        frame = pd.read_sql_query(query + " ORDER BY as_of, teamId", conn, params=params)
    finally:
        conn.close()
    names = load_team_names(season, db_path)
    teams = []
    for team, group in frame.groupby("teamId", sort=False):
        info = names.get(int(team), {})
        teams.append({
            "teamId": int(team),
            "teamName": info.get("teamName", str(team)),
            "abbreviation": info.get("teamAbbreviation", ""),
            "points": [
                {"as_of": row.as_of, "p_made_playoffs": _round(row.p_made_playoffs),
                 "p_play_in": _round(row.p_play_in), "p_won_conf_finals": _round(row.p_won_conf_finals),
                 "p_champion": _round(row.p_champion), "mean_wins": _round(row.mean_wins, 1),
                 "games_completed": None if pd.isna(row.games_completed) else int(row.games_completed),
                 "kind": row.kind}
                for row in group.itertuples(index=False)
            ],
        })
    latest = {t["teamId"]: t["points"][-1]["p_champion"] or 0 for t in teams}
    teams.sort(key=lambda t: -latest[t["teamId"]])
    return {"season": int(season), "dates": sorted(frame["as_of"].unique().tolist()), "teams": teams}


def _round(value, digits=4):
    return None if value is None or pd.isna(value) else round(float(value), digits)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Store playoff/title odds snapshots for charts.")
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("snapshot", help="One snapshot from a date (default today).")
    one.add_argument("--season", type=int, required=True)
    one.add_argument("--as-of", default=None)
    many = sub.add_parser("backfill", help="Snapshots every N days through a season.")
    many.add_argument("--season", type=int, required=True)
    many.add_argument("--every", type=int, default=7)
    many.add_argument("--through", default=None)
    for command in (one, many):
        command.add_argument("--simulations", type=int, default=SNAPSHOT_SIMULATIONS)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    inputs = load_model_inputs()
    if args.command == "snapshot":
        as_of = args.as_of or str(pd.Timestamp.today().date())
        rows = snapshot(args.season, as_of, inputs, n_simulations=args.simulations)
        print(f"Stored {rows} team rows for {args.season} as of {as_of}.")
    else:
        through = args.through or str(pd.Timestamp.today().date())
        report = backfill(args.season, inputs, args.every, n_simulations=args.simulations, through=through)
        print(f"Stored {report['snapshots']} snapshots ({report['rows']} rows) for {args.season}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
