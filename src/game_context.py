"""Who is missing and how tired: availability and schedule context for games.

The frozen production model (``elo_boosted_ensemble``) sees each team's
rolling form and its rest days, but not who is in the lineup tonight and not
the harder schedule spots (the second night of a back-to-back after a flight,
three games in four nights, the end of a long road trip). This module measures
both and fits a small, separately graded overlay on top of the model:

* **Player value.** A box-score rating per 48 minutes, fitted by ridge
  regression of a team's game margin on its box-score totals (a "box
  plus-minus" fitted on this database). A player's value is their share of the
  team's minutes times their rating above a replacement-level player: roughly
  the points of margin the team gives up without them.
* **Absences.** A team's rotation before a game is the players who appeared in
  at least 3 of its last 10 games that season, averaging 15+ minutes. A
  rotation player who then sits is missing (one who has since moved to another
  team is not). For a game not played yet, the missing list is the stored
  injury report's "out" players who are in the rotation.
* **Schedule spots.** Back-to-backs, 3 games in 4 nights, kilometres flown
  since the last game, time zones crossed and road-trip length.
* **Overlay.** A logistic regression that keeps the production model's
  log-odds as a fixed offset, so it can only add what the model misses. It is
  fitted on seasons before ``TEST_START`` and graded on the seasons from it
  on; the report says whether it beat the plain model, and the plain model's
  number is always shown beside the adjusted one.

``fit`` writes ``models/game_context_model.json``; ``load_or_fit`` fits on
first use when that file is missing.
"""

import argparse
import bisect
import json
import math
import sqlite3
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from src.main import TEAM_DB_PATH
except ImportError:  # pragma: no cover - direct-script support
    from main import TEAM_DB_PATH


ROOT = Path(__file__).resolve().parents[1]
MODEL_FILE = ROOT / "models" / "game_context_model.json"
GAME_TYPES = ("Regular Season", "Play-in Tournament", "Playoffs")
FIT_FIRST_SEASON = 2004
TEST_START = 2022
RATING_WINDOW = 40          # appearances behind a player's rating
MINUTES_WINDOW = 10         # appearances behind a player's expected minutes
ROTATION_GAMES = 10
ROTATION_APPEARANCES = 3
ROTATION_MINUTES = 15.0
RATING_PRIOR_MINUTES = 400.0
REPLACEMENT_QUANTILE = 0.2
RIDGE = 1.0
LONG_ROAD_TRIP = 4

BOX_STATS = [
    "points", "fieldGoalsMade", "fieldGoalsAttempted", "freeThrowsMade", "freeThrowsAttempted",
    "threePointersMade", "reboundsOffensive", "reboundsDefensive", "assists", "steals", "blocks",
    "turnovers", "foulsPersonal",
]
FATIGUE_FEATURES = ["back_to_back", "three_in_four", "travel_1000km", "time_zones", "long_road_trip"]
OVERLAY_FEATURES = ["missing_value"] + FATIGUE_FEATURES
FEATURE_LABELS = {
    "missing_value": "Missing players (points of value, away minus home)",
    "back_to_back": "Second night of a back-to-back (home minus away)",
    "three_in_four": "Third game in four nights (home minus away)",
    "travel_1000km": "Thousands of km flown since the last game (home minus away)",
    "time_zones": "Time zones crossed since the last game (home minus away)",
    "long_road_trip": f"Game {LONG_ROAD_TRIP}+ of a road trip (home minus away)",
}

