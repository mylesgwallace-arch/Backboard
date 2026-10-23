"""The model against the betting market.

ESPN's scoreboard (the feed the daily refresh already reads) carries a
sportsbook's line for most games: a point spread, a total and moneylines. The
daily refresh stores the latest pregame line per game in ``market_lines``
(a line is only updated while the game has not started, so the stored one is
the closing line as of the last refresh before tip-off).

The market's win probability is the moneylines' implied probabilities with
the bookmaker's margin (the "vig") removed proportionally; a game with only a
spread uses ``Phi(-spread / sd)`` with the margin model's residual SD.

``comparison`` lines the market up against the production model: one day's
biggest disagreements, and for a season, whose numbers were closer on the
games that have been played (log loss, Brier score, picks, and a running
count of games where each was closer).
"""

import sqlite3
from datetime import datetime, timedelta
from statistics import NormalDist

import numpy as np
import pandas as pd

try:
    from src.main import TEAM_DB_PATH
    from src import league_views
    from src.live_results import EASTERN, ESPN_SCOREBOARD_URL, ESPN_SEASON_TYPES, ESPN_TEAM_IDS, fetch_day
except ImportError:  # pragma: no cover - direct-script support
    from main import TEAM_DB_PATH
    import league_views
    from live_results import EASTERN, ESPN_SCOREBOARD_URL, ESPN_SEASON_TYPES, ESPN_TEAM_IDS, fetch_day


SCHEMA = """
CREATE TABLE IF NOT EXISTS market_lines (
    gameId INTEGER PRIMARY KEY,
    gameDate TEXT NOT NULL,
    homeTeamId INTEGER NOT NULL,
    awayTeamId INTEGER NOT NULL,
    provider TEXT,
    home_spread REAL,
    over_under REAL,
    home_moneyline INTEGER,
    away_moneyline INTEGER,
    market_home_probability REAL NOT NULL,
    captured_at TEXT NOT NULL
)
"""
DEFAULT_MARGIN_SD = 13.47
BIG_GAP = 0.08
_NORMAL = NormalDist()


def ensure_schema(conn):
    conn.execute(SCHEMA)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _number(value):
    if value is None:
        return None
    try:
        text = str(value).strip().replace("+", "")
        if text.upper() in ("EVEN", "EV", "PK", "PICK"):
            return 100.0 if text.upper().startswith("EV") else 0.0
        return float(text)
    except (TypeError, ValueError):
        return None


def _nested(source, *path):
    for key in path:
        if not isinstance(source, dict):
            return None
        source = source.get(key)
    return source


def moneyline_probability(line):
    """American odds -> implied probability (vig included)."""
    if line is None or line == 0:
        return None
    return 100.0 / (line + 100.0) if line > 0 else -line / (-line + 100.0)


def market_probability(home_moneyline=None, away_moneyline=None, home_spread=None, sd=DEFAULT_MARGIN_SD):
    """The market's home win probability, vig removed."""
    home, away = moneyline_probability(home_moneyline), moneyline_probability(away_moneyline)
    if home is not None and away is not None and home + away > 0:
        return home / (home + away)
    if home_spread is not None:
        return _NORMAL.cdf(-home_spread / sd)
    return None


