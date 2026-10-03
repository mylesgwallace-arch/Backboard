"""Detailed player pages: what the database records about one player, plus what
can honestly be derived from it.

Every number belongs to one of four layers, and the output says which:

* **Recorded** -- box-score sums from ``player_statistics``: regular season and
  playoffs, per team stint and combined ("TOT"), career totals, career highs.
* **Calculated** -- standard rate statistics that need only the player's and
  the team's box scores: TS%, eFG%, usage, assist / rebound / steal / block /
  turnover percentages, per-36 and per-100-possession lines and Hollinger's
  game score. The formulas are the public Basketball-Reference ones (see
  ``ADVANCED_FORMULAS``); team shares are taken over the player's estimated
  on-court share of each team stint, so traded seasons combine correctly.
* **Modeled** -- the box-score team model behind the What-if Lab and the
  Sandbox (``era_swap``; validation in ``models/era_swap_model.json``) values
  a season as *box impact*: the calibrated change in team net rating per 100
  on-court possessions versus a league-average player, and the wins that adds
  over the player's minutes. The Sandbox uses exactly these numbers, so a
  player page and a trade simulation agree.
* **Extrapolated** -- league percentiles, the most similar player-seasons
  since 1985-86 (same age, league-relative per-100 profile), what those
  players did the following season, a league aging curve and a next-season
  projection built from them. These describe historical patterns among
  similar players; they are not forecasts of one person's health or role.
"""

import argparse
import json
import math
import sqlite3
import threading
from datetime import date
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from src import era_swap
    from src.main import TEAM_DB_PATH
    from src.season_tables import load_tables
except ImportError:  # pragma: no cover - direct-script support
    import era_swap
    from main import TEAM_DB_PATH
    from season_tables import load_tables


ROOT = Path(__file__).resolve().parents[1]
ERA_MODEL_REPORT = ROOT / "models" / "era_swap_model.json"
HEADSHOT_URL = "https://cdn.nba.com/headshots/nba/latest/1040x760/{person_id}.png"

KINDS = {"regular": "Regular Season", "playoffs": "Playoffs"}
GAME_TYPES_SHOWN = ("Regular Season", "Playoffs", "Play-in Tournament")
COUNTING = ["mp", "fgm", "fga", "tpm", "tpa", "ftm", "fta", "orb", "drb", "trb",
            "ast", "stl", "blk", "tov", "pf", "pts", "pm"]
QUALIFY_MINUTES = era_swap.QUALIFY_MINUTES
COMP_MIN_MINUTES = 1000
COMPS_SHOWN = 10
COMPS_FOR_PROJECTION = 30
SEASON_MINUTES = 82 * 240  # team minutes in a regulation 82-game season

ADVANCED_FORMULAS = {
    "ts_pct": "PTS / (2 * (FGA + 0.44 * FTA))",
    "efg_pct": "(FGM + 0.5 * 3PM) / FGA",
    "usg_pct": "(FGA + 0.44 * FTA + TOV) / team plays during the player's minutes",
    "ast_pct": "AST / (team FGM during the player's minutes - FGM)",
    "orb_pct": "ORB / (team ORB + opponent DRB during the player's minutes)",
    "drb_pct": "DRB / (team DRB + opponent ORB during the player's minutes)",
    "trb_pct": "TRB / (team TRB + opponent TRB during the player's minutes)",
    "stl_pct": "STL / opponent possessions during the player's minutes",
    "blk_pct": "BLK / opponent two-point attempts during the player's minutes",
    "tov_pct": "TOV / (FGA + 0.44 * FTA + TOV)",
    "game_score": "PTS + 0.4 FGM - 0.7 FGA - 0.4 (FTA - FTM) + 0.7 ORB + 0.3 DRB + STL "
                  "+ 0.7 AST + 0.7 BLK - 0.4 PF - TOV (per game)",
    "on_court_share": "player minutes / (team minutes / 5)",
}

# The source stores untracked early-era stats as 0 rather than NULL. These are
# the first seasons the NBA recorded each one.
FIRST_TRACKED = {"stl": 1973, "blk": 1973, "orb": 1973, "drb": 1973, "tov": 1977,
                 "tpm": 1979, "tpa": 1979, "pm": 1996}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def season_of(timestamp):
    """NBA season (start year) of a game timestamp.

    Seasons start in October (late-starting seasons in November/December).
    The 2019-20 season resumed in the Orlando bubble from July to October
    2020; those games belong to 2019.
    """
    ts = pd.Timestamp(timestamp)
    if pd.Timestamp("2020-07-01") <= ts < pd.Timestamp("2020-11-01"):
        return 2019
    return ts.year if ts.month >= 10 else ts.year - 1


def season_label(season):
    return f"{int(season)}-{str(int(season) + 1)[-2:]}"


def _ratio(numerator, denominator, digits=3):
    if denominator is None or numerator is None:
        return None
    try:
        if not np.isfinite(denominator) or denominator <= 0 or not np.isfinite(numerator):
            return None
    except TypeError:
        return None
    return round(float(numerator) / float(denominator), digits)


def _clean(value, digits=None):
    """JSON-safe scalar: numpy -> python, NaN/inf -> None, optional rounding."""
    if value is None:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        if not math.isfinite(float(value)):
            return None
        return round(float(value), digits) if digits is not None else float(value)
    return value


def age_on(birth_date, season):
    """Age on February 1 of the season (the Basketball-Reference convention)."""
    if not birth_date:
        return None
    born = pd.Timestamp(birth_date)
    ref = pd.Timestamp(f"{int(season) + 1}-02-01")
    return int(ref.year - born.year - ((ref.month, ref.day) < (born.month, born.day)))


def _height_label(inches):
    if inches is None or not np.isfinite(inches) or inches <= 0:
        return None
    inches = int(round(inches))
    return f"{inches // 12}-{inches % 12}"


# ---------------------------------------------------------------------------
# Team names and team-season totals (cached per process)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=4)
def _team_history(db_path=TEAM_DB_PATH):
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT teamId, teamCity, teamName, teamAbbrev, seasonFounded, seasonActiveTill "
            "FROM team_histories"
        ).fetchall()
    history = {}
    for team_id, city, name, abbrev, start, end in rows:
        history.setdefault(int(team_id), []).append(
            (int(start), int(end), f"{city} {name}".strip(), (abbrev or "").strip())
        )
    return history


def team_label(team_id, season, fallback_name=None, db_path=TEAM_DB_PATH):
    """``(full name, abbreviation)`` the franchise carried in ``season``."""
    if team_id is None or (isinstance(team_id, float) and not np.isfinite(team_id)):
        return fallback_name or "Unknown", "—"
    spans = _team_history(db_path).get(int(team_id), [])
    match = [span for span in spans if span[0] <= int(season) <= span[1]]
    if match:
        _, _, name, abbrev = sorted(match)[-1]
        return name, abbrev or name[:3].upper()
    if fallback_name:
        return fallback_name, fallback_name.split()[-1][:3].upper()
    if spans:
        _, _, name, abbrev = sorted(spans)[-1]
        return name, abbrev
    return str(team_id), "—"


TEAM_CHECKED = ["fgm", "fga", "fta", "orb", "drb", "trb", "tov"]
RECORDED_SHARE = 0.97


_TOTALS_LOCK = threading.Lock()


def team_season_totals(db_path=TEAM_DB_PATH):
    """Cached team totals (see ``_build_team_season_totals``); single-flight."""
    with _TOTALS_LOCK:
        return _build_team_season_totals(db_path)


