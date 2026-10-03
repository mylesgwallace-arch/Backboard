"""Complete player-team-season tables for the player pages and the Sandbox.

``era_swap.build_player_seasons`` keeps only box-score rows that carry a
``playerteamId``. About 109,000 regular-season rows in ``nba.db`` have no
team id (almost all of 2021-22, much of 1999-00 and 2000-01, and a few
thousand rows in most other seasons), so those player-seasons are missing or
short there. The validated What-if model was fit on that table and is left
unchanged; this module builds the same table with the team recovered from the
game itself (the player's team name matched against the home and away teams,
then the row's home flag), and caches it separately.

Columns, rates and the cleaning step are exactly ``era_swap``'s
(``add_rates``, ``_clean``), so the validated box-score coefficients apply.
"""

import sqlite3
import threading
from functools import lru_cache
from pathlib import Path

import pandas as pd

try:
    from src import era_swap
    from src.main import TEAM_DB_PATH
except ImportError:  # pragma: no cover - direct-script support
    import era_swap
    from main import TEAM_DB_PATH


ROOT = Path(__file__).resolve().parents[1]
FULL_PLAYER_SEASONS_PATH = ROOT / "data" / "processed" / "player_team_seasons_full.csv"

RECOVERED_TEAM_ID = """
    CASE WHEN ps.playerteamId IS NOT NULL THEN ps.playerteamId
         WHEN ps.playerteamName = g.hometeamName THEN g.hometeamId
         WHEN ps.playerteamName = g.awayteamName THEN g.awayteamId
         WHEN ps.home = 1 THEN g.hometeamId
         ELSE g.awayteamId END
"""


def build_player_seasons_full(db_path=TEAM_DB_PATH, team_seasons=None):
    """``era_swap.build_player_seasons`` with team ids recovered from ``games``."""
    season = era_swap.SEASON_SQL.format(col="ps.gameDateTimeEst")
    with sqlite3.connect(db_path) as connection:
        frame = pd.read_sql_query(
            f"""
            SELECT ps.personId AS personId, {RECOVERED_TEAM_ID} AS teamId, {season} AS season,
                   MAX(ps.firstName) AS firstName, MAX(ps.lastName) AS lastName,
                   COUNT(*) AS games, SUM(CAST(ps.numMinutes AS REAL)) AS minutes,
                   SUM(COALESCE(ps.points, 0)) AS pts,
                   SUM(COALESCE(ps.fieldGoalsMade, 0)) AS fgm,
                   SUM(COALESCE(ps.fieldGoalsAttempted, 0)) AS fga,
                   SUM(COALESCE(ps.threePointersMade, 0)) AS tpm,
                   SUM(COALESCE(ps.threePointersAttempted, 0)) AS tpa,
                   SUM(COALESCE(ps.freeThrowsMade, 0)) AS ftm,
                   SUM(COALESCE(ps.freeThrowsAttempted, 0)) AS fta,
                   SUM(COALESCE(ps.reboundsOffensive, 0)) AS orb,
                   SUM(COALESCE(ps.reboundsDefensive, 0)) AS drb,
                   SUM(COALESCE(ps.assists, 0)) AS ast,
                   SUM(COALESCE(ps.steals, 0)) AS stl,
                   SUM(COALESCE(ps.blocks, 0)) AS blk,
                   SUM(COALESCE(ps.turnovers, 0)) AS tov,
                   SUM(COALESCE(ps.foulsPersonal, 0)) AS pf
            FROM player_statistics ps
            LEFT JOIN games g ON g.gameId = ps.gameId
            WHERE COALESCE(ps.gameType, g.gameType) = 'Regular Season'
              AND CAST(ps.numMinutes AS REAL) > 0
              AND ps.gameDateTimeEst >= '{era_swap.FIRST_SEASON}-09-01'
            GROUP BY ps.personId, teamId, season
            """,
            connection,
        )
    frame = frame.dropna(subset=["teamId"])
    frame["teamId"] = frame["teamId"].astype(int)
    if team_seasons is None:
        team_seasons = era_swap.build_team_seasons(db_path)
    frame = frame.merge(
        team_seasons[["teamId", "season", "possessions", "team_minutes", "pace"]],
        on=["teamId", "season"], how="inner",
    )
    return era_swap.add_rates(frame)


_TABLES_LOCK = threading.Lock()


def load_tables(rebuild=False):
    """Cached, single-flight access to ``_load_tables`` (see there)."""
    with _TABLES_LOCK:
        return _load_tables(rebuild)


@lru_cache(maxsize=1)
def _load_tables(rebuild=False):
    """``(team_seasons, player_seasons)``: cleaned, complete, cached on disk.

    The team-season table is ``era_swap``'s (team box scores always carry a
    team id). The first call builds the player table (about 15 seconds) and
    writes it to ``data/processed``; later calls read the CSV.
    """
    team_seasons_path = era_swap.TEAM_SEASONS_PATH
    if team_seasons_path.exists() and not rebuild:
        team_seasons = pd.read_csv(team_seasons_path)
    else:
        team_seasons = era_swap.build_team_seasons()
        team_seasons_path.parent.mkdir(parents=True, exist_ok=True)
        team_seasons.to_csv(team_seasons_path, index=False)
    if FULL_PLAYER_SEASONS_PATH.exists() and not rebuild:
        player_seasons = pd.read_csv(FULL_PLAYER_SEASONS_PATH)
    else:
        player_seasons = build_player_seasons_full(team_seasons=team_seasons)
        FULL_PLAYER_SEASONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        player_seasons.to_csv(FULL_PLAYER_SEASONS_PATH, index=False)
    return era_swap._clean(team_seasons, player_seasons)
