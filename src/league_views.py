"""Standings, scoreboards, schedules and the production model's report card.

Factual views over the ``games`` table, joined to the frozen production
model's pregame probabilities. Nothing here trains or changes a model:

* A game that has been played and has a feature row gets the probability the
  model gave it before tip-off (``load_pregame_probabilities``: pregame Elo +
  the frozen boosted model on pregame rolling features). That is the record
  the report card grades.
* A game that has not been played (or has no feature row yet) is scored with
  ``frozen_matchup_probabilities`` -- strength frozen at the cutoff date,
  exactly as ``predict_matchup(game_date=...)`` does.

Standings and records count only games with a recorded winner, so they are
whatever the database holds (see ``data_status`` for how current that is).
"""

import json
import math
import sqlite3
from statistics import NormalDist

import numpy as np
import pandas as pd

try:
    from src.main import METRICS_PATH, TEAM_DB_PATH
    from src.forward_projection import frozen_matchup_probabilities, strength_table
    from src.playoffs import conference_of
except ImportError:  # pragma: no cover - direct-script support
    from main import METRICS_PATH, TEAM_DB_PATH
    from forward_projection import frozen_matchup_probabilities, strength_table
    from playoffs import conference_of


SCOREBOARD_GAME_TYPES = ("Regular Season", "Play-in Tournament", "Playoffs", "NBA Cup",
                         "Emirates NBA Cup")
UPSET_PROBABILITY = 0.40
CONFIDENCE_TIERS = [
    ("Toss-up", 0.50, 0.55),
    ("Lean", 0.55, 0.65),
    ("Strong", 0.65, 0.80),
    ("Lock", 0.80, 1.0001),
]
CALIBRATION_EDGES = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 1.0001]


def current_teams(db_path=TEAM_DB_PATH):
    """``{teamId: {"name", "city", "abbreviation", "conference"}}`` for the 30 franchises."""
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT teamId, teamCity, teamName, teamAbbrev FROM team_histories "
            "WHERE seasonActiveTill >= 2100"
        ).fetchall()
    return {
        int(team_id): {
            "name": f"{city} {name}".strip(),
            "city": city,
            "abbreviation": (abbrev or "").strip(),
            "conference": conference_of(int(team_id)),
        }
        for team_id, city, name, abbrev in rows
    }


def season_window(season):
    """Regular season + postseason span of a season (start year)."""
    return f"{int(season)}-09-01", f"{int(season) + 1}-07-31 23:59:59"


def load_games(start, end, db_path=TEAM_DB_PATH, game_types=("Regular Season",)):
    """Games between two timestamps (inclusive), played or not."""
    marks = ", ".join("?" * len(game_types))
    with sqlite3.connect(db_path) as connection:
        frame = pd.read_sql_query(
            f"""
            SELECT gameId, gameDateTimeEst, hometeamId AS homeTeamId, awayteamId AS awayTeamId,
                   homeScore, awayScore, winner, gameType, gameLabel
            FROM games
            WHERE gameType IN ({marks}) AND gameDateTimeEst >= ? AND gameDateTimeEst <= ?
            ORDER BY gameDateTimeEst, gameId
            """,
            connection,
            params=(*game_types, start, end),
        )
    frame["gameDateTimeEst"] = pd.to_datetime(frame["gameDateTimeEst"])
    for column in ("homeTeamId", "awayTeamId"):
        frame[column] = frame[column].astype(int)
    frame["winner"] = pd.to_numeric(frame["winner"], errors="coerce")
    frame["played"] = frame["winner"].notna()
    return frame


def _record(wins, losses):
    return f"{int(wins)}-{int(losses)}"


def _streak(results):
    """'W3' / 'L2' from a chronological list of booleans (True = win)."""
    if not results:
        return None
    last = results[-1]
    count = 0
    for result in reversed(results):
        if result != last:
            break
        count += 1
    return f"{'W' if last else 'L'}{count}"


# ---------------------------------------------------------------------------
# Standings
# ---------------------------------------------------------------------------

