"""Sandbox: build any roster scenario, then play out the season and playoffs.

A scenario is a season plus an ordered list of moves:

* ``trade``   -- players change teams (two or more teams; anyone can be sent
  anywhere: ``{"type": "trade", "assets": [{"person_id", "from_team_id",
  "to_team_id"}, ...]}``).
* ``sign``    -- add any player-season from 1985-86 on, including a player from
  another era (``{"type": "sign", "person_id", "to_team_id", "from_season"}``);
  a season other than the host season is era-translated first.
* ``release`` -- remove a player from a team (waived, retired, out for the
  year): ``{"type": "release", "person_id", "team_id"}``.
* ``injury``  -- a player misses part of the season:
  ``{"type": "injury", "person_id", "team_id", "games_missed"}``.
* ``minutes`` -- set a player's minutes per game, and optionally games played
  (e.g. a full healthy season): ``{"type": "minutes", "person_id", "team_id",
  "mpg", "games"}``.

Two kinds of season (``mode``):

* ``replay`` (1985-86 to the latest completed season): the real rosters and
  the production model's game-by-game probabilities for that season -- the
  path the What-if Lab uses for one swap, generalized to any number of moves
  and teams. Each game already reflects that season's earlier results, so
  this answers "that season, as it unfolded, with these changes".
* ``next`` (the season after the latest one in the database): rosters as
  they ended last season (each player on their last team), team strength
  frozen at the end of that season by the production model, last season's
  schedule reused as the matchup grid (no real schedule is in the database
  yet), and the calibrated preseason strength uncertainty of the forward
  projection (``forward_projection.default_strength_sd(0)``) shared by the
  regular season and the playoffs.

How a move changes a team (``era_swap``'s validated box-score model): each
player is a vector of league-relative per-100 box-score features; a team's
composite is the minutes-weighted mean vector; the ridge coefficients map a
composite to net rating. Minutes are re-balanced after every move: incoming
players keep the minutes share they had (availability included), returning
players scale up or down proportionally (capped at 42 minutes a game), and any
gap a thin roster cannot fill goes to replacement-level players (the
minutes-weighted average of that season's 50-500 minute players). The change
in composite times the coefficients is the full box-score change; the
realization factor measured on real roster changes (about 0.22, 80% range
0.18-0.25) scales it to the calibrated estimate the season is simulated with.

A team's net-rating change becomes a per-game margin at its pace, then a
probit shift of every game it plays: ``p' = Phi(Phi^-1(p) + (dm_home -
dm_away) / sigma)`` with ``era_swap``'s sigma. Baseline and scenario seasons
are simulated with common random numbers, so differences come from the
moves, not from luck.
"""

import json
import sqlite3
import threading
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from src import era_swap
    from src.main import TEAM_DB_PATH
    from src.season_tables import RECOVERED_TEAM_ID, load_tables
    from src.simulate_season import conference_of, load_team_names
    from src.forward_projection import (
        default_strength_sd,
        frozen_matchup_probabilities,
    )
    from src import playoffs as po
except ImportError:  # pragma: no cover - direct-script support
    import era_swap
    from main import TEAM_DB_PATH
    from season_tables import RECOVERED_TEAM_ID, load_tables
    from simulate_season import conference_of, load_team_names
    from forward_projection import default_strength_sd, frozen_matchup_probabilities
    import playoffs as po


ROOT = Path(__file__).resolve().parents[1]
ERA_MODEL_REPORT = ROOT / "models" / "era_swap_model.json"
NBA_TEAM_IDS = list(range(1610612737, 1610612767))
MAX_SHARE = 0.175            # ~42 minutes a game over a full season
MAX_GROWTH = 1.6             # returning players grow at most 60% in minutes
MAX_FIXED_TOTAL = 0.92       # incoming + set minutes cannot exceed this share
REPLACEMENT_MINUTES = (50, 500)
STAGES = ["made_playoffs", "won_first_round", "won_conf_semifinals", "won_conf_finals", "champion"]
STORIES = 6
MOVE_TYPES = ("trade", "sign", "release", "injury", "minutes")


class ScenarioError(ValueError):
    """A move that cannot be applied (unknown player, wrong team, bad value)."""


# ---------------------------------------------------------------------------
# Model pieces (cached per process)
# ---------------------------------------------------------------------------

_ENGINE_LOCK = threading.Lock()


def engine():
    """Season tables, league context and the validated box-score model numbers.

    Built once per process; the lock makes concurrent first callers (the
    server's warm-up thread and a request) wait for one build.
    """
    with _ENGINE_LOCK:
        return _build_engine()


@lru_cache(maxsize=1)
def _build_engine():
    team_seasons, player_seasons = load_tables()
    context = era_swap.league_context(player_seasons)
    reports = json.loads(ERA_MODEL_REPORT.read_text(encoding="utf-8"))
    beta = np.array([reports["team_model"]["coefficients"][f] for f in era_swap.FEATURES])
    calibration = reports["roster_change_calibration"]
    combined = era_swap.combined_index(player_seasons)
    names = player_seasons.groupby("personId")[["firstName", "lastName"]].last()
    name_of = {int(pid): f"{row.firstName} {row.lastName}".strip() for pid, row in names.iterrows()}
    replacement = {}
    for season, group in player_seasons.groupby("season"):
        fringe = group[group["minutes"].between(*REPLACEMENT_MINUTES)]
        vectors = np.vstack([era_swap.player_features(r, season, context) for r in fringe.to_dict("records")])
        mask = np.all(np.isfinite(vectors), axis=1)
        replacement[int(season)] = np.average(vectors[mask], axis=0,
                                              weights=fringe["minutes"].to_numpy()[mask])
    season_games = team_seasons.groupby("season")["games"].median().astype(int).to_dict()
    return {
        "team_seasons": team_seasons,
        "player_seasons": player_seasons,
        "context": context,
        "beta": beta,
        "realization": float(calibration["realization_factor"]),
        "realization_interval": [float(v) for v in calibration["realization_factor_80pct_interval"]],
        "wins_slope": float(reports["wins_per_net_rating_point"]["slope"]),
        "sigma": float(reports["probit_sigma"]["value"]),
        "team_model_r2": float(reports["team_model"]["test_r2"]),
        "calibration": calibration,
        "combined": combined,
        "name_of": name_of,
        "replacement": replacement,
        "season_games": {int(k): int(v) for k, v in season_games.items()},
        "first_season": era_swap.FIRST_SEASON,
        "latest_season": int(player_seasons["season"].max()),
    }


