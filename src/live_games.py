"""Live scores and in-game win probability for today's games.

ESPN's public scoreboard (the same unofficial feed the daily refresh reads)
gives each game's status, period, clock and score. Nothing here is written to
the database: live states are fetched on request, cached for
``CACHE_SECONDS`` so many viewers cost one request, and kept in memory for the
running server only.

The in-game probability is the random-walk model of Stern (1994), "A Brownian
Motion Model for the Progress of Sports Scores" (JASA 89:427): the rest of
the game adds a normal amount to the current lead, centred on the pregame
expected margin scaled to the time left. The pregame expected margin is backed
out of the production model's pregame probability, and the spread is the
margin model's holdout residual SD (``models/margin_metrics.json``). It is not
fitted or validated on play-by-play data (the database has none).
"""

import json
import threading
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

try:
    from src.main import TEAM_DB_PATH
    from src import league_views
    from src.live_results import (
        EASTERN, ESPN_SCOREBOARD_URL, ESPN_SEASON_TYPES, ESPN_TEAM_IDS, FetchError, fetch_day,
    )
except ImportError:  # pragma: no cover - direct-script support
    from main import TEAM_DB_PATH
    import league_views
    from live_results import (
        EASTERN, ESPN_SCOREBOARD_URL, ESPN_SEASON_TYPES, ESPN_TEAM_IDS, FetchError, fetch_day,
    )


ROOT = Path(__file__).resolve().parents[1]
MARGIN_METRICS_PATH = ROOT / "models" / "margin_metrics.json"
DEFAULT_MARGIN_SD = 13.47
CACHE_SECONDS = 20
REGULATION_PERIODS = 4
PERIOD_MINUTES = 12.0
OVERTIME_MINUTES = 5.0

_CACHE = {}
_HISTORY = {}
_LOCK = threading.Lock()


def margin_sd(path=MARGIN_METRICS_PATH):
    """The margin model's holdout residual SD (points), the random walk's full-game spread."""
    try:
        report = json.loads(Path(path).read_text(encoding="utf-8"))
        return float(report["margin"]["holdout_residual_sd"])
    except (OSError, ValueError, KeyError, TypeError):
        return DEFAULT_MARGIN_SD


def minutes_remaining(period, clock_seconds):
    """Regulation-equivalent minutes left from the period number and clock."""
    period = int(period or 0)
    clock_minutes = max(0.0, float(clock_seconds or 0)) / 60.0
    if period <= 0:
        return REGULATION_PERIODS * PERIOD_MINUTES
    if period <= REGULATION_PERIODS:
        return (REGULATION_PERIODS - period) * PERIOD_MINUTES + clock_minutes
    return clock_minutes  # overtime: only this period's clock is left


def parse_live_event(event):
    """One ESPN scoreboard event -> live state dict, or ``None`` if not an NBA game."""
    try:
        competition = event["competitions"][0]
        if event["season"]["type"] not in ESPN_SEASON_TYPES:
            return None
        sides = {c["homeAway"]: c for c in competition["competitors"]}
        home_id = ESPN_TEAM_IDS.get(sides["home"]["team"]["abbreviation"])
        away_id = ESPN_TEAM_IDS.get(sides["away"]["team"]["abbreviation"])
        if home_id is None or away_id is None or home_id == away_id:
            return None
        status = competition.get("status") or event.get("status") or {}
        kind = status.get("type") or {}
        state = kind.get("state") or ("post" if kind.get("completed") else "pre")
        period = int(status.get("period") or 0)
        clock = float(status.get("clock") or 0.0)

        def score(side):
            value = sides[side].get("score")
            try:
                return int(value)
            except (TypeError, ValueError):
                return None

        utc = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
        return {
            "espn_id": int(event["id"]),
            "date": utc.astimezone(EASTERN).strftime("%Y-%m-%d"),
            "tip": utc.astimezone(EASTERN).strftime("%Y-%m-%d %H:%M"),
            "home_id": home_id,
            "away_id": away_id,
            "state": state,  # pre | in | post
            "status_detail": kind.get("shortDetail") or kind.get("detail") or kind.get("description"),
            "period": period,
            "clock_seconds": clock,
            "display_clock": status.get("displayClock"),
            "home_score": score("home") if state != "pre" else None,
            "away_score": score("away") if state != "pre" else None,
        }
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def fetch_live(day, session=None, base_url=ESPN_SCOREBOARD_URL, now=None):
    """Parsed live states for ``day``, cached for ``CACHE_SECONDS``."""
    now = now if now is not None else time.monotonic()
    key = str(day)
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and now - cached[0] < CACHE_SECONDS:
            return cached[1], cached[2]
    payload, _ = fetch_day(pd.Timestamp(day).date(), base_url, session)
    games = [g for g in (parse_live_event(e) for e in payload.get("events", [])) if g]
    fetched_at = datetime.now(EASTERN).strftime("%Y-%m-%d %H:%M:%S")
    with _LOCK:
        _CACHE[key] = (now, games, fetched_at)
    return games, fetched_at