# Arena locations by the city name the games table uses (latitude,
# longitude, standard UTC offset). Former homes are kept so older seasons'
# travel is right.
CITY_LOCATIONS = {
    "Atlanta": (33.757, -84.396, -5), "Boston": (42.366, -71.062, -5),
    "Brooklyn": (40.683, -73.975, -5), "New Jersey": (40.734, -74.171, -5),
    "Charlotte": (35.225, -80.839, -5), "Chicago": (41.881, -87.674, -6),
    "Cleveland": (41.496, -81.688, -5), "Dallas": (32.790, -96.810, -6),
    "Denver": (39.749, -105.008, -7), "Detroit": (42.341, -83.055, -5),
    "Golden State": (37.768, -122.388, -8), "Houston": (29.751, -95.362, -6),
    "Indiana": (39.764, -86.155, -5), "LA": (34.043, -118.267, -8),
    "Los Angeles": (34.043, -118.267, -8), "Memphis": (35.138, -90.051, -6),
    "Miami": (25.781, -80.188, -5), "Milwaukee": (43.045, -87.917, -6),
    "Minnesota": (44.979, -93.276, -6), "New Orleans": (29.949, -90.082, -6),
    "New Orleans/Oklahoma City": (35.463, -97.515, -6), "New York": (40.751, -73.993, -5),
    "Oklahoma City": (35.463, -97.515, -6), "Orlando": (28.539, -81.384, -5),
    "Philadelphia": (39.901, -75.172, -5), "Phoenix": (33.446, -112.071, -7),
    "Portland": (45.532, -122.667, -8), "Sacramento": (38.580, -121.500, -8),
    "San Antonio": (29.427, -98.438, -6), "Seattle": (47.622, -122.354, -8),
    "Toronto": (43.643, -79.379, -5), "Utah": (40.768, -111.901, -7),
    "Vancouver": (49.278, -123.109, -8), "Washington": (38.898, -77.021, -5),
}

_MODEL_LOCK = threading.Lock()
_MODEL_CACHE = {}


def season_of(stamp):
    stamp = pd.Timestamp(stamp)
    return stamp.year - (stamp.month < 9)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_schedule(db_path, start, end, team_ids=None):
    """Games (played or not) with the home city, one row per game."""
    query = f"""
        SELECT gameId, gameDateTimeEst, hometeamId AS homeTeamId, awayteamId AS awayTeamId,
               hometeamCity AS homeCity, homeScore, awayScore, winner, gameType
        FROM games
        WHERE gameType IN ({", ".join("?" * len(GAME_TYPES))})
          AND gameDateTimeEst >= ? AND gameDateTimeEst <= ?
    """
    params = [*GAME_TYPES, str(start), str(end)]
    if team_ids:
        marks = ", ".join("?" * len(team_ids))
        query += f" AND (hometeamId IN ({marks}) OR awayteamId IN ({marks}))"
        params += [int(t) for t in team_ids] * 2
    with sqlite3.connect(db_path) as connection:
        frame = pd.read_sql_query(query + " ORDER BY gameDateTimeEst, gameId", connection, params=params)
    frame["gameDateTimeEst"] = pd.to_datetime(frame["gameDateTimeEst"])
    for column in ("gameId", "homeTeamId", "awayTeamId"):
        frame[column] = frame[column].astype(int)
    frame["winner"] = pd.to_numeric(frame["winner"], errors="coerce")
    frame["season"] = frame["gameDateTimeEst"].dt.year - (frame["gameDateTimeEst"].dt.month < 9)
    return frame


