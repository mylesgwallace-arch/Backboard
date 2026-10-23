import sqlite3

import pandas as pd
import pytest

from src.player_impact import (
    _add_team_participation_controls,
    _select_external_roster_change_appearances,
    estimate_player_impact,
    load_player_history,
    load_validation_data,
    main,
    validate_player_impact,
)
from src.roster_change_data import validate_roster_change_events


def test_player_impact_uses_minutes_weighted_prior_production():
    history = pd.DataFrame(
        [
            {"minutes": 24, "netRating": 10},
            {"minutes": 48, "netRating": -2},
        ]
    )

    result = estimate_player_impact(history)

    assert result["prior_games"] == 2
    assert result["player_net_rating"] == pytest.approx(2.0)
    assert result["expected_minutes"] == pytest.approx(36.0)
    assert result["estimated_net_rating_change"] == pytest.approx(1.5)


def test_player_removal_is_negative_of_addition():
    history = pd.DataFrame([{"minutes": 24, "netRating": 8}])

    addition = estimate_player_impact(history)
    removal = estimate_player_impact(history, direction="removal")

    assert removal["estimated_net_rating_change"] == pytest.approx(
        -addition["estimated_net_rating_change"]
    )


def test_validation_target_uses_only_prior_team_games():
    player_games = pd.DataFrame(
        [
            {"personId": 1, "gameId": i, "teamId": 10,
             "gameDateTimeEst": f"2020-01-{i:02d}", "minutes": 24,
             "netRating": 8, "points": 20, "player_possessions": 20}
            for i in range(1, 13)
        ]
    )
    team_games = pd.DataFrame(
        [
            {"gameId": i, "teamId": 10, "gameDateTimeEst": f"2020-01-{i:02d}",
             "netRating": 0, "team_points": 100, "opponent_points": 100,
             "team_possessions": 100}
            for i in range(1, 13)
        ]
    )
    team_games.loc[team_games["gameId"] == 12, "netRating"] = 20
    team_games.loc[team_games["gameId"] == 11, "netRating"] = 4

    result = validate_player_impact(player_games, team_games)

    assert result["evaluated_player_games"] == 2
    assert result["zero_change_baseline_mae"] > 0
    assert (
        result["association_diagnostic_mae"]
        < result["zero_change_baseline_mae"]
    )
    holdout = result["chronological_holdout"]
    assert holdout["training_games"] == 1
    assert holdout["holdout_games"] == 1
    assert holdout["improves_zero_change_baseline"]
    assert holdout["improves_zero_change_baseline_all_seasons"]
    intervals = holdout["bootstrap_intervals"]
    assert intervals["cluster"] == "gameId"
    assert intervals["replicates"] == 1000
    assert len(intervals["calibrated_minus_baseline_mae"]) == 2
    independent = result["independent_pregame_target"]
    assert independent["target"] == "current team net rating"
    assert not independent[
        "target_is_derived_by_removing_current_player_scoring"
    ]
    assert independent["control_predictors"] == [
        "prior_team_net_rating",
        "prior_active_players",
        "prior_rotation_minutes",
    ]
    assert independent["candidate_predictors"] == [
        "prior_team_net_rating",
        "prior_active_players",
        "prior_rotation_minutes",
        "player_signal",
    ]
    assert independent["holdout_player_games"] == 1
    assert independent["bootstrap_intervals"]["cluster"] == "gameId"
    robustness = result["independent_pregame_robustness"]
    assert set(robustness) == {"0.1", "0.2", "0.3"}
    for window in robustness.values():
        assert window["usage_strata_definition"].startswith(
            "Low, middle, and high strata"
        )
        assert set(window["usage_strata"]) <= {"low", "middle", "high"}
        assert window["control_predictors"] == independent["control_predictors"]
    later = result["later_team_game_validation"]
    assert later["validation_start_season"] == 2024
    assert later["status"] == "insufficient_later_season_data"
    assert not later["control_has_player_signal"]
    assert later["candidate_adds_player_signal"]
    assert later["control_predictors"][-2:] == ["rest_days", "home"]
    split_results = result["later_team_game_validation_by_season"]
    assert set(split_results) == {"2022", "2023", "2024", "2025"}
    for split in split_results.values():
        assert split["status"] == "insufficient_later_season_data"
        assert not split["control_has_player_signal"]
    roster_changes = result["roster_change_validation"]
    assert roster_changes["status"] == "insufficient_roster_change_data"


