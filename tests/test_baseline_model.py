import json
import math

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression

from src.train_baseline_model import (
    add_elo_rating_deltas,
    add_opponent_form_features,
    add_player_context_features,
    average_probability_predictions,
    build_game_dataset,
    candidate_beats_production,
    compare_calibration_methods,
    elo_win_probability,
    evaluate_boosted_hybrid_ablation,
    evaluate_boosted_hybrid_parameter_grid,
    evaluate_calibration,
    evaluate_calibration_by_group,
    evaluate_elo,
    evaluate_elo_by_season,
    evaluate_opponent_form_experiment,
    evaluate_player_context_experiment,
    evaluate_player_efficiency_experiment,
    select_recommended_model,
    summarize_candidate_comparison,
    summarize_feature_importance,
    tune_hybrid_logistic,
)
from src.model_calibration import CalibratedProbabilityModel


def test_game_dataset_pairs_home_and_away_rows_without_current_game_metrics():
    features = pd.DataFrame(
        [
            {
                "gameId": 1,
                "gameDateTimeEst": "2020-01-01",
                "teamId": 10,
                "home": 1,
                "win": 1,
                "teamScore": 110,
                "teamScore_rolling_10": 100,
            },
            {
                "gameId": 1,
                "gameDateTimeEst": "2020-01-01",
                "teamId": 20,
                "home": 0,
                "win": 0,
                "teamScore": 90,
                "teamScore_rolling_10": 95,
            },
        ]
    )

    games, predictors = build_game_dataset(features)

    assert len(games) == 1
    assert predictors == ["teamScore_rolling_10"]
    assert games.loc[0, "target"] == 1
    assert games.loc[0, "teamScore_rolling_10"] == 5
    assert "teamScore" not in predictors


def test_game_dataset_includes_pregame_rest_difference_when_available():
    features = pd.DataFrame(
        [
            {
                "gameId": 1,
                "gameDateTimeEst": "2020-01-03",
                "teamId": 10,
                "home": 1,
                "win": 1,
                "teamScore_rolling_10": 100,
                "rest_days": 2,
            },
            {
                "gameId": 1,
                "gameDateTimeEst": "2020-01-03",
                "teamId": 20,
                "home": 0,
                "win": 0,
                "teamScore_rolling_10": 95,
                "rest_days": 1,
            },
        ]
    )

    games, predictors = build_game_dataset(features)

    assert predictors == ["teamScore_rolling_10", "rest_days"]
    assert games.loc[0, "rest_days"] == 1


def test_game_dataset_includes_pregame_player_availability_differences():
    features = pd.DataFrame(
        [
            {
                "gameId": 1,
                "gameDateTimeEst": "2020-01-03",
                "teamId": 10,
                "home": 1,
                "win": 1,
                "teamScore_rolling_10": 100,
                "active_players_rolling_10": 8,
                "active_players_last_game": 7,
            },
            {
                "gameId": 1,
                "gameDateTimeEst": "2020-01-03",
                "teamId": 20,
                "home": 0,
                "win": 0,
                "teamScore_rolling_10": 95,
                "active_players_rolling_10": 9,
                "active_players_last_game": 9,
            },
        ]
    )

    games, predictors = build_game_dataset(features)

    assert predictors == [
        "teamScore_rolling_10",
        "active_players_rolling_10",
        "active_players_last_game",
    ]
    assert games.loc[0, "active_players_rolling_10"] == -1
    assert games.loc[0, "active_players_last_game"] == -2


def test_game_dataset_includes_player_points_per_minute_when_available():
    features = pd.DataFrame(
        [
            {
                "gameId": 1,
                "gameDateTimeEst": "2020-01-03",
                "teamId": 10,
                "home": 1,
                "win": 1,
                "teamScore_rolling_10": 100,
                "player_points_per_minute_rolling_10": 0.5,
            },
            {
                "gameId": 1,
                "gameDateTimeEst": "2020-01-03",
                "teamId": 20,
                "home": 0,
                "win": 0,
                "teamScore_rolling_10": 95,
                "player_points_per_minute_rolling_10": 0.4,
            },
        ]
    )

    games, predictors = build_game_dataset(features)

    assert predictors == [
        "teamScore_rolling_10",
        "player_points_per_minute_rolling_10",
    ]
    assert np.isclose(games.loc[0, "player_points_per_minute_rolling_10"], 0.1)


