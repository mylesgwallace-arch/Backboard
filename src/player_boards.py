"""League leaderboards, character-select player cards and similar-player search.

All three read the player-season reference that the player pages already use
(``player_profile.league_reference``: complete season tables since 1985-86,
league-relative z-scores, and the calibrated box-impact model). Leaders are
recorded box-score numbers; ratings on the cards are percentiles within the
season; "similar players" is the same weighted z-score distance the Outlook
tab's comparables use. Nothing here is a new model.
"""

import math
import sqlite3
import threading
from functools import lru_cache

import numpy as np
import pandas as pd

try:
    from src.main import TEAM_DB_PATH
    from src import player_profile as profiles
    from src.season_tables import RECOVERED_TEAM_ID
except ImportError:  # pragma: no cover - direct-script support
    from main import TEAM_DB_PATH
    import player_profile as profiles
    from season_tables import RECOVERED_TEAM_ID


# stat key -> (label, kind, column or (numerator, denominator), higher is better, digits)
LEADER_STATS = {
    "pts": ("Points per game", "per_game", "pts", True, 1),
    "trb": ("Rebounds per game", "per_game", "trb", True, 1),
    "ast": ("Assists per game", "per_game", "ast", True, 1),
    "stl": ("Steals per game", "per_game", "stl", True, 1),
    "blk": ("Blocks per game", "per_game", "blk", True, 1),
    "tpm": ("Threes made per game", "per_game", "tpm", True, 1),
    "mpg": ("Minutes per game", "column", "mpg", True, 1),
    "ts": ("True shooting %", "column", "ts", True, 3),
    "tp_pct": ("Three-point %", "column", "tp_pct", True, 3),
    "ft_pct": ("Free-throw %", "column", "ft_pct", True, 3),
    "pts_100": ("Points per 100 possessions", "column", "pts_100", True, 1),
    "box_impact": ("Box impact (model)", "column", "box_impact", True, 2),
    "wins_added": ("Wins added (model)", "column", "wins_added", True, 1),
}
# Minimum attempts per game played for the percentage leaderboards, in the
# spirit of the NBA's own qualifiers (scaled to games played).
ATTEMPT_MINIMUMS = {"ts": ("tsa", 5.0), "tp_pct": ("tpa", 1.5), "ft_pct": ("fta", 1.5)}
QUALIFY_GAME_SHARE = 0.70

# The six ratings on a player card: label -> stats whose within-season
# percentiles are averaged.
CARD_RATINGS = [
    ("scoring", "Scoring", ["pts_100"]),
    ("shooting", "Shooting", ["ts"]),
    ("playmaking", "Playmaking", ["ast_100"]),
    ("rebounding", "Rebounding", ["trb_100"]),
    ("defense", "Defense (box)", ["stl_100", "blk_100"]),
    ("impact", "Impact", ["box_impact"]),
]
# A card needs about six minutes a game over the season so far (capped at
# the 500-minute qualifier), so early-season cards still exist.
CARD_MINUTES_PER_TEAM_GAME = 6.0
MIN_GAMES_FOR_DEFAULT = 20


def _frame():
    frame = profiles.league_reference()["frame"].copy()
    frame["trb"] = frame["orb"] + frame["drb"]
    return frame


def seasons_available():
    frame = profiles.league_reference()["frame"]
    games = frame.groupby("season")["season_games"].max()
    return [int(s) for s in sorted(frame["season"].unique())], games


def default_season():
    """Latest season far enough along (median team games) for stable percentiles."""
    seasons, games = seasons_available()
    for season in reversed(seasons):
        if (games.get(season) or 0) >= MIN_GAMES_FOR_DEFAULT:
            return season
    return seasons[-1]


def _qualifier_games(season_frame):
    team_games = float(np.nanmax(season_frame["season_games"].to_numpy(dtype=float))) \
        if season_frame["season_games"].notna().any() else float(season_frame["games"].max())
    return max(1, int(math.ceil(QUALIFY_GAME_SHARE * team_games)))