def standings(season, as_of=None, db_path=TEAM_DB_PATH):
    """Conference standings from recorded results through ``as_of`` (inclusive)."""
    start, end = season_window(season)
    if as_of is not None:
        end = min(end, f"{pd.Timestamp(as_of).date()} 23:59:59")
    games = load_games(start, end, db_path)
    teams = current_teams(db_path)
    played = games[games["played"]]
    lines = {
        team_id: {"results": [], "home": [0, 0], "road": [0, 0], "conf": [0, 0],
                  "points_for": 0, "points_against": 0}
        for team_id in teams
    }
    for game in played.itertuples(index=False):
        home_won = int(game.winner) == game.homeTeamId
        same_conference = conference_of(game.homeTeamId) == conference_of(game.awayTeamId)
        for team, opponent, is_home in ((game.homeTeamId, game.awayTeamId, True),
                                        (game.awayTeamId, game.homeTeamId, False)):
            line = lines.get(team)
            if line is None:
                continue
            won = home_won == is_home
            line["results"].append(won)
            line["home" if is_home else "road"][0 if won else 1] += 1
            if same_conference:
                line["conf"][0 if won else 1] += 1
            scored, allowed = ((game.homeScore, game.awayScore) if is_home
                               else (game.awayScore, game.homeScore))
            if scored is not None and allowed is not None and not pd.isna(scored):
                line["points_for"] += float(scored)
                line["points_against"] += float(allowed)

    rows = []
    for team_id, line in lines.items():
        results = line["results"]
        wins = sum(results)
        losses = len(results) - wins
        played_count = len(results)
        last_ten = results[-10:]
        info = teams[team_id]
        rows.append({
            "teamId": team_id,
            "teamName": info["name"],
            "abbreviation": info["abbreviation"],
            "conference": info["conference"],
            "wins": wins,
            "losses": losses,
            "games": played_count,
            "win_pct": round(wins / played_count, 3) if played_count else None,
            "home": _record(*line["home"]),
            "road": _record(*line["road"]),
            "conference_record": _record(*line["conf"]),
            "last_10": _record(sum(last_ten), len(last_ten) - sum(last_ten)),
            "streak": _streak(results),
            "points_per_game": round(line["points_for"] / played_count, 1) if played_count else None,
            "opponent_points_per_game": round(line["points_against"] / played_count, 1) if played_count else None,
            "point_differential": round((line["points_for"] - line["points_against"]) / played_count, 1)
            if played_count else None,
        })

    def order(row):
        return (-(row["win_pct"] if row["win_pct"] is not None else -1), -row["wins"],
                -(row["point_differential"] or 0), row["teamName"])

    conferences = {}
    for conference in ("East", "West"):
        members = sorted((row for row in rows if row["conference"] == conference), key=order)
        if members:
            leader = members[0]
            for rank, row in enumerate(members, 1):
                row["conference_rank"] = rank
                row["games_behind"] = ((leader["wins"] - row["wins"]) + (row["losses"] - leader["losses"])) / 2.0
        conferences[conference] = members
    league = sorted(rows, key=order)
    for rank, row in enumerate(league, 1):
        row["league_rank"] = rank
    latest = played["gameDateTimeEst"].max() if not played.empty else None
    return {
        "season": int(season),
        "as_of": None if as_of is None else str(pd.Timestamp(as_of).date()),
        "games_counted": int(len(played)),
        "latest_result": None if latest is None else str(latest.date()),
        "conferences": conferences,
        "league": league,
    }


# ---------------------------------------------------------------------------
# Pregame probabilities for any set of games
# ---------------------------------------------------------------------------

def attach_pregame_probabilities(games, probabilities, inputs, cutoff):
    """Add ``home_win_probability`` and ``probability_source`` to ``games``.

    Played games with a feature row keep the recorded pregame probability.
    Every other game is scored with strength frozen at ``cutoff``.
    """
    games = games.copy()
    recorded = probabilities.set_index("gameId")["home_win_probability"] if probabilities is not None else pd.Series(dtype=float)
    games["home_win_probability"] = games["gameId"].map(recorded)
    games["probability_source"] = np.where(games["home_win_probability"].notna(), "pregame", None)
    missing = games["home_win_probability"].isna()
    if missing.any():
        scored = frozen_matchup_probabilities(
            games.loc[missing, ["homeTeamId", "awayTeamId"]], cutoff, inputs
        )
        games.loc[missing, "home_win_probability"] = scored["home_win_probability"].to_numpy()
        games.loc[missing, "probability_source"] = "frozen"
    return games