def seasons_available():
    eng = engine()
    return {
        "replay": list(range(eng["first_season"], eng["latest_season"] + 1)),
        "next": eng["latest_season"] + 1,
        "latest_completed": eng["latest_season"],
    }


def _label(season):
    return f"{int(season)}-{str(int(season) + 1)[-2:]}"


def _host_season(season, mode):
    """The season whose box scores describe the rosters."""
    eng = engine()
    if mode == "next":
        if int(season) != eng["latest_season"] + 1:
            raise ScenarioError(
                f"'next' mode projects {_label(eng['latest_season'] + 1)} only "
                f"(the season after the latest one in the database)."
            )
        return eng["latest_season"]
    if mode != "replay":
        raise ScenarioError("mode must be 'replay' or 'next'.")
    if not eng["first_season"] <= int(season) <= eng["latest_season"]:
        raise ScenarioError(
            f"Replay seasons run from {_label(eng['first_season'])} (complete team box scores) "
            f"to {_label(eng['latest_season'])}."
        )
    return int(season)


def _vector(rates, origin, target):
    eng = engine()
    if int(origin) != int(target):
        rates = era_swap.translate_rates(rates, origin, target, eng["context"], "zscore")
    vector = era_swap.player_features(rates, int(target), eng["context"])
    return np.where(np.isfinite(vector), vector, 0.0), rates


def _impact(vector):
    """Calibrated box impact per 100 on-court possessions (as on player pages)."""
    eng = engine()
    return eng["realization"] * float(eng["beta"] @ vector) / 5.0


# ---------------------------------------------------------------------------
# Baseline rosters
# ---------------------------------------------------------------------------

@lru_cache(maxsize=8)
def _last_teams(host):
    """Each player's team in their last regular-season game of ``host``."""
    with sqlite3.connect(TEAM_DB_PATH) as connection:
        frame = pd.read_sql_query(
            f"""
            SELECT ps.personId AS personId, {RECOVERED_TEAM_ID} AS teamId,
                   ps.gameDateTimeEst AS date
            FROM player_statistics ps LEFT JOIN games g ON g.gameId = ps.gameId
            WHERE COALESCE(ps.gameType, g.gameType) = 'Regular Season'
              AND CAST(ps.numMinutes AS REAL) > 0
              AND ps.gameDateTimeEst >= ? AND ps.gameDateTimeEst < ?
            """,
            connection,
            params=(f"{int(host)}-09-01", f"{int(host) + 1}-09-01"),
        )
    frame = frame.dropna(subset=["teamId"]).sort_values("date")
    last = frame.groupby("personId").tail(1)
    return {int(p): int(t) for p, t in zip(last["personId"], last["teamId"])}


def _entry(person_id, rates, origin, host, share, games, status, team_games):
    vector, translated = _vector(rates, origin, host)
    eng = engine()
    return {
        "person_id": int(person_id),
        "name": eng["name_of"].get(int(person_id), str(person_id)),
        "source_season": int(origin),
        "vector": vector,
        "share": float(share),
        "natural_share": float(share),
        # Display only: real minutes per game played, and the share they map to.
        "base_mpg": float(rates["minutes"]) / float(rates["games"]) if rates.get("games") else 0.0,
        "ref_share": float(share),
        "games": float(games),
        "team_games": int(team_games),
        "status": status,
        "fixed": False,
        "notes": [],
        "pts_100": translated.get("pts_100"),
        "ts": translated.get("ts"),
    }


@lru_cache(maxsize=8)
def _baseline_cached(season, mode):
    eng = engine()
    host = _host_season(season, mode)
    players = eng["player_seasons"]
    rows = players[players["season"] == host]
    games = eng["season_games"].get(host, 82)
    rosters = {team: [] for team in NBA_TEAM_IDS}
    if mode == "replay":
        team_minutes = rows.groupby("teamId")["minutes"].sum()
        for row in rows.to_dict("records"):
            team = int(row["teamId"])
            if team not in rosters:
                continue
            share = row["minutes"] / team_minutes[team]
            rosters[team].append(_entry(row["personId"], row, host, host, share, row["games"],
                                        "returning", games))
    else:
        last = _last_teams(host)
        for person in rows["personId"].unique():
            team = last.get(int(person))
            if team not in rosters:
                continue
            whole = eng["combined"][(int(person), host)]
            share = whole["minutes"] / (games * 240.0)
            rosters[team].append(_entry(person, whole, host, host, share, whole["games"],
                                        "returning", games))
    for team, roster in rosters.items():
        total = sum(entry["share"] for entry in roster)
        for entry in roster:
            entry["share"] = entry["natural_share"] = entry["ref_share"] = (
                entry["share"] / total if total else 0.0)
        roster.sort(key=lambda entry: -entry["share"])
    return host, rosters


def baseline_rosters(season, mode="replay"):
    """``(host_season, {teamId: [entries]})``; entries are fresh copies."""
    host, rosters = _baseline_cached(int(season), mode)
    return host, {team: [dict(entry, notes=[]) for entry in roster] for team, roster in rosters.items()}


# ---------------------------------------------------------------------------
# Applying moves
# ---------------------------------------------------------------------------

def _find(rosters, person_id, team_id=None):
    for team, roster in rosters.items():
        if team_id is not None and team != int(team_id):
            continue
        for index, entry in enumerate(roster):
            if entry["person_id"] == int(person_id) and entry["status"] != "replacement":
                return team, index
    return None, None


