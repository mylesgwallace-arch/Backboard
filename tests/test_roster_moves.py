"""Hypothetical roster moves on the strength layer (synthetic data, no database)."""

import pandas as pd
import pytest

from src import roster_moves as rm
from src import strength_layer as sl
from tests.test_forward_projection import EAST_A, EAST_B, TEAMS, WEST_A, WEST_B, _synthetic_inputs
from tests.test_strength_layer import _player_seasons, _transactions

LAYER = {"signals": ["model", "margin", "roster"], "shrink_minutes": 0.0,
         "weights_by_checkpoint": {"0.00": [0.0, 0.0, 0.3], "1.00": [0.0, 0.0, 0.3]}}
STAR = 130  # a WEST_B player with the best value in _player_seasons


@pytest.fixture
def synthetic(monkeypatch):
    inputs = _synthetic_inputs()
    inputs.features["teamScore"] = 100.0
    inputs.features["opponentScore"] = 100.0
    players = _player_seasons()
    feed = _transactions([])
    monkeypatch.setattr(rm, "_feed", lambda: feed)
    monkeypatch.setattr(rm, "_player_seasons", lambda season, layer: players)
    monkeypatch.setattr(sl, "load_player_seasons", lambda seasons, db_path=None: players)
    sl._RUNTIME_CACHE.clear()
    yield inputs
    sl._RUNTIME_CACHE.clear()


def test_move_events_remove_from_origin_and_add_to_destination():
    events = rm.move_events([{"person_id": STAR, "to_team_id": EAST_A}], "2024-10-22",
                            {STAR: WEST_B})
    assert list(zip(events["change_type"], events["team_id"])) == [("remove", WEST_B), ("add", EAST_A)]
    assert (events["event_timestamp"] < pd.Timestamp("2024-10-22", tz="UTC")).all()
    # A free agent (no current team) only gets an add.
    assert list(rm.move_events([{"person_id": 5, "to_team_id": EAST_A}], "2024-10-22", {})
                ["change_type"]) == ["add"]


def test_moving_a_good_player_helps_the_destination_and_hurts_the_origin(synthetic):
    result = rm.project_with_moves(
        2024, "2024-10-26", synthetic, [{"person_id": STAR, "to_team_id": EAST_B}],
        n_simulations=400, schedule=synthetic.games, strength_layer=LAYER,
    )
    effects = {row["teamId"]: row for row in result["team_effects"]}
    assert set(effects) == {WEST_B, EAST_B}
    assert effects[EAST_B]["mean_wins_change"] > 0 > effects[WEST_B]["mean_wins_change"]
    assert effects[EAST_B]["roster_value_after"] > effects[EAST_B]["roster_value_before"]
    assert result["moves"][0]["from_team_id"] == WEST_B
    assert result["roster_signal_weight"] == pytest.approx(0.3)


def test_moves_need_a_layer_with_a_roster_signal(synthetic):
    layer = {**LAYER, "signals": ["model", "margin"],
             "weights_by_checkpoint": {"0.00": [0, 0], "1.00": [0, 0]}}
    with pytest.raises(ValueError, match="roster signal"):
        rm.project_with_moves(2024, "2024-10-22", synthetic,
                              [{"person_id": STAR, "to_team_id": EAST_A}],
                              schedule=synthetic.games, strength_layer=layer)
    with pytest.raises(ValueError, match="no games"):
        rm.project_with_moves(2024, "2024-10-22", synthetic,
                              [{"person_id": STAR, "to_team_id": 123}],
                              schedule=synthetic.games, strength_layer=LAYER)