def stat_leaders(season=None, stat="pts", limit=25, min_games=None):
    """Top players of a season in one stat, among qualified players."""
    if stat not in LEADER_STATS:
        raise ValueError(f"Unknown stat '{stat}'. Choose one of: {', '.join(LEADER_STATS)}.")
    frame = _frame()
    season = int(season) if season is not None else default_season()
    rows = frame[frame["season"] == season].copy()
    if rows.empty:
        raise ValueError(f"No player seasons for {profiles.season_label(season)}.")
    label, kind, column, higher, digits = LEADER_STATS[stat]
    qualifier = int(min_games) if min_games is not None else _qualifier_games(rows)
    rows["value"] = rows[column] / rows["games"] if kind == "per_game" else rows[column]
    eligible = rows[rows["games"] >= qualifier]
    if stat in ATTEMPT_MINIMUMS:
        attempts, per_game = ATTEMPT_MINIMUMS[stat]
        eligible = eligible[eligible[attempts] >= per_game * eligible["games"]]
    eligible = eligible[np.isfinite(eligible["value"].to_numpy(dtype=float))]
    ranked = eligible.sort_values("value", ascending=not higher).head(int(limit))
    teams = current_team_by_player(season)
    names = _team_names()
    leaders = []
    for rank, row in enumerate(ranked.to_dict("records"), 1):
        team_id = teams.get(int(row["personId"]))
        leaders.append({
            "rank": rank,
            "person_id": int(row["personId"]),
            "name": row["name"],
            "team_id": team_id,
            "team": names.get(team_id, {}).get("abbreviation") if team_id else None,
            "games": int(row["games"]),
            "value": round(float(row["value"]), digits),
            "age": profiles._clean(row["age"]),
        })
    return {
        "season": season,
        "season_label": profiles.season_label(season),
        "stat": stat,
        "label": label,
        "higher_is_better": higher,
        "min_games": qualifier,
        "qualified_players": int(len(eligible)),
        "leaders": leaders,
        "stats": [{"stat": key, "label": spec[0]} for key, spec in LEADER_STATS.items()],
        "seasons": seasons_available()[0][::-1],
    }


@lru_cache(maxsize=8)
def _current_team_cached(season, db_path):
    start, end = f"{int(season)}-09-01", f"{int(season) + 1}-07-01"
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            f"""
            SELECT ps.personId, {RECOVERED_TEAM_ID} AS teamId, MAX(ps.gameDateTimeEst)
            FROM player_statistics ps JOIN games g ON g.gameId = ps.gameId
            WHERE ps.gameDateTimeEst >= ? AND ps.gameDateTimeEst < ?
              AND COALESCE(ps.gameType, g.gameType) = 'Regular Season'
            GROUP BY ps.personId
            """,
            (start, end),
        ).fetchall()
    return {int(person): int(team) for person, team, _ in rows if team is not None}


_TEAM_LOCK = threading.Lock()


def current_team_by_player(season, db_path=TEAM_DB_PATH):
    """personId -> team of the player's last regular-season game that season."""
    with _TEAM_LOCK:
        return _current_team_cached(int(season), str(db_path))


@lru_cache(maxsize=1)
def _team_names_cached(db_path):
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT teamId, teamCity, teamName, teamAbbrev FROM team_histories WHERE seasonActiveTill >= 2100"
        ).fetchall()
    return {int(t): {"name": f"{c} {n}", "abbreviation": a} for t, c, n, a in rows}


def _team_names(db_path=TEAM_DB_PATH):
    return _team_names_cached(str(db_path))


@lru_cache(maxsize=1)
def _positions_cached(db_path):
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute("SELECT personId, guard, forward, center FROM players").fetchall()
    out = {}
    for person, guard, forward, center in rows:
        out[int(person)] = "-".join(p for p, flag in (("G", guard), ("F", forward), ("C", center)) if flag) or None
    return out


# Extra percentiles the role tags read (player_profile._profile_tags);
# turnovers rank lower-is-better, like the Outlook tab.
TAG_STATS = ["tsa_100", "tpa_100", "tp_pct", "tov_100", "mpg", "orb_100"]
LOWER_IS_BETTER = {"tov_100"}


def _season_percentiles(season_frame, stats):
    qualified = season_frame[season_frame["minutes"] >= profiles.QUALIFY_MINUTES]
    if len(qualified) < 30:  # early season: rank everyone with real minutes
        qualified = season_frame[season_frame["minutes"] >= _card_minimum(season_frame) / 2]
    out = {}
    for stat in stats:
        reference = np.sort(qualified[stat].to_numpy(dtype=float))
        reference = reference[np.isfinite(reference)]
        values = season_frame[stat].to_numpy(dtype=float)
        if len(reference) == 0:
            out[stat] = np.full(len(values), np.nan)
            continue
        below = np.searchsorted(reference, values, side="left")
        equal = np.searchsorted(reference, values, side="right") - below
        pct = 100.0 * (below + 0.5 * equal) / len(reference)
        if stat in LOWER_IS_BETTER:
            pct = 100.0 - pct
        pct[~np.isfinite(values)] = np.nan
        out[stat] = pct
    return out, len(qualified)


