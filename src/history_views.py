"""Rivalries, season recaps and career arcs: history views over recorded data.

* ``rivalry``: every meeting between two franchises (regular season, play-in
  and playoffs), streaks, margins, playoff series and the production model's
  record on those games.
* ``season_recap``: a season told from data already stored -- best records
  and the champion, the biggest upsets, the model's best and worst calls,
  the teams that beat or missed their preseason projection by the most, and
  the turning points in the stored playoff odds.
* ``career_arc``: a player's season-by-season box impact next to the careers
  of the most similar players at the same age, with the range those careers
  went on to take.

Nothing here trains a model; the numbers are recorded results, the frozen
model's stored pregame probabilities and the player reference's box impact.
"""

import sqlite3

import numpy as np
import pandas as pd

try:
    from src.main import TEAM_DB_PATH
    from src import league_views, odds_history
    from src import player_profile as profiles
    from src.forward_projection import project_from_date, season_schedule
except ImportError:  # pragma: no cover - direct-script support
    from main import TEAM_DB_PATH
    import league_views
    import odds_history
    import player_profile as profiles
    from forward_projection import project_from_date, season_schedule


RIVALRY_TYPES = ("Regular Season", "Play-in Tournament", "Playoffs")


def _season(stamp):
    return stamp.year - (stamp.month < 9)


def _streaks(results):
    """Current streak and the longest run for each side, from oldest-first 'A'/'B' results."""
    longest = {"A": 0, "B": 0}
    run_side, run = None, 0
    for result in results:
        run = run + 1 if result == run_side else 1
        run_side = result
        longest[result] = max(longest[result], run)
    return {"current": {"side": run_side, "games": run} if run_side else None, "longest": longest}


def rivalry(team_a, team_b, probabilities, db_path=TEAM_DB_PATH):
    """Every meeting between two franchises with the model's record on them."""
    team_a, team_b = int(team_a), int(team_b)
    with sqlite3.connect(db_path) as connection:
        frame = pd.read_sql_query(
            f"""
            SELECT gameId, gameDateTimeEst, hometeamId, awayteamId, homeScore, awayScore, winner,
                   gameType, gameLabel
            FROM games
            WHERE gameType IN ({", ".join("?" * len(RIVALRY_TYPES))}) AND winner IS NOT NULL
              AND ((hometeamId = ? AND awayteamId = ?) OR (hometeamId = ? AND awayteamId = ?))
            ORDER BY gameDateTimeEst, gameId
            """,
            connection, params=(*RIVALRY_TYPES, team_a, team_b, team_b, team_a))
    teams = league_views.current_teams(db_path)
    names = {team: league_views._team_brief(teams, team) for team in (team_a, team_b)}
    if frame.empty:
        return {"team_a": {"teamId": team_a, **names[team_a]}, "team_b": {"teamId": team_b, **names[team_b]},
                "games": 0, "meetings": [], "seasons": [], "series": [], "summary": None}
    frame["gameDateTimeEst"] = pd.to_datetime(frame["gameDateTimeEst"])
    recorded = probabilities.set_index("gameId")["home_win_probability"] if probabilities is not None else {}
    meetings = []
    for game in frame.itertuples(index=False):
        a_home = int(game.hometeamId) == team_a
        a_score = game.homeScore if a_home else game.awayScore
        b_score = game.awayScore if a_home else game.homeScore
        p_home = recorded.get(int(game.gameId)) if hasattr(recorded, "get") else None
        p_a = None if p_home is None or pd.isna(p_home) else (p_home if a_home else 1 - p_home)
        a_won = int(game.winner) == team_a
        meetings.append({
            "gameId": int(game.gameId),
            "date": game.gameDateTimeEst.strftime("%Y-%m-%d"),
            "season": _season(game.gameDateTimeEst),
            "game_type": game.gameType,
            "label": game.gameLabel or None,
            "a_home": a_home,
            "a_score": None if pd.isna(a_score) else int(a_score),
            "b_score": None if pd.isna(b_score) else int(b_score),
            "winner": "A" if a_won else "B",
            "margin": None if pd.isna(a_score) or pd.isna(b_score) else int(a_score) - int(b_score),
            "model_p_a": None if p_a is None else round(float(p_a), 4),
            "model_correct": None if p_a is None else bool((p_a >= 0.5) == a_won),
        })
    margins = [m["margin"] for m in meetings if m["margin"] is not None]
    graded = [m for m in meetings if m["model_correct"] is not None]

    def record(rows):
        return {"a_wins": sum(1 for m in rows if m["winner"] == "A"),
                "b_wins": sum(1 for m in rows if m["winner"] == "B")}

    seasons = []
    for season, rows in pd.DataFrame(meetings).groupby("season"):
        rows = rows.to_dict("records")
        season_margins = [m["margin"] for m in rows if m["margin"] is not None]
        seasons.append({"season": int(season), "label": profiles.season_label(season), **record(rows),
                        "mean_margin": round(float(np.mean(season_margins)), 1) if season_margins else None})
    series = []
    for season, rows in pd.DataFrame([m for m in meetings if m["game_type"] == "Playoffs"]).groupby("season") \
            if any(m["game_type"] == "Playoffs" for m in meetings) else []:
        rows = rows.to_dict("records")
        tally = record(rows)
        series.append({"season": int(season), "label": profiles.season_label(season), **tally,
                       "winner": "A" if tally["a_wins"] > tally["b_wins"] else "B",
                       "round": rows[0].get("label")})
    biggest = {
        side: max((m for m in meetings if m["winner"] == side and m["margin"] is not None),
                  key=lambda m: abs(m["margin"]), default=None)
        for side in ("A", "B")
    }
    return {
        "team_a": {"teamId": team_a, **names[team_a]},
        "team_b": {"teamId": team_b, **names[team_b]},
        "games": len(meetings),
        "summary": {
            **record(meetings),
            "regular_season": record([m for m in meetings if m["game_type"] == "Regular Season"]),
            "playoffs": record([m for m in meetings if m["game_type"] == "Playoffs"]),
            "mean_margin": round(float(np.mean(margins)), 2) if margins else None,
            "streaks": _streaks([m["winner"] for m in meetings]),
            "biggest_win": biggest,
            "first_meeting": meetings[0]["date"],
            "last_meeting": meetings[-1]["date"],
            "model": {
                "games": len(graded),
                "correct": sum(1 for m in graded if m["model_correct"]),
                "accuracy": round(sum(1 for m in graded if m["model_correct"]) / len(graded), 4) if graded else None,
                "brier": round(float(np.mean([(m["model_p_a"] - (m["winner"] == "A")) ** 2 for m in graded])), 4)
                if graded else None,
            },
        },
        "seasons": seasons,
        "series": series,
        "meetings": meetings[::-1],
    }