def test_main_validate_roster_events_mode_prints_summary(tmp_path, capsys):
    events = pd.DataFrame(
        [
            {
                "event_id": "add-1",
                "event_timestamp": "2024-10-01T15:00:00-04:00",
                "team_id": 10,
                "person_id": 100,
                "change_type": "add",
                "source": "independent source",
                "source_url": "https://example.test/add-1",
            },
            {
                "event_id": "remove-1",
                "event_timestamp": "2024-10-02T15:00:00-04:00",
                "team_id": 20,
                "person_id": 200,
                "change_type": "remove",
                "source": "independent source",
                "source_url": "https://example.test/remove-1",
            },
        ]
    )
    path = tmp_path / "roster_changes.csv"
    events.to_csv(path, index=False)

    exit_code = main(["--validate-roster-events", str(path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert '"event_count": 2' in captured.out
    assert '"add_count": 1' in captured.out
    assert '"remove_count": 1' in captured.out


def test_select_external_roster_change_appearances_uses_high_confidence_only():
    values = pd.DataFrame(
        [
            {
                "personId": 100,
                "teamId": 10,
                "gameId": 1,
                "gameDateTimeEst": "2024-10-01T00:00:00Z",
                "home": 1,
                "current_team_net_rating": 5.0,
                "prior_team_net_rating": 0.0,
                "prior_opponent_net_rating": 0.0,
                "prior_active_players": 7,
                "prior_rotation_minutes": 200,
                "prior_team_possessions": 90,
                "player_signal": 1.5,
            }
        ]
    )
    events = pd.DataFrame(
        [
            {
                "event_id": "high-1",
                "event_timestamp": "2024-09-30T00:00:00Z",
                "team_id": 10,
                "person_id": 100,
                "change_type": "add",
                "source": "independent source",
                "source_url": "https://example.test/high-1",
                "confidence_level": "high",
            },
            {
                "event_id": "low-1",
                "event_timestamp": "2024-09-29T00:00:00Z",
                "team_id": 10,
                "person_id": 100,
                "change_type": "add",
                "source": "independent source",
                "source_url": "https://example.test/low-1",
                "confidence_level": "low",
            },
        ]
    )

    matched = _select_external_roster_change_appearances(values, events)

    assert len(matched) == 1
    assert matched.iloc[0]["external_event_id"] == "high-1"


def test_main_person_id_mode_returns_descriptive_player_impact_summary(monkeypatch, tmp_path, capsys):
    db_path = tmp_path / "impact.db"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "CREATE TABLE games (gameId INTEGER PRIMARY KEY, gameDateTimeEst TEXT, gameType TEXT, hometeamId INTEGER, hometeamName TEXT, awayteamId INTEGER, awayteamName TEXT)"
        )
        connection.execute(
            "CREATE TABLE player_statistics_extended (personId INTEGER, gameId INTEGER, gameDateTimeEst TEXT, playerteamId INTEGER, playerteamName TEXT, home INTEGER, numMinutes REAL, netRating REAL, gameType TEXT)"
        )
        player_rows = [
            (1, 1, "2024-01-01 00:00:00", 7, 20.0, 10.0),
            (1, 2, "2024-01-02 00:00:00", 7, 30.0, 0.0),
            (1, 3, "2024-01-03 00:00:00", 7, 40.0, 5.0),
        ]
        connection.executemany(
            "INSERT INTO player_statistics_extended (personId, gameId, gameDateTimeEst, playerteamId, numMinutes, netRating, gameType) VALUES (?, ?, ?, ?, ?, ?, 'Regular Season')",
            player_rows,
        )
        for game_id, game_date in [(1, "2024-01-01 00:00:00"), (2, "2024-01-02 00:00:00"), (3, "2024-01-03 00:00:00")]:
            connection.execute(
                "INSERT INTO games (gameId, gameDateTimeEst, gameType) VALUES (?, ?, 'Regular Season')",
                (game_id, game_date),
            )
        connection.commit()
    finally:
        connection.close()

    monkeypatch.setattr("src.player_impact.DB_PATH", db_path)
    exit_code = main(["--person-id", "1"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert '"person_id": 1' in captured.out
    assert '"prior_games": 3' in captured.out
    assert '"estimated_net_rating_change"' in captured.out


def _team_gap_db(path):
    """Three games of team 7 (home) vs 8; player 1 plays for 7, player 2 for 8."""
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE games (gameId INTEGER, gameDateTimeEst TEXT, gameType TEXT, "
            "hometeamId INTEGER, hometeamName TEXT, awayteamId INTEGER, awayteamName TEXT)"
        )
        connection.execute(
            "CREATE TABLE player_statistics_extended (personId INTEGER, gameId INTEGER, "
            "gameDateTimeEst TEXT, playerteamId INTEGER, playerteamName TEXT, home INTEGER, "
            "numMinutes REAL, netRating REAL, points REAL, possessions REAL, gameType TEXT)"
        )
        connection.execute(
            "CREATE TABLE team_statistics_extended (teamId INTEGER, opponentTeamId INTEGER, "
            "gameId INTEGER, gameDateTimeEst TEXT, home INTEGER, netRating REAL, "
            "teamScore REAL, opponentScore REAL, possessions REAL, gameType TEXT)"
        )
        for game in (1, 2, 3):
            date = f"2024-01-0{game} 19:00:00"
            connection.execute(
                "INSERT INTO games VALUES (?, ?, 'Regular Season', 7, 'Sevens', 8, 'Eights')",
                (game, date),
            )
            for person, team, name, home in ((1, 7, "Sevens", 1), (2, 8, "Eights", 0)):
                connection.execute(
                    "INSERT INTO player_statistics_extended VALUES "
                    "(?, ?, ?, ?, ?, ?, 30.0, 5.0, 12.0, 60.0, 'Regular Season')",
                    (person, game, date, team, name, home),
                )
            for team, opponent, home in ((7, 8, 1), (8, 7, 0)):
                connection.execute(
                    "INSERT INTO team_statistics_extended VALUES "
                    "(?, ?, ?, ?, ?, 2.0, 100.0, 98.0, 99.0, 'Regular Season')",
                    (team, opponent, game, date, home),
                )
        connection.commit()


def test_rows_without_a_team_id_take_the_team_from_the_game(tmp_path):
    db_path = tmp_path / "gap.db"
    _team_gap_db(db_path)
    with sqlite3.connect(db_path) as connection:
        expected_history = load_player_history(connection, 2)
        expected_players, expected_teams = load_validation_data(connection)
        # 2021-22 style gap: no playerteamId (team from the name, else the home
        # flag) and no gameType on the team rows (taken from the game).
        connection.execute(
            "UPDATE player_statistics_extended SET playerteamId = NULL WHERE gameId >= 2"
        )
        connection.execute(
            "UPDATE player_statistics_extended SET playerteamName = 'Old name' WHERE gameId = 3"
        )
        connection.execute("UPDATE team_statistics_extended SET gameType = NULL WHERE gameId >= 2")
        history = load_player_history(connection, 2)
        players, teams = load_validation_data(connection)

    assert history["teamId"].tolist() == [8, 8, 8]
    pd.testing.assert_frame_equal(history, expected_history)
    for actual, expected, key in (
        (players, expected_players, ["gameId", "personId"]),
        (teams, expected_teams, ["gameId", "teamId"]),
    ):
        assert len(actual) == 6
        pd.testing.assert_frame_equal(
            actual.sort_values(key).reset_index(drop=True),
            expected.sort_values(key).reset_index(drop=True),
        )


def test_player_rows_without_a_team_row_do_not_shift_other_rows_history():
    player_games = pd.DataFrame(
        [
            {"personId": 2, "gameId": i, "teamId": 10,
             "gameDateTimeEst": f"2020-01-{i:02d}", "minutes": 24,
             "netRating": 8, "points": 10 + i, "player_possessions": 20}
            for i in range(1, 16)
        ]
    )
    team_games = pd.DataFrame(
        [
            {"gameId": i, "teamId": 10, "gameDateTimeEst": f"2020-01-{i:02d}",
             "netRating": (i % 5) * 3, "team_points": 100 + i, "opponent_points": 100,
             "team_possessions": 100}
            for i in range(1, 16)
        ]
    )
    expected = validate_player_impact(player_games, team_games)
    # Sorts first and has no team row, so the inner join drops it.
    orphan = pd.DataFrame(
        [{"personId": 1, "gameId": 99, "teamId": 99, "gameDateTimeEst": "2019-12-01",
          "minutes": 30, "netRating": 0, "points": 50, "player_possessions": 25}]
    )
    result = validate_player_impact(pd.concat([orphan, player_games]), team_games)

    assert result["evaluated_player_games"] == expected["evaluated_player_games"] == 5
    assert result["association_diagnostic_mae"] == pytest.approx(
        expected["association_diagnostic_mae"]
    )
    assert result["prediction_target_correlation"] == pytest.approx(
        expected["prediction_target_correlation"]
    )


def test_team_participation_controls_use_only_the_prior_team_game():
    players = pd.DataFrame(
        [
            {
                "personId": 1,
                "teamId": 10,
                "gameId": 1,
                "gameDateTimeEst": "2020-01-01",
                "minutes": 30,
            },
            {
                "personId": 2,
                "teamId": 10,
                "gameId": 1,
                "gameDateTimeEst": "2020-01-01",
                "minutes": 20,
            },
            {
                "personId": 1,
                "teamId": 10,
                "gameId": 2,
                "gameDateTimeEst": "2020-01-02",
                "minutes": 35,
            },
        ]
    )

    result = _add_team_participation_controls(players)

    current = result[result["gameId"] == 2].iloc[0]
    assert current["prior_active_players"] == 2
    assert current["prior_rotation_minutes"] == 50
    assert result[result["gameId"] == 1][
        ["prior_active_players", "prior_rotation_minutes"]
    ].isna().all().all()


def test_external_addition_links_only_to_first_later_appearance():
    values = pd.DataFrame(
        [
            {"personId": 100, "teamId": 10, "gameId": 1, "gameDateTimeEst": "2024-10-01"},
            {"personId": 100, "teamId": 10, "gameId": 2, "gameDateTimeEst": "2024-10-03"},
            {"personId": 100, "teamId": 10, "gameId": 3, "gameDateTimeEst": "2024-10-05"},
        ]
    )
    events = validate_roster_change_events(
        pd.DataFrame(
            [{
                "event_id": "add-100",
                "event_timestamp": "2024-10-02T12:00:00Z",
                "team_id": 10,
                "person_id": 100,
                "change_type": "add",
                "source": "independent source",
                "source_url": "https://example.test/add-100",
            }]
        )
    )

    result = _select_external_roster_change_appearances(values, events)

    assert result["external_event_id"].tolist() == ["add-100"]
    assert result["gameId"].tolist() == [2]
