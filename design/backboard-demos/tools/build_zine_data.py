#!/usr/bin/env python
"""Build shared/zine-data.js: the extra real engine output the street-zine site needs.

shared/demo-data.js already holds matchups, team form, playoff odds, calibration
and one what-if. This file adds what the other feature pages show: every
franchise's 2025-26 record and all-time head-to-head record, a player-impact diagnostic for each
player who appeared in 2025-26, the offseason roster moves and roster-adjusted
strength for every team, two extra era-swap what-ifs, a few assistant answers and
the platform status. All of it comes from the production tool layer
(src/tools.py and src/assistant.py), nothing is invented.

Run from the repository root:

    .venv/Scripts/python.exe design/backboard-demos/tools/build_zine_data.py

It takes about ten minutes, almost all of it the two era-swap simulations. The
generated file is static and the site does not need the engine to read it.
"""

import json
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))

from src import assistant as bb_assistant  # noqa: E402
from src import tools as bb_tools  # noqa: E402

DB_PATH = ROOT / "data" / "database" / "nba.db"
OUT = HERE.parent / "shared" / "zine-data.js"
SEASON_START = "2025-09-01"

# (host team id, host season, player out id, player in name, player in season)
ERA_SWAPS = [
    (1610612741, 1995, 893, "Stephen Curry", 2015),   # 1995-96 Bulls, Curry for Jordan
    (1610612744, 2015, 201939, "Michael Jordan", 1995),  # 2015-16 Warriors, Jordan for Curry
]
# Matchup questions are left out on purpose: the assistant answers them as of today,
# off-season, which would not match the Finals-date numbers on the matchup page.
QUESTIONS = [
    "What is the Spurs record in 2025?",
    "What is the Knicks record in 2025?",
    "How many wins are the Thunder projected to get?",
    "What is Stephen Curry's player impact?",
    "What is Victor Wembanyama's player impact?",
]


def log(message):
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def data_of(name, params):
    result = bb_tools.execute_tool(name, params)
    if result.get("status") != "success":
        raise RuntimeError(f"{name} {params}: {result.get('error') or result}")
    return result["data"]


def team_ids(connection):
    rows = connection.execute(
        "SELECT DISTINCT teamId FROM team_statistics WHERE gameDateTimeEst >= ? "
        "AND teamId BETWEEN 1610612737 AND 1610612766 ORDER BY teamId",
        (SEASON_START,),
    ).fetchall()
    return [row[0] for row in rows]


def build_records(ids):
    """2025-26 regular-season W-L for every team (the demo data only has playoff teams)."""
    out = {}
    for team_id in ids:
        row = data_of("team_record", {"team_id": team_id, "season": 2025})
        out[team_id] = {"w": row["wins"], "l": row["losses"]}
    return out


def build_h2h(ids):
    games, wins = {}, {}
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            row = data_of("head_to_head", {"team_a_id": a, "team_b_id": b})
            games[f"{a}-{b}"] = row["games"]
            wins[f"{a}-{b}"] = row["team_a_wins"]
    return {"games": games, "winsA": wins}


def build_players(connection):
    rows = connection.execute(
        """
        SELECT ps.personId, MAX(ps.firstName || ' ' || ps.lastName)
        FROM player_statistics ps JOIN games g ON g.gameId = ps.gameId
        WHERE g.gameDateTimeEst >= ?
          AND COALESCE(ps.gameType, g.gameType) = 'Regular Season'
          AND CAST(ps.numMinutes AS REAL) > 0
        GROUP BY ps.personId
        """,
        (SEASON_START,),
    ).fetchall()
    out = []
    for person_id, name in rows:
        try:
            result = bb_tools.execute_tool("player_impact", {"person_id": person_id})
        except Exception:  # noqa: BLE001 - one bad player must not stop the build
            continue
        if result.get("status") != "success":
            continue
        d = result["data"]
        diag = d["diagnostic"]
        team = diag.get("recent_team_id")
        out.append({
            "id": int(person_id), "name": name, "team": int(team) if team == team and team is not None else None,
            "games": diag["prior_games"],
            "net": round(diag["player_net_rating"], 2),
            "min": round(diag["expected_minutes"], 1),
            "chg": round(diag["estimated_net_rating_change"], 2),
            "conf": d["confidence"],
        })
    out.sort(key=lambda p: p["name"])
    return out


