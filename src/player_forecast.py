"""Player stat forecasts for one game: points, rebounds, assists and threes.

For a player's next game the forecast is

    expected minutes x per-minute rate x opponent factor

* **Expected minutes**: an exponentially weighted average of recent minutes
  (half-life ``MINUTES_HALFLIFE`` appearances).
* **Per-minute rate**: a blend of recent form (exponentially weighted,
  half-life ``RATE_HALFLIFE``) and the player's last ``LONG_WINDOW``
  appearances, so a hot week moves the number but does not take it over.
* **Opponent factor**: what the opponent has allowed per game in that stat
  over its last ``OPPONENT_WINDOW`` games, relative to the league that season,
  halfway shrunk toward 1 (pace and defence both show up here).

The range is the 10th to 90th percentile: the mean plus the 10th/90th
percentiles of past forecast errors scaled by sqrt(mean) (fitted on the
seasons before ``TEST_START``), so it is wider for bigger lines and adapts to
how streaky each stat really is. Forecasts assume the player plays: a game
they miss is not in the backtest, and the tool says so.

``backtest`` grades the method on seasons from ``TEST_START`` against the
naive forecast (the player's season average so far) and writes
``models/player_forecast_backtest.json``.
"""

import argparse
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from src.main import TEAM_DB_PATH
except ImportError:  # pragma: no cover - direct-script support
    from main import TEAM_DB_PATH


ROOT = Path(__file__).resolve().parents[1]
BACKTEST_FILE = ROOT / "models" / "player_forecast_backtest.json"
GAME_TYPES = ("Regular Season", "Play-in Tournament", "Playoffs")
STATS = {"pts": "points", "reb": "reboundsTotal", "ast": "assists", "tpm": "threePointersMade"}
STAT_LABELS = {"pts": "Points", "reb": "Rebounds", "ast": "Assists", "tpm": "Threes"}
MINUTES_HALFLIFE = 4
RATE_HALFLIFE = 12
LONG_WINDOW = 82
RECENT_WEIGHT = 0.5
OPPONENT_WINDOW = 30
OPPONENT_SHRINK = 0.5
MIN_EXPECTED_MINUTES = 10.0
FIRST_SEASON = 2016
TEST_START = 2022
INTERVAL = (0.1, 0.9)
# Error quantiles in units of sqrt(mean), used before a backtest fits them.
DEFAULT_SPREAD = {key: [-1.6, 1.6] for key in STATS}

_LOCK = threading.Lock()
_CACHE = {}


def season_of(stamp):
    stamp = pd.Timestamp(stamp)
    return stamp.year - (stamp.month < 9)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_appearances(db_path, start, end=None, person_ids=None):
    columns = ", ".join(f"p.{column}" for column in STATS.values())
    query = f"""
        SELECT p.personId, p.firstName, p.lastName, p.playerteamId AS teamId,
               p.opponentteamId AS opponentId, p.home, p.gameId, p.gameDateTimeEst,
               p.numMinutes, {columns}
        FROM player_statistics p
        WHERE p.gameType IN ({", ".join("?" * len(GAME_TYPES))}) AND p.gameDateTimeEst >= ?
    """
    params = [*GAME_TYPES, str(start)]
    if end is not None:
        query += " AND p.gameDateTimeEst < ?"
        params.append(str(end))
    if person_ids is not None:
        query += f" AND p.personId IN ({', '.join('?' * len(person_ids)) or 'NULL'})"
        params += [int(p) for p in person_ids]
    with sqlite3.connect(db_path) as connection:
        frame = pd.read_sql_query(query, connection, params=params)
    frame["numMinutes"] = pd.to_numeric(frame["numMinutes"], errors="coerce")
    frame = frame[frame["numMinutes"] > 0].copy()
    for column in STATS.values():
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    frame["gameDateTimeEst"] = pd.to_datetime(frame["gameDateTimeEst"])
    for column in ("personId", "teamId", "opponentId", "gameId"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["personId", "teamId", "opponentId", "gameId"])
    for column in ("personId", "teamId", "opponentId", "gameId"):
        frame[column] = frame[column].astype(int)
    frame["name"] = (frame["firstName"].fillna("") + " " + frame["lastName"].fillna("")).str.strip()
    frame["season"] = frame["gameDateTimeEst"].dt.year - (frame["gameDateTimeEst"].dt.month < 9)
    return frame.drop(columns=["firstName", "lastName"]).sort_values(["personId", "gameDateTimeEst", "gameId"])