def load_player_games(db_path, start, end=None, person_ids=None, team_ids=None):
    """Player appearances (minutes > 0) with box-score counts."""
    stats = ", ".join(f"p.{stat}" for stat in BOX_STATS)
    query = f"""
        SELECT p.personId, p.playerteamId AS teamId, p.gameId, p.gameDateTimeEst,
               p.firstName, p.lastName, p.numMinutes, {stats}
        FROM player_statistics p
        WHERE p.gameType IN ({", ".join("?" * len(GAME_TYPES))}) AND p.gameDateTimeEst >= ?
    """
    params = [*GAME_TYPES, str(start)]
    if end is not None:
        query += " AND p.gameDateTimeEst < ?"
        params.append(str(end))
    if person_ids is not None:
        marks = ", ".join("?" * len(person_ids)) or "NULL"
        query += f" AND p.personId IN ({marks})"
        params += [int(p) for p in person_ids]
    if team_ids is not None:
        marks = ", ".join("?" * len(team_ids)) or "NULL"
        query += f" AND p.playerteamId IN ({marks})"
        params += [int(t) for t in team_ids]
    with sqlite3.connect(db_path) as connection:
        frame = pd.read_sql_query(query, connection, params=params)
    frame["numMinutes"] = pd.to_numeric(frame["numMinutes"], errors="coerce")
    frame = frame[frame["numMinutes"] > 0].copy()
    for stat in BOX_STATS:
        frame[stat] = pd.to_numeric(frame[stat], errors="coerce").fillna(0.0)
    frame["gameDateTimeEst"] = pd.to_datetime(frame["gameDateTimeEst"])
    for column in ("personId", "teamId", "gameId"):
        frame[column] = frame[column].astype(int)
    frame["name"] = (frame["firstName"].fillna("") + " " + frame["lastName"].fillna("")).str.strip()
    return frame.drop(columns=["firstName", "lastName"]).sort_values(["gameDateTimeEst", "gameId"])


# ---------------------------------------------------------------------------
# Box rating and player value
# ---------------------------------------------------------------------------

def fit_box_rating(player_games, schedule, ridge=RIDGE):
    """Ridge fit of a team's game margin on its box-score totals.

    Returns per-48-minute coefficients for one player slot: a team whose five
    on-court players all rate 0 is average.
    """
    totals = player_games.groupby(["gameId", "teamId"])[BOX_STATS].sum().reset_index()
    played = schedule.dropna(subset=["homeScore", "awayScore"])
    margins = pd.concat([
        pd.DataFrame({"gameId": played["gameId"], "teamId": played["homeTeamId"],
                      "margin": played["homeScore"] - played["awayScore"]}),
        pd.DataFrame({"gameId": played["gameId"], "teamId": played["awayTeamId"],
                      "margin": played["awayScore"] - played["homeScore"]}),
    ])
    data = totals.merge(margins, on=["gameId", "teamId"])
    if len(data) < 50:
        raise ValueError("Not enough team-games to fit the box rating.")
    x = data[BOX_STATS].to_numpy(dtype=float)
    y = data["margin"].to_numpy(dtype=float)
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale == 0] = 1.0
    z = (x - mean) / scale
    beta_z = np.linalg.solve(z.T @ z + ridge * np.eye(z.shape[1]), z.T @ (y - y.mean()))
    beta = beta_z / scale
    intercept = float(y.mean() - mean @ beta)
    # Team totals are five player slots of 48 minutes: per-48 player rating is
    # stats_per_48 . beta + intercept / 5.
    return {"coefficients": dict(zip(BOX_STATS, beta.round(5).tolist())),
            "intercept_per_slot": round(intercept / 5.0, 4), "team_games": int(len(data))}


def _rolling_sum(frame, columns, window):
    """Per-player rolling sum over the last ``window`` appearances, current one included."""
    cumulative = frame.groupby("personId")[columns].cumsum()
    lagged = cumulative.groupby(frame["personId"]).shift(window).fillna(0.0)
    return cumulative - lagged


def player_states(player_games, rating):
    """Each appearance with the player's rating, minutes and value *after* it.

    ``rating`` is the fitted box rating plus ``replacement`` (per 48). Value
    is expected minutes / 48 times the shrunk rating above replacement,
    floored at zero (a coach does not lose anything by sitting a player who is
    below replacement level).
    """
    frame = player_games.sort_values(["personId", "gameDateTimeEst", "gameId"]).reset_index(drop=True)
    columns = BOX_STATS + ["numMinutes"]
    sums = _rolling_sum(frame, columns, RATING_WINDOW)
    beta = np.array([rating["coefficients"][stat] for stat in BOX_STATS])
    minutes = sums["numMinutes"].to_numpy()
    raw = sums[BOX_STATS].to_numpy() @ beta / np.maximum(minutes, 1e-9) * 48.0 + rating["intercept_per_slot"]
    replacement = rating["replacement"]
    shrunk = (minutes * raw + RATING_PRIOR_MINUTES * replacement) / (minutes + RATING_PRIOR_MINUTES)
    recent = _rolling_sum(frame, ["numMinutes"], MINUTES_WINDOW)["numMinutes"].to_numpy()
    count = frame.groupby("personId").cumcount().to_numpy() + 1
    mpg = recent / np.minimum(count, MINUTES_WINDOW)
    frame["rating"] = shrunk
    frame["mpg"] = mpg
    frame["value"] = np.maximum(0.0, mpg / 48.0 * (shrunk - replacement))
    return frame


