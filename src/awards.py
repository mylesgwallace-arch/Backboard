"""Season awards race: MVP, Rookie of the Year, Sixth Man and Most Improved.

Every award here ranks players on the same measure: **value added**, the
points of team margin a player's box score is worth above a replacement-level
player (``game_context``'s fitted box rating), summed over the games played.
Because it adds up game by game, the race can be replayed week by week for a
trend line.

* **MVP index**: value added, weighted by the team's win percentage
  (``0.5 + win%``: voters reward winning), with a flag for players not on
  pace for the 65 games the award requires.
* **Rookie of the Year**: value added among players whose first NBA game is
  this season.
* **Sixth Man**: value added among players who started fewer than half of
  their games (and played at least a fifth of the team's games).
* **Most Improved**: the rise in value added per game over the player's
  previous season (at least 20 games in each, 15+ minutes a game now).

These are indexes from box scores, not a model of how voters vote: the box
score misses most defence, and narrative and team context sway real ballots.
"""

import sqlite3

import numpy as np
import pandas as pd

try:
    from src.main import TEAM_DB_PATH
    from src import game_context, league_views
except ImportError:  # pragma: no cover - direct-script support
    from main import TEAM_DB_PATH
    import game_context
    import league_views


AWARDS = {
    "mvp": "Most Valuable Player",
    "roy": "Rookie of the Year",
    "sixth": "Sixth Man of the Year",
    "mip": "Most Improved Player",
}
MVP_GAMES = 65
SEASON_GAMES = 82
SHOWN = 10
TREND_PLAYERS = 5
SIXTH_START_SHARE = 0.5
SIXTH_GAME_SHARE = 0.2
MIP_MIN_GAMES = 20
MIP_MIN_MPG = 15.0


def _season_rows(season, through, model, db_path):
    start, end = league_views.season_window(season)
    end = min(pd.Timestamp(end), pd.Timestamp(through) + pd.Timedelta(days=1)) if through else end
    rows = game_context.load_player_games(db_path, start, end)
    rows = rows[rows["gameId"].isin(_regular_season_ids(db_path, start, end))]
    if rows.empty:
        return rows
    rating = model["box_rating"]
    beta = np.array([rating["coefficients"][stat] for stat in game_context.BOX_STATS])
    per_minute = (rating["intercept_per_slot"] - rating["replacement"]) / 48.0
    rows = rows.copy()
    rows["value"] = rows[game_context.BOX_STATS].to_numpy(dtype=float) @ beta + rows["numMinutes"] * per_minute
    return rows


def _regular_season_ids(db_path, start, end):
    with sqlite3.connect(db_path) as connection:
        return {int(r[0]) for r in connection.execute(
            "SELECT gameId FROM games WHERE gameType = 'Regular Season' AND gameDateTimeEst >= ? "
            "AND gameDateTimeEst < ?", (str(start), str(end))).fetchall()}


def _starts(db_path, season, game_ids):
    start, end = league_views.season_window(season)
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT personId, gameId FROM player_statistics WHERE gameDateTimeEst >= ? AND gameDateTimeEst <= ? "
            "AND startingPosition IS NOT NULL AND TRIM(startingPosition) != ''", (start, end)).fetchall()
    counts = {}
    for person, game in rows:
        if int(game) in game_ids:
            counts[int(person)] = counts.get(int(person), 0) + 1
    return counts


def _debut_seasons(db_path, person_ids):
    if not person_ids:
        return {}
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            f"SELECT personId, MIN(gameDateTimeEst) FROM player_statistics WHERE personId IN "
            f"({', '.join('?' * len(person_ids))}) GROUP BY personId", [int(p) for p in person_ids]).fetchall()
    return {int(p): game_context.season_of(stamp) for p, stamp in rows if stamp}