def _card_minimum(season_frame):
    team_games = season_frame["season_games"].max()
    if not np.isfinite(team_games or np.nan):
        team_games = season_frame["games"].max()
    return min(float(profiles.QUALIFY_MINUTES), CARD_MINUTES_PER_TEAM_GAME * float(team_games))


def player_cards(season=None, team_id=None, min_minutes=None, db_path=TEAM_DB_PATH):
    """Character-select cards: every player with real minutes in a season, rated 0-100.

    Ratings are percentiles among the season's qualified players (500+
    minutes, or everyone with real minutes early in a season); "Defense
    (box)" averages steals and blocks only, so it misses most defense.
    """
    frame = _frame()
    season = int(season) if season is not None else default_season()
    rows = frame[frame["season"] == season].copy()
    if rows.empty:
        raise ValueError(f"No player seasons for {profiles.season_label(season)}.")
    stats = sorted({s for _, _, group in CARD_RATINGS for s in group}
                   | {s for s in TAG_STATS if s in rows.columns})
    percentiles, qualified_count = _season_percentiles(rows, stats)
    for stat, values in percentiles.items():
        rows[f"pct_{stat}"] = values
    minimum = _card_minimum(rows) if min_minutes is None else float(min_minutes)
    rows = rows[rows["minutes"] >= minimum]
    teams = current_team_by_player(season, db_path)
    names = _team_names(db_path)
    positions = _positions_cached(str(db_path))
    cards = []
    for row in rows.to_dict("records"):
        person = int(row["personId"])
        team = teams.get(person)
        if team_id is not None and team != int(team_id):
            continue
        ratings = {}
        for key, _label, group in CARD_RATINGS:
            values = [row[f"pct_{s}"] for s in group if np.isfinite(row[f"pct_{s}"])]
            ratings[key] = int(round(float(np.mean(values)))) if values else None
        games = max(1, int(row["games"]))
        tag_input = [{"stat": s, "percentile": row.get(f"pct_{s}")} for s in stats]
        cards.append({
            "person_id": person,
            "name": row["name"],
            "team_id": team,
            "team": names.get(team, {}).get("abbreviation") if team else None,
            "position": positions.get(person),
            "age": profiles._clean(row["age"]),
            "games": int(row["games"]),
            "mpg": round(float(row["mpg"]), 1),
            "ppg": round(float(row["pts"]) / games, 1),
            "rpg": round(float(row["orb"] + row["drb"]) / games, 1),
            "apg": round(float(row["ast"]) / games, 1),
            "ts": profiles._clean(row["ts"], 3),
            "box_impact": profiles._clean(row["box_impact"], 2),
            "wins_added": profiles._clean(row["wins_added"], 1),
            "ratings": ratings,
            "overall": ratings.get("impact"),
            "tags": [t["label"] for t in profiles._profile_tags(
                [t for t in tag_input if t["percentile"] is not None and np.isfinite(t["percentile"])])],
        })
    # Stars first: impact over the minutes actually played.
    cards.sort(key=lambda c: (-(c["wins_added"] if c["wins_added"] is not None else -99), c["name"]))
    return {
        "season": season,
        "season_label": profiles.season_label(season),
        "team_id": None if team_id is None else int(team_id),
        "qualified_players": qualified_count,
        "min_minutes": round(minimum),
        "ratings": [{"key": key, "label": label, "stats": group} for key, label, group in CARD_RATINGS],
        "cards": cards,
        "seasons": seasons_available()[0][::-1],
        "default_season": default_season(),
    }


def similar_players(person_id, season=None, limit=12, any_age=False):
    """Most similar player-seasons in history to one player-season (Outlook's comparables)."""
    reference = profiles.league_reference()
    frame = reference["frame"]
    mine = frame[frame["personId"] == int(person_id)]
    if mine.empty:
        raise ValueError(f"personId {person_id} has no seasons in the player reference.")
    season = int(season) if season is not None else int(mine["season"].max())
    row = profiles._row_for(frame, person_id, season)
    if row is None:
        raise ValueError(f"personId {person_id} did not play in {profiles.season_label(season)}.")
    shown, _ = profiles._comps(frame, row, n_shown=int(limit), age_window=None if any_age else 1)
    return {
        "person_id": int(person_id),
        "name": row["name"],
        "season": season,
        "season_label": profiles.season_label(season),
        "age": profiles._clean(row["age"]),
        "any_age": bool(any_age),
        "comps": [profiles._comp_entry(c) for c in shown.to_dict("records")],
        "method": ("Weighted distance between league-relative z-scores of per-100 scoring, shot "
                   "volume, efficiency, playmaking, turnovers, rebounding, steals, blocks, threes "
                   "and minutes; one season per comparable player"
                   + ("" if any_age else ", within a year of the same age") + "."),
    }
