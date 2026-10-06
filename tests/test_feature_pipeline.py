import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from src.build_features import (
    add_opponent_adjusted_margin,
    add_opponent_adjusted_win_rate,
    add_pregame_player_features,
    load_player_activity,
    load_player_history,
)


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "database" / "nba.db"
FEATURES_PATH = ROOT / "data" / "processed" / "game_features.csv"
PLAYER_FEATURE = "active_players_rolling_10"
LAST_GAME_PLAYER_FEATURE = "active_players_last_game"
WIN_RATE_FEATURE = "win_rate_rolling_10"

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


def load_expected_features():
    with sqlite3.connect(DB_PATH) as connection:
        source = pd.read_sql_query(
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

    source["gameDateTimeEst"] = pd.to_datetime(source["gameDateTimeEst"])
    source["season"] = source["gameDateTimeEst"].dt.year - (
        source["gameDateTimeEst"].dt.month < 10
    )
    source = source.drop_duplicates(["gameId", "teamId"])
    source = source.sort_values("gameDateTimeEst")
    source["rest_days"] = (
        source.groupby("teamId")["gameDateTimeEst"]
        .diff()
        .dt.total_seconds()
        .div(86400)
    )

    for stat in PERCENTAGE_STATS:
        source.loc[~source[stat].between(0, 1), stat] = np.nan

    for stat in STATS:
        source[f"{stat}_rolling_10"] = (
            source.groupby("teamId")[stat]
            .transform(lambda values: values.shift(1).rolling(10, min_periods=5).mean())
        )

    rolling_columns = [f"{stat}_rolling_10" for stat in STATS]
    source[WIN_RATE_FEATURE] = (
        source.groupby("teamId")["win"]
        .transform(lambda values: values.shift(1).rolling(10, min_periods=5).mean())
    )
    source = add_opponent_adjusted_win_rate(source)
    source = add_opponent_adjusted_margin(source)
    return source.dropna(
        subset=STATS
        + rolling_columns
        + [WIN_RATE_FEATURE]
        + ["opponent_adjusted_win_rate_rolling_10", "opponent_adjusted_plusMinusPoints_rolling_10"]
    )


def test_generated_features_match_source_and_rolling_history():
    expected = load_expected_features()
    actual = pd.read_csv(FEATURES_PATH)

    assert len(expected) == 133_348
    assert len(actual) == len(expected)
    assert not actual.duplicated(["gameId", "teamId"]).any()
    assert not actual[STATS].isna().any().any()
    assert not actual[[f"{stat}_rolling_10" for stat in STATS]].isna().any().any()
    assert not actual["rest_days"].isna().any()
    for stat in PERCENTAGE_STATS:
        assert actual[stat].between(0, 1).all()
    np.testing.assert_array_equal(actual["season"].to_numpy(), expected["season"].to_numpy())
    assert (actual["season"] == 2021).sum() == 2_445

    expected = expected.set_index(["gameId", "teamId"])
    actual = actual.set_index(["gameId", "teamId"])
    assert actual.index.equals(expected.index)

    for stat in STATS:
        column = f"{stat}_rolling_10"
        np.testing.assert_allclose(
            actual[column].to_numpy(),
            expected[column].to_numpy(),
            rtol=1e-12,
            atol=1e-12,
        )
    np.testing.assert_allclose(
        actual["rest_days"].to_numpy(),
        expected["rest_days"].to_numpy(),
        rtol=1e-12,
        atol=1e-12,
    )
    np.testing.assert_allclose(
        actual[WIN_RATE_FEATURE].to_numpy(),
        expected[WIN_RATE_FEATURE].to_numpy(),
        rtol=1e-12,
        atol=1e-12,
    )


def test_player_rows_without_a_team_id_take_the_team_from_the_game(tmp_path):
    db_path = tmp_path / "gap.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE games (gameId INTEGER, gameType TEXT, hometeamId INTEGER, "
            "hometeamName TEXT, awayteamId INTEGER, awayteamName TEXT)"
        )
        connection.execute(
            "CREATE TABLE player_statistics (gameId INTEGER, personId INTEGER, "
            "playerteamId INTEGER, playerteamName TEXT, home INTEGER, numMinutes TEXT, "
            "points INTEGER, assists INTEGER, reboundsTotal INTEGER, gameType TEXT)"
        )
        for game in (1, 2, 3):
            connection.execute(
                "INSERT INTO games VALUES (?, 'Regular Season', 10, 'Tens', 20, 'Twenties')",
                (game,),
            )
            for person, team, name, home, minutes in (
                (100, 10, "Tens", 1, "30"),
                (101, 10, "Tens", 1, "0"),
                (200, 20, "Twenties", 0, "25"),
            ):
                connection.execute(
                    "INSERT INTO player_statistics VALUES (?, ?, ?, ?, ?, ?, 10, 2, 5, NULL)",
                    (game, person, team, name, home, minutes),
                )
        connection.commit()
        expected_activity = load_player_activity(connection)
        expected_history = load_player_history(connection)
        # 2021-22 style gap: no playerteamId; team from the name, else the home flag.
        connection.execute("UPDATE player_statistics SET playerteamId = NULL WHERE gameId >= 2")
        connection.execute(
            "UPDATE player_statistics SET playerteamName = 'Old name' WHERE gameId = 3"
        )
        activity = load_player_activity(connection)
        history = load_player_history(connection)

    for actual, expected in ((activity, expected_activity), (history, expected_history)):
        key = ["gameId", "teamId", "personId"]
        assert actual["teamId"].notna().all()
        pd.testing.assert_frame_equal(
            actual.sort_values(key).reset_index(drop=True),
            expected.sort_values(key).reset_index(drop=True),
        )
    assert len(activity) == 6
    assert len(history) == 9