def _team_name(team_id, season):
    info = load_team_names(season).get(int(team_id))
    return info["teamName"] if info else str(team_id)


def _check_team(team_id, field):
    try:
        team = int(team_id)
    except (TypeError, ValueError):
        raise ScenarioError(f"'{field}' must be a teamId.")
    if team not in NBA_TEAM_IDS:
        raise ScenarioError(f"teamId {team_id} is not one of the 30 NBA franchises.")
    return team


def _incoming_share(entry):
    """A moved player's minutes share on the new team (availability kept)."""
    return min(entry["natural_share"], MAX_SHARE)


def _whole_season_entry(person, origin, host, status):
    """A player's whole ``origin`` season (all teams) as one roster entry."""
    eng = engine()
    whole = eng["combined"].get((int(person), int(origin)))
    if whole is None or not whole.get("player_possessions"):
        raise ScenarioError(
            f"{eng['name_of'].get(int(person), person)} has no regular-season minutes in {_label(origin)}.")
    share = whole["minutes"] / (eng["season_games"].get(int(origin), 82) * 240.0)
    entry = _entry(person, whole, origin, host, share, whole["games"], status,
                   eng["season_games"].get(int(host), 82))
    entry["fixed"] = True
    entry["share"] = entry["ref_share"] = _incoming_share(entry)
    entry["base_mpg"] = min(entry["base_mpg"], 42.0) if entry["natural_share"] > MAX_SHARE else entry["base_mpg"]
    return entry


def _remove_everywhere(rosters, person):
    """Remove every stint of ``person``; return the teams they were removed from."""
    removed = []
    for team, roster in rosters.items():
        kept = [e for e in roster if not (e["person_id"] == int(person) and e["status"] != "replacement")]
        if len(kept) != len(roster):
            removed.append(team)
            roster[:] = kept
    return removed


def apply_moves(season, mode, moves):
    """Apply ``moves`` in order. Returns ``(host, base, rosters, log, touched)``."""
    host, base = baseline_rosters(season, mode)
    _, rosters = baseline_rosters(season, mode)
    eng = engine()
    games = eng["season_games"].get(host, 82)
    log, touched = [], set()
    if not isinstance(moves, list):
        raise ScenarioError("'moves' must be a list.")
    for number, move in enumerate(moves, start=1):
        if not isinstance(move, dict) or move.get("type") not in MOVE_TYPES:
            raise ScenarioError(f"Move {number}: 'type' must be one of {', '.join(MOVE_TYPES)}.")
        kind = move["type"]
        try:
            if kind == "trade":
                assets = move.get("assets") or []
                if not assets:
                    raise ScenarioError("a trade needs at least one player in 'assets'.")
                people = [int(asset.get("person_id", 0)) for asset in assets]
                if len(set(people)) != len(people):
                    raise ScenarioError("the same player appears twice in one trade.")
                pulled = []
                for asset in assets:
                    origin = _check_team(asset.get("from_team_id"), "from_team_id")
                    dest = _check_team(asset.get("to_team_id"), "to_team_id")
                    if origin == dest:
                        raise ScenarioError("a player cannot be traded to the team they are already on.")
                    team, index = _find(rosters, asset.get("person_id"), origin)
                    if team is None:
                        raise ScenarioError(
                            f"{eng['name_of'].get(int(asset.get('person_id', 0)), asset.get('person_id'))} "
                            f"is not on the {_team_name(origin, host)} in this scenario.")
                    pulled.append((rosters[team][index], origin, dest))
                moved = []
                for entry, origin, dest in pulled:
                    person = entry["person_id"]
                    touched.update(_remove_everywhere(rosters, person))
                    if entry["status"] == "returning":
                        # A real roster player moves with their whole season.
                        entry = _whole_season_entry(person, entry["source_season"], host, "acquired")
                    else:
                        entry.update(status="acquired", fixed=True)
                    entry["notes"].append(f"from {_team_name(origin, host)}")
                    moved.append((entry, origin, dest))
                for entry, origin, dest in moved:
                    rosters[dest].append(entry)
                    touched.update({origin, dest})
                pulled = moved
                teams = sorted({o for _, o, _ in pulled} | {d for _, _, d in pulled})
                log.append({"move": number, "type": "trade", "teams": teams,
                            "players": [{"person_id": e["person_id"], "name": e["name"],
                                         "from_team_id": o, "to_team_id": d} for e, o, d in pulled]})
            elif kind == "sign":
                dest = _check_team(move.get("to_team_id"), "to_team_id")
                person = int(move.get("person_id"))
                origin = int(move.get("from_season") or host)
                if origin < eng["first_season"]:
                    raise ScenarioError(
                        f"player seasons start in {_label(eng['first_season'])} (complete team box scores).")
                if origin > eng["latest_season"]:
                    raise ScenarioError(f"the latest season in the database is {_label(eng['latest_season'])}.")
                entry = _whole_season_entry(person, origin, host, "signed")
                left = _remove_everywhere(rosters, person)
                touched.update(left)
                if origin != host:
                    entry["notes"].append(f"{_label(origin)} season, era-translated into {_label(host)}")
                if left:
                    entry["notes"].append("left the " + ", ".join(_team_name(t, host) for t in left))
                rosters[dest].append(entry)
                touched.add(dest)
                log.append({"move": number, "type": "sign", "person_id": person, "name": entry["name"],
                            "to_team_id": dest, "from_season": origin, "removed_from_team_ids": left})
            elif kind == "release":
                team_id = _check_team(move.get("team_id"), "team_id")
                team, index = _find(rosters, move.get("person_id"), team_id)
                if team is None:
                    raise ScenarioError(f"that player is not on the {_team_name(team_id, host)} in this scenario.")
                entry = rosters[team].pop(index)
                touched.add(team)
                log.append({"move": number, "type": "release", "person_id": entry["person_id"],
                            "name": entry["name"], "team_id": team})
            elif kind == "injury":
                team_id = _check_team(move.get("team_id"), "team_id")
                team, index = _find(rosters, move.get("person_id"), team_id)
                if team is None:
                    raise ScenarioError(f"that player is not on the {_team_name(team_id, host)} in this scenario.")
                missed = float(move.get("games_missed", 0))
                if not 0 <= missed <= games:
                    raise ScenarioError(f"'games_missed' must be between 0 and {games}.")
                if missed > rosters[team][index]["games"] * rosters[team][index].get("games_factor", 1.0):
                    missed = rosters[team][index]["games"] * rosters[team][index].get("games_factor", 1.0)
                entry = rosters[team][index]
                played = entry["games"] * entry.get("games_factor", 1.0)
                remaining = max(0.0, 1.0 - missed / played) if played > 0 else 0.0
                entry.update(share=entry["share"] * remaining, fixed=True,
                             games_factor=entry.get("games_factor", 1.0) * remaining)
                entry["notes"].append(f"misses {int(missed)} games")
                touched.add(team)
                log.append({"move": number, "type": "injury", "person_id": entry["person_id"],
                            "name": entry["name"], "team_id": team, "games_missed": int(missed)})
            elif kind == "minutes":
                team_id = _check_team(move.get("team_id"), "team_id")
                team, index = _find(rosters, move.get("person_id"), team_id)
                if team is None:
                    raise ScenarioError(f"that player is not on the {_team_name(team_id, host)} in this scenario.")
                mpg = float(move.get("mpg", -1))
                if not 0 <= mpg <= 48:
                    raise ScenarioError("'mpg' must be between 0 and 48.")
                entry = rosters[team][index]
                if move.get("games") is not None:
                    played = float(move["games"])
                    if not 1 <= played <= entry["team_games"]:
                        raise ScenarioError(f"'games' must be between 1 and {entry['team_games']}.")
                    entry["games_factor"] = played / entry["games"] if entry["games"] else 1.0
                    if not entry["games"]:
                        entry["games"] = played
                availability = _availability(entry) * entry.get("games_factor", 1.0)
                share = mpg * availability / 240.0
                entry.update(share=share, fixed=True, base_mpg=mpg,
                             ref_share=share / entry.get("games_factor", 1.0))
                played_note = (f" in {int(round(entry['games'] * entry.get('games_factor', 1.0)))} games"
                               if move.get("games") is not None else "")
                entry["notes"].append(f"set to {mpg:g} minutes a game{played_note}")
                touched.add(team)
                log.append({"move": number, "type": "minutes", "person_id": entry["person_id"],
                            "name": entry["name"], "team_id": team, "mpg": mpg,
                            "games": None if move.get("games") is None else int(move["games"])})
        except ScenarioError as exc:
            raise ScenarioError(f"Move {number} ({kind}): {exc}") from None
        except (TypeError, ValueError) as exc:
            raise ScenarioError(f"Move {number} ({kind}): {exc}") from None
    for team in touched:
        _rebalance(rosters[team], host)
    return host, base, rosters, log, touched