# ---------------------------------------------------------------------------
# Scoreboard
# ---------------------------------------------------------------------------

def _neighbour_dates(day, db_path):
    with sqlite3.connect(db_path) as connection:
        before = connection.execute(
            "SELECT MAX(substr(gameDateTimeEst, 1, 10)) FROM games WHERE gameDateTimeEst < ?",
            (f"{day} 00:00:00",),
        ).fetchone()[0]
        after = connection.execute(
            "SELECT MIN(substr(gameDateTimeEst, 1, 10)) FROM games WHERE gameDateTimeEst > ?",
            (f"{day} 23:59:59",),
        ).fetchone()[0]
    return before, after


def scoreboard(day, inputs, probabilities, margin_fn=None, db_path=TEAM_DB_PATH):
    """Every game on ``day`` with the model's pregame call and, once played, the result.

    ``margin_fn(home, away, cutoff)`` optionally returns the predicted margin
    payload (``predict_margin``); a failure there leaves the margin out.
    """
    day = pd.Timestamp(day).normalize()
    label = str(day.date())
    games = load_games(f"{label} 00:00:00", f"{label} 23:59:59", db_path, SCOREBOARD_GAME_TYPES)
    teams = current_teams(db_path)
    before, after = _neighbour_dates(label, db_path)
    rows = []
    if not games.empty:
        games = attach_pregame_probabilities(games, probabilities, inputs, day)
    for game in games.itertuples(index=False):
        p_home = float(game.home_win_probability)
        favorite = game.homeTeamId if p_home >= 0.5 else game.awayTeamId
        row = {
            "gameId": int(game.gameId),
            "tip": game.gameDateTimeEst.strftime("%Y-%m-%d %H:%M"),
            "game_type": game.gameType,
            "label": game.gameLabel or None,
            "home": {"teamId": game.homeTeamId, **_team_brief(teams, game.homeTeamId)},
            "away": {"teamId": game.awayTeamId, **_team_brief(teams, game.awayTeamId)},
            "home_win_probability": round(p_home, 4),
            "probability_source": game.probability_source,
            "favorite_team_id": int(favorite),
            "status": "final" if game.played else "scheduled",
            "home_score": None if pd.isna(game.homeScore) else int(game.homeScore),
            "away_score": None if pd.isna(game.awayScore) else int(game.awayScore),
        }
        if game.played:
            winner = int(game.winner)
            winner_probability = p_home if winner == game.homeTeamId else 1.0 - p_home
            row.update({
                "winner_team_id": winner,
                "model_correct": winner == favorite,
                "upset": winner_probability < UPSET_PROBABILITY,
                "winner_pregame_probability": round(winner_probability, 4),
            })
        if margin_fn is not None:
            try:
                margin = margin_fn(game.homeTeamId, game.awayTeamId, day)
                row["predicted_home_margin"] = margin.get("predicted_home_margin")
                row["margin_interval_80"] = margin.get("margin_interval_80")
                row["predicted_total_points"] = margin.get("predicted_total_points")
                row["predicted_home_points"] = margin.get("predicted_home_points")
                row["predicted_away_points"] = margin.get("predicted_away_points")
            except (ValueError, KeyError):
                pass
        rows.append(row)
    finals = [row for row in rows if row["status"] == "final"]
    return {
        "date": label,
        "previous_date": before,
        "next_date": after,
        "games": rows,
        "summary": {
            "games": len(rows),
            "final": len(finals),
            "scheduled": len(rows) - len(finals),
            "model_correct": sum(1 for row in finals if row.get("model_correct")),
            "upsets": sum(1 for row in finals if row.get("upset")),
        },
    }


def _team_brief(teams, team_id):
    info = teams.get(int(team_id), {})
    return {"name": info.get("name", str(team_id)), "abbreviation": info.get("abbreviation", "")}


# ---------------------------------------------------------------------------
# Team schedule and strength of schedule
# ---------------------------------------------------------------------------

def _back_to_backs(dates):
    dates = sorted(pd.Timestamp(d).normalize() for d in dates)
    return sum(1 for a, b in zip(dates, dates[1:]) if (b - a).days == 1)