def test_game_dataset_includes_opponent_adjusted_form_differences():
    features = pd.DataFrame(
        [
            {
                "gameId": 1,
                "gameDateTimeEst": "2020-01-03",
                "teamId": 10,
                "home": 1,
                "win": 1,
                "teamScore_rolling_10": 100,
                "opponent_adjusted_win_rate_rolling_10": 0.1,
                "opponent_adjusted_plusMinusPoints_rolling_10": 3.5,
            },
            {
                "gameId": 1,
                "gameDateTimeEst": "2020-01-03",
                "teamId": 20,
                "home": 0,
                "win": 0,
                "teamScore_rolling_10": 95,
                "opponent_adjusted_win_rate_rolling_10": -0.2,
                "opponent_adjusted_plusMinusPoints_rolling_10": -1.5,
            },
        ]
    )

    games, predictors = build_game_dataset(features)

    assert predictors == [
        "teamScore_rolling_10",
        "opponent_adjusted_win_rate_rolling_10",
        "opponent_adjusted_plusMinusPoints_rolling_10",
    ]
    assert np.isclose(games.loc[0, "opponent_adjusted_win_rate_rolling_10"], 0.3)
    assert np.isclose(games.loc[0, "opponent_adjusted_plusMinusPoints_rolling_10"], 5.0)


def test_elo_updates_after_completed_games_only():
    games = pd.DataFrame(
        [
            {
                "gameId": 1,
                "gameDateTimeEst": "2020-01-01",
                "homeTeamId": 10,
                "awayTeamId": 20,
                "target": 1,
            },
            {
                "gameId": 2,
                "gameDateTimeEst": "2020-01-02",
                "homeTeamId": 10,
                "awayTeamId": 20,
                "target": 0,
            },
        ]
    )

    metrics = evaluate_elo(
        games,
        split=1,
        initial_rating=1500,
        k_factor=20,
        home_advantage=0,
    )

    expected_log_loss = -math.log(
        1 - elo_win_probability(1500 + 10, 1500 - 10, home_advantage=0)
    )
    assert metrics["accuracy"] == 0.0
    assert abs(metrics["log_loss"] - expected_log_loss) < 1e-12


def test_elo_season_metrics_use_pregame_ratings_and_report_games():
    games = pd.DataFrame(
        [
            {
                "gameId": 1,
                "gameDateTimeEst": "2020-01-01",
                "homeTeamId": 10,
                "awayTeamId": 20,
                "target": 1,
                "season": 2019,
            },
            {
                "gameId": 2,
                "gameDateTimeEst": "2020-01-02",
                "homeTeamId": 10,
                "awayTeamId": 20,
                "target": 0,
                "season": 2019,
            },
            {
                "gameId": 3,
                "gameDateTimeEst": "2021-01-01",
                "homeTeamId": 10,
                "awayTeamId": 20,
                "target": 1,
                "season": 2020,
            },
        ]
    )

    metrics = evaluate_elo_by_season(
        games, split=1, initial_rating=1500, k_factor=20, home_advantage=0
    )

    assert metrics["2019"]["games"] == 1
    assert metrics["2020"]["games"] == 1
    assert metrics["2019"]["accuracy"] == 0.0
    assert metrics["2020"]["accuracy"] == 0.0


def test_select_recommended_model_uses_lowest_log_loss():
    metrics = {
        "home_win_rate": {"accuracy": 0.5, "log_loss": 0.69, "brier_score": 0.25},
        "rolling_logistic": {"accuracy": 0.62, "log_loss": 0.65, "brier_score": 0.23},
        "elo": {"accuracy": 0.65, "log_loss": 0.62, "brier_score": 0.21},
    }

    assert select_recommended_model(metrics) == "elo"


def test_select_recommended_model_excludes_trivial_home_win_rate_baseline():
    # home_win_rate should never be "recommended" even if it happens to have
    # the lowest log loss on some slice, because it is a reference baseline,
    # not a deployable model.
    metrics = {
        "home_win_rate": {"accuracy": 0.5, "log_loss": 0.1, "brier_score": 0.05},
        "rolling_logistic": {"accuracy": 0.62, "log_loss": 0.65, "brier_score": 0.23},
    }

    assert select_recommended_model(metrics) == "rolling_logistic"


def test_average_probability_predictions_returns_simple_mean():
    probabilities = [
        pd.Series([0.8, 0.3, 0.9]),
        pd.Series([0.6, 0.2, 0.8]),
    ]

    ensemble = average_probability_predictions(*probabilities)

    np.testing.assert_allclose(ensemble, np.array([0.7, 0.25, 0.85]))


