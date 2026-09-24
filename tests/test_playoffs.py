"""Series math, play-in enumeration, exact bracket odds and season title odds."""

import itertools

import numpy as np
import pandas as pd
import pytest

from src import playoffs as po
from src.forward_projection import ModelInputs
from src.simulate_season import (
    EASTERN_CONFERENCE_TEAM_IDS,
    WESTERN_CONFERENCE_TEAM_IDS,
    add_season_labels,
)

EAST = sorted(EASTERN_CONFERENCE_TEAM_IDS)[:10]
WEST = sorted(WESTERN_CONFERENCE_TEAM_IDS)[:10]
ELO = {"initial_rating": 1500.0, "k_factor": 20.0, "home_advantage": 65.0}


def _flat_matrix(teams, p_home=0.5):
    return {(a, b): p_home for a in teams for b in teams if a != b}


def test_series_probability_known_values():
    assert po.series_win_probability(0.5, 0.5) == pytest.approx(0.5)
    assert po.series_win_probability(1.0, 1.0) == pytest.approx(1.0)
    assert po.series_win_probability(0.0, 0.0) == pytest.approx(0.0)
    # Best of 7 at a constant 60%: sum_k C(3+k, k) 0.6^4 0.4^k = 0.710208.
    assert po.series_win_probability(0.6, 0.6) == pytest.approx(0.710208)
    # Best of 5 at a constant 60%: 0.6^3 (1 + 3*0.4 + 6*0.16) = 0.68256.
    assert po.series_win_probability(0.6, 0.6, po.PATTERN_2_2_1) == pytest.approx(0.68256)


def test_home_court_matters_in_the_expected_direction():
    with_home = po.series_win_probability(0.6, 0.45, po.PATTERN_2_2_1_1_1)
    # Same team, but pretend it had the road games' pattern (swap venues).
    without_home = 1 - po.series_win_probability(0.55, 0.40, po.PATTERN_2_2_1_1_1)
    assert with_home > without_home


def test_series_pattern_by_era():
    assert po.series_pattern(2024, "first_round", 7) == po.PATTERN_2_2_1_1_1
    assert po.series_pattern(1995, "first_round", 5) == po.PATTERN_2_2_1
    assert po.series_pattern(1992, "finals", 7) == po.PATTERN_2_3_2
    assert po.series_pattern(2014, "finals", 7) == po.PATTERN_2_2_1_1_1


def test_play_in_outcomes_are_a_complete_distribution():
    seeds = {7: 1, 8: 2, 9: 3, 10: 4}
    matrix = {(a, b): 0.5 + 0.05 * (b - a) for a in range(1, 5) for b in range(1, 5) if a != b}
    outcomes = po.play_in_outcomes(seeds, matrix)
    assert len(outcomes) == 8
    assert sum(probability for probability, _, _ in outcomes) == pytest.approx(1.0)
    for _, seed7, seed8 in outcomes:
        assert seed7 in (1, 2)          # the 7 seed comes from the 7 v 8 game
        assert seed8 != seed7
    # The 10 seed can only become the 8 seed.
    assert all(seed7 != 4 for _, seed7, _ in outcomes)


def test_exact_bracket_totals_and_a_dominant_team():
    fields = {
        "East": {seed + 1: team for seed, team in enumerate(EAST)},
        "West": {seed + 1: team for seed, team in enumerate(WEST)},
    }
    teams = EAST + WEST
    matrix = _flat_matrix(teams)
    record = {team: 41 for team in teams}
    odds = po.exact_playoff_odds(fields, record, matrix, 2024)
    total = lambda key: sum(row[key] for row in odds.values())  # noqa: E731
    assert total("made_playoffs") == pytest.approx(16)
    assert total("won_first_round") == pytest.approx(8)
    assert total("won_conf_semifinals") == pytest.approx(4)
    assert total("won_conf_finals") == pytest.approx(2)
    assert total("champion") == pytest.approx(1)
    # Seeds 1-6 always make it; each play-in team does not.
    assert odds[EAST[0]]["made_playoffs"] == pytest.approx(1)
    assert odds[EAST[9]]["made_playoffs"] < 1

    dominant = WEST[3]
    for other in teams:
        if other != dominant:
            matrix[(dominant, other)] = 1.0
            matrix[(other, dominant)] = 0.0
    odds = po.exact_playoff_odds(fields, record, matrix, 2024)
    assert odds[dominant]["champion"] == pytest.approx(1.0)


def test_no_play_in_field_uses_eight_seeds():
    fields = {
        "East": {seed + 1: team for seed, team in enumerate(EAST[:8])},
        "West": {seed + 1: team for seed, team in enumerate(WEST[:8])},
    }
    teams = EAST[:8] + WEST[:8]
    odds = po.exact_playoff_odds(fields, {t: 41 for t in teams}, _flat_matrix(teams), 1995)
    assert all(row["made_playoffs"] == pytest.approx(1) for row in odds.values())
    assert sum(row["champion"] for row in odds.values()) == pytest.approx(1)


