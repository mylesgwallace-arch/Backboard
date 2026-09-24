"""Forward (as-of-date) projection: leakage safety, record handling, uncertainty."""

import numpy as np
import pandas as pd
import pytest

from src.forward_projection import (
    ModelInputs,
    crps_from_samples,
    default_strength_sd,
    elo_ratings_before,
    frozen_matchup_probabilities,
    project_from_date,
    simulate_remaining_wins,
)
from src.main import compute_elo_ratings_as_of
from src.simulate_season import add_season_labels

EAST_A, EAST_B = 1610612737, 1610612738
WEST_A, WEST_B = 1610612740, 1610612742
TEAMS = [EAST_A, EAST_B, WEST_A, WEST_B]
ELO = {"initial_rating": 1500.0, "k_factor": 20.0, "home_advantage": 65.0}


class StubModel:
    """predict_proba from the teamScore delta and elo_delta, deterministic."""

    def predict_proba(self, frame):
        logit = frame["teamScore_rolling_10"].to_numpy() / 10.0 + frame["elo_delta"].to_numpy() / 400.0
        home = 1.0 / (1.0 + np.exp(-logit))
        return np.column_stack([1 - home, home])


def _synthetic_inputs():
    """Round-robin season starting 2024-10-22, one game per day."""
    pairings = [
        (EAST_A, EAST_B), (WEST_A, WEST_B), (EAST_A, WEST_A), (EAST_B, WEST_B),
        (WEST_A, EAST_B), (WEST_B, EAST_A), (EAST_B, EAST_A), (WEST_B, WEST_A),
    ] * 3
    games, features = [], []
    start = pd.Timestamp("2024-10-22 19:00")
    for index, (home, away) in enumerate(pairings):
        date = start + pd.DateOffset(days=index)
        target = int((home + index) % 3 != 0)
        games.append({"gameId": index + 1, "gameDateTimeEst": date,
                      "homeTeamId": home, "awayTeamId": away, "target": target})
        for team, is_home in ((home, 1), (away, 0)):
            features.append({
                "gameId": index + 1, "gameDateTimeEst": date, "teamId": team,
                "home": is_home, "teamScore_rolling_10": 100.0 + (team % 7) + index * 0.1,
            })
    games = add_season_labels(pd.DataFrame(games))
    return ModelInputs(
        features=pd.DataFrame(features),
        games=games,
        model=StubModel(),
        predictors=["teamScore_rolling_10", "elo_delta"],
        elo_config=ELO,
    )


def test_fast_elo_replay_matches_production_replay():
    inputs = _synthetic_inputs()
    cutoff = pd.Timestamp("2024-11-01")
    fast = elo_ratings_before(inputs.games, cutoff, ELO)
    slow, seen = compute_elo_ratings_as_of(inputs.games, cutoff=cutoff, **ELO)
    assert set(fast) == {int(team) for team in seen}
    for team, rating in slow.items():
        assert fast[int(team)] == pytest.approx(rating, abs=1e-9)


def test_probabilities_ignore_everything_on_or_after_the_cutoff():
    inputs = _synthetic_inputs()
    cutoff = pd.Timestamp("2024-11-01")
    pairs = pd.DataFrame({"homeTeamId": [EAST_A, WEST_B], "awayTeamId": [WEST_A, EAST_B]})
    before = frozen_matchup_probabilities(pairs, cutoff, inputs)

    # Flip every later result and scramble every later feature row.
    later_games = inputs.games["gameDateTimeEst"] >= cutoff
    inputs.games.loc[later_games, "target"] = 1 - inputs.games.loc[later_games, "target"]
    later_features = inputs.features["gameDateTimeEst"] > cutoff
    inputs.features.loc[later_features, "teamScore_rolling_10"] = 999.0
    after = frozen_matchup_probabilities(pairs, cutoff, inputs)

    np.testing.assert_allclose(
        before["home_win_probability"], after["home_win_probability"]
    )


def test_unknown_team_is_rejected():
    inputs = _synthetic_inputs()
    pairs = pd.DataFrame({"homeTeamId": [EAST_A], "awayTeamId": [999]})
    with pytest.raises(ValueError, match="999"):
        frozen_matchup_probabilities(pairs, pd.Timestamp("2024-11-01"), inputs)


def test_projection_keeps_actual_record_and_simulates_the_rest():
    inputs = _synthetic_inputs()
    as_of = "2024-11-03"
    projection = project_from_date(2024, as_of, inputs, n_simulations=200, random_state=3)

    completed = inputs.games[inputs.games["gameDateTimeEst"] < pd.Timestamp(as_of)]
    assert projection["games_completed"] == len(completed)
    assert projection["games_remaining"] == len(inputs.games) - len(completed)
    rows = {row["teamId"]: row for row in projection["projected_standings"]}
    for team in TEAMS:
        wins = int(((completed["homeTeamId"] == team) & (completed["target"] == 1)).sum()
                   + ((completed["awayTeamId"] == team) & (completed["target"] == 0)).sum())
        played = int(((completed["homeTeamId"] == team) | (completed["awayTeamId"] == team)).sum())
        assert rows[team]["current_wins"] == wins
        assert rows[team]["current_losses"] == played - wins
        # Nobody can finish below their current wins or above wins + remaining.
        assert rows[team]["p5_wins"] >= wins
        assert rows[team]["p95_wins"] <= wins + rows[team]["games_remaining"]
    total_games = sum(row["current_wins"] + row["current_losses"] + row["games_remaining"]
                      for row in rows.values())
    assert total_games == 2 * len(inputs.games)