def test_select_recommended_model_prefers_ensemble_when_it_has_best_log_loss():
    metrics = {
        "home_win_rate": {"accuracy": 0.5, "log_loss": 0.69, "brier_score": 0.25},
        "elo": {"accuracy": 0.6497, "log_loss": 0.6262, "brier_score": 0.2181},
        "boosted_hybrid": {"accuracy": 0.6522, "log_loss": 0.6255, "brier_score": 0.2174},
        "elo_boosted_ensemble": {
            "accuracy": 0.6498,
            "log_loss": 0.6229,
            "brier_score": 0.2166,
        },
    }

    assert select_recommended_model(metrics) == "elo_boosted_ensemble"


def test_add_opponent_form_features_creates_relative_form_columns():
    features = pd.DataFrame(
        [
            {
                "gameId": 1,
                "teamId": 10,
                "opponentTeamId": 20,
                "win_rate_rolling_10": 0.7,
                "plusMinusPoints_rolling_10": 5.0,
            },
            {
                "gameId": 1,
                "teamId": 20,
                "opponentTeamId": 10,
                "win_rate_rolling_10": 0.5,
                "plusMinusPoints_rolling_10": 2.0,
            },
        ]
    )

    augmented = add_opponent_form_features(features)

    assert "opponent_adjusted_win_rate_rolling_10" in augmented.columns
    assert "opponent_adjusted_plusMinusPoints_rolling_10" in augmented.columns
    assert np.isclose(
        augmented.loc[augmented["teamId"] == 10, "opponent_adjusted_win_rate_rolling_10"].iloc[0],
        0.2,
    )
    assert np.isclose(
        augmented.loc[augmented["teamId"] == 10, "opponent_adjusted_plusMinusPoints_rolling_10"].iloc[0],
        3.0,
    )


@pytest.mark.requires_data
def test_evaluate_opponent_form_experiment_reports_key_metrics():
    features = pd.read_csv("data/processed/game_features.csv")
    metrics = evaluate_opponent_form_experiment(features)

    assert set(metrics) >= {"baseline", "with_opponent_form"}
    assert "opponent_adjusted_win_rate_rolling_10" in metrics["augmented_feature_names"]
    assert set(metrics["baseline"]) >= {"accuracy", "log_loss", "brier_score"}


@pytest.mark.requires_data
def test_evaluate_player_efficiency_experiment_reports_key_metrics():
    features = pd.read_csv("data/processed/game_features.csv")
    metrics = evaluate_player_efficiency_experiment(features)

    assert set(metrics) >= {"baseline", "with_player_efficiency"}
    assert "player_points_per_minute_rolling_10" in metrics["candidate_feature_names"]
    assert set(metrics["baseline"]) >= {"accuracy", "log_loss", "brier_score"}


def test_add_player_context_features_normalizes_player_volume_by_rotation_size():
    features = pd.DataFrame(
        [
            {
                "gameId": 1,
                "teamId": 10,
                "active_players_rolling_10": 5,
                "player_minutes_rolling_10": 400,
                "player_points_rolling_10": 120,
                "player_assists_rolling_10": 24,
            },
            {
                "gameId": 2,
                "teamId": 20,
                "active_players_rolling_10": 8,
                "player_minutes_rolling_10": 640,
                "player_points_rolling_10": 200,
                "player_assists_rolling_10": 28,
            },
        ]
    )

    result = add_player_context_features(features)

    assert result["player_minutes_rolling_10_per_active_player_rolling_10"].tolist() == [80.0, 80.0]
    assert result["player_points_rolling_10_per_active_player_rolling_10"].tolist() == [24.0, 25.0]
    assert result["player_assists_rolling_10_per_active_player_rolling_10"].tolist() == [4.8, 3.5]


@pytest.mark.requires_data
def test_evaluate_player_context_experiment_reports_key_metrics():
    features = pd.read_csv("data/processed/game_features.csv")
    metrics = evaluate_player_context_experiment(features)

    assert set(metrics) >= {"baseline", "with_player_context"}
    assert "player_minutes_rolling_10_per_active_player_rolling_10" in metrics["candidate_feature_names"]
    assert set(metrics["baseline"]) >= {"accuracy", "log_loss", "brier_score"}


@pytest.mark.requires_data
def test_player_context_experiment_does_not_beat_production_holdout():
    features = pd.read_csv("data/processed/game_features.csv")
    candidate = evaluate_player_context_experiment(features)
    with open("models/baseline_metrics.json", "r", encoding="utf-8") as handle:
        production = json.load(handle)["metrics"]["elo_boosted_ensemble"]

    assert candidate["with_player_context"]["accuracy"] < production["accuracy"]
    assert candidate["with_player_context"]["log_loss"] > production["log_loss"]
    assert candidate["with_player_context"]["brier_score"] > production["brier_score"]