def replacement_level(states):
    """The rating of a fringe rotation player (20th percentile of regulars)."""
    regulars = states[states["mpg"] >= 10]
    if regulars.empty:
        return 0.0
    return float(regulars["rating"].quantile(REPLACEMENT_QUANTILE))


class PlayerTimeline:
    """Lookup of a player's latest state before a date and their next team."""

    def __init__(self, states):
        self.rows = {}
        for person, group in states.groupby("personId", sort=False):
            self.rows[int(person)] = (
                list(group["gameDateTimeEst"]), group["teamId"].tolist(), group["value"].tolist(),
                group["rating"].tolist(), group["mpg"].tolist(), group["name"].tolist(),
            )

    def before(self, person, stamp):
        entry = self.rows.get(int(person))
        if not entry:
            return None
        index = bisect.bisect_left(entry[0], stamp) - 1
        if index < 0:
            return None
        return {"value": entry[2][index], "rating": entry[3][index], "mpg": entry[4][index],
                "name": entry[5][index], "teamId": entry[1][index], "last_played": entry[0][index]}

    def next_team(self, person, stamp):
        entry = self.rows.get(int(person))
        if not entry:
            return None
        index = bisect.bisect_left(entry[0], stamp)  # a same-day game elsewhere counts
        return entry[1][index] if index < len(entry[0]) else None


# ---------------------------------------------------------------------------
# Rotations, absences and schedule spots
# ---------------------------------------------------------------------------