def test_projection_after_the_last_game_is_the_actual_standings():
    inputs = _synthetic_inputs()
    projection = project_from_date(2024, "2025-06-30", inputs, n_simulations=50)
    assert projection["games_remaining"] == 0
    for row in projection["projected_standings"]:
        assert row["mean_wins"] == row["current_wins"]
        assert row["p5_wins"] == row["p95_wins"] == row["current_wins"]


def test_default_strength_sd_is_interpolated_by_season_share():
    assert default_strength_sd(0.0) == pytest.approx(0.6)
    assert default_strength_sd(0.125) == pytest.approx(0.5)
    assert default_strength_sd(0.5) == pytest.approx(0.4)
    assert default_strength_sd(1.0) == pytest.approx(0.4)


def test_strength_shocks_widen_the_distribution_but_not_the_centre():
    home = np.array([0, 1] * 200)
    away = np.array([1, 0] * 200)
    probabilities = np.full(len(home), 0.5)
    base = [0, 0]
    narrow = simulate_remaining_wins(base, home, away, probabilities, 2000, 1, strength_sd=0.0)
    wide = simulate_remaining_wins(base, home, away, probabilities, 2000, 1, strength_sd=0.6)
    assert narrow[:, 0].std() < wide[:, 0].std()
    assert narrow[:, 0].mean() == pytest.approx(wide[:, 0].mean(), rel=0.05)
    # Every game is won by exactly one team in every simulation.
    assert (wide.sum(axis=1) == len(home)).all()


def test_crps_known_values():
    constant = np.full((10, 1), 5.0)
    assert crps_from_samples(constant, [7.0])[0] == pytest.approx(2.0)
    coin = np.array([[0.0], [1.0]])
    # E|X - 0| = 0.5 and E|X - X'| = 0.5, so CRPS = 0.5 - 0.25.
    assert crps_from_samples(coin, [0.0])[0] == pytest.approx(0.25)


def test_upcoming_season_schedule_comes_from_the_games_table(tmp_path):
    import sqlite3

    from src.forward_projection import schedule_from_database, season_schedule, strength_freshness

    db_path = tmp_path / "nba.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE games (gameId INTEGER, gameDateTimeEst TEXT, gameType TEXT, "
                           "hometeamId INTEGER, awayteamId INTEGER, winner INTEGER)")
        connection.executemany(
            "INSERT INTO games VALUES (?, ?, ?, ?, ?, ?)",
            [
                (1, "2026-10-20 19:00:00", "Regular Season", EAST_A, EAST_B, EAST_A),
                (2, "2026-10-21 19:00:00", "Regular Season", WEST_A, WEST_B, None),
                (3, "2026-10-05 19:00:00", "Preseason", EAST_A, WEST_A, EAST_A),
            ],
        )
        connection.commit()

    database = schedule_from_database(2026, db_path=db_path)
    assert list(database["gameId"]) == [1, 2]
    assert database["target"].tolist()[0] == 1.0
    assert np.isnan(database["target"].tolist()[1])

    inputs = _synthetic_inputs()  # its games are all season 2024
    schedule = season_schedule(inputs, 2026, db_path=db_path)
    assert list(schedule["gameId"]) == [1, 2]
    freshness = strength_freshness(inputs, schedule, "2026-10-22")
    assert freshness["features_stale"] is True
    assert freshness["played_games_after_latest_features"] == 1

    with pytest.raises(ValueError, match="ingest its schedule"):
        season_schedule(inputs, 2030, db_path=db_path)


def test_projection_accepts_unplayed_database_schedule():
    inputs = _synthetic_inputs()
    schedule = inputs.games.copy()
    schedule.loc[schedule["gameDateTimeEst"] >= pd.Timestamp("2024-11-05"), "target"] = np.nan
    projection = project_from_date(2024, "2024-11-05", inputs, n_simulations=50, schedule=schedule)
    assert projection["games_remaining"] == int(schedule["target"].isna().sum())
    with pytest.raises(ValueError, match="no recorded result"):
        project_from_date(2024, "2024-11-10", inputs, n_simulations=10, schedule=schedule)


def test_strength_table_is_a_mean_of_home_and_road_probabilities():
    from src.forward_projection import strength_table

    inputs = _synthetic_inputs()
    rows = strength_table(inputs, "2024-11-10")
    assert {row["teamId"] for row in rows} == set(TEAMS)
    for row in rows:
        assert row["win_probability_vs_average"] == pytest.approx(
            (row["home_win_probability_vs_average"] + row["road_win_probability_vs_average"]) / 2)
    # Across all teams the road/home means are complementary on average.
    assert np.mean([row["win_probability_vs_average"] for row in rows]) == pytest.approx(0.5)
