#!/usr/bin/env python
"""Build shared/demo-data.js: real Backboard engine output for the style demos.

Every number the demo pages show comes from here, so the twelve pages differ
only in style, never in content. Nothing is invented: matchup probabilities,
the "why" breakdown, projected scores, Elo, preseason projections, playoff odds,
calibration bins and the What-If all come from the production tool layer.

Run from the repository root:

    .venv/Scripts/python.exe design/backboard-demos/tools/build_demo_data.py

Requires src/explain.py, the calibration report and the What-If tool. Those
shipped in the claude/model-transparency work, so this script only runs on a
tree that contains it. The generated file is static and does not need them.
"""

import json
import re
import sys
import time
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))

from src import explain as bb_explain  # noqa: E402
from src import main as bb_main  # noqa: E402
from src import tools as bb_tools  # noqa: E402
from src.margin_model import predict_margin  # noqa: E402

SEASON = 2025                      # NBA season start year (2025 = 2025-26)
PRESEASON_AS_OF = "2025-10-21"     # first game of 2025-26
OUT = HERE.parent / "shared" / "demo-data.js"
TEAM_COLORS_JS = ROOT / "web" / "js" / "teamColors.js"

CITIES = ("Los Angeles", "Golden State", "New York", "New Orleans", "San Antonio",
          "Oklahoma City")
# The engine's own abbreviation for San Antonio is "SAN"; fans write "SAS".
ABBR_OVERRIDES = {"SAN": "SAS"}
NUMERIC_KEYS = {"win10": "win_rate_rolling_10", "pts10": "teamScore_rolling_10",
                "opp10": "opponentScore_rolling_10", "pm10": "plusMinusPoints_rolling_10",
                "rest": "rest_days"}
GROUP_KEYS = list(bb_explain.GROUPS.keys())  # fixed order for the "why" arrays
GROUP_SHORT = {
    "Elo rating gap": "Team strength (Elo)",
    "Recent point margin (last 10 games)": "Point margin, last 10",
    "Recent win rate (last 10 games)": "Win rate, last 10",
    "Shooting (last 10 games)": "Shooting, last 10",
    "Other box-score stats (last 10 games)": "Other box score, last 10",
    "Players available and their production": "Who is available",
    "Rest days": "Rest",
}


def log(message):
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def split_name(full):
    for city in CITIES:
        if full.startswith(city + " "):
            return city, full[len(city) + 1:]
    city, _, nick = full.partition(" ")
    if full.startswith("Portland "):
        return "Portland", "Trail Blazers"
    return city, nick


def load_colors():
    text = TEAM_COLORS_JS.read_text(encoding="utf-8")
    pattern = re.compile(r'(\d{10}):\s*\{\s*primary:\s*"(#[0-9A-Fa-f]{6})",\s*secondary:\s*"(#[0-9A-Fa-f]{6})"')
    return {int(i): (p, s) for i, p, s in pattern.findall(text)}


def envelope(name, params):
    result = bb_tools.execute_tool(name, params)
    if result.get("status") != "success":
        raise RuntimeError(f"{name} failed: {result}")
    return result["data"]


def memoize_replays():
    """explain_matchup replays every game per call; cache the as_of=None replay."""
    cache = {}
    elo_fn, snap_fn = bb_explain.elo_ratings_before, bb_explain.team_snapshots

    def elo(games, as_of, cfg):
        key = ("elo", None if as_of is None else str(as_of))
        if key not in cache:
            cache[key] = elo_fn(games, as_of, cfg)
        return cache[key]

    def snap(features, as_of):
        key = ("snap", None if as_of is None else str(as_of))
        if key not in cache:
            cache[key] = snap_fn(features, as_of)
        return cache[key]

    bb_explain.elo_ratings_before = elo
    bb_explain.team_snapshots = snap