def team_schedule(team_id, season, inputs, probabilities, as_of, db_path=TEAM_DB_PATH):
    """A team's regular season: results so far and win chances in the games left."""
    team_id = int(team_id)
    start, end = season_window(season)
    games = load_games(start, end, db_path)
    games = games[(games["homeTeamId"] == team_id) | (games["awayTeamId"] == team_id)]
    if games.empty:
        raise ValueError(f"No regular-season games for teamId {team_id} in season {season}.")
    cutoff = pd.Timestamp(as_of).normalize()
    games = attach_pregame_probabilities(games, probabilities, inputs, cutoff)
    teams = current_teams(db_path)
    rows, wins, losses = [], 0, 0
    for game in games.itertuples(index=False):
        is_home = game.homeTeamId == team_id
        opponent = game.awayTeamId if is_home else game.homeTeamId
        p_team = float(game.home_win_probability if is_home else 1.0 - game.home_win_probability)
        row = {
            "gameId": int(game.gameId),
            "date": str(game.gameDateTimeEst.date()),
            "home": bool(is_home),
            "opponent_id": int(opponent),
            "opponent": _team_brief(teams, opponent)["name"],
            "opponent_abbreviation": _team_brief(teams, opponent)["abbreviation"],
            "win_probability": round(p_team, 4),
            "probability_source": game.probability_source,
            "played": bool(game.played),
        }
        if game.played:
            won = int(game.winner) == team_id
            wins += won
            losses += not won
            scored, allowed = ((game.homeScore, game.awayScore) if is_home else (game.awayScore, game.homeScore))
            row.update({
                "result": "W" if won else "L",
                "score": None if pd.isna(scored) else f"{int(scored)}-{int(allowed)}",
                "record_after": _record(wins, losses),
            })
        rows.append(row)
    remaining = [row for row in rows if not row["played"]]
    return {
        "team_id": team_id,
        "team_name": _team_brief(teams, team_id)["name"],
        "season": int(season),
        "as_of": str(cutoff.date()),
        "record": _record(wins, losses),
        "games": rows,
        "remaining": {
            "games": len(remaining),
            "home": sum(1 for row in remaining if row["home"]),
            "road": sum(1 for row in remaining if not row["home"]),
            "expected_wins": round(sum(row["win_probability"] for row in remaining), 1),
            "back_to_backs": _back_to_backs(row["date"] for row in remaining),
        },
    }


def strength_of_schedule(season, inputs, as_of, db_path=TEAM_DB_PATH):
    """Every team's schedule difficulty: average opponent strength, played and remaining.

    Opponent strength is the production model's chance to beat an average
    team (``strength_table``), frozen at ``as_of``. Expected remaining wins
    sum the team's frozen win probability in each remaining game, so they
    include home court and rest-free strength but nothing else.
    """
    cutoff = pd.Timestamp(as_of).normalize()
    start, end = season_window(season)
    games = load_games(start, end, db_path)
    if games.empty:
        raise ValueError(f"No regular-season games found for season {season}.")
    strengths = {row["teamId"]: row["win_probability_vs_average"]
                 for row in strength_table(inputs, cutoff)}
    remaining = games[~games["played"]]
    scored = None
    if not remaining.empty:
        scored = frozen_matchup_probabilities(remaining[["homeTeamId", "awayTeamId"]], cutoff, inputs)
        remaining = remaining.assign(home_win_probability=scored["home_win_probability"].to_numpy())
    teams = current_teams(db_path)
    rows = []
    for team_id, info in teams.items():
        if team_id not in strengths:
            continue
        mine = games[(games["homeTeamId"] == team_id) | (games["awayTeamId"] == team_id)]
        opponents = np.where(mine["homeTeamId"] == team_id, mine["awayTeamId"], mine["homeTeamId"])
        played_mask = mine["played"].to_numpy()
        opp_strength = np.array([strengths.get(int(o), np.nan) for o in opponents])
        left = remaining[(remaining["homeTeamId"] == team_id) | (remaining["awayTeamId"] == team_id)]
        expected = 0.0
        if not left.empty:
            p = np.where(left["homeTeamId"] == team_id, left["home_win_probability"],
                         1.0 - left["home_win_probability"])
            expected = float(np.sum(p))
        rows.append({
            "teamId": team_id,
            "teamName": info["name"],
            "abbreviation": info["abbreviation"],
            "conference": info["conference"],
            "strength": round(float(strengths[team_id]), 4),
            "played_games": int(played_mask.sum()),
            "played_opponent_strength": _mean_or_none(opp_strength[played_mask]),
            "remaining_games": int((~played_mask).sum()),
            "remaining_opponent_strength": _mean_or_none(opp_strength[~played_mask]),
            "remaining_home_games": int(((mine["homeTeamId"] == team_id) & ~mine["played"]).sum()),
            "remaining_back_to_backs": _back_to_backs(left["gameDateTimeEst"]),
            "expected_remaining_wins": round(expected, 1),
        })
    key = "remaining_opponent_strength" if any(r["remaining_games"] for r in rows) else "played_opponent_strength"
    ranked = sorted(rows, key=lambda row: -(row[key] if row[key] is not None else -1))
    for rank, row in enumerate(ranked, 1):
        row["difficulty_rank"] = rank
    return {
        "season": int(season),
        "as_of": str(cutoff.date()),
        "ranked_by": key,
        "teams": ranked,
    }