def _postseason_games():
    """A tiny East-only-play-in postseason in the shape the database stores."""
    e = EAST
    rows = []
    game_id = itertools.count(1)
    day = itertools.count(0)

    def add(game_type, home, away, winner):
        date = pd.Timestamp("2025-04-15") + pd.DateOffset(days=next(day))
        rows.append({"gameId": next(game_id), "gameDateTimeEst": date, "gameType": game_type,
                     "gameLabel": None, "hometeamId": home, "awayteamId": away,
                     "homeScore": 100, "awayScore": 90, "winner": winner})

    # Play-in: 7 (e[6]) hosts 8 (e[7]) and loses; 9 (e[8]) hosts 10 (e[9]) and wins;
    # the 7/8 loser e[6] hosts e[8] and wins -> playoff 7 = e[7], playoff 8 = e[6].
    add("Play-in Tournament", e[6], e[7], e[7])
    add("Play-in Tournament", e[8], e[9], e[8])
    add("Play-in Tournament", e[6], e[8], e[6])

    def series(high, low, winner):
        for _ in range(4):
            add("Playoffs", high, low, winner)

    series(e[0], e[6], e[0])   # 1 v 8
    series(e[1], e[7], e[1])   # 2 v 7
    series(e[2], e[5], e[5])   # 3 v 6 (6 wins)
    series(e[3], e[4], e[3])   # 4 v 5
    series(e[0], e[3], e[0])   # top semifinal: 1/8 winner v 4/5 winner
    series(e[1], e[5], e[1])   # bottom semifinal
    return pd.DataFrame(rows)


def test_structure_recovery_needs_both_conferences():
    games = _postseason_games()
    field = po._field_from_bracket_structure(games)
    # West has no games in this fixture, so the structure is incomplete there.
    assert field is None

    east_series = po.actual_series(2024, games=games)
    rounds = [item["round"] for item in east_series]
    assert rounds.count("first_round") == 4 and rounds.count("conf_semifinals") == 2


def test_structure_recovery_for_one_conference(monkeypatch):
    games = _postseason_games()
    real = po.actual_series

    def east_only(season, db_path=None, games=None):
        return real(season, games=games)

    monkeypatch.setattr(po, "actual_series", east_only)
    # Evaluate the East half directly by pretending the West has the same shape.
    west_map = dict(zip(EAST, WEST))
    west_games = games.copy()
    for column in ("hometeamId", "awayteamId", "winner"):
        west_games[column] = west_games[column].map(west_map)
    west_games["gameId"] += 1000
    both = pd.concat([games, west_games], ignore_index=True)
    field = po._field_from_bracket_structure(both)
    e = EAST
    assert field["East"] == {1: e[0], 2: e[1], 3: e[2], 4: e[3], 5: e[4], 6: e[5],
                             7: e[6], 8: e[7], 9: e[8], 10: e[9]}
    assert field["West"][1] == WEST[0]


class StubModel:
    def predict_proba(self, frame):
        logit = frame["teamScore_rolling_10"].to_numpy() / 5.0 + frame["elo_delta"].to_numpy() / 400.0
        home = 1.0 / (1.0 + np.exp(-logit))
        return np.column_stack([1 - home, home])


def _league_inputs(season_start):
    teams = EAST + WEST
    games, features = [], []
    start = pd.Timestamp(f"{season_start}-10-22 19:00")
    for index, (home, away) in enumerate(itertools.combinations(teams, 2)):
        date = start + pd.DateOffset(days=index // 5)
        target = int((home * 7 + away) % 5 < 3)
        games.append({"gameId": index + 1, "gameDateTimeEst": date,
                      "homeTeamId": home, "awayTeamId": away, "target": target})
        for team, is_home in ((home, 1), (away, 0)):
            features.append({"gameId": index + 1, "gameDateTimeEst": date, "teamId": team,
                             "home": is_home, "teamScore_rolling_10": float(team % 11)})
    return ModelInputs(
        features=pd.DataFrame(features),
        games=add_season_labels(pd.DataFrame(games)),
        model=StubModel(),
        predictors=["teamScore_rolling_10", "elo_delta"],
        elo_config=ELO,
    )


@pytest.mark.parametrize("season_start,play_in", [(2024, True), (2015, False)])
def test_season_playoff_odds_totals(season_start, play_in):
    inputs = _league_inputs(season_start)
    as_of = inputs.games["gameDateTimeEst"].iloc[len(inputs.games) // 2].normalize()
    result = po.season_playoff_odds(season_start, as_of, inputs, n_simulations=60, random_state=5)
    rows = result["teams"]
    assert result["play_in"] is play_in
    assert sum(row["p_champion"] for row in rows) == pytest.approx(1.0)
    assert sum(row["p_made_playoffs"] for row in rows) == pytest.approx(16.0)
    assert sum(row["p_won_conf_finals"] for row in rows) == pytest.approx(2.0)
    if play_in:
        assert sum(row["p_play_in"] for row in rows) == pytest.approx(8.0)
    for row in rows:
        assert row["p_champion"] <= row["p_won_conf_finals"] <= row["p_won_conf_semifinals"]
        assert row["p_won_conf_semifinals"] <= row["p_won_first_round"] <= row["p_made_playoffs"]