def team_allowed(appearances):
    """Per team-game: what the opponent's players put up (the team "allowed")."""
    totals = appearances.groupby(["gameId", "teamId", "opponentId"], as_index=False).agg(
        gameDateTimeEst=("gameDateTimeEst", "first"), season=("season", "first"),
        **{key: (column, "sum") for key, column in STATS.items()})
    # The rows for team X's players are what X's opponent allowed.
    allowed = totals.rename(columns={"teamId": "scorerId", "opponentId": "teamId"})
    return allowed.sort_values(["gameDateTimeEst", "gameId"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------

def opponent_factors(allowed, include_current=False):
    """Each team-game's opponent factor per stat, from games before it.

    With ``include_current`` the factor includes the game itself (the
    state *after* it, used for the next game).
    """
    allowed = allowed.copy()
    shift = 0 if include_current else 1
    for key in STATS:
        league = allowed.groupby("season")[key].transform(
            lambda values: values.expanding().mean().shift(shift))
        team = allowed.groupby("teamId")[key].transform(
            lambda values: values.rolling(OPPONENT_WINDOW, min_periods=5).mean().shift(shift))
        raw = (team / league).where(league > 0)
        allowed[f"factor_{key}"] = (1.0 + OPPONENT_SHRINK * (raw - 1.0)).fillna(1.0).clip(0.7, 1.3)
    return allowed


def player_features(appearances, include_current=False):
    """Per appearance: expected minutes and per-minute rates from earlier games.

    With ``include_current`` they include that appearance (the player's
    state going into the *next* game).
    """
    frame = appearances.copy()
    shift = 0 if include_current else 1
    by_player = frame.groupby("personId", sort=False)

    def ewm(column, halflife):
        return by_player[column].transform(lambda values: values.ewm(halflife=halflife).mean().shift(shift))

    def rolling_sum(column):
        return by_player[column].transform(
            lambda values: values.rolling(LONG_WINDOW, min_periods=1).sum().shift(shift))

    frame["expected_minutes"] = ewm("numMinutes", MINUTES_HALFLIFE)
    recent_minutes = ewm("numMinutes", RATE_HALFLIFE)
    long_minutes = rolling_sum("numMinutes")
    frame["prior_games"] = by_player.cumcount() + (1 - shift)
    for key, column in STATS.items():
        recent = ewm(column, RATE_HALFLIFE) / recent_minutes
        long = rolling_sum(column) / long_minutes
        frame[f"rate_{key}"] = RECENT_WEIGHT * recent + (1 - RECENT_WEIGHT) * long
        # The naive baseline: season average per game so far.
        season_mean = frame.groupby(["personId", "season"])[column].transform(
            lambda values: values.expanding().mean().shift(shift))
        frame[f"naive_{key}"] = season_mean
    return frame


def expected_lines(features, factors):
    """Join opponent factors and compute each stat's expected value."""
    merged = features.merge(
        factors[["gameId", "teamId"] + [f"factor_{key}" for key in STATS]],
        left_on=["gameId", "opponentId"], right_on=["gameId", "teamId"], how="left",
        suffixes=("", "_opp"))
    merged = merged.drop(columns=["teamId_opp"])
    for key in STATS:
        factor = merged[f"factor_{key}"].fillna(1.0)
        merged[f"expected_{key}"] = merged["expected_minutes"] * merged[f"rate_{key}"] * factor
    return merged


def interval(mean, spread):
    """The 80% range: ``mean + spread * sqrt(mean)``, floored at zero, whole numbers."""
    mean = max(float(mean), 0.0)
    root = np.sqrt(mean)
    return [max(0, int(np.floor(mean + spread[0] * root + 0.5))), max(0, int(np.floor(mean + spread[1] * root + 0.5)))]


def _fit_spread(expected, actual):
    scaled = (actual - expected) / np.sqrt(np.maximum(expected, 0.25))
    return [round(float(np.quantile(scaled, q)), 3) for q in INTERVAL]


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------

def backtest(db_path=TEAM_DB_PATH, first_season=FIRST_SEASON, test_start=TEST_START, output=BACKTEST_FILE):
    appearances = load_appearances(db_path, f"{first_season - 1}-09-01")
    if appearances.empty:
        raise ValueError("No player box scores to backtest on.")
    lines = expected_lines(player_features(appearances), opponent_factors(team_allowed(appearances)))
    lines = lines[(lines["season"] >= first_season) & (lines["expected_minutes"] >= MIN_EXPECTED_MINUTES)
                  & (lines["prior_games"] >= 5)]
    lines = lines.dropna(subset=[f"expected_{key}" for key in STATS] + [f"naive_{key}" for key in STATS])
    train, test = lines[lines["season"] < test_start], lines[lines["season"] >= test_start]
    if len(train) < 500 or test.empty:
        raise ValueError("Not enough player games to backtest on.")
    report = {"fitted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "train_seasons": [int(train["season"].min()), int(train["season"].max())],
              "test_seasons": [int(test["season"].min()), int(test["season"].max())],
              "test_player_games": int(len(test)), "spread": {}, "stats": {}}
    for key, column in STATS.items():
        spread = _fit_spread(train[f"expected_{key}"].to_numpy(), train[column].to_numpy())
        report["spread"][key] = spread
        expected, naive, actual = (test[f"expected_{key}"].to_numpy(), test[f"naive_{key}"].to_numpy(),
                                   test[column].to_numpy())
        bounds = np.array([interval(value, spread) for value in expected])
        low, high = bounds[:, 0], bounds[:, 1]
        report["stats"][key] = {
            "label": STAT_LABELS[key],
            "mae": round(float(np.mean(np.abs(actual - expected))), 3),
            "naive_mae": round(float(np.mean(np.abs(actual - naive))), 3),
            "bias": round(float(np.mean(expected - actual)), 3),
            "interval_coverage": round(float(np.mean((actual >= low) & (actual <= high))), 4),
        }
    if output is not None:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def load_backtest(path=BACKTEST_FILE):
    try:
        stamp = Path(path).stat().st_mtime_ns
    except OSError:
        return None
    cached = _CACHE.get(str(path))
    if cached and cached[0] == stamp:
        return cached[1]
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    _CACHE[str(path)] = (stamp, report)
    return report


def load_or_backtest(db_path=TEAM_DB_PATH, path=BACKTEST_FILE):
    report = load_backtest(path)
    if report is not None:
        return report
    with _LOCK:
        report = load_backtest(path)
        if report is None:
            backtest(db_path=db_path, output=path)
            report = load_backtest(path)
    return report


# ---------------------------------------------------------------------------
# Serving
# ---------------------------------------------------------------------------

def next_game(db_path, team_id, day):
    """The team's first game on or after ``day`` (played or not)."""
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            f"""SELECT gameId, gameDateTimeEst, hometeamId, awayteamId FROM games
                WHERE gameType IN ({", ".join("?" * len(GAME_TYPES))}) AND gameDateTimeEst >= ?
                  AND (hometeamId = ? OR awayteamId = ?)
                ORDER BY gameDateTimeEst LIMIT 1""",
            (*GAME_TYPES, f"{pd.Timestamp(day).date()} 00:00:00", int(team_id), int(team_id))).fetchone()
    if row is None:
        return None
    game_id, stamp, home, away = row
    home = int(home)
    return {"gameId": int(game_id), "date": str(stamp)[:16], "home": home == int(team_id),
            "opponentId": int(away) if home == int(team_id) else home}


def _states_before(db_path, day, person_ids=None, team_ids=None):
    """Players' state going into ``day`` and every team's opponent factor."""
    day = pd.Timestamp(day).normalize()
    start = day - pd.Timedelta(days=500)
    if person_ids is None and team_ids is not None:
        with sqlite3.connect(db_path) as connection:
            rows = connection.execute(
                f"SELECT DISTINCT personId FROM player_statistics WHERE gameDateTimeEst >= ? "
                f"AND gameDateTimeEst < ? AND playerteamId IN ({', '.join('?' * len(team_ids))})",
                (str(day - pd.Timedelta(days=60)), str(day), *[int(t) for t in team_ids])).fetchall()
        person_ids = [int(r[0]) for r in rows]
    players = load_appearances(db_path, start, day, person_ids=person_ids)
    league = load_appearances(db_path, max(start, day - pd.Timedelta(days=200)), day)
    factors = opponent_factors(team_allowed(league), include_current=True)
    latest_factor = factors.groupby("teamId").tail(1).set_index("teamId")
    if players.empty:
        return players, latest_factor
    features = player_features(players, include_current=True)
    latest = features.groupby("personId").tail(1).set_index("personId")
    return latest, latest_factor


def _forecast_row(state, opponent_id, factors, spread):
    out = {"person_id": int(state.name), "name": state["name"], "team_id": int(state["teamId"]),
           "expected_minutes": round(float(state["expected_minutes"]), 1), "stats": {}}
    for key in STATS:
        factor = float(factors.loc[opponent_id, f"factor_{key}"]) if opponent_id in factors.index else 1.0
        mean = float(state["expected_minutes"]) * float(state[f"rate_{key}"]) * factor
        if not np.isfinite(mean):
            mean = 0.0
        low, high = interval(mean, spread.get(key, DEFAULT_SPREAD[key]))
        out["stats"][key] = {"label": STAT_LABELS[key], "expected": round(mean, 1), "low": low, "high": high,
                             "opponent_factor": round(factor, 3),
                             "season_average": None if not np.isfinite(state[f"naive_{key}"])
                             else round(float(state[f"naive_{key}"]), 1)}
    return out


def player_game_forecast(person_id, day, db_path=TEAM_DB_PATH, opponent_id=None, home=None, backtest_report=None):
    """One player's forecast for their team's next game on/after ``day``."""
    day = pd.Timestamp(day).normalize()
    states, factors = _states_before(db_path, day, person_ids=[int(person_id)])
    if states.empty or int(person_id) not in states.index:
        raise ValueError(f"personId {person_id} has no box scores in the 500 days before {day.date()}.")
    state = states.loc[int(person_id)]
    game = None
    if opponent_id is None:
        game = next_game(db_path, int(state["teamId"]), day)
        if game is None:
            raise ValueError("No upcoming game for the player's latest team; pass an opponent.")
        opponent_id, home = game["opponentId"], game["home"]
    spread = (backtest_report or {}).get("spread") or DEFAULT_SPREAD
    forecast = _forecast_row(state, int(opponent_id), factors, spread)
    with sqlite3.connect(db_path) as connection:
        recent = pd.read_sql_query(
            f"""SELECT gameDateTimeEst, opponentteamId, numMinutes, {", ".join(STATS.values())}
                FROM player_statistics WHERE personId = ? AND gameDateTimeEst < ?
                  AND gameType IN ({", ".join("?" * len(GAME_TYPES))}) AND numMinutes > 0
                ORDER BY gameDateTimeEst DESC LIMIT 10""",
            connection, params=(int(person_id), str(day), *GAME_TYPES))
    forecast.update({
        "date": str(day.date()),
        "game": game,
        "opponent_id": int(opponent_id),
        "home": home,
        "last_played": str(state["gameDateTimeEst"])[:10],
        "recent_games": [
            {"date": str(r.gameDateTimeEst)[:10], "opponent_id": int(r.opponentteamId),
             "minutes": round(float(r.numMinutes), 1),
             **{key: int(getattr(r, column)) for key, column in STATS.items()}}
            for r in recent.itertuples(index=False)],
        "backtest": _backtest_summary(backtest_report),
    })
    return forecast


def slate_forecasts(day, games, db_path=TEAM_DB_PATH, per_team=3, backtest_report=None):
    """The top expected scorers on each side of each game in ``games``.

    ``games`` holds ``gameId``, ``homeTeamId`` and ``awayTeamId``. A player
    counts for a team when their latest appearance was for it.
    """
    day = pd.Timestamp(day).normalize()
    teams = sorted(set(games["homeTeamId"]) | set(games["awayTeamId"]))
    if not teams:
        return {"date": str(day.date()), "games": [], "backtest": _backtest_summary(backtest_report)}
    states, factors = _states_before(db_path, day, team_ids=teams)
    spread = (backtest_report or {}).get("spread") or DEFAULT_SPREAD
    recent_cutoff = day - pd.Timedelta(days=30)
    rows = []
    for game in games.itertuples(index=False):
        sides = {}
        for side, team, opponent in (("home", game.homeTeamId, game.awayTeamId),
                                     ("away", game.awayTeamId, game.homeTeamId)):
            pool = states[(states["teamId"] == int(team)) & (states["gameDateTimeEst"] >= recent_cutoff)
                          & (states["expected_minutes"] >= MIN_EXPECTED_MINUTES)] if not states.empty else states
            players = [_forecast_row(state, int(opponent), factors, spread)
                       for _, state in pool.iterrows()]
            players.sort(key=lambda p: -p["stats"]["pts"]["expected"])
            sides[side] = players[:per_team]
        rows.append({"gameId": int(game.gameId), "home": sides["home"], "away": sides["away"]})
    return {"date": str(day.date()), "games": rows, "backtest": _backtest_summary(backtest_report)}


def _backtest_summary(report):
    if not report:
        return None
    return {key: report.get(key) for key in ("test_seasons", "test_player_games", "stats", "fitted_at")}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Backtest the player game forecasts.")
    parser.add_argument("command", choices=["backtest"])
    parser.add_argument("--first-season", type=int, default=FIRST_SEASON)
    parser.add_argument("--test-start", type=int, default=TEST_START)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    print(json.dumps(backtest(first_season=args.first_season, test_start=args.test_start), indent=2))


if __name__ == "__main__":
    main()