def test_candidate_beats_production_requires_strict_improvement_on_core_metrics():
    metrics = {
        "elo_boosted_ensemble": {"accuracy": 0.65, "log_loss": 0.63, "brier_score": 0.22},
        "player_history_logistic": {"accuracy": 0.65, "log_loss": 0.62, "brier_score": 0.21},
    }
    assert candidate_beats_production(metrics, "player_history_logistic")

    metrics["player_history_logistic"] = {
        "accuracy": 0.64,
        "log_loss": 0.62,
        "brier_score": 0.22,
    }
    assert not candidate_beats_production(metrics, "player_history_logistic")


def test_select_recommended_model_keeps_current_production_when_candidate_loses():
    metrics = {
        "home_win_rate": {"accuracy": 0.56, "log_loss": 0.69, "brier_score": 0.25},
        "elo_boosted_ensemble": {"accuracy": 0.65, "log_loss": 0.62, "brier_score": 0.22},
        "player_history_logistic": {"accuracy": 0.64, "log_loss": 0.63, "brier_score": 0.23},
    }

    assert select_recommended_model(metrics) == "elo_boosted_ensemble"


def test_summarize_candidate_comparison_reports_metric_deltas():
    metrics = {
        "elo_boosted_ensemble": {"accuracy": 0.65, "log_loss": 0.62, "brier_score": 0.22},
        "player_history_logistic": {"accuracy": 0.66, "log_loss": 0.61, "brier_score": 0.21},
    }

    summary = summarize_candidate_comparison(metrics, "player_history_logistic")

    assert summary["candidate_beats_production"] is True
    assert summary["accuracy_delta"] > 0
    assert summary["log_loss_delta"] < 0
    assert summary["brier_score_delta"] < 0


def test_add_elo_rating_deltas_tracks_pregame_strength_gap():
    games = pd.DataFrame(
        [
            {"gameId": 1, "homeTeamId": 10, "awayTeamId": 20, "target": 1},
            {"gameId": 2, "homeTeamId": 20, "awayTeamId": 10, "target": 0},
        ]
    )

    rows = add_elo_rating_deltas(games, initial_rating=1500, k_factor=20, home_advantage=65)

    assert abs(rows.loc[0, "elo_delta"] - 65.0) < 1e-9
    assert abs(rows.loc[0, "elo_probability"] - elo_win_probability(1500, 1500, 65)) < 1e-9
    expected_second_delta = (
        (1500 + 20 * (0 - (1 - elo_win_probability(1500, 1500, 65))) )
        - (1500 + 20 * (1 - elo_win_probability(1500, 1500, 65)))
        + 65
    )
    assert abs(rows.loc[1, "elo_delta"] - expected_second_delta) < 1e-6


def test_tune_hybrid_logistic_returns_valid_grid_metrics():
    train = pd.DataFrame(
        {
            "target": [0, 1, 0, 1, 1, 0],
            "elo_delta": [-3.0, -2.0, -1.0, 1.0, 2.0, 3.0],
        }
    )
    test = pd.DataFrame(
        {
            "target": [0, 1, 1, 0],
            "elo_delta": [-4.0, -1.5, 1.5, 4.0],
        }
    )

    model, c_value, metrics = tune_hybrid_logistic(
        train, test, ["elo_delta"], c_values=[0.01, 1.0]
    )

    assert model is not None
    assert c_value in {0.01, 1.0}
    assert set(metrics) == {"accuracy", "log_loss", "brier_score"}


def test_summarize_feature_importance_ranks_predictive_features():
    train = pd.DataFrame(
        {
            "target": [0, 0, 0, 0, 1, 1, 1, 1],
            "strong_signal": [0.1, 0.2, 0.3, 0.4, 1.4, 1.5, 1.6, 1.8],
            "weak_signal": [0.6, 0.7, 0.5, 0.9, 0.8, 0.4, 0.5, 0.7],
        }
    )
    model = LogisticRegression(max_iter=2000)
    model.fit(train[["strong_signal", "weak_signal"]], train["target"])

    features = summarize_feature_importance(
        model,
        ["strong_signal", "weak_signal"],
        top_n=2,
    )

    assert len(features) == 2
    assert features[0]["feature"] == "strong_signal"
    assert features[0]["importance"] >= features[1]["importance"]
    assert abs(sum(item["importance"] for item in features) - 1.0) < 1e-9