def _rebalance(roster, host):
    """Fill exactly one team's worth of minutes after moves (see module docs)."""
    roster[:] = [entry for entry in roster if entry["status"] != "replacement"]
    fixed = [entry for entry in roster if entry["fixed"]]
    free = [entry for entry in roster if not entry["fixed"]]
    fixed_total = sum(entry["share"] for entry in fixed)
    if fixed_total > MAX_FIXED_TOTAL:
        scale = MAX_FIXED_TOTAL / fixed_total
        for entry in fixed:
            entry["share"] *= scale
            entry["notes"].append("minutes scaled down: the roster had more minutes than a team plays")
        fixed_total = MAX_FIXED_TOTAL
    target = 1.0 - fixed_total
    caps = {id(e): max(min(MAX_SHARE, e["natural_share"] * MAX_GROWTH,
                           _availability(e) * e.get("games_factor", 1.0) * 42.0 / 240.0),
                       e["natural_share"]) for e in free}
    base = {id(e): e["natural_share"] for e in free}
    # Water-filling: scale uncapped players proportionally until the target
    # is met or everyone is capped.
    shares = dict(base)
    for _ in range(50):
        total = sum(shares.values())
        if total <= 0 or abs(total - target) < 1e-9:
            break
        open_ids = [key for key in shares if shares[key] < caps[key] - 1e-12] if total < target else list(shares)
        if not open_ids:
            break
        open_total = sum(shares[key] for key in open_ids)
        if open_total <= 0:
            break
        factor = 1.0 + (target - total) / open_total
        for key in open_ids:
            shares[key] = min(shares[key] * factor, caps[key]) if factor > 1 else shares[key] * factor
    for entry in free:
        entry["share"] = shares[id(entry)]
    gap = 1.0 - sum(entry["share"] for entry in roster)
    if gap > 0.005:
        eng = engine()
        roster.append({
            "person_id": 0, "name": "Replacement-level players", "source_season": host,
            "vector": eng["replacement"][host], "share": gap, "natural_share": 0.0, "games": 0.0,
            "team_games": eng["season_games"].get(host, 82), "status": "replacement", "fixed": True,
            "notes": ["fills minutes the roster cannot cover"], "pts_100": None, "ts": None,
        })
    roster.sort(key=lambda entry: (entry["status"] == "replacement", -entry["share"]))


def _availability(entry):
    return min(entry["games"] / entry["team_games"], 1.0) if entry["team_games"] else 1.0


def _display(entry):
    """``(minutes per game played, games)`` for display.

    Starts from the player's real minutes per game and scales it by how much
    the model's minutes share changed (injuries change games, not minutes).
    """
    factor = entry.get("games_factor", 1.0)
    games = entry["games"] * factor
    if entry["status"] == "replacement" or games <= 0:
        return round(entry["share"] * 240.0, 1), None
    if entry.get("ref_share"):
        mpg = entry["base_mpg"] * entry["share"] / (entry["ref_share"] * factor)
    else:
        mpg = entry["share"] * 240.0 * entry["team_games"] / games
    return round(min(mpg, 48.0), 1), int(round(games))


