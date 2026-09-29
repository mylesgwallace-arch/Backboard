"""Matchup explanations (group ablation) and the reliability table."""

import numpy as np
import pandas as pd
import pytest

from src.calibration_report import reliability_table
from src.explain import explain_matchup
from src.forward_projection import frozen_matchup_probabilities
from tests.test_forward_projection import EAST_A, WEST_B, _synthetic_inputs


def test_explained_probability_is_the_production_probability():
    inputs = _synthetic_inputs()
    result = explain_matchup(inputs, EAST_A, WEST_B, "2024-11-05")
    reference = frozen_matchup_probabilities(
        pd.DataFrame({"homeTeamId": [EAST_A], "awayTeamId": [WEST_B]}), "2024-11-05", inputs)
    assert result["home_win_probability"] == pytest.approx(reference["home_win_probability"][0])


def test_group_contributions_follow_the_inputs():
    inputs = _synthetic_inputs()
    # Stub model: logit = teamScore delta / 10 + elo_delta / 400. Make the home
    # team clearly better on recent scoring.
    later = inputs.features["teamId"] == EAST_A
    inputs.features.loc[later, "teamScore_rolling_10"] += 20.0
    result = explain_matchup(inputs, EAST_A, WEST_B, "2024-11-05")
    by_group = {row["group"]: row for row in result["contributions"]}
    margin = by_group["Recent point margin (last 10 games)"]
    assert margin["contribution"] > 0.05 and margin["favors"] == "home"
    assert margin["probability_if_even"] == pytest.approx(
        result["home_win_probability"] - margin["contribution"])
    assert "Elo rating gap" in by_group
    # Contributions are sorted by size.
    sizes = [abs(row["contribution"]) for row in result["contributions"]]
    assert sizes == sorted(sizes, reverse=True)
    # Even teams leave only home court: above 50% for the home side.
    assert 0.5 < result["home_court_only_probability"] < result["home_win_probability"]


def test_unknown_team_is_rejected():
    with pytest.raises(ValueError, match="999"):
        explain_matchup(_synthetic_inputs(), EAST_A, 999, "2024-11-05")


def test_reliability_table_bins_and_ece():
    rng = np.random.default_rng(1)
    p = rng.uniform(0.05, 0.95, 20000)
    y = rng.random(20000) < p                       # perfectly calibrated
    table = reliability_table(p, y)
    assert table["games"] == 20000
    assert table["expected_calibration_error"] < 0.02
    for row in table["bins"]:
        low, high = row["observed_90pct_interval"]
        assert low <= row["observed_rate"] <= high
        assert row["bin"][0] <= row["mean_predicted"] <= row["bin"][1]
    overconfident = reliability_table(p, rng.random(20000) < 0.5 + 0.5 * (p - 0.5))
    assert overconfident["expected_calibration_error"] > 0.08