def test_evaluate_boosted_hybrid_parameter_grid_returns_metrics():
    train = pd.DataFrame(
        {
            "target": [0, 1, 0, 1, 1, 0, 1, 0],
            "elo_delta": [-2.0, -1.0, 0.0, 1.0, 2.0, -3.0, 3.0, -4.0],
        }
    )
    test = pd.DataFrame(
        {
            "target": [0, 1, 0, 1],
            "elo_delta": [-1.5, 0.5, 2.5, -2.5],
        }
    )

    results = evaluate_boosted_hybrid_parameter_grid(train, test, ["elo_delta"])

    assert len(results) == 4
    assert all(set(result) >= {"max_depth", "learning_rate", "max_iter", "random_state", "accuracy", "log_loss", "brier_score"} for result in results)


def test_evaluate_boosted_hybrid_ablation_excludes_requested_column():
    train = pd.DataFrame(
        {
            "target": [0, 1, 0, 1, 1, 0, 1, 0],
            "elo_delta": [-2.0, -1.0, 0.0, 1.0, 2.0, -3.0, 3.0, -4.0],
            "win_rate_rolling_10": [0.2, 0.4, 0.5, 0.6, 0.7, 0.3, 0.8, 0.1],
        }
    )
    test = train.iloc[:4].copy()
    test["season"] = [2020, 2020, 2021, 2021]
    train["season"] = 2020

    result = evaluate_boosted_hybrid_ablation(
        train,
        test,
        ["elo_delta", "win_rate_rolling_10"],
        "win_rate_rolling_10",
        {"max_depth": 3, "learning_rate": 0.05, "max_iter": 20, "random_state": 42},
    )

    assert set(result["metrics"]) == {"accuracy", "log_loss", "brier_score"}
    assert set(result["by_season"]) == {"2020", "2021"}


def test_evaluate_calibration_reports_expected_calibration_error():
    target = pd.Series([0, 1, 0, 1])
    probabilities = pd.Series([0.1, 0.9, 0.2, 0.8])

    metrics = evaluate_calibration(target, probabilities, bin_count=2)

    assert metrics["bins"] == 2
    assert abs(metrics["expected_calibration_error"] - 0.15) < 1e-12
    assert metrics["games"] == 4


def test_evaluate_calibration_by_group_marks_small_groups_insufficient():
    target = pd.Series([0, 1, 0, 1])
    probabilities = pd.Series([0.1, 0.9, 0.2, 0.8])
    groups = pd.Series(["early", "early", "late", "late"])

    results = evaluate_calibration_by_group(
        target, probabilities, groups, bin_count=2, minimum_games=3
    )

    assert results["early"]["status"] == "insufficient_sample"
    assert results["early"]["games"] == 2


def test_sigmoid_calibrated_model_returns_probability_pairs():
    from src.train_baseline_model import fit_calibrated_boosted_hybrid

    train = pd.DataFrame(
        {
            "target": [0, 1, 0, 1, 1, 0, 1, 0, 1, 0],
            "elo_delta": [-4, -2, -1, 1, 2, 3, 4, -3, 3, -4],
        }
    )
    test = pd.DataFrame({"target": [0, 1, 1, 0], "elo_delta": [-2, 1, 2, -1]})
    parameters = {
        "max_depth": 3,
        "learning_rate": 0.05,
        "max_iter": 20,
        "random_state": 42,
    }

    model, metrics = fit_calibrated_boosted_hybrid(
        train, test, ["elo_delta"], parameters, validation_fraction=0.2
    )

    assert isinstance(model, CalibratedProbabilityModel)
    probabilities = model.predict_proba(test[["elo_delta"]])
    assert probabilities.shape == (4, 2)
    assert np.all((probabilities >= 0) & (probabilities <= 1))
    assert set(metrics) == {"accuracy", "log_loss", "brier_score"}


def test_compare_calibration_methods_selects_from_validation_metrics():
    train = pd.DataFrame(
        {
            "target": [0, 1, 0, 1, 1, 0, 1, 0, 1, 0],
            "elo_delta": [-4, -2, -1, 1, 2, 3, 4, -3, 3, -4],
        }
    )
    test = pd.DataFrame({"target": [0, 1, 1, 0], "elo_delta": [-2, 1, 2, -1]})
    parameters = {
        "max_depth": 3,
        "learning_rate": 0.05,
        "max_iter": 20,
        "random_state": 42,
    }

    model, metrics, selection = compare_calibration_methods(
        train, test, ["elo_delta"], parameters, validation_fraction=0.2
    )

    assert isinstance(model, CalibratedProbabilityModel)
    assert selection["selected_method"] in {"sigmoid", "isotonic"}
    assert set(selection["validation_metrics"]) == {"sigmoid", "isotonic"}
    assert set(metrics) == {"accuracy", "log_loss", "brier_score"}
