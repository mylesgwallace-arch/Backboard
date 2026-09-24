"""Era translation and the what-if swap, on small synthetic league tables."""

import numpy as np
import pandas as pd
import pytest

from src import era_swap as es

TEAM, OTHER = 1610612741, 1610612738


def _league(season, scale=1.0, three_scale=1.0, n_players=24):
    """A synthetic league-season: two teams, ``n_players`` players with spread-out lines."""
    rng = np.random.default_rng(season)
    rows = []
    for index in range(n_players):
        team = TEAM if index % 2 == 0 else OTHER
        minutes = 600 + 100 * index
        fga = rng.uniform(8, 20) * minutes / 36 * scale
        fta = rng.uniform(1, 6) * minutes / 36
        tpa = rng.uniform(0, 6) * minutes / 36 * three_scale
        rows.append({
            "personId": 1000 + index, "teamId": team, "season": season,
            "firstName": "P", "lastName": str(index), "games": 70, "minutes": minutes,
            "fga": fga, "fgm": fga * rng.uniform(0.42, 0.52), "tpa": tpa, "tpm": tpa * rng.uniform(0.3, 0.4),
            "fta": fta, "ftm": fta * rng.uniform(0.7, 0.9),
            "orb": rng.uniform(0.5, 3) * minutes / 36, "drb": rng.uniform(2, 8) * minutes / 36,
            "ast": rng.uniform(1, 8) * minutes / 36, "stl": rng.uniform(0.5, 2) * minutes / 36,
            "blk": rng.uniform(0.1, 2) * minutes / 36, "tov": rng.uniform(1, 3) * minutes / 36,
            "pf": rng.uniform(1.5, 3.5) * minutes / 36,
        })
    frame = pd.DataFrame(rows)
    frame["pts"] = 2 * frame["fgm"] + frame["tpm"] + frame["ftm"]
    team_minutes = frame.groupby("teamId")["minutes"].transform("sum")
    frame["team_minutes"] = team_minutes
    frame["possessions"] = team_minutes / 5.0 / 48.0 * 96.0
    return es.add_rates(frame)


@pytest.fixture
def league():
    players = pd.concat([_league(1992), _league(2015, scale=1.05, three_scale=2.8)], ignore_index=True)
    return players, es.league_context(players)


def test_translating_into_the_same_season_is_the_identity(league):
    players, context = league
    row = players.iloc[3].to_dict()
    for method in ("zscore", "ratio"):
        same = es.translate_rates(row, 2015, 2015, context, method)
        for stat, value in same.items():
            assert value == pytest.approx(row[stat], rel=1e-12), (method, stat)


def test_round_trip_between_eras_is_exact(league):
    players, context = league
    row = players[players["season"] == 2015].iloc[5].to_dict()
    there = es.translate_rates(row, 2015, 1992, context, "zscore")
    back = es.translate_rates({**row, **there}, 1992, 2015, context, "zscore")
    for stat in ("tpa_100", "ast_100", "ts", "stl_100"):
        assert back[stat] == pytest.approx(row[stat], rel=1e-9)


def test_zscore_keeps_a_players_standing_within_the_league(league):
    players, context = league
    row = players[players["season"] == 2015].sort_values("tpa_100").iloc[-1].to_dict()
    translated = es.translate_rates(row, 2015, 1992, context, "zscore")
    z_origin = (row["tpa_100"] - context.loc[2015, "mu_tpa_100"]) / context.loc[2015, "sd_tpa_100"]
    z_target = (translated["tpa_100"] - context.loc[1992, "mu_tpa_100"]) / context.loc[1992, "sd_tpa_100"]
    assert z_target == pytest.approx(z_origin)
    # A heavy 2015 three-point shooter takes fewer threes in the low-volume 1992 league.
    assert translated["tpa_100"] < row["tpa_100"]
    # Points stay consistent with translated efficiency and volume.
    assert translated["pts_100"] == pytest.approx(2 * translated["ts"] * translated["tsa_100"])