def test_pregame_player_feature_uses_only_previous_team_games():
    team_games = pd.DataFrame(
        [
            {"gameId": 1, "teamId": 10, "gameDateTimeEst": "2020-01-01"},
            {"gameId": 2, "teamId": 10, "gameDateTimeEst": "2020-01-02"},
        ]
    )
    activity = pd.DataFrame(
        [
            {"gameId": 1, "teamId": 10, "personId": 100},
            {"gameId": 1, "teamId": 10, "personId": 101},
            {"gameId": 2, "teamId": 10, "personId": 102},
        ]
    )

    result = add_pregame_player_features(team_games, activity)

    assert result[PLAYER_FEATURE].tolist() == [0, 2]
    assert result[LAST_GAME_PLAYER_FEATURE].tolist() == [0, 2]


def test_pregame_player_history_uses_only_previous_team_games():
    team_games = pd.DataFrame(
        [
            {"gameId": 1, "teamId": 10, "gameDateTimeEst": "2020-01-01"},
            {"gameId": 2, "teamId": 10, "gameDateTimeEst": "2020-01-02"},
            {"gameId": 3, "teamId": 10, "gameDateTimeEst": "2020-01-03"},
        ]
    )
    activity = pd.DataFrame(
        [
            {"gameId": 1, "teamId": 10, "personId": 100},
            {"gameId": 2, "teamId": 10, "personId": 100},
        ]
    )
    player_history = pd.DataFrame(
        [
            {
                "gameId": 1,
                "teamId": 10,
                "personId": 100,
                "minutes": 200,
                "points": 100,
                "assists": 20,
                "rebounds": 40,
            },
            {
                "gameId": 2,
                "teamId": 10,
                "personId": 100,
                "minutes": 210,
                "points": 110,
                "assists": 22,
                "rebounds": 42,
            },
        ]
    )

    result = add_pregame_player_features(team_games, activity, player_history)

    assert result["player_minutes_rolling_10"].isna().iloc[0]
    assert not pd.isna(result["player_minutes_rolling_10"].iloc[1])
    assert result["player_minutes_rolling_10"].iloc[1] == 200
    assert result["player_minutes_rolling_10"].iloc[2] == 205
    assert result["player_points_rolling_10"].iloc[2] == 105
    assert result["player_points_per_minute_rolling_10"].iloc[1] == 0.5
    assert np.isclose(result["player_points_per_minute_rolling_10"].iloc[2], 0.5119047619047619)


def test_opponent_adjusted_win_rate_uses_opponent_pregame_form():
    games = pd.DataFrame(
        [
            {
                "gameId": 1,
                "teamId": 10,
                "opponentTeamId": 20,
                "win": 1,
                "win_rate_rolling_10": 0.6,
            },
            {
                "gameId": 1,
                "teamId": 20,
                "opponentTeamId": 10,
                "win": 0,
                "win_rate_rolling_10": 0.4,
            },
        ]
    )

    merged = add_opponent_adjusted_win_rate(games)

    assert np.allclose(
        merged["opponent_adjusted_win_rate_rolling_10"].to_numpy(), [0.2, -0.2]
    )


def test_opponent_adjusted_margin_uses_opponent_pregame_margin():
    games = pd.DataFrame(
        [
            {
                "gameId": 1,
                "teamId": 10,
                "opponentTeamId": 20,
                "plusMinusPoints_rolling_10": 6.0,
            },
            {
                "gameId": 1,
                "teamId": 20,
                "opponentTeamId": 10,
                "plusMinusPoints_rolling_10": 2.0,
            },
        ]
    )

    merged = add_opponent_adjusted_margin(games)

    assert np.allclose(
        merged["opponent_adjusted_plusMinusPoints_rolling_10"].to_numpy(), [4.0, -4.0]
    )