def _composite(roster):
    total = sum(entry["share"] for entry in roster)
    if total <= 0:
        return np.zeros(len(era_swap.FEATURES))
    return sum(entry["share"] * entry["vector"] for entry in roster) / total


# ---------------------------------------------------------------------------
# Team effects
# ---------------------------------------------------------------------------

def _roster_table(base_roster, new_roster, host):
    eng = engine()
    before = {e["person_id"]: e for e in base_roster}
    after = {e["person_id"]: e for e in new_roster}
    rows = []
    for person in list(after) + [p for p in before if p not in after]:
        old, new = before.get(person), after.get(person)
        entry = new or old
        status = new["status"] if new else "departed"
        rows.append({
            "person_id": entry["person_id"], "name": entry["name"], "status": status,
            "source_season": entry["source_season"],
            "mpg_before": None if old is None else _display(old)[0],
            "mpg_after": None if new is None else _display(new)[0],
            "games_before": None if old is None else _display(old)[1],
            "games_after": None if new is None else _display(new)[1],
            "minutes_share_before": None if old is None else round(old["share"], 4),
            "minutes_share_after": None if new is None else round(new["share"], 4),
            "box_impact": round(_impact(entry["vector"]), 2),
            "pts_100": None if entry.get("pts_100") is None or not np.isfinite(entry["pts_100"])
            else round(float(entry["pts_100"]), 1),
            "notes": (new or {}).get("notes", []),
            "contribution_full": round(float(eng["beta"] @ (
                (0 if new is None else new["share"]) * entry["vector"]
                - (0 if old is None else old["share"]) * entry["vector"])), 3),
        })
    rows.sort(key=lambda row: (row["status"] == "departed", row["status"] == "replacement",
                               -(row["minutes_share_after"] or row["minutes_share_before"] or 0)))
    return rows


def team_effects(host, base, rosters, touched):
    eng = engine()
    beta, factor = eng["beta"], eng["realization"]
    low, high = eng["realization_interval"]
    paces = eng["team_seasons"][eng["team_seasons"]["season"] == host].set_index("teamId")["pace"]
    effects = {}
    for team in sorted(touched):
        delta_vector = _composite(rosters[team]) - _composite(base[team])
        full = float(beta @ delta_vector)
        contributions = beta * delta_vector
        order = np.argsort(-np.abs(contributions))
        pace = float(paces.get(team, paces.mean()))
        effects[team] = {
            "team_id": team,
            "team_name": _team_name(team, host),
            "pace": round(pace, 1),
            "delta_net_rating_full": round(full, 3),
            "delta_net_rating": round(factor * full, 3),
            "delta_net_rating_80pct": sorted([round(low * full, 3), round(high * full, 3)]),
            "delta_wins_estimate": round(factor * full * eng["wins_slope"], 1),
            "factors": [{"feature": era_swap.FEATURES[i], "delta_full": round(float(contributions[i]), 3)}
                        for i in order[:4]],
            "roster": _roster_table(base[team], rosters[team], host),
        }
    return effects


def preview(season, mode, moves):
    """Apply moves and report each changed team's net-rating change (no simulation)."""
    host, base, rosters, log, touched = apply_moves(season, mode, moves)
    effects = team_effects(host, base, rosters, touched)
    return {
        "season": int(season), "season_label": _label(season), "mode": mode, "host_season": host,
        "moves": log, "teams": list(effects.values()),
        "realization_factor": engine()["realization"],
    }


def roster_snapshot(season, mode="replay"):
    """Every team's baseline roster for the builder UI (minutes and box impact)."""
    host, rosters = baseline_rosters(season, mode)
    names = load_team_names(host if mode == "replay" else host + 1)
    teams = []
    for team in NBA_TEAM_IDS:
        roster = rosters[team]
        if not roster:
            continue
        info = names.get(team, {})
        teams.append({
            "team_id": team,
            "team_name": info.get("teamName", str(team)),
            "abbreviation": info.get("teamAbbreviation", ""),
            "conference": conference_of(team),
            "players": [{
                "person_id": e["person_id"], "name": e["name"],
                "mpg": _display(e)[0], "games": int(e["games"]),
                "minutes_share": round(e["share"], 4),
                "box_impact": round(_impact(e["vector"]), 2),
                "pts_100": None if e.get("pts_100") is None else round(float(e["pts_100"]), 1),
            } for e in roster],
        })
    return {"season": int(season), "season_label": _label(season), "mode": mode, "host_season": host,
            "host_season_label": _label(host), "teams": teams}


# ---------------------------------------------------------------------------
# Season + playoff simulation
# ---------------------------------------------------------------------------

def _shift(probability, delta_margin, sigma):
    from scipy.stats import norm

    probability = np.clip(np.asarray(probability, dtype=float), 1e-6, 1 - 1e-6)
    return norm.cdf(norm.ppf(probability) + np.asarray(delta_margin) / sigma)


def _logit(probability):
    probability = np.clip(probability, 1e-6, 1 - 1e-6)
    return np.log(probability / (1 - probability))


_SETUP_CACHE = {}
_SETUP_LOCK = threading.Lock()


def season_setup(season, mode, probabilities=None, inputs=None):
    """Games with base home-win probabilities, the playoff matrix and settings.

    Deterministic for a season, mode and set of model inputs, so it is built
    once per process (scoring the schedule and the playoff matrix with the
    frozen model takes a few seconds).
    """
    key = (int(season), mode, id(probabilities), id(inputs))
    with _SETUP_LOCK:
        if key not in _SETUP_CACHE:
            _SETUP_CACHE[key] = _season_setup(season, mode, probabilities, inputs)
    return _SETUP_CACHE[key]