@lru_cache(maxsize=4)
def _build_team_season_totals(db_path=TEAM_DB_PATH):
    """Team and opponent box-score totals per (teamId, season, kind).

    ``kind`` is ``regular`` or ``playoffs``. Possessions use the same
    estimate as ``era_swap`` (FGA - ORB + TOV + 0.44 FTA, averaged with the
    opponent's).
    """
    with sqlite3.connect(db_path) as connection:
        frame = pd.read_sql_query(
            """
            SELECT t.teamId, t.opponentTeamId, t.gameDateTimeEst,
                   COALESCE(t.gameType, g.gameType) AS gameType,
                   CAST(t.numMinutes AS REAL) AS minutes, t.teamScore AS pts,
                   t.fieldGoalsMade AS fgm, t.fieldGoalsAttempted AS fga,
                   t.threePointersAttempted AS tpa, t.freeThrowsAttempted AS fta,
                   t.reboundsOffensive AS orb, t.reboundsDefensive AS drb,
                   t.reboundsTotal AS trb, t.turnovers AS tov, t.win AS win
            FROM team_statistics t
            LEFT JOIN games g ON g.gameId = t.gameId
            WHERE t.gameDateTimeEst IS NOT NULL
            """,
            connection,
        )
    frame = frame[frame["gameType"].isin(KINDS.values())].copy()
    frame["kind"] = np.where(frame["gameType"] == "Playoffs", "playoffs", "regular")
    frame["season"] = [season_of(ts) for ts in frame["gameDateTimeEst"]]
    frame["minutes"] = frame["minutes"].where(frame["minutes"] > 0, 240.0)
    stats = ["minutes", "pts", "fgm", "fga", "tpa", "fta", "orb", "drb", "trb", "tov", "win"]
    frame[stats] = frame[stats].fillna(0.0)
    own = frame.groupby(["teamId", "season", "kind"])[stats].sum()
    own["games"] = frame.groupby(["teamId", "season", "kind"]).size()
    # Early box scores store unrecorded team stats as 0: a stat counts only
    # when nearly every game recorded it (3PA is exempt: 0 is a real value).
    recorded = (frame[TEAM_CHECKED] > 0).groupby([frame["teamId"], frame["season"], frame["kind"]]).mean()
    own = own.join(recorded.add_prefix("rec_"))
    opp = frame.groupby(["opponentTeamId", "season", "kind"])[stats].sum()
    opp_recorded = (frame[TEAM_CHECKED] > 0).groupby(
        [frame["opponentTeamId"], frame["season"], frame["kind"]]).mean()
    opp = opp.join(opp_recorded.add_prefix("rec_"))
    opp.index = opp.index.set_names(["teamId", "season", "kind"])
    opp.columns = [f"opp_{column}" for column in opp.columns]
    totals = own.join(opp, how="left").fillna(0.0)
    own_poss = totals["fga"] - totals["orb"] + totals["tov"] + 0.44 * totals["fta"]
    opp_poss = totals["opp_fga"] - totals["opp_orb"] + totals["opp_tov"] + 0.44 * totals["opp_fta"]
    totals["poss"] = 0.5 * (own_poss + opp_poss)
    totals["pace"] = totals["poss"] / (totals["minutes"] / 5.0) * 48.0
    return totals


# ---------------------------------------------------------------------------
# Bio and per-game rows
# ---------------------------------------------------------------------------

def load_bio(person_id, db_path=TEAM_DB_PATH):
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT personId, firstName, lastName, birthDate, school, country, heightInches,
                   bodyWeightLbs, jersey, guard, forward, center, draftYear, draftRound,
                   draftNumber, fromYear, toYear, nbaFlag
            FROM players WHERE personId = ?
            """,
            (int(person_id),),
        ).fetchone()
    if row is None:
        raise ValueError(f"No player with personId {person_id}.")
    (pid, first, last, birth, school, country, height, weight, jersey, guard, forward,
     center, draft_year, draft_round, draft_number, from_year, to_year, nba_flag) = row
    positions = [label for label, flag in (("G", guard), ("F", forward), ("C", center)) if flag]
    names = {"G": "Guard", "F": "Forward", "C": "Center"}

    def number(value):
        return None if value in (None, -1, -1.0) or (isinstance(value, float) and not np.isfinite(value)) else int(value)

    draft = None
    if number(draft_year) and number(draft_number):
        draft = {"year": number(draft_year), "round": number(draft_round), "pick": number(draft_number)}
    return {
        "person_id": int(pid),
        "name": f"{first or ''} {last or ''}".strip(),
        "first_name": first,
        "last_name": last,
        "birth_date": (birth or None) and str(birth)[:10],
        "school": school or None,
        "country": country or None,
        "height_inches": number(height),
        "height": _height_label(height) if height is not None else None,
        "weight_lbs": number(weight),
        "jersey": (str(jersey).strip() or None) if jersey not in (None, "") else None,
        "positions": positions,
        "position_label": "-".join(names[p] for p in positions) or None,
        "draft": draft,
        "undrafted": draft is None and number(draft_year) is None,
        "from_year": number(from_year),
        "to_year": number(to_year),
        "nba_flag": bool(nba_flag),
        "headshot_url": HEADSHOT_URL.format(person_id=int(pid)),
    }


def load_games(person_id, db_path=TEAM_DB_PATH):
    """Every regular-season, play-in and playoff game the player appeared in.

    A game counts when minutes were logged, or (for eras that did not log
    minutes) when the row has a stat line and no did-not-play comment.
    """
    with sqlite3.connect(db_path) as connection:
        frame = pd.read_sql_query(
            """
            SELECT ps.gameId, ps.gameDateTimeEst AS date,
                   COALESCE(ps.gameType, g.gameType) AS gameType,
                   CASE WHEN ps.playerteamId IS NOT NULL THEN ps.playerteamId
                        WHEN ps.playerteamName = g.hometeamName THEN g.hometeamId
                        WHEN ps.playerteamName = g.awayteamName THEN g.awayteamId
                        WHEN ps.home = 1 THEN g.hometeamId ELSE g.awayteamId END AS teamId,
                   CASE WHEN ps.opponentteamId IS NOT NULL THEN ps.opponentteamId
                        WHEN ps.playerteamName = g.hometeamName THEN g.awayteamId
                        WHEN ps.playerteamName = g.awayteamName THEN g.hometeamId
                        WHEN ps.home = 1 THEN g.awayteamId ELSE g.hometeamId END AS oppId,
                   ps.playerteamCity || ' ' || ps.playerteamName AS teamName,
                   ps.opponentteamCity || ' ' || ps.opponentteamName AS oppName,
                   ps.home, ps.win, CAST(ps.numMinutes AS REAL) AS mp,
                   ps.points AS pts, ps.fieldGoalsMade AS fgm, ps.fieldGoalsAttempted AS fga,
                   ps.threePointersMade AS tpm, ps.threePointersAttempted AS tpa,
                   ps.freeThrowsMade AS ftm, ps.freeThrowsAttempted AS fta,
                   ps.reboundsOffensive AS orb, ps.reboundsDefensive AS drb,
                   ps.reboundsTotal AS trb, ps.assists AS ast, ps.steals AS stl,
                   ps.blocks AS blk, ps.turnovers AS tov, ps.foulsPersonal AS pf,
                   ps.plusMinusPoints AS pm, ps.startingPosition AS start, ps.comment,
                   g.homeScore, g.awayScore
            FROM player_statistics ps
            LEFT JOIN games g ON g.gameId = ps.gameId
            WHERE ps.personId = ? AND ps.gameDateTimeEst IS NOT NULL
            """,
            connection,
            params=(int(person_id),),
        )
    frame = frame[frame["gameType"].isin(GAME_TYPES_SHOWN)].copy()
    if frame.empty:
        return frame
    stats = [c for c in COUNTING if c != "mp"]
    frame[stats] = frame[stats].apply(pd.to_numeric, errors="coerce")
    frame["mp"] = pd.to_numeric(frame["mp"], errors="coerce")
    activity = frame[["pts", "fga", "fta", "trb", "ast", "pf"]].fillna(0).sum(axis=1)
    played = (frame["mp"] > 0) | ((frame["mp"].fillna(0) <= 0) & frame["comment"].isna() & (activity > 0))
    frame = frame[played].copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame["season"] = [season_of(ts) for ts in frame["date"]]
    frame["kind"] = frame["gameType"].map(
        {"Regular Season": "regular", "Playoffs": "playoffs", "Play-in Tournament": "play_in"})
    frame["mp_logged"] = frame["mp"] > 0
    frame["mp"] = frame["mp"].where(frame["mp_logged"], 0.0)
    frame["started"] = frame["start"].fillna("").astype(str).str.strip() != ""
    for stat, first in FIRST_TRACKED.items():
        frame.loc[frame["season"] < first, stat] = np.nan
    frame[stats] = frame[stats].astype(float)
    frame["game_score"] = (
        frame["pts"] + 0.4 * frame["fgm"] - 0.7 * frame["fga"] - 0.4 * (frame["fta"] - frame["ftm"])
        + 0.7 * frame["orb"].fillna(0) + 0.3 * frame["drb"].fillna(0) + frame["stl"].fillna(0)
        + 0.7 * frame["ast"] + 0.7 * frame["blk"].fillna(0) - 0.4 * frame["pf"].fillna(0)
        - frame["tov"].fillna(0)
    )
    return frame.sort_values(["date", "gameId"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Season rows (recorded + calculated)
# ---------------------------------------------------------------------------

CONTEXT_KEYS = ["fgm", "fga", "fta", "tov", "orb", "drb", "trb", "poss",
                "opp_fga", "opp_tpa", "opp_orb", "opp_drb", "opp_trb"]


def _stint_team_context(team_id, season, kind, minutes, totals):
    """Team/opponent totals scaled to the player's on-court share of a stint."""
    try:
        team = totals.loc[(int(team_id), int(season), kind)]
    except (KeyError, TypeError, ValueError):
        return None
    if team["minutes"] <= 0:
        return None
    share = minutes / (team["minutes"] / 5.0)

    def recorded(key):
        base = key[4:] if key.startswith("opp_") else key
        prefix = "opp_rec_" if key.startswith("opp_") else "rec_"
        if base == "tpa":
            return True
        if base == "poss":
            return all(recorded(k) for k in ("fga", "orb", "tov", "fta", "opp_fga", "opp_orb",
                                              "opp_tov", "opp_fta"))
        return float(team.get(prefix + base, 0.0)) >= RECORDED_SHARE

    scaled = {key: (float(team[key]) * share if recorded(key) else None) for key in CONTEXT_KEYS}
    has_pace = recorded("poss")
    scaled["team_minutes"] = float(team["minutes"])
    scaled["team_poss"] = float(team["poss"]) if has_pace else None
    scaled["pace"] = float(team["pace"]) if has_pace else None
    scaled["team_games"] = int(team["games"])
    scaled["team_wins"] = int(team["win"])
    return scaled