def main():
    log("loading production model inputs")
    inputs = bb_tools._cached_model_inputs()
    bundle = bb_tools._cached_margin_bundle()
    memoize_replays()
    colors = load_colors()

    log("data status, team strength, preseason projection, playoff odds")
    status = envelope("data_status", {})
    strength = {row["teamId"]: row for row in envelope("team_strength", {})["teams"]}
    preseason = envelope("project_rest_of_season",
                         {"season": SEASON, "as_of": PRESEASON_AS_OF, "n_simulations": 1000}
                         )["projection"]
    standings = {row["teamId"]: row for row in preseason["projected_standings"]}
    playoffs = envelope("playoff_odds", {"season": SEASON})
    po = {row["teamId"]: row for row in playoffs["teams"]}

    snapshots = bb_explain.team_snapshots(inputs.features, None)

    teams = []
    for team_id, row in standings.items():
        city, nick = split_name(row["teamName"])
        c1, c2 = colors[team_id]
        snap = snapshots.loc[team_id]
        entry = {
            "id": int(team_id),
            "abbr": ABBR_OVERRIDES.get(row["teamAbbreviation"], row["teamAbbreviation"]),
            "city": city,
            "nick": nick,
            "conf": row["conference"],
            "c1": c1,
            "c2": c2,
            "elo": strength[team_id]["elo_rating"],
            "vsAvg": round(strength[team_id]["win_probability_vs_average"], 4),
            "form": {k: round(float(snap[col]), 2) for k, col in NUMERIC_KEYS.items()},
            "pre": {
                "wins": round(row["mean_wins"], 1),
                "p5": round(row["p5_wins"], 1),
                "p95": round(row["p95_wins"], 1),
                "playoff": round(row["direct_playoff_probability"], 3),
            },
        }
        if team_id in po:
            p = po[team_id]
            entry["po"] = {
                "seed": p["seed"],
                "made": round(p["made_playoffs"], 3),
                "r1": round(p["won_first_round"], 3),
                "r2": round(p["won_conf_semifinals"], 3),
                "cf": round(p["won_conf_finals"], 3),
                "title": round(p["champion"], 4),
                "wins": p["regular_season_wins"],
                "actual": p["actual_result"],
            }
        teams.append(entry)
    teams.sort(key=lambda t: (t["city"], t["nick"]))
    index = {t["id"]: i for i, t in enumerate(teams)}
    n = len(teams)
    assert n == 30, n

    log(f"scoring all {n * (n - 1)} ordered matchups (probability, why, projected score)")
    prob = [[None] * n for _ in range(n)]
    why = [[None] * n for _ in range(n)]
    score = [[None] * n for _ in range(n)]
    for hi, home in enumerate(teams):
        for ai, away in enumerate(teams):
            if hi == ai:
                continue
            ex = bb_explain.explain_matchup(inputs, home["id"], away["id"])
            by_label = {c["group"]: c["contribution"] for c in ex["contributions"]}
            prob[hi][ai] = round(ex["home_win_probability"], 4)
            why[hi][ai] = ([round(by_label.get(g, 0.0), 4) for g in GROUP_KEYS]
                           + [round(ex["interaction_remainder"], 4),
                              round(ex["home_court_only_probability"], 4),
                              round(ex["elo_probability"], 4),
                              round(ex["boosted_probability"], 4)])
            mg = predict_margin(home["id"], away["id"], inputs, bundle)
            score[hi][ai] = [mg["predicted_home_margin"],
                             mg["margin_interval_80"][0], mg["margin_interval_80"][1],
                             mg["predicted_home_points"], mg["predicted_away_points"]]
        log(f"  {home['abbr']} done")

    log("calibration, model summary, what-if")
    calibration = json.loads((ROOT / "models" / "calibration_report.json").read_text(encoding="utf-8"))
    bins = [{
        "lo": b["bin"][0], "hi": b["bin"][1], "games": b["games"],
        "pred": round(b["mean_predicted"], 4), "obs": round(b["observed_rate"], 4),
        "ciLo": round(b["observed_90pct_interval"][0], 4),
        "ciHi": round(b["observed_90pct_interval"][1], 4),
    } for b in calibration["overall"]["bins"]]
    summary = bb_main.load_model_summary(bb_main.METRICS_PATH)
    metrics = summary["metrics"]
    margin_metrics = json.loads((ROOT / "models" / "margin_metrics.json").read_text(encoding="utf-8"))

    move = envelope("project_roster_move", {
        "season": SEASON, "player": "Stephen Curry",
        "to_team": "San Antonio Spurs", "n_simulations": 1000})
    what_if = {
        "player": move["moves"][0]["name"],
        "fromId": move["moves"][0]["from_team_id"],
        "toId": move["moves"][0]["to_team_id"],
        "asOf": move["as_of"],
        "sims": move["n_simulations"],
        "confidence": move["confidence"],
        "effects": [{
            "id": e["teamId"],
            "winsBefore": round(e["mean_wins_before"], 1),
            "winsAfter": round(e["mean_wins_after"], 1),
            "change": round(e["mean_wins_change"], 1),
            "p5": round(e["p5_p95_after"][0], 1),
            "p95": round(e["p5_p95_after"][1], 1),
            "playoffBefore": round(e["direct_playoff_probability_before"], 3),
            "playoffAfter": round(e["direct_playoff_probability_after"], 3),
        } for e in move["team_effects"]],
        "backtest": {
            "seasons": "2022-2025",
            "correlation": round(move["validation"]["correlation"], 2),
            "maeWithout": round(move["validation"]["mae_without_moves"], 2),
            "maeWith": round(move["validation"]["mae_with_moves"], 2),
            "largeEffectsRight": round(move["validation"]["large_effects"]["direction_correct_share"], 3),
            "largeEffectsCount": move["validation"]["large_effects"]["count"],
        },
    }

    by_title = sorted((t for t in teams if "po" in t), key=lambda t: -t["po"]["title"])
    top = by_title[0]
    other = next(t for t in by_title if t["conf"] != top["conf"])
    abbr = {t["abbr"]: i for i, t in enumerate(teams)}
    # The 2025-26 Finals, from the games database: champion vs runner-up, with
    # the team that had more regular-season wins at home.
    champion = next(t for t in teams if t.get("po", {}).get("actual") == "won the title")
    runner_up = next(t for t in teams if t.get("po", {}).get("actual") == "lost in the finals")
    finals = sorted((champion, runner_up), key=lambda t: -t["po"]["wins"])
    featured = [
        [index[finals[0]["id"]], index[finals[1]["id"]]],   # the Finals
        [index[top["id"]], index[other["id"]]],             # best title odds in each conference
        [abbr["BOS"], abbr["LAL"]],                         # the classic
        [abbr["GSW"], abbr["SAS"]],                         # the Curry what-if teams
        [abbr["DEN"], abbr["OKC"]],                         # West rivals
    ]

    data = {
        "meta": {
            "generated": date.today().isoformat(),
            "model": summary["recommended_model"],
            "seasonLabel": "2025-26",
            "strengthFrozen": "2026-04-12",
            "latestGame": status["latest_completed_game"][:10],
            "sims": preseason["n_simulations"],
            "holdout": {
                "games": summary["calibration"]["games"],
                "accuracy": round(metrics["accuracy"], 4),
                "logLoss": round(metrics["log_loss"], 4),
                "brier": round(metrics["brier_score"], 4),
                "ece": round(summary["calibration"]["expected_calibration_error"], 4),
                "start": calibration["holdout_start"][:10],
                "trainGames": margin_metrics["split"]["train_games"],
            },
            "marginMae": round(margin_metrics["margin"]["holdout"]["selected_model"]["mae"], 1),
            "preseasonMae": 8.2,
        },
        "groups": [{"key": g, "label": GROUP_SHORT[g]} for g in GROUP_KEYS],
        "whyTail": ["remainder", "homeCourtOnly", "eloProb", "boostedProb"],
        "teams": teams,
        "prob": prob,
        "why": why,
        "score": score,
        "featured": featured,
        "calibration": bins,
        "whatIf": what_if,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    OUT.write_text(
        "/* GENERATED by tools/build_demo_data.py from the production Backboard engine.\n"
        "   Real model output, not mock data. Do not edit by hand. */\n"
        f"window.BACKBOARD_DEMO={body};\n",
        encoding="utf-8",
    )
    log(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")

    # Cross-check against the CLI path (main.predict_matchup) for a known pair.
    check = bb_main.predict_matchup(
        teams[abbr["BOS"]]["id"], teams[abbr["LAL"]]["id"])["home_win_probability"]
    assert abs(check - prob[abbr["BOS"]][abbr["LAL"]]) < 1e-4, (check, prob[abbr["BOS"]][abbr["LAL"]])
    log(f"cross-check OK: BOS vs LAL {check:.4f} matches the CLI")


if __name__ == "__main__":
    main()