# ---------------------------------------------------------------------------
# Season recap
# ---------------------------------------------------------------------------

def _champion(season, db_path):
    start, end = league_views.season_window(season)
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT winner, gameDateTimeEst FROM games WHERE gameType = 'Playoffs' AND winner IS NOT NULL "
            "AND gameDateTimeEst >= ? AND gameDateTimeEst <= ? ORDER BY gameDateTimeEst DESC LIMIT 1",
            (start, end)).fetchone()
        remaining = connection.execute(
            "SELECT COUNT(*) FROM games WHERE gameType = 'Playoffs' AND winner IS NULL "
            "AND gameDateTimeEst >= ? AND gameDateTimeEst <= ?", (start, end)).fetchone()[0]
    if row is None or remaining:
        return None
    return int(row[0])


def _turning_points(season, db_path, limit=8):
    history = odds_history.history(season, db_path=db_path)
    swings = []
    for team in history["teams"]:
        points = team["points"]
        for before, after in zip(points, points[1:]):
            for key, label in (("p_made_playoffs", "playoff odds"), ("p_champion", "title odds")):
                if before[key] is None or after[key] is None:
                    continue
                change = after[key] - before[key]
                swings.append({"teamId": team["teamId"], "teamName": team["teamName"],
                               "abbreviation": team["abbreviation"], "measure": label,
                               "from": before["as_of"], "to": after["as_of"],
                               "before": before[key], "after": after[key], "change": round(change, 4)})
    swings.sort(key=lambda s: -abs(s["change"]))
    seen, out = set(), []
    for swing in swings:  # one swing per team and measure
        key = (swing["teamId"], swing["measure"])
        if key in seen:
            continue
        seen.add(key)
        out.append(swing)
        if len(out) == limit:
            break
    return {"snapshots": len(history["dates"]), "swings": out}