def _sum_contexts(contexts):
    contexts = [c for c in contexts if c is not None]
    if not contexts:
        return None
    combined = {key: (None if any(c[key] is None for c in contexts) else sum(c[key] for c in contexts))
                for key in CONTEXT_KEYS}
    paces = [c["pace"] for c in contexts]
    combined["pace"] = None if any(p is None for p in paces) else float(np.mean(paces))
    return combined


def _advanced(sums, ctx, season):
    """Calculated rates from player sums and on-court team context."""
    fga, fta, tov = sums["fga"], sums["fta"], sums["tov"]
    out = {
        "fg_pct": _ratio(sums["fgm"], fga),
        "tp_pct": _ratio(sums["tpm"], sums["tpa"]),
        "ft_pct": _ratio(sums["ftm"], fta),
        "two_pct": _ratio(sums["fgm"] - (sums["tpm"] or 0), fga - (sums["tpa"] or 0)),
        "efg_pct": _ratio(sums["fgm"] + 0.5 * (sums["tpm"] or 0), fga),
        "ts_pct": _ratio(sums["pts"], 2.0 * (fga + 0.44 * fta)),
        "tpar": _ratio(sums["tpa"], fga) if sums["tpa"] is not None else None,
        "ftr": _ratio(fta, fga),
        "tov_pct": _ratio(tov, fga + 0.44 * fta + tov) if tov is not None else None,
    }
    for key in ("usg_pct", "ast_pct", "orb_pct", "drb_pct", "trb_pct", "stl_pct", "blk_pct", "poss"):
        out[key] = None
    if ctx is None or not sums.get("minutes_complete"):
        return out

    def team(*keys):
        values = [ctx.get(key) for key in keys]
        return None if any(v is None for v in values) else values

    if tov is not None and team("fga", "fta", "tov"):
        out["usg_pct"] = _ratio(fga + 0.44 * fta + tov, ctx["fga"] + 0.44 * ctx["fta"] + ctx["tov"])
    if team("fgm"):
        out["ast_pct"] = _ratio(sums["ast"], ctx["fgm"] - sums["fgm"])
    if sums["orb"] is not None and team("orb", "opp_drb"):
        out["orb_pct"] = _ratio(sums["orb"], ctx["orb"] + ctx["opp_drb"])
    if sums["drb"] is not None and team("drb", "opp_orb"):
        out["drb_pct"] = _ratio(sums["drb"], ctx["drb"] + ctx["opp_orb"])
    if team("trb", "opp_trb"):
        out["trb_pct"] = _ratio(sums["trb"], ctx["trb"] + ctx["opp_trb"])
    if sums["stl"] is not None and team("poss"):
        out["stl_pct"] = _ratio(sums["stl"], ctx["poss"])
    if sums["blk"] is not None and team("opp_fga", "opp_tpa"):
        out["blk_pct"] = _ratio(sums["blk"], ctx["opp_fga"] - ctx["opp_tpa"])
    if team("poss") and ctx["poss"] > 0:
        out["poss"] = round(float(ctx["poss"]), 1)
    return out


def _sums(frame):
    sums = {}
    for stat in COUNTING:
        column = frame[stat]
        sums[stat] = None if column.isna().all() else float(column.sum(skipna=True))
    sums["g"] = int(len(frame))
    sums["gs"] = int(frame["started"].sum())
    sums["mp_games"] = int(frame["mp_logged"].sum())
    # Rates that need minutes are shown when (nearly) every game logged them.
    sums["minutes_complete"] = sums["g"] > 0 and sums["mp_games"] >= 0.95 * sums["g"]
    sums["game_score"] = float(frame["game_score"].mean()) if len(frame) else None
    return sums


def _season_row(frame, season, team_id, team_name, abbrev, ctx, bio_birth, is_total=False,
                team_ids=None, starts_seasons=frozenset()):
    sums = _sums(frame)
    row = {
        "season": int(season),
        "season_label": season_label(season),
        "age": age_on(bio_birth, season),
        "team_id": None if is_total else (None if team_id is None else int(team_id)),
        "team_ids": team_ids or ([] if team_id is None else [int(team_id)]),
        "team": "TOT" if is_total else abbrev,
        "team_name": "Two or more teams" if is_total else team_name,
        "is_total": is_total,
        "g": sums["g"],
        "gs": sums["gs"] if int(season) in starts_seasons else None,
        "mp": round(sums["mp"], 1) if sums["mp_games"] else None,
        "mp_games": sums["mp_games"],
        "minutes_complete": sums["minutes_complete"],
        "game_score": _clean(sums["game_score"], 1),
    }
    for stat in COUNTING:
        if stat == "mp":
            continue
        row[stat] = _clean(sums[stat], 0) if sums[stat] is not None else None
    row.update(_advanced(sums, ctx, season))
    row["pace"] = _clean(ctx["pace"], 1) if ctx else None
    return row


