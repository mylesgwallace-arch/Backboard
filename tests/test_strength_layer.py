"""Projection strength layer: leakage rules, roster bookkeeping, fitting, wiring."""

import numpy as np
import pandas as pd
import pytest

from src import strength_layer as sl
from src.forward_projection import project_from_date
from tests.test_forward_projection import EAST_A, EAST_B, TEAMS, WEST_A, WEST_B, _synthetic_inputs


def _player_seasons():
    """Season 2023 (the 'previous' season for 2024): two players per team."""
    rows = []
    for index, team in enumerate(TEAMS):
        for slot in range(2):
            rows.append({
                "personId": 100 + 10 * index + slot, "season": 2023,
                "minutes": 1000.0, "plus_minus": 100.0 * (index - 1.5), "games": 40,
                "last_team": team, "last_game": pd.Timestamp("2024-04-10"),
            })
    # A fringe player sets replacement level at -10 per 48.
    rows.append({"personId": 999, "season": 2023, "minutes": 48.0, "plus_minus": -10.0,
                 "games": 5, "last_team": EAST_A, "last_game": pd.Timestamp("2024-01-10")})
    return sl.add_player_values(pd.DataFrame(rows), shrink=0.0)


def _transactions(rows):
    frame = pd.DataFrame(rows, columns=["event_timestamp", "team_id", "person_id", "change_type"])
    frame["event_timestamp"] = pd.to_datetime(frame["event_timestamp"], utc=True)
    return frame


def test_roster_starts_from_last_team_and_applies_only_moves_before_cutoff():
    players = _player_seasons()
    moves = _transactions([
        ("2024-07-01", WEST_B, 100, "add"),      # EAST_A's player signs with WEST_B
        ("2024-07-02", EAST_B, 110, "remove"),   # EAST_B waives one of its players
        ("2024-10-30", EAST_A, 130, "add"),      # after the cutoff: ignored
    ])
    rosters = sl.opening_rosters(2024, "2024-10-22", players, moves, TEAMS)
    members = {team: {p["person_id"] for p in roster} for team, roster in rosters.items()}
    assert 100 in members[WEST_B] and 100 not in members[EAST_A]
    assert 110 not in members[EAST_B]
    assert 130 in members[WEST_B] and 130 not in members[EAST_A]


def test_roster_value_fills_uncovered_minutes_at_replacement_level():
    players = _player_seasons()
    values = sl.roster_values(2024, "2024-10-22", players, _transactions([]), TEAMS)
    # The minute pool is total minutes / teams; a two-player roster that
    # covers less than the pool gets the rest at replacement level.
    pool = players["minutes"].sum() / 4
    replacement = sl.replacement_value(players, 2023)
    assert replacement == pytest.approx(-10.0)
    west_b = players[players["last_team"] == WEST_B]
    expected = ((west_b["minutes"] * west_b["value"]).sum()
                + (pool - west_b["minutes"].sum()) * replacement) / pool
    assert values[WEST_B] == pytest.approx(expected)
    # Better players -> higher roster value (ordering preserved).
    assert values[WEST_B] > values[WEST_A] > values[EAST_B]


def test_margin_signal_ignores_games_on_or_after_the_cutoff():
    inputs = _synthetic_inputs()
    inputs.features["teamScore"] = 100.0
    inputs.features["opponentScore"] = 95.0 + (inputs.features["teamId"] % 3)
    before = sl.margin_to_date(inputs.features, 2024, "2024-11-01", TEAMS)
    later = inputs.features["gameDateTimeEst"] >= pd.Timestamp("2024-11-01")
    inputs.features.loc[later, "teamScore"] = 500.0
    after = sl.margin_to_date(inputs.features, 2024, "2024-11-01", TEAMS)
    assert before == after
    assert sl.margin_to_date(inputs.features, 2024, "2024-10-01", TEAMS) == {t: 0.0 for t in TEAMS}


def test_offset_logistic_recovers_a_known_weight():
    rng = np.random.default_rng(0)
    x = rng.normal(size=(20000, 1))
    offset = rng.normal(scale=0.5, size=20000)
    y = rng.random(20000) < 1 / (1 + np.exp(-(offset + 0.8 * x[:, 0])))
    weights = sl.fit_offset_logistic(x, offset, y, ridge=0.0)
    assert weights[0] == pytest.approx(0.8, abs=0.05)


def test_zero_weights_leave_probabilities_unchanged_and_weights_interpolate():
    probabilities = np.array([0.2, 0.5, 0.9])
    np.testing.assert_allclose(sl.apply_layer(probabilities, np.ones((3, 2)), [0.0, 0.0]),
                               probabilities)
    layer = {"signals": ["model", "margin"],
             "weights_by_checkpoint": {"0.00": [0.0, 0.0], "0.50": [-0.5, 0.1], "1.00": [-0.5, 0.1]}}
    np.testing.assert_allclose(sl.interpolate_weights(layer, 0.25), [-0.25, 0.05])


def test_projection_applies_a_layer_only_when_asked(monkeypatch):
    inputs = _synthetic_inputs()
    signals = {"model": {team: 0.0 for team in TEAMS},
               "margin": {EAST_A: 5.0, EAST_B: -5.0, WEST_A: 0.0, WEST_B: 0.0}}
    monkeypatch.setattr(sl, "runtime_signals", lambda *args, **kwargs: signals)
    layer = {"signals": ["model", "margin"],
             "weights_by_checkpoint": {"0.00": [0.0, 0.5], "1.00": [0.0, 0.5]}}
    kwargs = dict(n_simulations=400, random_state=1, strength_sd=0.0)
    frozen = project_from_date(2024, "2024-10-25", inputs, strength_layer=False, **kwargs)
    layered = project_from_date(2024, "2024-10-25", inputs, strength_layer=layer, **kwargs)
    assert frozen["strength_layer"] == {"applied": False}
    assert layered["strength_layer"]["applied"] is True
    wins = lambda projection: {row["teamId"]: row["mean_wins"] for row in projection["projected_standings"]}
    assert wins(layered)[EAST_A] > wins(frozen)[EAST_A]
    assert wins(layered)[EAST_B] < wins(frozen)[EAST_B]