def _haversine_km(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def team_game_sequences(schedule):
    """{teamId: [game rows in order]} with each team's side of each game."""
    sequences = {}
    for game in schedule.itertuples(index=False):
        for team, home in ((game.homeTeamId, True), (game.awayTeamId, False)):
            sequences.setdefault(int(team), []).append((game, home))
    return sequences


def schedule_spots(schedule):
    """{(gameId, teamId): schedule-spot features} for every game in ``schedule``."""
    spots = {}
    for team, games in team_game_sequences(schedule).items():
        days = []
        road_trip = 0
        previous = None
        for game, home in games:
            day = game.gameDateTimeEst.normalize()
            season = game.season
            same_season = previous is not None and previous[0].season == season
            rest = (day - previous[0].gameDateTimeEst.normalize()).days if same_season else None
            days = [d for d in days if (day - d).days <= 3] if same_season else []
            days.append(day)
            road_trip = 0 if home else (road_trip + 1 if same_season else 1)
            here = CITY_LOCATIONS.get(game.homeCity)
            travel_km = zones = 0.0
            if same_season and rest is not None and rest <= 3:
                there = CITY_LOCATIONS.get(previous[0].homeCity)
                if here and there:
                    travel_km = _haversine_km(there, here)
                    zones = abs(here[2] - there[2])
            spots[(int(game.gameId), team)] = {
                "rest_days": rest,
                "back_to_back": int(rest == 1),
                "three_in_four": int(len(days) >= 3),
                "travel_km": round(travel_km),
                "travel_1000km": travel_km / 1000.0,
                "time_zones": zones,
                "road_trip_game": road_trip,
                "long_road_trip": int(road_trip >= LONG_ROAD_TRIP),
            }
            previous = (game, home)
    return spots


def rotation_before(appearances):
    """Rotation from a team's last games: {personId: mean minutes}.

    ``appearances`` is a list (oldest first) of {personId: minutes} dicts for
    the team's last ``ROTATION_GAMES`` games of the same season.
    """
    counts, minutes = {}, {}
    for game in appearances:
        for person, played in game.items():
            counts[person] = counts.get(person, 0) + 1
            minutes[person] = minutes.get(person, 0.0) + played
    return {person: minutes[person] / counts[person] for person in counts
            if counts[person] >= ROTATION_APPEARANCES and minutes[person] / counts[person] >= ROTATION_MINUTES}


def historical_absences(schedule, player_games, timeline):
    """{(gameId, teamId): [missing rotation player dicts]} for played games."""
    played_by = {}
    for row in player_games[["gameId", "teamId", "personId", "numMinutes"]].itertuples(index=False):
        played_by.setdefault((int(row.gameId), int(row.teamId)), {})[int(row.personId)] = float(row.numMinutes)
    out = {}
    for team, games in team_game_sequences(schedule).items():
        window = deque(maxlen=ROTATION_GAMES)
        season = None
        for game, _home in games:
            if game.season != season:
                window.clear()
                season = game.season
            key = (int(game.gameId), team)
            appeared = played_by.get(key)
            if appeared is None:  # not played yet, or no box score
                continue
            missing = []
            for person in rotation_before(window):
                if person in appeared:
                    continue
                moved = timeline.next_team(person, game.gameDateTimeEst)
                if moved is not None and moved != team:
                    continue
                state = timeline.before(person, game.gameDateTimeEst)
                if state is None or state["teamId"] != team:
                    continue
                missing.append({"person_id": person, "name": state["name"], "value": state["value"],
                                "mpg": state["mpg"]})
            out[key] = missing
            window.append(appeared)
    return out


# ---------------------------------------------------------------------------
# Overlay
# ---------------------------------------------------------------------------

def _logit(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def overlay_features(home_missing, away_missing, home_spot, away_spot):
    row = {"missing_value": sum(m["value"] for m in away_missing) - sum(m["value"] for m in home_missing)}
    for feature in FATIGUE_FEATURES:
        row[feature] = float((home_spot or {}).get(feature, 0.0)) - float((away_spot or {}).get(feature, 0.0))
    return row


def fit_overlay(x, offset, y, ridge=RIDGE, iterations=25):
    """Newton fit of logit(p) = offset + x . theta (no intercept)."""
    theta = np.zeros(x.shape[1])
    for _ in range(iterations):
        p = _sigmoid(offset + x @ theta)
        gradient = x.T @ (p - y) + ridge * theta
        hessian = (x * (p * (1 - p))[:, None]).T @ x + ridge * np.eye(x.shape[1])
        step = np.linalg.solve(hessian, gradient)
        theta -= step
        if np.max(np.abs(step)) < 1e-8:
            break
    return theta


def _grade(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return {
        "games": int(len(y)),
        "log_loss": round(float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))), 5),
        "brier": round(float(np.mean((p - y) ** 2)), 5),
        "accuracy": round(float(np.mean((p >= 0.5) == (y == 1))), 4),
    }