def person_names(connection, ids):
    marks = ",".join("?" for _ in ids)
    rows = connection.execute(
        f"SELECT personId, firstName || ' ' || lastName FROM players WHERE personId IN ({marks})",
        list(ids),
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def build_moves(connection, ids):
    moves = {}
    for team_id in ids:
        d = data_of("team_roster", {"team_id": team_id})
        roster = d["roster"]
        wanted = {p["person_id"] for p in roster["arrived"]}
        names = person_names(connection, wanted) if wanted else {}

        def row(p):
            return {"name": names.get(p["person_id"]) or p["name"],
                    "min": round(p["player_minutes_rolling_10"], 1),
                    "pts": round(p["player_points_rolling_10"], 1),
                    "nba": not p["no_nba_history"]}

        moves[team_id] = {
            "arrived": [row(p) for p in roster["arrived"]],
            "departed": [row(p) for p in roster["departed"]],
        }
    return moves


def build_strength():
    d = data_of("team_strength", {"roster_adjusted": True})
    teams = {}
    for t in d["teams"]:
        teams[t["teamId"]] = {
            "elo": t["elo_rating"],
            "vsAvg": t["win_probability_vs_average"],
            "vsAvgAdj": t.get("roster_adjusted_win_probability_vs_average"),
            "changes": t.get("roster_changes"),
        }
    return {"asOf": d["as_of"], "snapshot": d["teams"][0]["snapshot_date"], "teams": teams}


def build_era_swaps():
    swaps = []
    for team_id, season, out_id, in_name, in_season in ERA_SWAPS:
        log(f"era swap: team {team_id} {season}, {in_name} {in_season} for {out_id} (about 70 s)")
        swaps.append(data_of("simulate_era_swap", {
            "team_id": team_id, "season": season, "out_person_id": out_id,
            "in_player": in_name, "in_season": in_season,
        }))
    return swaps


def build_questions():
    answers = []
    for question in QUESTIONS:
        result = bb_assistant.answer_question(question)
        if result["status"] != "success":
            log(f"skipped (not answerable): {question}")
            continue
        envelope = result["envelope"]
        answers.append({
            "q": question, "tool": result["tool"], "answer": result["answer"],
            "assumptions": envelope.get("assumptions", []),
            "limitations": envelope.get("limitations", []),
        })
    return answers


def build_tools():
    return [{"name": t["name"], "description": t["description"]} for t in bb_tools.list_tools()]


def main():
    connection = sqlite3.connect(DB_PATH)
    ids = team_ids(connection)
    log(f"{len(ids)} teams")
    data = {"generated": time.strftime("%Y-%m-%d"), "teamIds": ids}
    log("2025-26 records")
    data["records"] = build_records(ids)
    log("head-to-head, all pairs")
    data["h2h"] = build_h2h(ids)
    log("player impact")
    data["players"] = build_players(connection)
    log(f"{len(data['players'])} players")
    log("roster moves")
    data["moves"] = build_moves(connection, ids)
    log("roster-adjusted strength")
    data["strength"] = build_strength()
    log("assistant answers")
    data["qa"] = build_questions()
    log("platform status")
    data["status"] = data_of("data_status", {})
    data["tools"] = build_tools()
    data["eraSwaps"] = build_era_swaps()
    body = json.dumps(data, separators=(",", ":"), default=str)
    OUT.write_text(
        "/* GENERATED by tools/build_zine_data.py from the production Backboard engine.\n"
        "   Real model output, not mock data. Do not edit by hand. */\n"
        f"window.BACKBOARD_ZINE={body};\n",
        encoding="utf-8",
    )
    log(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