def season_rows(games, kind, birth_date, totals, db_path=TEAM_DB_PATH):
    """Per (season, team) rows plus a combined TOT row for multi-team seasons."""
    subset = games[games["kind"] == kind]
    starts = starts_recorded_seasons(db_path)
    rows = []
    for season, season_games in subset.groupby("season", sort=True):
        stints = []
        # Stint order: by first game date with that team.
        order = season_games.groupby("teamId")["date"].min().sort_values().index
        for team_id in order:
            stint = season_games[season_games["teamId"] == team_id]
            fallback = stint["teamName"].dropna().iloc[0] if stint["teamName"].notna().any() else None
            name, abbrev = team_label(team_id, season, fallback, db_path)
            minutes = float(stint["mp"].sum())
            ctx = _stint_team_context(team_id, season, kind, minutes, totals)
            stints.append((ctx, _season_row(stint, season, team_id, name, abbrev, ctx, birth_date,
                                            starts_seasons=starts)))
        if len(stints) > 1:
            total_ctx = _sum_contexts([ctx for ctx, _ in stints])
            rows.append(_season_row(season_games, season, None, None, None, total_ctx, birth_date,
                                    is_total=True, team_ids=[int(t) for t in order],
                                    starts_seasons=starts))
            for _, row in stints:
                row["is_stint"] = True
                rows.append(row)
        else:
            rows.append(stints[0][1])
    return rows


def career_row(games, kind, totals):
    subset = games[games["kind"] == kind]
    if subset.empty:
        return None
    sums = _sums(subset)
    contexts = []
    for (season, team_id), stint in subset.groupby(["season", "teamId"]):
        contexts.append(_stint_team_context(team_id, season, kind, float(stint["mp"].sum()), totals))
    ctx = _sum_contexts(contexts) if sums["minutes_complete"] else None
    first, last = int(subset["season"].min()), int(subset["season"].max())
    row = {"seasons": int(subset["season"].nunique()), "first_season": first, "last_season": last,
           "g": sums["g"], "mp": round(sums["mp"], 1), "mp_games": sums["mp_games"],
           "minutes_complete": sums["minutes_complete"], "game_score": _clean(sums["game_score"], 1)}
    for stat in COUNTING:
        if stat != "mp":
            row[stat] = _clean(sums[stat], 0) if sums[stat] is not None else None
    row.update(_advanced(sums, ctx, last))
    return row


HIGH_STATS = [("pts", "Points"), ("trb", "Rebounds"), ("ast", "Assists"), ("stl", "Steals"),
              ("blk", "Blocks"), ("tpm", "Threes made"), ("ftm", "Free throws made"),
              ("game_score", "Game score")]


def _game_ref(row, db_path=TEAM_DB_PATH):
    _, opp_abbrev = team_label(row["oppId"], row["season"], row.get("oppName"), db_path)
    return {"date": str(pd.Timestamp(row["date"]).date()), "game_id": int(row["gameId"]),
            "opponent": opp_abbrev, "home": bool(row["home"]), "win": bool(row["win"]),
            "season": int(row["season"])}


def career_highs(games, kind, db_path=TEAM_DB_PATH):
    subset = games[games["kind"] == kind]
    highs = {}
    for stat, label in HIGH_STATS:
        column = subset[stat]
        if subset.empty or column.isna().all():
            continue
        best = subset.loc[column.idxmax()]
        highs[stat] = {"label": label, "value": _clean(best[stat], 1 if stat == "game_score" else 0),
                       **_game_ref(best, db_path)}
    return highs


def milestones(games, kind):
    subset = games[games["kind"] == kind]
    if subset.empty:
        return None
    tens = (subset[["pts", "trb", "ast", "stl", "blk"]].fillna(0) >= 10).sum(axis=1)
    return {
        "games_20_points": int((subset["pts"] >= 20).sum()),
        "games_30_points": int((subset["pts"] >= 30).sum()),
        "games_40_points": int((subset["pts"] >= 40).sum()),
        "games_50_points": int((subset["pts"] >= 50).sum()),
        "double_doubles": int((tens >= 2).sum()),
        "triple_doubles": int((tens >= 3).sum()),
        "wins": int(subset["win"].fillna(0).sum()),
        "losses": int(len(subset) - subset["win"].fillna(0).sum()),
    }


def player_profile(person_id, db_path=TEAM_DB_PATH):
    """Bio, season-by-season tables, career totals and highs for one player."""
    bio = load_bio(person_id, db_path)
    games = load_games(person_id, db_path)
    if games.empty:
        return {"person_id": int(person_id), "bio": bio, "has_games": False,
                "seasons": {"regular": [], "playoffs": []}, "career": {}, "highs": {},
                "milestones": {}, "formulas": ADVANCED_FORMULAS}
    totals = team_season_totals(db_path)
    birth = bio["birth_date"]
    last_game = games.iloc[-1]
    team_name, team_abbrev = team_label(last_game["teamId"], last_game["season"],
                                        last_game["teamName"], db_path)
    latest_db_season = latest_database_season(db_path)
    seasons_played = sorted(int(s) for s in games["season"].unique())
    bio["age"] = age_on(birth, latest_db_season) if bio["to_year"] is None or \
        seasons_played[-1] >= latest_db_season else None
    return {
        "person_id": int(person_id),
        "bio": bio,
        "has_games": True,
        "last_team": {"team_id": None if pd.isna(last_game["teamId"]) else int(last_game["teamId"]),
                      "name": team_name, "abbreviation": team_abbrev,
                      "season": int(last_game["season"]),
                      "last_game": str(last_game["date"].date())},
        "active": seasons_played[-1] >= latest_db_season,
        "latest_database_season": latest_db_season,
        "seasons_played": seasons_played,
        "seasons": {kind: season_rows(games, kind, birth, totals, db_path) for kind in KINDS},
        "career": {kind: career_row(games, kind, totals) for kind in KINDS},
        "highs": {kind: career_highs(games, kind, db_path) for kind in KINDS},
        "milestones": {kind: milestones(games, kind) for kind in KINDS},
        "formulas": ADVANCED_FORMULAS,
        "sources": ["nba.db players (bio)", "nba.db player_statistics (every game with a stat line)",
                    "nba.db team_statistics (team and opponent totals for the rate statistics)"],
    }