def fit(probabilities, db_path=TEAM_DB_PATH, first_season=FIT_FIRST_SEASON, test_start=TEST_START,
        output=MODEL_FILE):
    """Fit the box rating and the overlay; grade it on seasons >= ``test_start``."""
    start = f"{first_season - 1}-09-01"
    schedule = load_schedule(db_path, start, "2100-01-01")
    player_games = load_player_games(db_path, start)
    if player_games.empty or schedule.empty:
        raise ValueError("No player or game records to fit on.")
    fit_seasons = schedule["season"] < test_start
    rating = fit_box_rating(player_games[player_games["gameId"].isin(schedule.loc[fit_seasons, "gameId"])],
                            schedule[fit_seasons])
    rating["replacement"] = 0.0
    rating["replacement"] = replacement_level(player_states(player_games, rating))
    states = player_states(player_games, rating)
    timeline = PlayerTimeline(states)
    absences = historical_absences(schedule, player_games, timeline)
    spots = schedule_spots(schedule)

    graded = probabilities.dropna(subset=["target"])[["gameId", "home_win_probability", "target", "season"]]
    graded = graded.merge(schedule[["gameId", "homeTeamId", "awayTeamId"]], on="gameId")
    rows = []
    for game in graded.itertuples(index=False):
        home_key, away_key = (int(game.gameId), int(game.homeTeamId)), (int(game.gameId), int(game.awayTeamId))
        if home_key not in absences or away_key not in absences:
            continue
        features = overlay_features(absences[home_key], absences[away_key], spots.get(home_key), spots.get(away_key))
        rows.append({"season": int(game.season), "p": float(game.home_win_probability),
                     "y": float(game.target), **features})
    data = pd.DataFrame(rows)
    if data.empty:
        raise ValueError("No graded games overlap the player records.")
    data = data[data["season"] >= first_season]
    train, test = data[data["season"] < test_start], data[data["season"] >= test_start]
    if len(train) < 200:
        raise ValueError(f"Only {len(train)} games before {test_start} to fit the overlay on.")
    x_train = train[OVERLAY_FEATURES].to_numpy(dtype=float)
    theta = fit_overlay(x_train, _logit(train["p"]), train["y"].to_numpy())
    report = {"train": {"seasons": [int(train["season"].min()), int(train["season"].max())],
                        "plain": _grade(train["p"].to_numpy(), train["y"].to_numpy()),
                        "adjusted": _grade(_sigmoid(_logit(train["p"]) + x_train @ theta), train["y"].to_numpy())}}
    if not test.empty:
        x_test = test[OVERLAY_FEATURES].to_numpy(dtype=float)
        plain = _grade(test["p"].to_numpy(), test["y"].to_numpy())
        adjusted = _grade(_sigmoid(_logit(test["p"]) + x_test @ theta), test["y"].to_numpy())
        report["test"] = {"seasons": [int(test["season"].min()), int(test["season"].max())],
                          "plain": plain, "adjusted": adjusted}
    beats = "test" in report and report["test"]["adjusted"]["log_loss"] < report["test"]["plain"]["log_loss"]
    model = {
        "fitted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "first_season": int(first_season),
        "test_start": int(test_start),
        "box_rating": rating,
        "overlay": dict(zip(OVERLAY_FEATURES, [round(float(t), 5) for t in theta])),
        "mean_missing_value": round(float(train["missing_value"].abs().mean()), 3),
        "validation": report,
        "beats_plain_model": bool(beats),
    }
    if output is not None:
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(model, indent=2), encoding="utf-8")
    return model


def load_model(path=MODEL_FILE):
    try:
        stamp = Path(path).stat().st_mtime_ns
    except OSError:
        return None
    cached = _MODEL_CACHE.get(str(path))
    if cached and cached[0] == stamp:
        return cached[1]
    model = json.loads(Path(path).read_text(encoding="utf-8"))
    _MODEL_CACHE[str(path)] = (stamp, model)
    return model


def load_or_fit(probabilities_fn, db_path=TEAM_DB_PATH, path=MODEL_FILE):
    """The fitted model, fitting (once, under a lock) when the file is missing."""
    model = load_model(path)
    if model is not None:
        return model
    with _MODEL_LOCK:
        model = load_model(path)
        if model is None:
            fit(probabilities_fn(), db_path=db_path, output=path)
            model = load_model(path)
    return model


