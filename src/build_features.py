import sqlite3
from collections import deque
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from src.era_swap import PLAYER_TEAM_SQL
except ImportError:  # pragma: no cover - direct-script support
    from era_swap import PLAYER_TEAM_SQL


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "database" / "nba.db"
OUTPUT_PATH = ROOT / "data" / "processed" / "game_features.csv"

STATS = [
    "teamScore",
    "opponentScore",
    "assists",
    "steals",
    "blocks",
    "fieldGoalsPercentage",
    "threePointersPercentage",
    "freeThrowsPercentage",
    "reboundsTotal",
    "turnovers",
    "plusMinusPoints",
]
PERCENTAGE_STATS = [
    "fieldGoalsPercentage",
    "threePointersPercentage",
    "freeThrowsPercentage",
]
PLAYER_FEATURE = "active_players_rolling_10"
LAST_GAME_PLAYER_FEATURE = "active_players_last_game"
WIN_RATE_FEATURE = "win_rate_rolling_10"
PLAYER_HISTORY_FEATURES = {
    "player_minutes_rolling_10": "minutes",
    "player_points_rolling_10": "points",
    "player_points_per_minute_rolling_10": "points_per_minute",
    "player_assists_rolling_10": "assists",
    "player_rebounds_rolling_10": "rebounds",
}


def load_team_games(connection):
    return pd.read_sql_query(
        """
        SELECT team_statistics.gameId, team_statistics.gameDateTimeEst,
               team_statistics.teamId, team_statistics.opponentTeamId,
               team_statistics.home, team_statistics.win,
               team_statistics.teamScore, team_statistics.opponentScore,
               team_statistics.assists, team_statistics.steals,
               team_statistics.blocks, team_statistics.fieldGoalsPercentage,
               team_statistics.threePointersPercentage,
               team_statistics.freeThrowsPercentage,
               team_statistics.reboundsTotal, team_statistics.turnovers,
               team_statistics.plusMinusPoints
        FROM team_statistics
        JOIN games ON games.gameId = team_statistics.gameId
        WHERE COALESCE(team_statistics.gameType, games.gameType) =
              'Regular Season'
        ORDER BY team_statistics.gameDateTimeEst
        """,
        connection,
    )


def load_player_activity(connection):
    # Rows without a playerteamId (most of 2021-22, about half of 2000-01) take
    # their team from the game; see PLAYER_TEAM_SQL.
    return pd.read_sql_query(
        f"""
        SELECT DISTINCT ps.gameId,
               {PLAYER_TEAM_SQL} AS teamId,
               ps.personId
        FROM player_statistics ps
        JOIN games g ON g.gameId = ps.gameId
        WHERE COALESCE(ps.gameType, g.gameType) = 'Regular Season'
          AND CAST(ps.numMinutes AS REAL) > 0
        """,
        connection,
    )


def load_player_history(connection):
    return pd.read_sql_query(
        f"""
        SELECT ps.gameId,
               {PLAYER_TEAM_SQL} AS teamId,
               ps.personId,
               SUM(COALESCE(CAST(ps.numMinutes AS REAL), 0)) AS minutes,
               SUM(COALESCE(ps.points, 0)) AS points,
               CASE
                   WHEN SUM(COALESCE(CAST(ps.numMinutes AS REAL), 0)) > 0
                   THEN SUM(COALESCE(ps.points, 0)) /
                        SUM(COALESCE(CAST(ps.numMinutes AS REAL), 0))
                   ELSE NULL
               END AS points_per_minute,
               SUM(COALESCE(ps.assists, 0)) AS assists,
               SUM(COALESCE(ps.reboundsTotal, 0)) AS rebounds
        FROM player_statistics ps
        JOIN games g ON g.gameId = ps.gameId
        WHERE COALESCE(ps.gameType, g.gameType) = 'Regular Season'
          AND ({PLAYER_TEAM_SQL}) IS NOT NULL
        GROUP BY ps.gameId, teamId, ps.personId
        """,
        connection,
    )