def _season_setup(season, mode, probabilities=None, inputs=None):
    host = _host_season(season, mode)
    if mode == "replay":
        if probabilities is None:
            raise ScenarioError("replay mode needs the season's pregame probabilities.")
        games = probabilities[probabilities["season"] == int(season)][
            ["homeTeamId", "awayTeamId", "home_win_probability"]].reset_index(drop=True)
        if games.empty:
            raise ScenarioError(f"No games for {_label(season)} in the model dataset.")
        postseason, note = era_swap._postseason_format(season)
        teams = sorted(int(t) for t in set(games["homeTeamId"]) | set(games["awayTeamId"]))
        matrix = po.matchup_matrix(teams, postseason["cutoff"], inputs) if postseason and inputs is not None else None
        if postseason and inputs is None:
            postseason, note = None, "model inputs were not provided"
        return {
            "games": games, "teams": teams, "matrix": matrix, "strength_sd": 0.0,
            "play_in": bool(postseason and postseason["play_in"]),
            "postseason": postseason is not None, "postseason_note": note,
            "schedule_note": f"The real {_label(season)} schedule and the production model's game-by-game "
                             "probabilities (each reflects that season's earlier results).",
        }
    if inputs is None:
        raise ScenarioError("next-season mode needs the model inputs.")
    template = inputs.games[inputs.games["season"] == host][["homeTeamId", "awayTeamId"]]
    cutoff = pd.Timestamp(f"{int(season)}-10-01")
    scored = frozen_matchup_probabilities(template.reset_index(drop=True), cutoff, inputs)
    games = scored[["homeTeamId", "awayTeamId", "home_win_probability"]].reset_index(drop=True)
    teams = sorted(int(t) for t in set(games["homeTeamId"]) | set(games["awayTeamId"]))
    return {
        "games": games, "teams": teams, "matrix": po.matchup_matrix(teams, cutoff, inputs),
        "strength_sd": default_strength_sd(0.0), "play_in": True, "postseason": True,
        "postseason_note": None,
        "schedule_note": f"No {_label(season)} schedule is in the database, so the {_label(host)} matchups "
                         "(same opponents and home/road split) are reused. Team strength is frozen where the "
                         f"production model left it after {_label(host)}, with the calibrated preseason "
                         "uncertainty.",
    }


def _series_length(pattern):
    return len(pattern) // 2 + 1


class _Bracket:
    """Samples play-in and playoff games, optionally recording the story."""

    def __init__(self, setup, season, deltas, sigma, shocks):
        self.matrix = setup["matrix"]
        self.season = int(season)
        self.deltas = deltas
        self.sigma = sigma
        self.shocks = shocks
        best_of_first = 5 if 1983 <= self.season <= 2001 else 7
        self.first_pattern = po.series_pattern(self.season, "first_round", best_of_first)
        self.later_pattern = po.series_pattern(self.season, "conf_semifinals", 7)
        self.finals_pattern = po.series_pattern(self.season, "finals", 7)
        self._cache = {}

    def p_home(self, home, away, shock):
        key = (home, away)
        if key not in self._cache:
            base = self.matrix[key]
            margin = self.deltas.get(home, 0.0) - self.deltas.get(away, 0.0)
            self._cache[key] = float(_shift(base, margin, self.sigma)) if margin else base
        p = self._cache[key]
        if shock is not None:
            p = 1.0 / (1.0 + np.exp(-(_logit(p) + shock.get(home, 0.0) - shock.get(away, 0.0))))
        return p

    def game(self, rng, home, away, shock):
        return home if rng.random() < self.p_home(home, away, shock) else away

    def series(self, rng, higher, lower, pattern, shock):
        need = _series_length(pattern)
        won_high = won_low = 0
        for venue in pattern:
            home, away = (higher, lower) if venue == "H" else (lower, higher)
            winner = self.game(rng, home, away, shock)
            if winner == higher:
                won_high += 1
            else:
                won_low += 1
            if won_high == need or won_low == need:
                break
        return (higher if won_high == need else lower), won_high, won_low


def _run_postseason(bracket, wins, teams, rng, play_in, n_simulations, shocks_matrix, story_indices):
    index_of = {team: i for i, team in enumerate(teams)}
    conferences = {conf: [i for i, t in enumerate(teams) if conference_of(t) == conf] for conf in ("East", "West")}
    counts = {team: dict.fromkeys(STAGES + ["play_in", "top_six"], 0) for team in teams}
    seed_sums = {team: 0.0 for team in teams}
    stories = []
    n_seeds = 10 if play_in else 8
    for sim in range(int(n_simulations)):
        shock = ({team: float(v) for team, v in zip(teams, shocks_matrix[sim])}
                 if shocks_matrix is not None else None)
        record = sim in story_indices
        story = {"conferences": {}, "finals": None} if record else None
        champions = {}
        for conf, members in conferences.items():
            member_ids = [teams[i] for i in members]
            tiebreak = rng.random(len(member_ids))
            order = sorted(range(len(member_ids)), key=lambda i: (-wins[sim, members[i]], tiebreak[i]))
            ranked = [member_ids[i] for i in order]
            for seed, team in enumerate(ranked, start=1):
                seed_sums[team] += seed
                if seed <= 6:
                    counts[team]["top_six"] += 1
            seeds = {seed: ranked[seed - 1] for seed in range(1, min(n_seeds, len(ranked)) + 1)}
            conf_story = None
            if record:
                conf_story = {
                    "standings": [{"seed": s, "team_id": t, "wins": int(wins[sim, index_of[t]])}
                                  for s, t in enumerate(ranked, start=1)],
                    "play_in": [], "rounds": [],
                }
            if play_in:
                for seed in (7, 8, 9, 10):
                    counts[seeds[seed]]["play_in"] += 1
                first = bracket.game(rng, seeds[7], seeds[8], shock)
                loser = seeds[8] if first == seeds[7] else seeds[7]
                second = bracket.game(rng, seeds[9], seeds[10], shock)
                third = bracket.game(rng, loser, second, shock)
                field = {s: seeds[s] for s in range(1, 7)}
                field[7], field[8] = first, third
                if record:
                    conf_story["play_in"] = [
                        {"home": seeds[7], "away": seeds[8], "winner": first, "for": "7 seed"},
                        {"home": seeds[9], "away": seeds[10], "winner": second, "for": "survival"},
                        {"home": loser, "away": second, "winner": third, "for": "8 seed"},
                    ]
            else:
                field = seeds
            seed_of = {team: seed for seed, team in field.items()}
            for team in field.values():
                counts[team]["made_playoffs"] += 1

            def play(a, b, pattern, label):
                higher, lower = (a, b) if seed_of[a] <= seed_of[b] else (b, a)
                winner, w_high, w_low = bracket.series(rng, higher, lower, pattern, shock)
                if record:
                    conf_story["rounds"].append({
                        "round": label, "higher": higher, "lower": lower,
                        "higher_seed": seed_of[higher], "lower_seed": seed_of[lower],
                        "winner": winner, "score": [w_high, w_low]})
                return winner

            round_one = {}
            for high, low in po.BRACKET_PAIRS:
                winner = play(field[high], field[low], bracket.first_pattern, "First round")
                round_one[(high, low)] = winner
                counts[winner]["won_first_round"] += 1
            top = play(round_one[(1, 8)], round_one[(4, 5)], bracket.later_pattern, "Conference semifinals")
            bottom = play(round_one[(2, 7)], round_one[(3, 6)], bracket.later_pattern, "Conference semifinals")
            counts[top]["won_conf_semifinals"] += 1
            counts[bottom]["won_conf_semifinals"] += 1
            champion = play(top, bottom, bracket.later_pattern, "Conference finals")
            counts[champion]["won_conf_finals"] += 1
            champions[conf] = champion
            if record:
                story["conferences"][conf] = conf_story
        east, west = champions["East"], champions["West"]
        if (wins[sim, index_of[east]], rng.random()) >= (wins[sim, index_of[west]], rng.random()):
            higher, lower = east, west
        else:
            higher, lower = west, east
        title, w_high, w_low = bracket.series(rng, higher, lower, bracket.finals_pattern, shock)
        counts[title]["champion"] += 1
        if record:
            story["finals"] = {"higher": higher, "lower": lower, "winner": title, "score": [w_high, w_low]}
            story["simulation"] = sim
            stories.append(story)
    return counts, seed_sums, stories