@lru_cache(maxsize=4)
def starts_recorded_seasons(db_path=TEAM_DB_PATH):
    """Seasons whose ``startingPosition`` marks starters only.

    In a real box score about 5 of every 11 players who log minutes start.
    Many seasons tag nobody (before 1996-97; most of 2021-22) or tag bench
    players too (about 85% of appearances in 1997-2016), so starts are shown
    only where the league-wide share of tagged appearances is 38-52%.
    """
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT CAST(substr(gameDateTimeEst, 1, 4) AS INTEGER)
                     - (CAST(substr(gameDateTimeEst, 6, 2) AS INTEGER) < 10) AS season,
                   COUNT(*), SUM(COALESCE(startingPosition, '') <> '')
            FROM player_statistics
            WHERE CAST(numMinutes AS REAL) > 0 AND gameDateTimeEst IS NOT NULL
            GROUP BY season
            """
        ).fetchall()
    return frozenset(int(season) for season, total, tagged in rows
                     if season is not None and total and 0.38 <= tagged / total <= 0.52)


@lru_cache(maxsize=4)
def latest_database_season(db_path=TEAM_DB_PATH):
    with sqlite3.connect(db_path) as connection:
        latest = connection.execute(
            "SELECT MAX(gameDateTimeEst) FROM games WHERE gameType = 'Regular Season' "
            "AND winner IS NOT NULL"
        ).fetchone()[0]
    return season_of(latest)


# ---------------------------------------------------------------------------
# Game log and splits
# ---------------------------------------------------------------------------

def _split_line(frame):
    if frame.empty:
        return None
    sums = _sums(frame)
    g = sums["g"]

    def per_game(stat):
        return None if sums[stat] is None else round(sums[stat] / g, 1)

    return {
        "g": g, "w": int(frame["win"].fillna(0).sum()),
        "mp": round(sums["mp"] / sums["mp_games"], 1) if sums["mp_games"] else None,
        "pts": per_game("pts"), "trb": per_game("trb"), "ast": per_game("ast"),
        "stl": per_game("stl"), "blk": per_game("blk"), "tov": per_game("tov"),
        "tpm": per_game("tpm"),
        "fg_pct": _ratio(sums["fgm"], sums["fga"]),
        "tp_pct": _ratio(sums["tpm"], sums["tpa"]),
        "ts_pct": _ratio(sums["pts"], 2.0 * (sums["fga"] + 0.44 * sums["fta"])),
        "pm": per_game("pm"),
        "game_score": _clean(sums["game_score"], 1),
    }


def player_game_log(person_id, season, kind="regular", db_path=TEAM_DB_PATH):
    """Every game of one season (regular, playoffs or play-in) with splits."""
    if kind not in ("regular", "playoffs", "play_in"):
        raise ValueError("kind must be 'regular', 'playoffs' or 'play_in'.")
    games = load_games(person_id, db_path)
    subset = games[(games["season"] == int(season)) & (games["kind"] == kind)].copy()
    available = sorted(int(s) for s in games.loc[games["kind"] == kind, "season"].unique())
    if subset.empty:
        raise ValueError(
            f"personId {person_id} has no {kind.replace('_', '-')} games in {season_label(season)}."
        )
    subset["rest"] = subset["date"].dt.normalize().diff().dt.days - 1
    starts_recorded = int(season) in starts_recorded_seasons(db_path)
    rows = []
    for number, row in enumerate(subset.to_dict("records"), start=1):
        _, team_abbrev = team_label(row["teamId"], row["season"], row["teamName"], db_path)
        _, opp_abbrev = team_label(row["oppId"], row["season"], row["oppName"], db_path)
        own, other = (row["homeScore"], row["awayScore"]) if row["home"] else (row["awayScore"], row["homeScore"])
        rows.append({
            "n": number, "date": str(row["date"].date()), "game_id": int(row["gameId"]),
            "team": team_abbrev, "opponent": opp_abbrev, "home": bool(row["home"]),
            "win": bool(row["win"]),
            "score": (f"{int(own)}-{int(other)}" if own is not None and other is not None
                      and np.isfinite(own) and np.isfinite(other) else None),
            "started": bool(row["started"]) if starts_recorded else None,
            "mp": _clean(row["mp"], 1) if row["mp_logged"] else None,
            **{stat: _clean(row[stat], 0) for stat in COUNTING if stat != "mp"},
            "game_score": _clean(row["game_score"], 1),
            "rest_days": None if pd.isna(row["rest"]) else int(row["rest"]),
        })
    rolling = subset[["pts", "trb", "ast", "game_score"]].rolling(10, min_periods=3).mean()
    for record, (_, avg) in zip(rows, rolling.iterrows()):
        record["rolling_10"] = {key: _clean(avg[key], 1) for key in ("pts", "trb", "ast", "game_score")}

    month_names = {10: "October", 11: "November", 12: "December", 1: "January", 2: "February",
                   3: "March", 4: "April", 5: "May", 6: "June", 7: "July", 8: "August", 9: "September"}
    months = []
    for month, frame in subset.groupby(subset["date"].dt.to_period("M"), sort=True):
        months.append({"label": month_names[month.month], **_split_line(frame)})
    rest = subset["rest"]
    splits = {
        "location": [{"label": "Home", **(_split_line(subset[subset["home"] == 1]) or {"g": 0})},
                     {"label": "Road", **(_split_line(subset[subset["home"] == 0]) or {"g": 0})}],
        "result": [{"label": "Wins", **(_split_line(subset[subset["win"] == 1]) or {"g": 0})},
                   {"label": "Losses", **(_split_line(subset[subset["win"] == 0]) or {"g": 0})}],
        "rest": [{"label": "Back-to-back", **(_split_line(subset[rest == 0]) or {"g": 0})},
                 {"label": "1 day rest", **(_split_line(subset[rest == 1]) or {"g": 0})},
                 {"label": "2+ days rest", **(_split_line(subset[rest >= 2]) or {"g": 0})}],
        "month": months,
    }
    if starts_recorded:
        splits["role"] = [{"label": "Starter", **(_split_line(subset[subset["started"]]) or {"g": 0})},
                          {"label": "Bench", **(_split_line(subset[~subset["started"]]) or {"g": 0})}]
    if subset["teamId"].nunique() > 1:
        splits["team"] = []
        for team_id, frame in subset.groupby("teamId", sort=False):
            _, abbrev = team_label(team_id, season, frame["teamName"].iloc[0], db_path)
            splits["team"].append({"label": abbrev, **_split_line(frame)})
    points = subset["pts"]
    return {
        "person_id": int(person_id),
        "season": int(season),
        "season_label": season_label(season),
        "kind": kind,
        "available_seasons": available,
        "games": rows,
        "starts_recorded": starts_recorded,
        "season_line": _split_line(subset),
        "splits": splits,
        "consistency": {
            "points_sd": _clean(points.std(ddof=0), 1),
            "game_score_sd": _clean(subset["game_score"].std(ddof=0), 1),
            "share_20_plus": _ratio(float((points >= 20).sum()), float(len(points))),
            "best_streak_20_plus": _longest_streak(points >= 20),
            "double_doubles": int(((subset[["pts", "trb", "ast", "stl", "blk"]].fillna(0) >= 10)
                                   .sum(axis=1) >= 2).sum()),
        },
    }


def _longest_streak(mask):
    best = current = 0
    for value in mask:
        current = current + 1 if value else 0
        best = max(best, current)
    return int(best)


# ---------------------------------------------------------------------------
# League reference: modeled + extrapolated layers (1985-86 onward)
# ---------------------------------------------------------------------------

SIM_FEATURES = ["pts_100", "tsa_100", "ast_100", "tov_100", "orb_100", "drb_100", "stl_100",
                "blk_100", "tpa_100", "ts"]
SIM_WEIGHTS = np.array([1.0, 1.0, 1.0, 0.5, 0.7, 0.7, 0.6, 0.7, 0.8, 1.0, 0.8])  # + mpg
PERCENTILE_STATS = [
    ("pts_100", "Scoring (points / 100)", True),
    ("ts", "Shooting efficiency (TS%)", True),
    ("tsa_100", "Shot volume (attempts / 100)", True),
    ("ast_100", "Playmaking (assists / 100)", True),
    ("trb_100", "Rebounding (rebounds / 100)", True),
    ("orb_100", "Offensive rebounding (/ 100)", True),
    ("stl_100", "Steals / 100", True),
    ("blk_100", "Blocks / 100", True),
    ("tov_100", "Ball security (turnovers / 100)", False),
    ("tpa_100", "Three-point volume (3PA / 100)", True),
    ("tp_pct", "Three-point accuracy", True),
    ("ft_pct", "Free-throw accuracy", True),
    ("mpg", "Minutes per game", True),
    ("box_impact", "Box impact (model)", True),
]


def _era_reports():
    return json.loads(ERA_MODEL_REPORT.read_text(encoding="utf-8"))


_REFERENCE_LOCK = threading.Lock()


def league_reference():
    """Cached league reference (see ``_build_league_reference``); single-flight."""
    with _REFERENCE_LOCK:
        return _build_league_reference()


@lru_cache(maxsize=1)
def _build_league_reference():
    """Whole-season player rows since 1985-86 with impact, z-scores and ages.

    Built once per process from the complete season tables
    (``season_tables``; a few seconds once cached); the box-score coefficients, realization factor and
    wins-per-point slope are the persisted, validated ones.
    """
    team_seasons, player_seasons = load_tables()
    context = era_swap.league_context(player_seasons)
    reports = _era_reports()
    beta = np.array([reports["team_model"]["coefficients"][f] for f in era_swap.FEATURES])
    realization = reports["roster_change_calibration"]["realization_factor"]
    wins_slope = reports["wins_per_net_rating_point"]["slope"]

    combined = era_swap.combined_index(player_seasons)
    frame = pd.DataFrame.from_records(
        [{"personId": int(pid), "season": int(season), **row}
         for (pid, season), row in combined.items()]
    )
    frame["trb_100"] = frame["orb_100"] + frame["drb_100"]
    games_per_team = team_seasons.groupby("season")["games"].median()
    frame["mpg"] = frame["minutes"] / frame["games"]
    vectors = np.vstack([
        era_swap.player_features(row, row["season"], context) for row in frame.to_dict("records")
    ])
    frame["impact_raw"] = vectors @ beta
    # Calibrated change in team net rating per 100 possessions *while the
    # player is on the floor* (one of five), versus a league-average player.
    frame["box_impact"] = realization * frame["impact_raw"] / 5.0
    # Over the player's minutes: share of an 82-game team season, times the
    # empirical wins per net-rating point.
    frame["wins_added"] = realization * frame["impact_raw"] * frame["minutes"] / SEASON_MINUTES * wins_slope
    frame["season_games"] = frame["season"].map(games_per_team)

    # League-relative z-scores (qualified-player distributions of each season).
    for stat in SIM_FEATURES:
        mu = frame["season"].map(context[f"mu_{stat}"])
        sd = frame["season"].map(context[f"sd_{stat}"])
        frame[f"z_{stat}"] = (frame[stat] - mu) / sd
    qualified = frame[frame["minutes"] >= QUALIFY_MINUTES]
    mpg_mu = qualified.groupby("season")["mpg"].mean()
    mpg_sd = qualified.groupby("season")["mpg"].std()
    frame["z_mpg"] = (frame["mpg"] - frame["season"].map(mpg_mu)) / frame["season"].map(mpg_sd)

    with sqlite3.connect(TEAM_DB_PATH) as connection:
        births = dict(connection.execute(
            "SELECT personId, birthDate FROM players WHERE birthDate IS NOT NULL").fetchall())
    frame["age"] = [age_on(births.get(int(pid)), season)
                    for pid, season in zip(frame["personId"], frame["season"])]
    names = player_seasons.groupby("personId")[["firstName", "lastName"]].last()
    frame["name"] = frame["personId"].map(
        lambda pid: f"{names.loc[pid, 'firstName']} {names.loc[pid, 'lastName']}".strip())

    # Next season (for comps and the aging curve).
    nxt = frame.set_index(["personId", "season"])
    keys = list(zip(frame["personId"], frame["season"] + 1))
    present = [key in nxt.index for key in keys]
    frame["has_next"] = present
    for column in ["minutes", "games", "mpg", "box_impact", "wins_added", "season_games"] + \
            [f"z_{stat}" for stat in SIM_FEATURES]:
        values = nxt[column]
        frame[f"next_{column}"] = [values.get(key, np.nan) if ok else np.nan
                                   for key, ok in zip(keys, present)]
    frame["last_season_in_data"] = int(frame["season"].max())

    teams = player_seasons.groupby(["personId", "season"])["teamId"].apply(list)
    frame["team_ids"] = [teams.get((pid, season), []) for pid, season in
                         zip(frame["personId"], frame["season"])]
    return {
        "frame": frame,
        "context": context,
        "team_seasons": team_seasons,
        "beta": beta,
        "realization_factor": realization,
        "realization_interval": reports["roster_change_calibration"]["realization_factor_80pct_interval"],
        "wins_slope": wins_slope,
        "aging": _aging_curve(frame),
        "first_season": era_swap.FIRST_SEASON,
    }


def _aging_curve(frame, min_minutes=250):
    """Minutes-weighted mean year-over-year change by age (the delta method)."""
    pairs = frame[frame["has_next"] & (frame["minutes"] >= min_minutes)
                  & (frame["next_minutes"] >= min_minutes) & frame["age"].notna()]
    rows = []
    for age, group in pairs.groupby("age"):
        if len(group) < 25 or age < 19 or age > 39:
            continue
        weights = 2.0 / (1.0 / group["minutes"] + 1.0 / group["next_minutes"])
        rows.append({
            "age": int(age), "to_age": int(age) + 1, "pairs": int(len(group)),
            "delta_box_impact": round(float(np.average(group["next_box_impact"] - group["box_impact"],
                                                       weights=weights)), 3),
            "delta_points_z": round(float(np.average(group["next_z_pts_100"] - group["z_pts_100"],
                                                     weights=weights)), 3),
            "delta_mpg": round(float(np.average(group["next_mpg"] - group["mpg"], weights=weights)), 2),
        })
    cumulative, level = [], 0.0
    for row in rows:
        cumulative.append({"age": row["age"], "relative_box_impact": round(level, 3)})
        level += row["delta_box_impact"]
    if rows:
        cumulative.append({"age": rows[-1]["to_age"], "relative_box_impact": round(level, 3)})
    if cumulative:
        peak = max(cumulative, key=lambda item: item["relative_box_impact"])["relative_box_impact"]
        for item in cumulative:
            item["relative_box_impact"] = round(item["relative_box_impact"] - peak, 3)
    return {"by_age": rows, "curve": cumulative,
            "method": "Delta method: for every player with 250+ minutes in two consecutive seasons "
                      "(1985-86 onward), the change from one season to the next, averaged by age "
                      "and weighted by the harmonic mean of the two seasons' minutes. The curve "
                      "chains those changes and is shown relative to its peak."}


def _percentile(values, value, higher_is_better=True):
    values = values[np.isfinite(values)]
    if value is None or not np.isfinite(value) or len(values) == 0:
        return None
    below = float((values < value).sum()) + 0.5 * float((values == value).sum())
    pct = 100.0 * below / len(values)
    return round(pct if higher_is_better else 100.0 - pct, 1)


def _row_for(frame, person_id, season):
    rows = frame[(frame["personId"] == int(person_id)) & (frame["season"] == int(season))]
    return None if rows.empty else rows.iloc[0]


def _profile_tags(percentiles):
    """Plain-language role tags from percentile rules (each lists its evidence)."""
    p = {item["stat"]: item["percentile"] for item in percentiles if item["percentile"] is not None}

    def at_least(stat, value):
        return p.get(stat, -1) >= value

    rules = [
        ("Primary creator", at_least("ast_100", 85) and at_least("tsa_100", 60), ["ast_100", "tsa_100"]),
        ("Volume scorer", at_least("pts_100", 85) and at_least("tsa_100", 80), ["pts_100", "tsa_100"]),
        ("Efficient finisher", at_least("ts", 85) and not at_least("tpa_100", 60), ["ts", "tpa_100"]),
        ("Floor spacer", at_least("tpa_100", 75) and at_least("tp_pct", 60), ["tpa_100", "tp_pct"]),
        ("Rim protector", at_least("blk_100", 88), ["blk_100"]),
        ("Glass cleaner", at_least("trb_100", 88), ["trb_100"]),
        ("Ball hawk", at_least("stl_100", 85), ["stl_100"]),
        ("Low-mistake player", at_least("tov_100", 80) and at_least("mpg", 40), ["tov_100"]),
        ("Two-way playmaker", at_least("ast_100", 70) and at_least("stl_100", 70), ["ast_100", "stl_100"]),
    ]
    tags = []
    for label, hit, stats in rules:
        if hit:
            tags.append({"label": label,
                         "evidence": [{"stat": s, "percentile": p.get(s)} for s in stats]})
    return tags[:4]


def _comps(frame, row, n_shown=COMPS_SHOWN, n_projection=COMPS_FOR_PROJECTION):
    target = np.array([row[f"z_{s}"] for s in SIM_FEATURES] + [row["z_mpg"]], dtype=float)
    pool = frame[(frame["personId"] != row["personId"]) & (frame["minutes"] >= COMP_MIN_MINUTES)
                 & frame["age"].notna()]
    if row["age"] is not None and np.isfinite(row["age"]):
        pool = pool[(pool["age"] - row["age"]).abs() <= 1]
    columns = [f"z_{s}" for s in SIM_FEATURES] + ["z_mpg"]
    matrix = pool[columns].to_numpy(dtype=float)
    finite_target = np.where(np.isfinite(target), target, 0.0)
    diffs = np.where(np.isfinite(matrix), matrix, 0.0) - finite_target
    distance = np.sqrt((SIM_WEIGHTS * diffs ** 2).sum(axis=1) / SIM_WEIGHTS.sum())
    pool = pool.assign(distance=distance).sort_values("distance")
    # One season per comparable player (their closest).
    pool = pool.drop_duplicates("personId")
    shown = pool.head(n_shown)
    future = pool[pool["season"] < row["last_season_in_data"]].head(n_projection)
    return shown, future


def _kernel(distance):
    """Similarity weight from the weighted RMS z-score distance."""
    return np.exp(-0.6 * np.asarray(distance, dtype=float) ** 2)


def _similarity(distance):
    return round(float(100.0 * _kernel(distance)), 1)


def _comp_entry(comp):
    entry = {
        "person_id": int(comp["personId"]), "name": comp["name"], "season": int(comp["season"]),
        "season_label": season_label(comp["season"]), "age": _clean(comp["age"]),
        "similarity": _similarity(comp["distance"]),
        "mpg": _clean(comp["mpg"], 1), "pts_100": _clean(comp["pts_100"], 1),
        "ts": _clean(comp["ts"], 3), "ast_100": _clean(comp["ast_100"], 1),
        "trb_100": _clean(comp["trb_100"], 1), "box_impact": _clean(comp["box_impact"], 2),
    }
    if comp["season"] >= comp["last_season_in_data"]:
        entry["next_season"] = {"status": "not yet played"}
    elif comp["has_next"]:
        entry["next_season"] = {
            "status": "played", "mpg": _clean(comp["next_mpg"], 1),
            "games": _clean(comp["next_games"]), "box_impact": _clean(comp["next_box_impact"], 2),
            "delta_box_impact": _clean(comp["next_box_impact"] - comp["box_impact"], 2),
        }
    else:
        entry["next_season"] = {"status": "did not play in the NBA"}
    return entry


def _weighted_quantile(values, weights, quantile):
    order = np.argsort(values)
    values, weights = np.asarray(values)[order], np.asarray(weights)[order]
    cumulative = np.cumsum(weights) - 0.5 * weights
    return float(np.interp(quantile * weights.sum(), cumulative, values))


def _projection(reference, row, future, team_pace):
    """Next-season line from what the most similar player-seasons did next."""
    if future.empty:
        return None
    context = reference["context"].loc[int(row["season"])]
    weights_all = _kernel(future["distance"].to_numpy(dtype=float))
    played = future["has_next"].to_numpy(dtype=bool)
    stay = float(weights_all[played].sum() / weights_all.sum())
    real = future[played]
    weights = weights_all[played]
    if real.empty:
        return {"comparables": int(len(future)), "share_still_playing": round(stay, 3)}
    season_games = real["next_season_games"].fillna(82).to_numpy(dtype=float)
    availability = np.clip(real["next_games"].to_numpy(dtype=float) / season_games, 0, 1)
    current_availability = min(float(row["games"]) / float(row["season_games"] or 82), 1.0)
    out = {"season": int(row["season"]) + 1, "season_label": season_label(int(row["season"]) + 1),
           "age": None if row["age"] is None else int(row["age"]) + 1,
           "comparables": int(len(future)), "comparables_who_played": int(len(real)),
           "share_still_playing": round(stay, 3),
           "share_500_minutes": round(float(weights_all[played][
               real["next_minutes"].to_numpy(dtype=float) >= 500].sum() / weights_all.sum()), 3)}
    delta_mpg = real["next_mpg"].to_numpy(dtype=float) - real["mpg"].to_numpy(dtype=float)
    mpg = np.clip(float(row["mpg"]) + delta_mpg, 0, 42)
    games = np.clip(availability * 82, 0, 82)
    pace = team_pace or float(reference["team_seasons"].loc[
        reference["team_seasons"]["season"] == int(row["season"]), "pace"].mean())
    lines = {}
    for stat in SIM_FEATURES:
        z_delta = real[f"next_z_{stat}"].to_numpy(dtype=float) - real[f"z_{stat}"].to_numpy(dtype=float)
        mask = np.isfinite(z_delta)
        if not mask.any() or not np.isfinite(row[f"z_{stat}"]):
            continue
        z = float(row[f"z_{stat}"]) + z_delta
        values = context[f"mu_{stat}"] + z * context[f"sd_{stat}"]
        lines[stat] = (values, mask)
    poss_per_game = mpg * pace / 48.0

    def summarize(values, mask=None, digits=1):
        mask = np.ones(len(values), dtype=bool) if mask is None else mask
        v, w = np.asarray(values, dtype=float)[mask], weights[mask]
        return {"mid": round(_weighted_quantile(v, w, 0.5), digits),
                "low": round(_weighted_quantile(v, w, 0.2), digits),
                "high": round(_weighted_quantile(v, w, 0.8), digits)}

    per_game = {"mpg": summarize(mpg), "games": summarize(games, digits=0)}
    for stat, label in (("pts_100", "pts"), ("ast_100", "ast"), ("tov_100", "tov"),
                        ("stl_100", "stl"), ("blk_100", "blk")):
        if stat in lines:
            values, mask = lines[stat]
            per_game[label] = summarize(np.maximum(values, 0) * poss_per_game / 100.0, mask)
    if "orb_100" in lines and "drb_100" in lines:
        mask = lines["orb_100"][1] & lines["drb_100"][1]
        per_game["trb"] = summarize(np.maximum(lines["orb_100"][0] + lines["drb_100"][0], 0)
                                    * poss_per_game / 100.0, mask)
    if "ts" in lines:
        per_game["ts_pct"] = summarize(np.clip(lines["ts"][0], 0.3, 0.8), lines["ts"][1], digits=3)
    impact = float(row["box_impact"]) + (real["next_box_impact"] - real["box_impact"]).to_numpy(dtype=float)
    per_game["box_impact"] = summarize(impact, digits=2)
    wins = reference["realization_factor"] * (impact * 5.0 / reference["realization_factor"]) \
        * (mpg * games) / SEASON_MINUTES * reference["wins_slope"]
    per_game["wins_added"] = summarize(wins, digits=1)
    out["line"] = per_game
    out["current"] = {"mpg": _clean(row["mpg"], 1), "games": int(row["games"]),
                      "availability": round(current_availability, 3),
                      "box_impact": _clean(row["box_impact"], 2),
                      "wins_added": _clean(row["wins_added"], 1)}
    aging = {item["age"]: item for item in reference["aging"]["by_age"]}
    if row["age"] is not None and int(row["age"]) in aging:
        out["aging_curve_check"] = {
            "expected_delta_box_impact": aging[int(row["age"])]["delta_box_impact"],
            "comps_delta_box_impact": round(per_game["box_impact"]["mid"] - float(row["box_impact"]), 2),
        }
    out["method"] = (
        "Each comparable player-season's change into the following season (league-relative per-100 "
        "rates, minutes and availability) is applied to this season, weighted by similarity. Rates "
        "are re-expressed in this season's league context; ranges are the 20th-80th percentiles "
        "across comparables. Comparables who left the NBA count toward the 'still playing' share."
    )
    return out


def _with_without(person_id, season, games, db_path=TEAM_DB_PATH):
    """Team record and margin in games the player played vs. missed (association)."""
    regular = games[(games["kind"] == "regular") & (games["season"] == int(season))]
    if regular.empty:
        return None
    out = []
    with sqlite3.connect(db_path) as connection:
        for team_id, stint in regular.groupby("teamId", sort=False):
            first, last = stint["date"].min(), stint["date"].max()
            team_games = pd.read_sql_query(
                """
                SELECT t.gameId, t.gameDateTimeEst, t.win, t.teamScore - t.opponentScore AS margin
                FROM team_statistics t LEFT JOIN games g ON g.gameId = t.gameId
                WHERE t.teamId = ? AND COALESCE(t.gameType, g.gameType) = 'Regular Season'
                  AND t.gameDateTimeEst >= ? AND t.gameDateTimeEst <= ?
                """,
                connection,
                params=(int(team_id), str(first), str(last)),
            )
            if team_games.empty:
                continue
            played_ids = set(stint["gameId"].astype(int))
            on = team_games[team_games["gameId"].astype(int).isin(played_ids)]
            off = team_games[~team_games["gameId"].astype(int).isin(played_ids)]
            _, abbrev = team_label(team_id, season, stint["teamName"].iloc[0], db_path)

            def line(frame):
                if frame.empty:
                    return {"g": 0, "w": 0, "win_pct": None, "margin": None}
                return {"g": int(len(frame)), "w": int(frame["win"].sum()),
                        "win_pct": _ratio(float(frame["win"].sum()), float(len(frame))),
                        "margin": _clean(frame["margin"].mean(), 1)}

            out.append({"team_id": int(team_id), "team": abbrev, "played": line(on), "missed": line(off),
                        "window": [str(first.date()), str(last.date())]})
    return out


def player_outlook(person_id, season=None, db_path=TEAM_DB_PATH):
    """Modeled and extrapolated layers for one player-season (1985-86 onward)."""
    reference = league_reference()
    frame = reference["frame"]
    mine = frame[frame["personId"] == int(person_id)].sort_values("season")
    if mine.empty:
        raise ValueError(
            f"personId {person_id} has no regular-season minutes from "
            f"{season_label(reference['first_season'])} on (the box-score model needs complete "
            "team box scores, which start that season)."
        )
    if season is None:
        season = int(mine["season"].max())
    row = _row_for(frame, person_id, season)
    if row is None:
        raise ValueError(
            f"personId {person_id} has no regular-season minutes in {season_label(season)} "
            f"(seasons available: {', '.join(season_label(s) for s in mine['season'])})."
        )
    league = frame[(frame["season"] == int(season)) & (frame["minutes"] >= QUALIFY_MINUTES)]
    percentiles = []
    for stat, label, higher in PERCENTILE_STATS:
        value = row[stat]
        percentiles.append({
            "stat": stat, "label": label, "value": _clean(value, 3 if stat in ("ts", "tp_pct", "ft_pct") else 2),
            "league_median": _clean(league[stat].median(), 3 if stat in ("ts", "tp_pct", "ft_pct") else 2),
            "percentile": _percentile(league[stat].to_numpy(dtype=float), value, higher),
            "higher_is_better": higher,
        })
    qualified = bool(row["minutes"] >= QUALIFY_MINUTES)
    ranked = [p for p in percentiles if p["percentile"] is not None and p["stat"] not in ("mpg", "box_impact")]
    strengths = sorted(ranked, key=lambda p: -p["percentile"])[:3]
    weaknesses = sorted(ranked, key=lambda p: p["percentile"])[:2]

    shown, future = _comps(frame, row)
    team_ids = row["team_ids"] or []
    paces = reference["team_seasons"]
    team_pace = paces[(paces["teamId"].isin(team_ids)) & (paces["season"] == int(season))]["pace"]
    projection = _projection(reference, row, future, float(team_pace.mean()) if len(team_pace) else None)

    trajectory = [
        {"season": int(r["season"]), "season_label": season_label(r["season"]),
         "age": _clean(r["age"]), "mpg": _clean(r["mpg"], 1), "games": int(r["games"]),
         "box_impact": _clean(r["box_impact"], 2), "wins_added": _clean(r["wins_added"], 1),
         "pts_100": _clean(r["pts_100"], 1), "ts": _clean(r["ts"], 3)}
        for r in mine.to_dict("records")
    ]
    peak = max(trajectory, key=lambda r: r["box_impact"] if r["box_impact"] is not None else -1e9)
    impact_rank = int((league["box_impact"] > row["box_impact"]).sum()) + 1 if qualified else None
    games = load_games(person_id, db_path)
    return {
        "person_id": int(person_id),
        "name": row["name"],
        "season": int(season),
        "season_label": season_label(season),
        "age": _clean(row["age"]),
        "qualified": qualified,
        "qualified_players": int(len(league)),
        "percentiles": percentiles,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "tags": _profile_tags(percentiles) if qualified else [],
        "impact": {
            "box_impact": _clean(row["box_impact"], 2),
            "wins_added": _clean(row["wins_added"], 1),
            "rank": impact_rank,
            "realization_factor": round(reference["realization_factor"], 3),
            "definition": (
                "Box impact: the calibrated change in team net rating per 100 possessions while the "
                "player is on the floor, versus a league-average player, from the box-score team model "
                "(ridge on league-relative per-100 rates, held-out R2 0.90) scaled by the realization "
                "factor measured on real roster changes. Wins added: that impact over the player's "
                "minutes, at the empirical wins per net-rating point. Box scores capture offense far "
                "better than defense."
            ),
        },
        "trajectory": trajectory,
        "peak": peak,
        "comps": [_comp_entry(c) for c in shown.to_dict("records")],
        "projection": projection,
        "aging_curve": reference["aging"],
        "with_without": _with_without(person_id, season, games, db_path),
        "layers": {
            "recorded": "box scores", "calculated": "percentiles from league distributions",
            "modeled": "box impact and wins added (era_swap box-score model, calibrated)",
            "extrapolated": "comparables, aging curve and next-season projection",
        },
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Detailed player profile (JSON).")
    parser.add_argument("person_id", type=int)
    parser.add_argument("--season", type=int)
    parser.add_argument("--section", choices=["profile", "log", "outlook"], default="profile")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.section == "profile":
        result = player_profile(args.person_id)
    elif args.section == "log":
        result = player_game_log(args.person_id, args.season)
    else:
        result = player_outlook(args.person_id, args.season)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