def season_recap(season, inputs, probabilities, db_path=TEAM_DB_PATH, n_simulations=500):
    """The season in data: records, champion, upsets, calls, surprises, turning points."""
    season = int(season)
    teams = league_views.current_teams(db_path)
    table = league_views.standings(season, db_path=db_path)
    if not table["games_counted"]:
        raise ValueError(f"No results recorded for {profiles.season_label(season)}.")
    best = {conference: rows[0] for conference, rows in table["conferences"].items() if rows}
    start, end = league_views.season_window(season)
    games = league_views.load_games(start, end, db_path, league_views.SCOREBOARD_GAME_TYPES)
    played = games[games["played"]]
    graded = played.merge(probabilities[["gameId", "home_win_probability", "elo_probability"]], on="gameId")

    def brief(team):
        return {"teamId": int(team), **league_views._team_brief(teams, team)}

    def call(game):
        home_won = int(game.winner) == int(game.homeTeamId)
        p_winner = game.home_win_probability if home_won else 1 - game.home_win_probability
        return {"gameId": int(game.gameId), "date": game.gameDateTimeEst.strftime("%Y-%m-%d"),
                "home": brief(game.homeTeamId), "away": brief(game.awayTeamId),
                "home_score": int(game.homeScore), "away_score": int(game.awayScore),
                "winner_team_id": int(game.winner), "winner_probability": round(float(p_winner), 4),
                "model_home_probability": round(float(game.home_win_probability), 4),
                "elo_home_probability": round(float(game.elo_probability), 4)}

    calls = [call(game) for game in graded.itertuples(index=False)]
    upsets = sorted(calls, key=lambda c: c["winner_probability"])[:10]
    misses = upsets[:5]
    # Best calls: the model and plain Elo picked different winners and the model was right.
    disagreements = [c for c in calls if (c["model_home_probability"] >= 0.5) != (c["elo_home_probability"] >= 0.5)
                     and c["winner_probability"] >= 0.5]
    best_calls = sorted(disagreements, key=lambda c: -abs(c["model_home_probability"] - c["elo_home_probability"]))[:5]

    surprises = []
    schedule = season_schedule(inputs, season)
    first_game = schedule["gameDateTimeEst"].min()
    try:
        preseason = project_from_date(season, first_game, inputs, n_simulations=n_simulations, schedule=schedule)
    except ValueError:
        preseason = None
    if preseason is not None:
        actual = {row["teamId"]: row for rows in table["conferences"].values() for row in rows}
        for row in preseason["projected_standings"]:
            team = actual.get(int(row["teamId"]))
            if not team:
                continue
            games_played = team["wins"] + team["losses"]
            scheduled = row["current_wins"] + row["current_losses"] + row["games_remaining"]
            expected = row["mean_wins"] * games_played / max(1, scheduled)
            surprises.append({**brief(row["teamId"]), "preseason_wins": round(float(row["mean_wins"]), 1),
                              "expected_to_date": round(float(expected), 1), "wins": team["wins"],
                              "losses": team["losses"], "difference": round(float(team["wins"] - expected), 1)})
        surprises.sort(key=lambda s: -abs(s["difference"]))

    card = None
    if not graded.empty:
        try:
            card = league_views.report_card(season, probabilities, db_path=db_path,
                                            split_start=league_views.holdout_start())
        except ValueError:
            card = None
    champion = _champion(season, db_path)
    return {
        "season": season,
        "label": profiles.season_label(season),
        "games_played": int(len(played)),
        "complete": bool(not games.empty and games["played"].all()),
        "champion": brief(champion) if champion else None,
        "best_records": {conference: {**brief(row["teamId"]), "wins": row["wins"], "losses": row["losses"]}
                         for conference, row in best.items()},
        "upsets": upsets,
        "worst_calls": misses,
        "best_calls": best_calls,
        "surprises": {"over": [s for s in surprises if s["difference"] > 0][:5],
                      "under": [s for s in surprises if s["difference"] < 0][:5]},
        "turning_points": _turning_points(season, db_path),
        "model": None if card is None else {"overall": card.get("overall")},
    }


# ---------------------------------------------------------------------------
# Career arcs
# ---------------------------------------------------------------------------

ARC_YEARS_AHEAD = 5
ARC_COMPS_SHOWN = 8
ARC_REGULAR_MINUTES = 500