def adjust(p_home, features, model):
    """Adjusted home probability and each feature's share of the change."""
    theta = model["overlay"]
    base = float(_logit(p_home))
    parts = {name: theta.get(name, 0.0) * features.get(name, 0.0) for name in OVERLAY_FEATURES}
    adjusted = float(_sigmoid(base + sum(parts.values())))
    injuries = float(_sigmoid(base + parts["missing_value"])) - p_home
    return {
        "home_win_probability": round(adjusted, 4),
        "change": round(adjusted - p_home, 4),
        "from_injuries": round(injuries, 4),
        "from_schedule": round(adjusted - p_home - injuries, 4),
    }


# ---------------------------------------------------------------------------
# One day's games
# ---------------------------------------------------------------------------

def _injured_out(db_path, team_ids):
    with sqlite3.connect(db_path) as connection:
        exists = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='injuries'").fetchone()[0]
        if not exists:
            return {}, None
        marks = ", ".join("?" * len(team_ids))
        rows = connection.execute(
            f"SELECT teamId, personId, playerName, status, injury, reported_at FROM injuries "
            f"WHERE is_out = 1 AND teamId IN ({marks})", [int(t) for t in team_ids]).fetchall()
    out, reported = {}, None
    for team, person, name, status, injury, reported_at in rows:
        reported = reported_at
        out.setdefault(int(team), []).append(
            {"person_id": int(person) if person is not None else None, "name": name, "status": status,
             "injury": injury})
    return out, reported


def day_context(day, games, model, db_path=TEAM_DB_PATH):
    """Missing players, schedule spots and the adjusted probability for each game.

    ``games`` holds ``gameId``, ``gameDateTimeEst``, ``homeTeamId``,
    ``awayTeamId``, ``home_win_probability`` and ``played``.
    """
    day = pd.Timestamp(day).normalize()
    tomorrow = day + pd.Timedelta(days=1)
    if games.empty:
        return {"date": str(day.date()), "games": [], "model": _model_summary(model)}
    teams = sorted(set(games["homeTeamId"]) | set(games["awayTeamId"]))
    season = season_of(day)
    season_start = pd.Timestamp(f"{season}-09-01")
    schedule = load_schedule(db_path, season_start, tomorrow, teams)
    spots = schedule_spots(schedule)
    # Early in a season the rotation also looks at the end of last season;
    # a player counts only once they are on the team this season (on its
    # injury report, or in one of its box scores).
    played_now = schedule[(schedule["gameDateTimeEst"] < day) & schedule["winner"].notna()]
    counts = pd.concat([played_now["homeTeamId"], played_now["awayTeamId"]]).value_counts()
    young = min(int(counts.get(team, 0)) for team in teams) < ROTATION_GAMES // 2
    since = season_start - pd.Timedelta(days=200) if young else max(season_start, day - pd.Timedelta(days=60))
    if young:
        schedule = load_schedule(db_path, since, tomorrow, teams)
    recent = load_player_games(db_path, since, tomorrow, team_ids=teams)
    on_team_now = recent[recent["gameDateTimeEst"] >= season_start].groupby("teamId")["personId"].agg(set).to_dict()

    sequences = team_game_sequences(schedule)
    played_by = {}
    for row in recent[["gameId", "teamId", "personId", "numMinutes"]].itertuples(index=False):
        played_by.setdefault((int(row.gameId), int(row.teamId)), {})[int(row.personId)] = float(row.numMinutes)
    rotations = {}
    for team in teams:
        before = [played_by.get((int(game.gameId), team)) for game, _ in sequences.get(team, [])
                  if game.gameDateTimeEst < day]
        rotations[team] = rotation_before([g for g in before if g][-ROTATION_GAMES:])

    people = sorted({p for rotation in rotations.values() for p in rotation})
    injured, reported_at = _injured_out(db_path, teams)
    people = sorted(set(people) | {p["person_id"] for team in injured.values() for p in team if p["person_id"]})
    history = load_player_games(db_path, day - pd.Timedelta(days=550), tomorrow, person_ids=people) if people else recent.iloc[0:0]
    timeline = PlayerTimeline(player_states(history, model["box_rating"])) if not history.empty else None

    rows = []
    for game in games.itertuples(index=False):
        sides = {}
        for side, team in (("home", int(game.homeTeamId)), ("away", int(game.awayTeamId))):
            tip = pd.Timestamp(game.gameDateTimeEst)
            missing, listed = [], []
            appeared = played_by.get((int(game.gameId), team))
            if game.played and appeared is not None:
                candidates = [{"person_id": p} for p in rotations[team] if p not in appeared
                              and (not young or p in on_team_now.get(team, set()))]
                source = "box score"
            else:
                candidates = injured.get(team, [])
                source = "injury report"
            for entry in candidates:
                person = entry.get("person_id")
                state = timeline.before(person, tip) if (timeline and person) else None
                moved = timeline.next_team(person, tip) if (timeline and person and game.played) else None
                if moved is not None and moved != team:
                    continue
                record = {"person_id": person, "name": entry.get("name") or (state or {}).get("name"),
                          "status": entry.get("status"), "injury": entry.get("injury"),
                          "in_rotation": person in rotations[team],
                          "value": round(state["value"], 2) if state and person in rotations[team] else 0.0,
                          "mpg": round(state["mpg"], 1) if state else None}
                (missing if record["in_rotation"] else listed).append(record)
            missing.sort(key=lambda m: -m["value"])
            spot = spots.get((int(game.gameId), team), {})
            sides[side] = {
                "teamId": team, "missing": missing, "listed_not_in_rotation": listed, "source": source,
                "missing_value": round(sum(m["value"] for m in missing), 2),
                "schedule": {key: spot.get(key) for key in ("rest_days", "back_to_back", "three_in_four",
                                                            "travel_km", "time_zones", "road_trip_game")},
                "flags": _flags(spot),
            }
        features = overlay_features(sides["home"]["missing"], sides["away"]["missing"],
                                    spots.get((int(game.gameId), int(game.homeTeamId))),
                                    spots.get((int(game.gameId), int(game.awayTeamId))))
        p_home = float(game.home_win_probability)
        rows.append({
            "gameId": int(game.gameId),
            "home": sides["home"],
            "away": sides["away"],
            "model_home_win_probability": round(p_home, 4),
            "adjusted": adjust(p_home, features, model),
            "features": {key: round(value, 3) for key, value in features.items()},
        })
    return {"date": str(day.date()), "injury_report_at": reported_at, "games": rows,
            "model": _model_summary(model)}