def parse_event_odds(event):
    """One ESPN scoreboard event -> line dict, or ``None`` (no line, not NBA, started)."""
    try:
        competition = event["competitions"][0]
        if event["season"]["type"] not in ESPN_SEASON_TYPES:
            return None
        state = (_nested(competition, "status", "type", "state") or _nested(event, "status", "type", "state")
                 or "pre")
        sides = {c["homeAway"]: c for c in competition["competitors"]}
        home = ESPN_TEAM_IDS.get(sides["home"]["team"]["abbreviation"])
        away = ESPN_TEAM_IDS.get(sides["away"]["team"]["abbreviation"])
        odds = (competition.get("odds") or [None])[0]
        if home is None or away is None or not odds:
            return None
        home_ml = _number(_nested(odds, "homeTeamOdds", "moneyLine"))
        away_ml = _number(_nested(odds, "awayTeamOdds", "moneyLine"))
        if home_ml is None:  # newer layout: moneyline.home.close.odds
            home_ml = _number(_nested(odds, "moneyline", "home", "close", "odds")) or \
                _number(_nested(odds, "moneyline", "home", "open", "odds"))
            away_ml = _number(_nested(odds, "moneyline", "away", "close", "odds")) or \
                _number(_nested(odds, "moneyline", "away", "open", "odds"))
        spread = _number(odds.get("spread"))
        if spread is None:
            spread = _number(_nested(odds, "pointSpread", "home", "close", "line"))
        utc = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
        p = market_probability(home_ml, away_ml, spread)
        if p is None:
            return None
        return {
            "date": utc.astimezone(EASTERN).strftime("%Y-%m-%d"),
            "state": state,
            "homeTeamId": home,
            "awayTeamId": away,
            "provider": _nested(odds, "provider", "name"),
            "home_spread": spread,
            "over_under": _number(odds.get("overUnder")),
            "home_moneyline": None if home_ml is None else int(home_ml),
            "away_moneyline": None if away_ml is None else int(away_ml),
            "market_home_probability": round(float(p), 4),
        }
    except (KeyError, IndexError, TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Storing
# ---------------------------------------------------------------------------

def snapshot(db_path=TEAM_DB_PATH, days=None, session=None, base_url=ESPN_SCOREBOARD_URL, now=None):
    """Store today's and tomorrow's pregame lines. Returns a small manifest."""
    now = now or datetime.now(EASTERN)
    days = days or [now.date(), now.date() + timedelta(days=1)]
    lines = []
    for day in days:
        payload, _ = fetch_day(day, base_url, session)
        lines += [line for line in (parse_event_odds(e) for e in payload.get("events", [])) if line]
    captured = now.strftime("%Y-%m-%d %H:%M:%S")
    stored = skipped = 0
    conn = sqlite3.connect(db_path)
    try:
        ensure_schema(conn)
        for line in lines:
            if line["state"] != "pre":
                skipped += 1  # keep the last pregame line
                continue
            row = conn.execute(
                "SELECT gameId FROM games WHERE substr(gameDateTimeEst, 1, 10) = ? "
                "AND hometeamId = ? AND awayteamId = ?",
                (line["date"], line["homeTeamId"], line["awayTeamId"])).fetchone()
            if row is None:
                skipped += 1
                continue
            conn.execute(
                "INSERT OR REPLACE INTO market_lines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (int(row[0]), line["date"], line["homeTeamId"], line["awayTeamId"], line["provider"],
                 line["home_spread"], line["over_under"], line["home_moneyline"], line["away_moneyline"],
                 line["market_home_probability"], captured))
            stored += 1
        conn.commit()
    finally:
        conn.close()
    return {"lines_seen": len(lines), "stored": stored, "skipped": skipped, "captured_at": captured}


def load_lines(db_path=TEAM_DB_PATH, start=None, end=None):
    conn = sqlite3.connect(db_path)
    try:
        exists = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='market_lines'").fetchone()[0]
        if not exists:
            return pd.DataFrame(columns=["gameId", "gameDate", "homeTeamId", "awayTeamId", "provider",
                                         "home_spread", "over_under", "home_moneyline", "away_moneyline",
                                         "market_home_probability", "captured_at"])
        query, params = "SELECT * FROM market_lines WHERE 1 = 1", []
        if start:
            query += " AND gameDate >= ?"
            params.append(str(start))
        if end:
            query += " AND gameDate <= ?"
            params.append(str(end))
        return pd.read_sql_query(query, conn, params=params)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Comparing
# ---------------------------------------------------------------------------