def career_arc(person_id, season=None, years_ahead=ARC_YEARS_AHEAD, comps_shown=ARC_COMPS_SHOWN):
    """A player's box impact by season next to similar players' whole careers."""
    reference = profiles.league_reference()
    frame = reference["frame"]
    mine = frame[frame["personId"] == int(person_id)].sort_values("season")
    if mine.empty:
        raise ValueError(f"personId {person_id} has no seasons in the player reference.")
    if season is None:
        # The latest season with real minutes: a few games into a new season
        # is too little to match comparables on.
        settled = mine[mine["minutes"] >= ARC_REGULAR_MINUTES]
        season = int((settled if not settled.empty else mine)["season"].max())
    season = int(season)
    row = profiles._row_for(frame, person_id, season)
    if row is None:
        raise ValueError(f"personId {person_id} did not play in {profiles.season_label(season)}.")
    qualified = frame[frame["minutes"] >= profiles.QUALIFY_MINUTES]
    by_season = {s: np.sort(g["box_impact"].to_numpy(dtype=float)) for s, g in qualified.groupby("season")}

    def percentile(value, of_season):
        values = by_season.get(int(of_season))
        if values is None or not len(values) or not np.isfinite(value):
            return None
        return round(float(100.0 * np.searchsorted(values, value, side="right") / len(values)), 1)

    def point(r, offset):
        return {"offset": int(offset), "season": int(r["season"]), "age": profiles._clean(r["age"]),
                "games": int(r["games"]), "mpg": round(float(r["mpg"]), 1),
                "box_impact": profiles._clean(r["box_impact"], 2), "wins_added": profiles._clean(r["wins_added"], 1),
                "percentile": percentile(r["box_impact"], r["season"])}

    arc = [point(r, r["season"] - season) for r in mine.to_dict("records")]
    shown, future = profiles._comps(frame, row, n_shown=comps_shown, n_projection=30)
    last_data = int(row["last_season_in_data"])
    grouped = {int(p): g.set_index("season") for p, g in frame[frame["personId"].isin(
        set(shown["personId"]) | set(future["personId"]))].groupby("personId")}

    comps = []
    for comp in shown.to_dict("records"):
        seasons = grouped[int(comp["personId"])]
        comps.append({
            "person_id": int(comp["personId"]), "name": comp["name"], "matched_season": int(comp["season"]),
            "age": profiles._clean(comp["age"]), "similarity": profiles._similarity(comp["distance"]),
            "arc": [point(r, r["season"] - int(comp["season"])) for r in seasons.reset_index().to_dict("records")],
        })

    band = []
    for offset in range(1, int(years_ahead) + 1):
        values, playing, known = [], 0, 0
        for comp in future.to_dict("records"):
            target = int(comp["season"]) + offset
            if target > last_data:
                continue  # not played yet: unknown, not retired
            known += 1
            seasons = grouped[int(comp["personId"])]
            if target in seasons.index and seasons.loc[target, "minutes"] >= ARC_REGULAR_MINUTES:
                playing += 1
                values.append(float(seasons.loc[target, "box_impact"]))
        band.append({
            "offset": offset, "season": season + offset,
            "age": None if row["age"] is None or not np.isfinite(row["age"]) else int(row["age"]) + offset,
            "comps_known": known,
            "share_still_regulars": round(playing / known, 3) if known else None,
            "box_impact_p25": round(float(np.quantile(values, 0.25)), 2) if len(values) >= 3 else None,
            "box_impact_median": round(float(np.median(values)), 2) if len(values) >= 3 else None,
            "box_impact_p75": round(float(np.quantile(values, 0.75)), 2) if len(values) >= 3 else None,
        })
    peak = max(arc, key=lambda p: p["box_impact"] if p["box_impact"] is not None else -1e9)
    return {
        "person_id": int(person_id), "name": row["name"], "season": season,
        "season_label": profiles.season_label(season), "age": profiles._clean(row["age"]),
        "arc": arc, "peak": peak, "comps": comps, "band": band,
        "method": ("Box impact: change in team net rating per 100 possessions with the player on the floor "
                   "(the player reference's calibrated box-score model). Comparables: the most similar "
                   "player-seasons within a year of the same age. The band is the middle half of what "
                   f"the 30 closest comparables did in the following seasons while they were still "
                   f"regulars ({ARC_REGULAR_MINUTES}+ minutes); 'still regulars' is the share who were."),
    }