def _remember(espn_id, minutes_left, probability, home_score, away_score):
    points = _HISTORY.setdefault(espn_id, [])
    elapsed = round(REGULATION_PERIODS * PERIOD_MINUTES - minutes_left, 2) if minutes_left is not None else None
    point = {"elapsed": elapsed, "home_win_probability": round(probability, 4),
             "home_score": home_score, "away_score": away_score}
    if not points or (points[-1]["home_score"], points[-1]["away_score"], points[-1]["elapsed"]) != (
            home_score, away_score, elapsed):
        points.append(point)
    return list(points)


def live_scoreboard(day, inputs, probabilities, session=None, db_path=TEAM_DB_PATH, sd=None):
    """Today's games with live scores and the in-game home win probability."""
    day = pd.Timestamp(day).normalize()
    games, fetched_at = fetch_live(day.date(), session=session)
    sd = sd or margin_sd()
    teams = league_views.current_teams(db_path)
    pairs = pd.DataFrame(
        [{"gameId": g["espn_id"], "homeTeamId": g["home_id"], "awayTeamId": g["away_id"]} for g in games],
        columns=["gameId", "homeTeamId", "awayTeamId"],
    )
    pregame = {}
    if not pairs.empty:
        recorded = _recorded_pregame(day, probabilities, db_path)
        missing = pairs[[(h, a) not in recorded for h, a in zip(pairs["homeTeamId"], pairs["awayTeamId"])]]
        if not missing.empty:
            scored = league_views.frozen_matchup_probabilities(missing[["homeTeamId", "awayTeamId"]], day, inputs)
            for (h, a), p in zip(zip(missing["homeTeamId"], missing["awayTeamId"]), scored["home_win_probability"]):
                recorded[(h, a)] = float(p)
        pregame = recorded
    rows = []
    for game in games:
        p0 = pregame.get((game["home_id"], game["away_id"]))
        row = {
            **game,
            "home": {"teamId": game["home_id"], **league_views._team_brief(teams, game["home_id"])},
            "away": {"teamId": game["away_id"], **league_views._team_brief(teams, game["away_id"])},
            "pregame_home_win_probability": None if p0 is None else round(p0, 4),
            "live_home_win_probability": None,
            "minutes_remaining": None,
            "history": [],
        }
        if p0 is not None and game["state"] in ("in", "post") and game["home_score"] is not None:
            left = 0.0 if game["state"] == "post" else minutes_remaining(game["period"], game["clock_seconds"])
            lead = game["home_score"] - game["away_score"]
            probability = league_views.in_game_win_probability(p0, lead, left, sd)
            row["minutes_remaining"] = round(left, 2)
            row["live_home_win_probability"] = round(probability, 4)
            with _LOCK:
                row["history"] = _remember(game["espn_id"], left, probability,
                                           game["home_score"], game["away_score"])
        rows.append(row)
    order = {"in": 0, "pre": 1, "post": 2}
    rows.sort(key=lambda r: (order.get(r["state"], 3), r["tip"]))
    return {
        "date": str(day.date()),
        "fetched_at": fetched_at,
        "cache_seconds": CACHE_SECONDS,
        "margin_sd": round(sd, 2),
        "games": rows,
        "live_games": sum(1 for r in rows if r["state"] == "in"),
    }


def _recorded_pregame(day, probabilities, db_path):
    """(home, away) -> recorded pregame probability for games in the database on ``day``."""
    label = str(pd.Timestamp(day).date())
    games = league_views.load_games(f"{label} 00:00:00", f"{label} 23:59:59", db_path,
                                    league_views.SCOREBOARD_GAME_TYPES)
    if games.empty or probabilities is None:
        return {}
    recorded = probabilities.set_index("gameId")["home_win_probability"]
    out = {}
    for game in games.itertuples(index=False):
        if game.gameId in recorded.index:
            out[(int(game.homeTeamId), int(game.awayTeamId))] = float(recorded[game.gameId])
    return out


def clear_cache():
    with _LOCK:
        _CACHE.clear()
        _HISTORY.clear()