def add_pregame_player_features(team_games, activity, player_history=None):
    activity_by_game = (
        activity.groupby(["teamId", "gameId"])["personId"].agg(set).to_dict()
    )
    values = {}
    last_game_values = {}
    for team_id, games in team_games.groupby("teamId", sort=False):
        previous_players = deque(maxlen=10)
        for row in games.sort_values(["gameDateTimeEst", "gameId"]).itertuples():
            players = set().union(*previous_players) if previous_players else set()
            values[(row.gameId, team_id)] = len(players)
            last_game_values[(row.gameId, team_id)] = (
                len(previous_players[-1]) if previous_players else 0
            )
            previous_players.append(activity_by_game.get((team_id, row.gameId), set()))

    feature = pd.Series(
        [
            values[(game_id, team_id)]
            for game_id, team_id in zip(team_games["gameId"], team_games["teamId"])
        ],
        index=team_games.index,
        name=PLAYER_FEATURE,
    )
    last_game_feature = pd.Series(
        [
            last_game_values[(game_id, team_id)]
            for game_id, team_id in zip(team_games["gameId"], team_games["teamId"])
        ],
        index=team_games.index,
        name=LAST_GAME_PLAYER_FEATURE,
    )
    result = team_games.assign(
        **{
            PLAYER_FEATURE: feature,
            LAST_GAME_PLAYER_FEATURE: last_game_feature,
        }
    )
    if player_history is None:
        return result

    history_by_game = {}
    for row in player_history.itertuples(index=False):
        history_by_game.setdefault((row.teamId, row.gameId), []).append(row)
    history_values = {}
    for team_id, games in team_games.groupby("teamId", sort=False):
        prior_history = {}
        prior_games = deque()
        feature_totals = {feature_name: 0.0 for feature_name in PLAYER_HISTORY_FEATURES}

        def update_player(person_id, values, direction):
            player_values = prior_history.setdefault(
                person_id,
                {
                    feature_name: deque()
                    for feature_name in PLAYER_HISTORY_FEATURES
                },
            )
            for feature_name, value in values.items():
                values_for_player = player_values[feature_name]
                if values_for_player:
                    feature_totals[feature_name] -= np.mean(values_for_player)
                if direction == 1:
                    values_for_player.append(value)
                else:
                    values_for_player.popleft()
                if values_for_player:
                    feature_totals[feature_name] += np.mean(values_for_player)

        for row in games.sort_values(["gameDateTimeEst", "gameId"]).itertuples():
            key = (team_id, row.gameId)
            for feature_name in PLAYER_HISTORY_FEATURES:
                history_values[(row.gameId, team_id, feature_name)] = (
                    feature_totals[feature_name]
                    if any(
                        values[feature_name] for values in prior_history.values()
                    )
                    else np.nan
                )
            current_game = []
            for historical_row in history_by_game.get(key, []):
                values = {}
                for feature_name, source_column in PLAYER_HISTORY_FEATURES.items():
                    if hasattr(historical_row, source_column):
                        values[feature_name] = getattr(historical_row, source_column)
                    elif source_column == "points_per_minute":
                        minutes = getattr(historical_row, "minutes", 0)
                        points = getattr(historical_row, "points", 0)
                        values[feature_name] = (
                            points / minutes if minutes not in (None, 0) else np.nan
                        )
                    else:
                        values[feature_name] = getattr(historical_row, source_column)
                current_game.append((historical_row.personId, values))
                update_player(historical_row.personId, values, 1)
            prior_games.append(current_game)
            if len(prior_games) > 10:
                for person_id, values in prior_games.popleft():
                    update_player(person_id, values, -1)

    for feature_name in PLAYER_HISTORY_FEATURES:
        result[feature_name] = [
            history_values[(game_id, team_id, feature_name)]
            for game_id, team_id in zip(
                result["gameId"], result["teamId"]
            )
        ]
    return result


def add_opponent_adjusted_win_rate(team_games):
    """Compute a candidate pregame opponent-form differential.

    Kept as an explicit evaluation helper rather than added to the active feature
    pipeline, because it is a candidate explanatory signal under review and the
    retained model is intentionally frozen while such features are validated.
    """
    opponent_wins = team_games[["gameId", "teamId", "win_rate_rolling_10"]].rename(
        columns={
            "teamId": "opponentTeamId",
            "win_rate_rolling_10": "opponent_win_rate_rolling_10",
        }
    )
    merged = team_games.merge(
        opponent_wins,
        on=["gameId", "opponentTeamId"],
        how="left",
        validate="many_to_one",
    )
    merged["opponent_adjusted_win_rate_rolling_10"] = (
        merged["win_rate_rolling_10"] - merged["opponent_win_rate_rolling_10"]
    )
    return merged


def add_opponent_adjusted_margin(team_games):
    """Compute a candidate pregame margin differential versus the opponent."""
    opponent_margins = team_games[["gameId", "teamId", "plusMinusPoints_rolling_10"]].rename(
        columns={
            "teamId": "opponentTeamId",
            "plusMinusPoints_rolling_10": "opponent_plusMinusPoints_rolling_10",
        }
    )
    merged = team_games.merge(
        opponent_margins,
        on=["gameId", "opponentTeamId"],
        how="left",
        validate="many_to_one",
    )
    merged["opponent_adjusted_plusMinusPoints_rolling_10"] = (
        merged["plusMinusPoints_rolling_10"] - merged["opponent_plusMinusPoints_rolling_10"]
    )
    return merged


def build_features():
    with sqlite3.connect(DB_PATH) as connection:
        df = load_team_games(connection)
        activity = load_player_activity(connection)
        player_history = load_player_history(connection)

    print(f"Loaded {len(df):,} team-game rows")
    df["gameDateTimeEst"] = pd.to_datetime(df["gameDateTimeEst"])
    df["season"] = df["gameDateTimeEst"].dt.year - (
        df["gameDateTimeEst"].dt.month < 10
    )
    df = df.drop_duplicates(subset=["gameId", "teamId"])
    df = df.sort_values("gameDateTimeEst")
    df["rest_days"] = (
        df.groupby("teamId")["gameDateTimeEst"]
        .diff()
        .dt.total_seconds()
        .div(86400)
    )

    for stat in PERCENTAGE_STATS:
        df.loc[~df[stat].between(0, 1), stat] = np.nan

    for stat in STATS:
        df[f"{stat}_rolling_10"] = (
            df.groupby("teamId")[stat]
            .transform(lambda values: values.shift(1).rolling(10, min_periods=5).mean())
        )
    df[WIN_RATE_FEATURE] = (
        df.groupby("teamId")["win"]
        .transform(lambda values: values.shift(1).rolling(10, min_periods=5).mean())
    )
    df = add_pregame_player_features(df, activity, player_history)
    df = add_opponent_adjusted_win_rate(df)
    df = add_opponent_adjusted_margin(df)
    rolling_columns = [f"{stat}_rolling_10" for stat in STATS]
    df = df.dropna(
        subset=STATS
        + rolling_columns
        + [WIN_RATE_FEATURE]
        + ["opponent_adjusted_win_rate_rolling_10", "opponent_adjusted_plusMinusPoints_rolling_10"]
        + ["rest_days"]
    )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_PATH, index=False)
    print(f"Saved {len(df):,} rows")
    print(f"Saved to: {OUTPUT_PATH}")
    print("\nColumns created:")
    print(df.columns.tolist())


if __name__ == "__main__":
    build_features()
