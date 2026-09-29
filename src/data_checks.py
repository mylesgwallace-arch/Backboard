"""Integrity checks for the local database and the processed feature table.

Every model in this project trusts ``data/database/nba.db`` and
``data/processed/game_features.csv``. These checks catch the problems that
would silently corrupt them -- duplicate games, box scores that disagree with
the game result, orphan rows, impossible values, feature columns that are
mostly empty -- and report each as ``pass``, ``warn`` (known or tolerable) or
``fail``.

    python src/data_checks.py          # print, write models/data_checks.json, exit 1 on fail

Run it after rebuilding the database or the features (``load_data.py``,
``build_features.py``, ``live_data.py`` ingestion).
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd

try:
    from src.provenance import with_provenance
except ImportError:  # pragma: no cover - direct-script support
    from provenance import with_provenance

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "database" / "nba.db"
FEATURES_PATH = ROOT / "data" / "processed" / "game_features.csv"
REPORT_PATH = ROOT / "models" / "data_checks.json"
MODEL_ERA_START = "2003-09-01"   # seasons the production model is trained/evaluated on
NULL_SHARE_WARN = 0.05
NULL_SHARE_FAIL = 0.50
# Documented in PROJECT_CONTEXT.md; fixing it touches the frozen model's
# training data, so it is reported as a warning rather than a failure.
KNOWN_SPARSE_FEATURES = {"player_points_per_minute_rolling_10"}


def _check(name, status, detail, count=None):
    row = {"check": name, "status": status, "detail": detail}
    if count is not None:
        row["count"] = int(count)
    return row


def _count_check(name, count, detail, fail_above=0, warn_above=None, examples=None):
    if count > fail_above:
        row = _check(name, "fail", detail, count)
    elif warn_above is not None and count > warn_above:
        row = _check(name, "warn", detail, count)
    else:
        return _check(name, "pass", detail, count)
    if examples:
        row["examples"] = examples[:5]
    return row


def _rows_check(connection, name, sql, detail, fail_above=0, warn_above=None):
    """Count check whose SQL returns one identifying value (e.g. gameId) per bad row."""
    values = [row[0] for row in connection.execute(sql).fetchall()]
    return _count_check(name, len(values), detail, fail_above, warn_above,
                        examples=[int(v) if isinstance(v, (int, float)) else v for v in values])


def database_checks(db_path=DB_PATH):
    """Checks on ``games``, ``team_statistics`` and ``player_statistics``."""
    results = []
    with sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True) as connection:
        one = lambda sql, *params: connection.execute(sql, params).fetchone()[0]
        results.append(_count_check(
            "games.duplicate_game_id",
            one("SELECT COUNT(*) FROM (SELECT gameId FROM games GROUP BY gameId HAVING COUNT(*) > 1)"),
            "gameId must be unique in games."))
        results.append(_count_check(
            "games.home_equals_away",
            one("SELECT COUNT(*) FROM games WHERE hometeamId = awayteamId"),
            "A game cannot have the same home and away team."))
        results.append(_count_check(
            "games.winner_not_a_participant",
            one("""SELECT COUNT(*) FROM games WHERE winner IS NOT NULL AND winner != ''
                   AND CAST(winner AS INTEGER) NOT IN (hometeamId, awayteamId)"""),
            "A recorded winner must be the home or away team."))
        results.append(_count_check(
            "games.winner_disagrees_with_score",
            one("""SELECT COUNT(*) FROM games WHERE homeScore IS NOT NULL AND awayScore IS NOT NULL
                   AND homeScore != awayScore AND winner IS NOT NULL AND winner != ''
                   AND CAST(winner AS INTEGER) != CASE WHEN homeScore > awayScore
                       THEN hometeamId ELSE awayteamId END"""),
            "The winner must be the team with more points."))
        results.append(_rows_check(
            connection, "games.tied_final_score",
            """SELECT gameId FROM games WHERE homeScore IS NOT NULL
               AND homeScore = awayScore AND homeScore > 0""",
            "NBA games cannot end tied; a tie is a source-data score error (the margin model "
            "reads these as 0).", fail_above=5, warn_above=0))
        results.append(_count_check(
            "games.regular_season_missing_team_rows",
            one(f"""SELECT COUNT(*) FROM games g WHERE g.gameType = 'Regular Season'
                    AND g.gameDateTimeEst >= '{MODEL_ERA_START}' AND g.winner IS NOT NULL
                    AND g.winner != ''
                    AND (SELECT COUNT(*) FROM team_statistics t WHERE t.gameId = g.gameId) != 2"""),
            f"Every played regular-season game since {MODEL_ERA_START[:4]} needs exactly two "
            "team_statistics rows.", fail_above=25, warn_above=0))
        results.append(_rows_check(
            connection, "team_statistics.team_not_in_game",
            f"""SELECT t.gameId FROM team_statistics t JOIN games g ON g.gameId = t.gameId
                WHERE t.gameDateTimeEst >= '{MODEL_ERA_START}'
                AND t.teamId NOT IN (g.hometeamId, g.awayteamId)""",
            "Rows whose teamId is neither team in the game (e.g. teamId 0 placeholders); "
            "they also create a bogus team in derived tables.", fail_above=20, warn_above=0))
        results.append(_rows_check(
            connection, "team_statistics.score_disagrees_with_game",
            f"""SELECT t.gameId FROM team_statistics t JOIN games g ON g.gameId = t.gameId
                WHERE t.gameDateTimeEst >= '{MODEL_ERA_START}' AND g.homeScore IS NOT NULL
                AND t.teamId IN (g.hometeamId, g.awayteamId)
                AND t.teamScore != CASE WHEN t.teamId = g.hometeamId THEN g.homeScore
                                        ELSE g.awayScore END""",
            "A team's box-score points must equal its score in games."))
        results.append(_count_check(
            "team_statistics.duplicate_team_game",
            one("""SELECT COUNT(*) FROM (SELECT gameId, teamId FROM team_statistics
                   GROUP BY gameId, teamId HAVING COUNT(*) > 1)"""),
            "One row per team per game."))
        results.append(_rows_check(
            connection, "player_statistics.negative_minutes",
            """SELECT DISTINCT p.gameId FROM player_statistics p JOIN games g ON g.gameId = p.gameId
               WHERE CAST(p.numMinutes AS REAL) < 0 AND g.gameType IN
               ('Regular Season', 'Playoffs', 'Play-in Tournament')""",
            "Minutes cannot be negative (games the models use)."))
        results.append(_rows_check(
            connection, "player_statistics.negative_minutes_other_games",
            """SELECT DISTINCT p.gameId FROM player_statistics p LEFT JOIN games g ON g.gameId = p.gameId
               WHERE CAST(p.numMinutes AS REAL) < 0 AND COALESCE(g.gameType, p.gameType) NOT IN
               ('Regular Season', 'Playoffs', 'Play-in Tournament')""",
            "Negative minutes in preseason/exhibition games (no model reads these).",
            fail_above=10**9, warn_above=0))
        results.append(_count_check(
            "player_statistics.team_not_in_game",
            one(f"""SELECT COUNT(*) FROM player_statistics p JOIN games g ON g.gameId = p.gameId
                    WHERE p.gameDateTimeEst >= '{MODEL_ERA_START}' AND p.playerteamId IS NOT NULL
                    AND p.playerteamId NOT IN (g.hometeamId, g.awayteamId)"""),
            "A player's team must be one of the two teams in the game."))
        latest = one("SELECT MAX(gameDateTimeEst) FROM games WHERE winner IS NOT NULL AND winner != ''")
        results.append(_check("games.latest_result", "pass", f"Latest game with a result: {latest}."))
    return results


def feature_checks(features_path=FEATURES_PATH, predictors=None):
    """Checks on the processed feature table the model reads."""
    features = pd.read_csv(features_path, low_memory=False)
    results = []
    results.append(_count_check(
        "features.duplicate_team_game",
        int(features.duplicated(["gameId", "teamId"]).sum()),
        "One feature row per team per game."))
    pairs = features.groupby("gameId")["teamId"].nunique()
    results.append(_count_check(
        "features.game_without_two_teams", int((pairs != 2).sum()),
        "Each game should have a row for both teams (unpaired games are dropped from the "
        "model dataset).", fail_above=len(pairs) * 0.01, warn_above=0))
    results.append(_count_check(
        "features.negative_rest_days", int((features.get("rest_days", pd.Series(dtype=float)) < 0).sum()),
        "rest_days cannot be negative (would mean the rows are out of order)."))
    recent = features[pd.to_datetime(features["gameDateTimeEst"]) >= MODEL_ERA_START]
    columns = predictors or [column for column in features.columns if column.endswith("_rolling_10")
                             or column in ("rest_days", "active_players_last_game")]
    for column in columns:
        if column not in recent.columns:
            results.append(_check(f"features.null_share.{column}", "fail", "Column is missing."))
            continue
        share = float(recent[column].isna().mean())
        detail = f"{share:.1%} of rows since {MODEL_ERA_START[:4]} are empty."
        if share > NULL_SHARE_FAIL and column not in KNOWN_SPARSE_FEATURES:
            status = "fail"
        elif share > NULL_SHARE_WARN:
            status = "warn"
            if column in KNOWN_SPARSE_FEATURES:
                detail += " Known issue (NaN propagation in build_features.py; documented)."
        else:
            status = "pass"
        results.append(_check(f"features.null_share.{column}", status, detail))
    return results


def run_checks(db_path=DB_PATH, features_path=FEATURES_PATH):
    results = database_checks(db_path) + feature_checks(features_path)
    counts = {status: sum(row["status"] == status for row in results)
              for status in ("pass", "warn", "fail")}
    return {"summary": counts, "ok": counts["fail"] == 0, "checks": results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-write", action="store_true", help="Print only.")
    args = parser.parse_args(argv)
    report = run_checks()
    for row in report["checks"]:
        if row["status"] != "pass":
            print(f"{row['status'].upper():4s}  {row['check']}: {row['detail']}"
                  + (f" ({row['count']})" if "count" in row else ""))
    print(f"{report['summary']['pass']} pass, {report['summary']['warn']} warn, "
          f"{report['summary']['fail']} fail")
    if not args.no_write:
        REPORT_PATH.write_text(json.dumps(with_provenance(report), indent=2) + "\n", encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