def _log_loss(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def _grade(p, y):
    return {"log_loss": round(_log_loss(p, y), 4), "brier": round(float(np.mean((p - y) ** 2)), 4),
            "accuracy": round(float(np.mean((p >= 0.5) == (y == 1))), 4)}


def comparison(season, day, inputs, probabilities, db_path=TEAM_DB_PATH):
    """One day's model-vs-market gaps and the season's scorecard."""
    day = pd.Timestamp(day).normalize()
    start, end = league_views.season_window(season)
    lines = load_lines(db_path, start[:10], end[:10])
    teams = league_views.current_teams(db_path)
    games = league_views.load_games(start, end, db_path, league_views.SCOREBOARD_GAME_TYPES)
    games = games[games["gameId"].isin(lines["gameId"].astype(int))] if not lines.empty else games.iloc[0:0]
    if not games.empty:
        games = league_views.attach_pregame_probabilities(games, probabilities, inputs, day)
        games = games.merge(lines[["gameId", "market_home_probability", "home_spread", "over_under",
                                   "home_moneyline", "away_moneyline", "provider"]], on="gameId")

    label = str(day.date())
    today = games[games["gameDateTimeEst"].dt.strftime("%Y-%m-%d") == label] if not games.empty else games
    slate = []
    for game in today.itertuples(index=False):
        model, market = float(game.home_win_probability), float(game.market_home_probability)
        slate.append({
            "gameId": int(game.gameId),
            "tip": game.gameDateTimeEst.strftime("%Y-%m-%d %H:%M"),
            "home": {"teamId": int(game.homeTeamId), **league_views._team_brief(teams, game.homeTeamId)},
            "away": {"teamId": int(game.awayTeamId), **league_views._team_brief(teams, game.awayTeamId)},
            "model_home_probability": round(model, 4),
            "market_home_probability": round(market, 4),
            "gap": round(model - market, 4),
            "big_gap": abs(model - market) >= BIG_GAP,
            "home_spread": game.home_spread,
            "over_under": game.over_under,
            "home_moneyline": None if pd.isna(game.home_moneyline) else int(game.home_moneyline),
            "away_moneyline": None if pd.isna(game.away_moneyline) else int(game.away_moneyline),
            "provider": game.provider,
            "status": "final" if game.played else "scheduled",
            "home_won": None if not game.played else bool(int(game.winner) == int(game.homeTeamId)),
        })
    slate.sort(key=lambda g: -abs(g["gap"]))

    played = games[games["played"]].sort_values("gameDateTimeEst") if not games.empty else games
    scorecard = None
    running = []
    if not played.empty:
        y = (played["winner"].astype(int) == played["homeTeamId"]).astype(float).to_numpy()
        model = played["home_win_probability"].to_numpy(dtype=float)
        market = played["market_home_probability"].to_numpy(dtype=float)
        model_closer = np.abs(model - y) < np.abs(market - y)
        market_closer = np.abs(market - y) < np.abs(model - y)
        disagreed = (model >= 0.5) != (market >= 0.5)
        scorecard = {
            "games": int(len(y)),
            "model": _grade(model, y),
            "market": _grade(market, y),
            "model_closer": int(model_closer.sum()),
            "market_closer": int(market_closer.sum()),
            "picks_disagreed": int(disagreed.sum()),
            "model_won_disagreements": int((disagreed & ((model >= 0.5) == (y == 1))).sum()),
            "mean_abs_gap": round(float(np.mean(np.abs(model - market))), 4),
        }
        frame = pd.DataFrame({"day": played["gameDateTimeEst"].dt.strftime("%Y-%m-%d").to_numpy(),
                              "model": model_closer.astype(int), "market": market_closer.astype(int)})
        by_day = frame.groupby("day")[["model", "market"]].sum().cumsum()
        running = [{"date": d, "model_closer": int(r.model), "market_closer": int(r.market)}
                   for d, r in by_day.iterrows()]
    return {
        "season": int(season),
        "date": label,
        "lines_stored": int(len(lines)),
        "slate": slate,
        "scorecard": scorecard,
        "running": running,
        "big_gap": BIG_GAP,
    }