def _team_records(rows, db_path, season, through):
    table = league_views.standings(season, as_of=str(pd.Timestamp(through).date()) if through else None,
                                   db_path=db_path)
    records = {}
    for conference in table["conferences"].values():
        for team in conference:
            games = team["wins"] + team["losses"]
            records[int(team["teamId"])] = {"wins": team["wins"], "losses": team["losses"],
                                            "win_pct": team["wins"] / games if games else 0.5,
                                            "abbreviation": team["abbreviation"], "games": games}
    return records


def _table(rows):
    """Season-to-date line per player."""
    ordered = rows.sort_values("gameDateTimeEst")
    grouped = ordered.groupby("personId")
    table = grouped.agg(name=("name", "last"), teamId=("teamId", "last"), games=("gameId", "nunique"),
                        minutes=("numMinutes", "sum"), value=("value", "sum"), pts=("points", "sum"),
                        reb_o=("reboundsOffensive", "sum"), reb_d=("reboundsDefensive", "sum"),
                        ast=("assists", "sum"))
    table["mpg"] = table["minutes"] / table["games"]
    table["value_per_game"] = table["value"] / table["games"]
    table["ppg"] = table["pts"] / table["games"]
    table["rpg"] = (table["reb_o"] + table["reb_d"]) / table["games"]
    table["apg"] = table["ast"] / table["games"]
    return table


def _entry(person, row, records, score, extra=None):
    team = records.get(int(row["teamId"]), {})
    return {
        "person_id": int(person),
        "name": row["name"],
        "team_id": int(row["teamId"]),
        "team": team.get("abbreviation"),
        "team_record": f"{team.get('wins', 0)}-{team.get('losses', 0)}" if team else None,
        "games": int(row["games"]),
        "mpg": round(float(row["mpg"]), 1),
        "ppg": round(float(row["ppg"]), 1),
        "rpg": round(float(row["rpg"]), 1),
        "apg": round(float(row["apg"]), 1),
        "value": round(float(row["value"]), 1),
        "value_per_game": round(float(row["value_per_game"]), 2),
        "score": round(float(score), 2),
        **(extra or {}),
    }


def _mvp(table, records, team_games):
    eligible_pace = MVP_GAMES / SEASON_GAMES
    out = []
    for person, row in table.iterrows():
        team = records.get(int(row["teamId"]))
        win_pct = team["win_pct"] if team else 0.5
        played_share = row["games"] / max(team_games, 1)
        out.append((row["value"] * (0.5 + win_pct), person, row,
                    {"on_pace_for_65": bool(played_share >= eligible_pace), "team_win_pct": round(win_pct, 3)}))
    return out


def _roy(table, debuts, season):
    return [(row["value"], person, row, {}) for person, row in table.iterrows()
            if debuts.get(int(person)) == int(season)]


def _sixth(table, starts, team_games):
    out = []
    for person, row in table.iterrows():
        started = starts.get(int(person), 0)
        if row["games"] < SIXTH_GAME_SHARE * team_games or started >= SIXTH_START_SHARE * row["games"]:
            continue
        out.append((row["value"], person, row, {"starts": int(started)}))
    return out