def test_league_average_player_has_a_zero_feature_vector(league):
    _, context = league
    lg = context.loc[1992]
    average = {f"{stat}_100": lg[f"lg_{stat}_100"] for stat in es.RATE_STATS}
    average["ts"] = lg["lg_ts"]
    assert np.allclose(es.player_features(average, 1992, context), 0.0)


def _model(players, context):
    return {
        "player_seasons": players,
        "context": context,
        "coefficients": np.array([0.0] + [1.0] * len(es.FEATURES)),
        "combined": es.combined_index(players),
        "team_seasons": pd.DataFrame([{"teamId": TEAM, "season": 1992, "net_rating": 5.0,
                                       "pace": 96.0, "wins": 55, "games": 82}]),
        "reports": {"roster_change_calibration": {"realization_factor": 0.2,
                                                  "realization_factor_80pct_interval": [0.15, 0.25]},
                    "probit_sigma": {"value": 12.5}},
    }


def test_swapping_a_player_for_the_same_player_changes_nothing(league):
    players, context = league
    model = _model(players, context)
    person = int(players[(players["season"] == 1992) & (players["teamId"] == TEAM)].iloc[0]["personId"])
    effect = es.swap_effect(model, TEAM, 1992, person, person, 1992)
    assert effect["delta_net_rating_full_transfer"] == pytest.approx(0.0, abs=1e-9)
    assert effect["delta_net_rating"] == pytest.approx(0.0, abs=1e-9)


def test_swap_scales_the_full_transfer_by_the_realization_factor(league):
    players, context = league
    model = _model(players, context)
    out_person = int(players[(players["season"] == 1992) & (players["teamId"] == TEAM)].iloc[0]["personId"])
    in_person = int(players[players["season"] == 2015].iloc[7]["personId"])
    effect = es.swap_effect(model, TEAM, 1992, out_person, in_person, 2015)
    assert effect["delta_net_rating"] == pytest.approx(0.2 * effect["delta_net_rating_full_transfer"])
    low, high = effect["delta_net_rating_80pct"]
    assert low <= effect["delta_net_rating"] <= high
    assert effect["out_player"]["team_minutes_share"] == pytest.approx(
        players.loc[players["personId"] == out_person, "minutes"].iloc[0]
        / players[(players["season"] == 1992) & (players["teamId"] == TEAM)]["minutes"].sum())


def test_unsupported_seasons_and_rosters_are_refused(league):
    players, context = league
    model = _model(players, context)
    with pytest.raises(ValueError, match="1985-86"):
        es.swap_effect(model, TEAM, 1980, 1000, 1001, 2015)
    with pytest.raises(ValueError, match="did not play"):
        es.swap_effect(model, TEAM, 1992, 1001, 1002, 2015)  # 1001 plays for OTHER


def test_probability_shift_is_monotonic_and_symmetric():
    base = np.array([0.2, 0.5, 0.8])
    assert np.allclose(es.shift_probability(base, 0.0, 12.0), base)
    up = es.shift_probability(base, 3.0, 12.0)
    down = es.shift_probability(base, -3.0, 12.0)
    assert np.all(up > base) and np.all(down < base)
    assert es.shift_probability(0.5, 3.0, 12.0) == pytest.approx(1 - es.shift_probability(0.5, -3.0, 12.0))


def test_only_the_host_teams_games_move():
    games = pd.DataFrame({
        "season": [1992] * 3, "homeTeamId": [TEAM, OTHER, 3], "awayTeamId": [OTHER, TEAM, 4],
        "home_win_probability": [0.6, 0.6, 0.6],
    })
    shifted = es.shifted_season_probabilities(games, 1992, TEAM, 2.0, 12.0)
    values = shifted["home_win_probability"].to_numpy()
    assert values[0] > 0.6       # host at home: better
    assert values[1] < 0.6       # host away: home side worse
    assert values[2] == pytest.approx(0.6)


def test_bubble_season_postseason_is_not_simulated():
    postseason, note = es._postseason_format(2019)
    assert postseason is None
    assert "bubble" in note