def _regular_season(setup, deltas, sigma, n_simulations, uniforms, shocks_matrix):
    games, teams = setup["games"], setup["teams"]
    index = {team: i for i, team in enumerate(teams)}
    home = games["homeTeamId"].map(index).to_numpy()
    away = games["awayTeamId"].map(index).to_numpy()
    margin = np.array([deltas.get(int(h), 0.0) - deltas.get(int(a), 0.0)
                       for h, a in zip(games["homeTeamId"], games["awayTeamId"])])
    base = games["home_win_probability"].to_numpy(dtype=float)
    p = np.where(margin != 0, _shift(base, margin, sigma), base)
    if shocks_matrix is not None:
        logits = _logit(p)[None, :] + shocks_matrix[:, home] - shocks_matrix[:, away]
        p = 1.0 / (1.0 + np.exp(-logits))
    home_wins = uniforms < p
    one_hot_home = np.zeros((len(games), len(teams)))
    one_hot_home[np.arange(len(games)), home] = 1
    one_hot_away = np.zeros((len(games), len(teams)))
    one_hot_away[np.arange(len(games)), away] = 1
    wins = home_wins.astype(float) @ one_hot_home + (~home_wins).astype(float) @ one_hot_away
    games_per_team = one_hot_home.sum(axis=0) + one_hot_away.sum(axis=0)
    return wins.astype(int), games_per_team.astype(int)


def _simulate(setup, season, deltas, sigma, n_simulations, random_state):
    """One full season (regular season + playoffs) at the given team margins."""
    rng = np.random.default_rng(random_state)
    n_games, teams = len(setup["games"]), setup["teams"]
    uniforms = rng.random((int(n_simulations), n_games))
    sd = setup["strength_sd"]
    shocks_matrix = rng.normal(0.0, sd, size=(int(n_simulations), len(teams))) if sd > 0 else None
    wins, games_per_team = _regular_season(setup, deltas, sigma, n_simulations, uniforms, shocks_matrix)
    result = {"wins": wins, "games_per_team": games_per_team, "counts": None, "stories": []}
    if setup["postseason"]:
        bracket = _Bracket(setup, season, deltas, sigma, shocks_matrix)
        post_rng = np.random.default_rng(random_state + 1)
        counts, seed_sums, stories = _run_postseason(
            bracket, wins, teams, post_rng, setup["play_in"], n_simulations, shocks_matrix,
            set(range(STORIES)))
        result.update(counts=counts, seed_sums=seed_sums, stories=stories)
    return result


def _team_rows(setup, outcome, n_simulations):
    rows = {}
    for i, team in enumerate(setup["teams"]):
        team_wins = outcome["wins"][:, i]
        row = {
            "mean_wins": round(float(team_wins.mean()), 2),
            "wins_10th_90th": [float(np.percentile(team_wins, 10)), float(np.percentile(team_wins, 90))],
            "games": int(outcome["games_per_team"][i]),
        }
        if outcome["counts"] is not None:
            counts = outcome["counts"][team]
            for stage in STAGES + ["play_in", "top_six"]:
                row[f"p_{stage}"] = round(counts[stage] / n_simulations, 4)
            row["mean_seed"] = round(outcome["seed_sums"][team] / n_simulations, 2)
        rows[team] = row
    return rows