def _flags(spot):
    flags = []
    if spot.get("back_to_back"):
        flags.append("Back-to-back")
    if spot.get("three_in_four"):
        flags.append("3 in 4 nights")
    if spot.get("road_trip_game", 0) >= LONG_ROAD_TRIP:
        flags.append(f"Road trip game {spot['road_trip_game']}")
    if spot.get("travel_km", 0) >= 2000:
        flags.append(f"{spot['travel_km']:,} km flight")
    if spot.get("time_zones", 0) >= 2:
        flags.append(f"{int(spot['time_zones'])} time zones")
    return flags


def _model_summary(model):
    validation = model.get("validation", {})
    return {
        "overlay": model.get("overlay"),
        "feature_labels": FEATURE_LABELS,
        "beats_plain_model": model.get("beats_plain_model"),
        "test": validation.get("test"),
        "fitted_at": model.get("fitted_at"),
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Fit the injury and schedule-spot overlay.")
    parser.add_argument("command", choices=["fit"])
    parser.add_argument("--first-season", type=int, default=FIT_FIRST_SEASON)
    parser.add_argument("--test-start", type=int, default=TEST_START)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        from src.simulate_season import load_pregame_probabilities
    except ImportError:  # pragma: no cover - direct-script support
        from simulate_season import load_pregame_probabilities
    model = fit(load_pregame_probabilities(), first_season=args.first_season, test_start=args.test_start)
    print(json.dumps({key: model[key] for key in ("overlay", "beats_plain_model", "validation")}, indent=2))


if __name__ == "__main__":
    main()
