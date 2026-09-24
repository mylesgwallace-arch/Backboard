"""Margin/total model: pregame inputs, baselines as candidates, serving path."""

import numpy as np
import pandas as pd
import pytest

from src import margin_model as mm
from src.forward_projection import ModelInputs
from src.train_baseline_model import add_elo_rating_deltas

ELO = {"initial_rating": 1500.0, "k_factor": 20.0, "home_advantage": 65.0}


def _games(n=40):
    rng = np.random.default_rng(0)
    teams = [1610612737, 1610612738, 1610612739, 1610612740]
    rows = []
    for index in range(n):
        home, away = rng.choice(teams, size=2, replace=False)
        rows.append({
            "gameId": index + 1,
            "gameDateTimeEst": pd.Timestamp("2024-10-22") + pd.DateOffset(days=index),
            "homeTeamId": int(home),
            "awayTeamId": int(away),
            "target": int(rng.random() < 0.6),
        })
    return pd.DataFrame(rows)


def test_fast_elo_deltas_equal_production_helper():
    games = _games()
    fast = mm.pregame_elo_deltas(games, ELO)
    slow = add_elo_rating_deltas(games, **{
        "initial_rating": ELO["initial_rating"],
        "k_factor": ELO["k_factor"],
        "home_advantage": ELO["home_advantage"],
    })["elo_delta"].to_numpy()
    np.testing.assert_allclose(fast, slow)


def test_deterministic_candidates():
    frame = pd.DataFrame({
        "home_points_for": [110.0], "home_points_against": [100.0],
        "away_points_for": [105.0], "away_points_against": [115.0],
        "elo_delta": [100.0],
    })
    assert mm.RecentFormTotal().fit(frame).predict(frame)[0] == pytest.approx(215.0)

    train = pd.DataFrame({"elo_delta": [0.0, 100.0, 200.0]})
    model = mm.EloLinearMargin().fit(train, np.array([2.0, 5.0, 8.0]))
    assert model.predict(frame)[0] == pytest.approx(5.0)


def test_candidate_sets_include_the_simple_baselines():
    assert "elo_linear" in mm._candidates(["elo_delta"], "margin")
    assert "recent_form_average" in mm._candidates(mm.TOTAL_FEATURES, "total")
    assert "recent_form_average" not in mm._candidates(["x"], "margin")


def test_selection_uses_validation_slice_and_reports_honest_interval():
    rng = np.random.default_rng(1)
    n = 400
    frame = pd.DataFrame({
        "elo_delta": rng.normal(0, 100, n),
        "noise": rng.normal(0, 1, n),
    })
    frame["margin"] = 0.05 * frame["elo_delta"] + rng.normal(0, 5, n)
    train, test = frame.iloc[:320], frame.iloc[320:]
    baselines = {"baseline_zero": np.zeros(len(test))}
    model, cols, report = mm._select_and_evaluate(
        train, test, "margin", ["elo_delta", "noise"], baselines
    )
    assert report["selected_model"] in report["validation_mae_by_candidate"]
    assert report["holdout"]["selected_model"]["mae"] < report["holdout"]["baseline_zero"]["mae"]
    interval = report["interval_80"]
    assert interval["residual_low"] < 0 < interval["residual_high"]
    assert 0.0 <= interval["holdout_coverage"] <= 1.0


class _Constant:
    def __init__(self, value):
        self.value = value

    def predict(self, frame):
        return np.full(len(frame), self.value)


def test_predict_margin_scores_add_up_and_use_pregame_snapshots():
    features = pd.DataFrame([
        {"gameId": 1, "gameDateTimeEst": pd.Timestamp("2025-01-01"), "teamId": 1,
         "teamScore_rolling_10": 110.0, "opponentScore_rolling_10": 104.0, "x": 3.0},
        {"gameId": 1, "gameDateTimeEst": pd.Timestamp("2025-01-01"), "teamId": 2,
         "teamScore_rolling_10": 108.0, "opponentScore_rolling_10": 109.0, "x": 1.0},
        # A later row that must be ignored for a 2025-01-02 cutoff.
        {"gameId": 2, "gameDateTimeEst": pd.Timestamp("2025-01-05"), "teamId": 1,
         "teamScore_rolling_10": 999.0, "opponentScore_rolling_10": 0.0, "x": 99.0},
    ])
    games = pd.DataFrame([{"gameId": 1, "gameDateTimeEst": pd.Timestamp("2025-01-01"),
                           "homeTeamId": 1, "awayTeamId": 2, "target": 1}])
    inputs = ModelInputs(features=features, games=games, model=None,
                         predictors=["x", "elo_delta"], elo_config=ELO)
    bundle = {
        "margin_model": _Constant(6.0),
        "margin_predictors": ["x", "elo_delta"],
        "total_model": _Constant(220.0),
        "total_predictors": mm.TOTAL_FEATURES,
        "margin_interval": {"residual_low": -15.0, "residual_high": 14.0},
        "total_interval": {"residual_low": -20.0, "residual_high": 21.0},
    }
    result = mm.predict_margin(1, 2, inputs, bundle, game_date=pd.Timestamp("2025-01-02"))
    assert result["predicted_home_margin"] == 6.0
    assert result["predicted_home_points"] + result["predicted_away_points"] == pytest.approx(220.0)
    assert result["predicted_home_points"] - result["predicted_away_points"] == pytest.approx(6.0)
    assert result["margin_interval_80"] == [-9.0, 20.0]
    assert result["feature_snapshot_date"] == {"home": "2025-01-01", "away": "2025-01-01"}

    with pytest.raises(ValueError):
        mm.predict_margin(1, 3, inputs, bundle, game_date=pd.Timestamp("2025-01-02"))