def _mip(table, previous, team_games):
    # Early in a season the 20-game bar scales down to half the team's games.
    current_min = max(1, min(MIP_MIN_GAMES, team_games // 2))
    out = []
    for person, row in table.iterrows():
        if person not in previous.index or row["games"] < current_min or row["mpg"] < MIP_MIN_MPG:
            continue
        before = previous.loc[person]
        if before["games"] < MIP_MIN_GAMES:
            continue
        rise = row["value_per_game"] - before["value_per_game"]
        out.append((rise, person, row, {"previous_value_per_game": round(float(before["value_per_game"]), 2),
                                        "rise_per_game": round(float(rise), 2)}))
    return out


def _rank(scored, records, limit=SHOWN):
    scored = sorted(scored, key=lambda item: -item[0])[:limit]
    return [_entry(person, row, records, score, extra) for score, person, row, extra in scored]


def _checkpoints(rows, through):
    first = rows["gameDateTimeEst"].min().normalize()
    last = pd.Timestamp(through).normalize() if through else rows["gameDateTimeEst"].max().normalize()
    points = list(pd.date_range(first + pd.Timedelta(days=6), last, freq="7D"))
    if not points or points[-1] < last:
        points.append(last)
    return points


def awards_race(season, model, through=None, db_path=TEAM_DB_PATH):
    """Top candidates for each award and their weekly trend."""
    rows = _season_rows(season, through, model, db_path)
    if rows.empty:
        raise ValueError(f"No regular-season box scores for {season}-{str(season + 1)[-2:]}.")
    records = _team_records(rows, db_path, season, through)
    team_games = int(np.median([r["games"] for r in records.values()])) if records else 0
    table = _table(rows)
    game_ids = set(rows["gameId"].astype(int))
    starts = _starts(db_path, season, game_ids)
    debuts = _debut_seasons(db_path, list(table.index))
    previous_rows = _season_rows(season - 1, None, model, db_path)
    previous = _table(previous_rows) if not previous_rows.empty else table.iloc[0:0]

    races = {
        "mvp": _rank(_mvp(table, records, team_games), records),
        "roy": _rank(_roy(table, debuts, season), records),
        "sixth": _rank(_sixth(table, starts, team_games), records),
        "mip": _rank(_mip(table, previous, team_games), records),
    }

    # Weekly replay of the MVP and rookie races for the current leaders.
    trend = {}
    checkpoints = _checkpoints(rows, through)
    win_pcts = {}
    for award in ("mvp", "roy"):
        leaders = [entry["person_id"] for entry in races[award][:TREND_PLAYERS]]
        series = {person: [] for person in leaders}
        for checkpoint in checkpoints:
            upto = rows[rows["gameDateTimeEst"] < checkpoint + pd.Timedelta(days=1)]
            partial = _table(upto[upto["personId"].isin(leaders)]) if leaders else None
            for person in leaders:
                if partial is None or person not in partial.index:
                    series[person].append({"date": str(checkpoint.date()), "score": 0.0})
                    continue
                row = partial.loc[person]
                score = row["value"]
                if award == "mvp":
                    key = (int(row["teamId"]), checkpoint)
                    if key not in win_pcts:
                        win_pcts[key] = _win_pct_through(db_path, key[0], season, checkpoint)
                    score = score * (0.5 + win_pcts[key])
                series[person].append({"date": str(checkpoint.date()), "score": round(float(score), 2)})
        trend[award] = [{"person_id": person, "name": table.loc[person, "name"], "points": series[person]}
                        for person in leaders]

    return {
        "season": int(season),
        "through": str(rows["gameDateTimeEst"].max().date()),
        "team_games": team_games,
        "awards": [{"key": key, "label": label, "candidates": races[key]} for key, label in AWARDS.items()],
        "trend": trend,
        "method": {
            "value": "Points of team margin above a replacement-level player, from a box-score rating "
                     "fitted on this database (the same one the injury overlay uses), summed over games.",
            "mvp": "Value added x (0.5 + team win%). Players not on pace for 65 games are flagged.",
            "roy": "Value added among players whose first NBA game came this season.",
            "sixth": "Value added among players who started under half their games.",
            "mip": "Rise in value added per game over last season (20+ games last season and 20+ now, or half "
                   "the team's games this early; 15+ minutes now).",
        },
    }


def _win_pct_through(db_path, team_id, season, checkpoint):
    start, _ = league_views.season_window(season)
    with sqlite3.connect(db_path) as connection:
        wins, games = connection.execute(
            "SELECT SUM(winner = ?), COUNT(*) FROM games WHERE gameType = 'Regular Season' AND winner IS NOT NULL "
            "AND (hometeamId = ? OR awayteamId = ?) AND gameDateTimeEst >= ? AND gameDateTimeEst < ?",
            (team_id, team_id, team_id, start, str(checkpoint + pd.Timedelta(days=1)))).fetchone()
    return (wins or 0) / games if games else 0.5