def _mean_or_none(values):
    values = values[np.isfinite(values)]
    return round(float(values.mean()), 4) if len(values) else None


# ---------------------------------------------------------------------------
# Report card
# ---------------------------------------------------------------------------

def holdout_start(metrics_path=METRICS_PATH):
    """First date of the production model's chronological holdout."""
    try:
        return json.loads(metrics_path.read_text(encoding="utf-8")).get("split_start")
    except (OSError, ValueError):
        return None


def _log_loss(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def _grade(p_home, target):
    picks_home = p_home >= 0.5
    correct = picks_home == (target == 1)
    return {
        "games": int(len(target)),
        "correct": int(correct.sum()),
        "accuracy": round(float(correct.mean()), 4) if len(target) else None,
        "brier": round(float(np.mean((p_home - target) ** 2)), 4) if len(target) else None,
        "log_loss": round(_log_loss(p_home, target), 4) if len(target) else None,
    }


def report_card(season, probabilities, db_path=TEAM_DB_PATH, split_start=None, n_misses=10):
    """Grade the model's pregame calls for one season's played regular-season games."""
    graded = probabilities[(probabilities["season"] == int(season)) & probabilities["target"].notna()].copy()
    seasons = (
        probabilities[probabilities["target"].notna()].groupby("season").size().sort_index()
    )
    if graded.empty:
        raise ValueError(f"No graded games for season {season}.")
    graded["gameDateTimeEst"] = pd.to_datetime(graded["gameDateTimeEst"])
    graded = graded.sort_values(["gameDateTimeEst", "gameId"]).reset_index(drop=True)
    p = graded["home_win_probability"].to_numpy(dtype=float)
    y = graded["target"].to_numpy(dtype=float)
    favorite_probability = np.maximum(p, 1 - p)
    correct = (p >= 0.5) == (y == 1)

    overall = _grade(p, y)
    baselines = {
        "always_home": round(float(np.mean(y == 1)), 4),
        "elo_only": _grade(graded["elo_probability"].to_numpy(dtype=float), y),
        "coin_flip_brier": 0.25,
    }

    tiers = []
    for label, low, high in CONFIDENCE_TIERS:
        mask = (favorite_probability >= low) & (favorite_probability < high)
        tiers.append({
            "tier": label,
            "range": [low, min(high, 1.0)],
            "games": int(mask.sum()),
            "favorite_won": round(float(correct[mask].mean()), 4) if mask.any() else None,
            "average_confidence": round(float(favorite_probability[mask].mean()), 4) if mask.any() else None,
        })

    calibration = []
    for low, high in zip(CALIBRATION_EDGES, CALIBRATION_EDGES[1:]):
        mask = (favorite_probability >= low) & (favorite_probability < high)
        if not mask.any():
            continue
        calibration.append({
            "bin": [low, min(high, 1.0)],
            "games": int(mask.sum()),
            "predicted": round(float(favorite_probability[mask].mean()), 4),
            "actual": round(float(correct[mask].mean()), 4),
        })

    graded["month"] = graded["gameDateTimeEst"].dt.strftime("%Y-%m")
    by_month = []
    for month, group in graded.groupby("month", sort=True):
        by_month.append({"month": month, **_grade(group["home_win_probability"].to_numpy(dtype=float),
                                                  group["target"].to_numpy(dtype=float))})

    cumulative = []
    graded["correct"] = correct
    for day, group in graded.groupby(graded["gameDateTimeEst"].dt.date, sort=True):
        done = graded[graded["gameDateTimeEst"].dt.date <= day]
        cumulative.append({"date": str(day), "games": int(len(done)),
                           "accuracy": round(float(done["correct"].mean()), 4)})

    winner_probability = np.where(y == 1, p, 1 - p)
    graded["winner_probability"] = winner_probability
    scores = _scores(graded["gameId"].tolist(), db_path)
    teams = current_teams(db_path)
    misses = []
    for game in graded.nsmallest(n_misses, "winner_probability").itertuples(index=False):
        home, away = int(game.homeTeamId), int(game.awayTeamId)
        winner = home if game.target == 1 else away
        score = scores.get(int(game.gameId), (None, None))
        misses.append({
            "gameId": int(game.gameId),
            "date": str(game.gameDateTimeEst.date()),
            "home": {"teamId": home, **_team_brief(teams, home)},
            "away": {"teamId": away, **_team_brief(teams, away)},
            "home_score": score[0],
            "away_score": score[1],
            "winner_team_id": winner,
            "winner_pregame_probability": round(float(game.winner_probability), 4),
        })

    in_holdout = None
    if split_start:
        in_holdout = int((graded["gameDateTimeEst"] >= pd.Timestamp(split_start)).sum())
    streak = 0
    for value in reversed(correct.tolist()):
        if not value:
            break
        streak += 1
    return {
        "season": int(season),
        "overall": overall,
        "baselines": baselines,
        "confidence_tiers": tiers,
        "calibration": calibration,
        "by_month": by_month,
        "cumulative": cumulative,
        "biggest_misses": misses,
        "current_correct_streak": streak,
        "holdout_start": split_start,
        "games_in_holdout": in_holdout,
        "all_out_of_sample": None if in_holdout is None else in_holdout == overall["games"],
        "seasons_available": [{"season": int(s), "games": int(n)} for s, n in seasons.items()],
        "latest_graded_game": str(graded["gameDateTimeEst"].max().date()),
    }


def _scores(game_ids, db_path):
    if not game_ids:
        return {}
    with sqlite3.connect(db_path) as connection:
        rows = []
        for chunk in range(0, len(game_ids), 900):
            ids = game_ids[chunk:chunk + 900]
            rows += connection.execute(
                f"SELECT gameId, homeScore, awayScore FROM games WHERE gameId IN ({', '.join('?' * len(ids))})",
                ids,
            ).fetchall()
    return {
        int(game_id): (None if home is None else int(home), None if away is None else int(away))
        for game_id, home, away in rows
    }


# ---------------------------------------------------------------------------
# In-game win probability (random-walk model)
# ---------------------------------------------------------------------------

REGULATION_MINUTES = 48.0


_STANDARD_NORMAL = NormalDist()


def _normal_cdf(x):
    return _STANDARD_NORMAL.cdf(x)


def _normal_ppf(p):
    """Inverse standard normal CDF, clipped away from 0 and 1."""
    return _STANDARD_NORMAL.inv_cdf(min(max(float(p), 1e-9), 1 - 1e-9))


def in_game_win_probability(pregame_home_probability, home_lead, minutes_remaining, margin_sd):
    """Home win probability mid-game (Stern 1994 random-walk model).

    The final margin is the current lead plus a normal remainder whose mean
    is the pregame expected margin scaled to the time left and whose standard
    deviation is ``margin_sd * sqrt(fraction left)``. The pregame expected
    margin is backed out of the production model's pregame probability, so
    the curve starts exactly where the pregame call did.
    """
    fraction = max(0.0, float(minutes_remaining)) / REGULATION_MINUTES
    if fraction <= 0:
        if home_lead > 0:
            return 1.0
        if home_lead < 0:
            return 0.0
        return float(pregame_home_probability)  # tied at the buzzer: overtime
    mu = margin_sd * _normal_ppf(pregame_home_probability)
    return _normal_cdf((home_lead + mu * fraction) / (margin_sd * math.sqrt(fraction)))