def _name_story(story, names):
    def label(team):
        info = names.get(int(team), {})
        return {"team_id": int(team), "abbreviation": info.get("teamAbbreviation", ""),
                "team_name": info.get("teamName", str(team))}

    out = {"simulation": story["simulation"], "conferences": {}, "finals": None}
    for conf, data in story["conferences"].items():
        out["conferences"][conf] = {
            "standings": [{**s, **label(s["team_id"])} for s in data["standings"]],
            "play_in": [{"for": g["for"], "home": label(g["home"]), "away": label(g["away"]),
                         "winner": int(g["winner"])} for g in data["play_in"]],
            "rounds": [{"round": r["round"], "higher": label(r["higher"]), "lower": label(r["lower"]),
                        "higher_seed": r["higher_seed"], "lower_seed": r["lower_seed"],
                        "winner": int(r["winner"]), "score": r["score"]} for r in data["rounds"]],
        }
    finals = story["finals"]
    out["finals"] = {"higher": label(finals["higher"]), "lower": label(finals["lower"]),
                     "winner": int(finals["winner"]), "score": finals["score"],
                     "champion": label(finals["winner"])}
    return out


def simulate_scenario(season, mode, moves, probabilities=None, inputs=None, n_simulations=1000,
                      random_state=42, transfer="calibrated"):
    """Baseline vs. scenario season and playoffs, with every changed team explained."""
    if transfer not in ("calibrated", "full"):
        raise ScenarioError("transfer must be 'calibrated' or 'full'.")
    n_simulations = int(n_simulations)
    if not 100 <= n_simulations <= 5000:
        raise ScenarioError("n_simulations must be between 100 and 5000.")
    eng = engine()
    host, base, rosters, log, touched = apply_moves(season, mode, moves)
    effects = team_effects(host, base, rosters, touched)
    setup = season_setup(season, mode, probabilities, inputs)
    key = "delta_net_rating" if transfer == "calibrated" else "delta_net_rating_full"
    deltas = {team: effect[key] * effect["pace"] / 100.0 for team, effect in effects.items()}
    sigma = eng["sigma"]
    baseline = _simulate(setup, season, {}, sigma, n_simulations, random_state)
    scenario = _simulate(setup, season, deltas, sigma, n_simulations, random_state)
    base_rows = _team_rows(setup, baseline, n_simulations)
    scenario_rows = _team_rows(setup, scenario, n_simulations)
    names = load_team_names(season if mode == "replay" else host)
    teams = []
    for team in setup["teams"]:
        before, after = base_rows[team], scenario_rows[team]
        delta = {k: round(after[k] - before[k], 4) for k in after if isinstance(after[k], (int, float))}
        info = names.get(team, {})
        teams.append({"team_id": team, "team_name": info.get("teamName", str(team)),
                      "abbreviation": info.get("teamAbbreviation", ""),
                      "conference": conference_of(team), "changed": team in touched,
                      "baseline": before, "scenario": after, "delta": delta})
    teams.sort(key=lambda row: (row["conference"], -row["scenario"]["mean_wins"]))
    for effect in effects.values():
        row = next((t for t in teams if t["team_id"] == effect["team_id"]), None)
        if row:
            effect["simulated_delta_wins"] = row["delta"]["mean_wins"]
            effect["simulated_delta_title"] = row["delta"].get("p_champion")
            effect["simulated_delta_playoffs"] = row["delta"].get("p_made_playoffs")
    verdicts = []
    for move in log:
        if move["type"] != "trade":
            continue
        sides = sorted(({"team_id": t, "team_name": effects[t]["team_name"],
                         "delta_wins": effects[t].get("simulated_delta_wins"),
                         "delta_title": effects[t].get("simulated_delta_title"),
                         "delta_net_rating": effects[t]["delta_net_rating"]}
                        for t in move["teams"] if t in effects),
                       key=lambda side: -(side["delta_wins"] or 0))
        verdicts.append({"move": move["move"], "sides": sides,
                         "note": "Combined effect of every move on each team, not this trade alone."
                         if len(log) > 1 else None})
    calibration = eng["calibration"]
    return {
        "season": int(season), "season_label": _label(season), "mode": mode,
        "host_season": host, "host_season_label": _label(host),
        "n_simulations": n_simulations, "random_state": int(random_state), "transfer": transfer,
        "postseason_simulated": setup["postseason"], "postseason_note": setup["postseason_note"],
        "play_in": setup["play_in"], "strength_sd": setup["strength_sd"],
        "schedule_note": setup["schedule_note"],
        "moves": log,
        "changed_teams": list(effects.values()),
        "trade_verdicts": verdicts,
        "teams": teams,
        "stories": {"baseline": [_name_story(s, names) for s in baseline["stories"]],
                    "scenario": [_name_story(s, names) for s in scenario["stories"]]},
        "method": {
            "realization_factor": round(eng["realization"], 3),
            "realization_factor_80pct": [round(v, 3) for v in eng["realization_interval"]],
            "margin_sigma": round(sigma, 2),
            "wins_per_net_rating_point": round(eng["wins_slope"], 2),
            "team_model_holdout_r2": round(eng["team_model_r2"], 2),
            "roster_change_holdout_mae": [round(calibration["test_mae_with_roster_change"], 2),
                                          round(calibration["test_mae_reversion_only"], 2)],
        },
        "assumptions": [
            "Moves happen before the season starts; a traded or signed player spends the whole season "
            "with the new team and keeps the minutes share (and games played) they had.",
            "Returning players absorb or give up minutes in proportion to their own (at most 42 a game, "
            "and at most 60% more than before); any gap goes to replacement-level players.",
            "A player's per-100 production carries over unchanged (players from other seasons are "
            "era-translated: same number of league standard deviations from the league mean).",
            "No fit, usage, chemistry or coaching effects; defense is captured only through steals, "
            "blocks, rebounds and fouls.",
            "Teams nobody touched keep their real strength; the baseline is the same simulation with no moves.",
        ],
        "limitations": [
            f"Confidence is low: box-score-valued roster changes explained only part of real changes "
            f"(held-out error {calibration['test_mae_with_roster_change']:.2f} vs "
            f"{calibration['test_mae_reversion_only']:.2f} net-rating points without them).",
            "Calibrated deltas use the realization factor; the 'full' setting is an unvalidated upper scenario.",
            "Ties in the standings are broken at random; real NBA tiebreakers and division rules are not modeled.",
            "Playoff seeding uses today's conference alignment.",
        ],
    }
