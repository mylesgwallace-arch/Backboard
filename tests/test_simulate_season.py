"""Focused regression coverage for the Monte Carlo season simulator."""

import json

import numpy as np
import pandas as pd

from src.simulate_season import (
    DEFAULT_SIMULATIONS,
    EASTERN_CONFERENCE_TEAM_IDS,
    WESTERN_CONFERENCE_TEAM_IDS,
    _conference_rank_matrix,
    add_season_labels,
    actual_season_standings,
    build_league_summary,
    build_seedings_table,
    conference_of,
    load_pregame_probabilities,
    project_season,
    simulate_season,
    summarize_team_wins,
    validate_season,
    validate_seasons,
    write_metrics,
)


def _synthetic_probabilities():
    """Six teams, two conferences, a small deterministic schedule."""
    return pd.DataFrame(
        [
            {
                "gameId": 1,
                "gameDateTimeEst": "2025-10-22 19:00:00",
                "season": 2025,
                "homeTeamId": 1610612738,  # East
                "awayTeamId": 1610612747,  # West
                "target": 1,
                "elo_probability": 0.5,
                "boosted_probability": 0.5,
                "home_win_probability": 0.5,
            },
            {
                "gameId": 2,
                "gameDateTimeEst": "2025-10-24 19:00:00",
                "season": 2025,
                "homeTeamId": 1610612738,
                "awayTeamId": 1610612760,  # West
                "target": 1,
                "elo_probability": 0.8,
                "boosted_probability": 0.8,
                "home_win_probability": 0.8,
            },
            {
                "gameId": 3,
                "gameDateTimeEst": "2025-10-26 19:00:00",
                "season": 2025,
                "homeTeamId": 1610612765,  # East
                "awayTeamId": 1610612760,
                "target": 0,
                "elo_probability": 0.3,
                "boosted_probability": 0.3,
                "home_win_probability": 0.3,
            },
            {
                "gameId": 4,
                "gameDateTimeEst": "2025-10-28 19:00:00",
                "season": 2025,
                "homeTeamId": 1610612760,
                "awayTeamId": 1610612738,
                "target": 1,
                "elo_probability": 0.4,
                "boosted_probability": 0.4,
                "home_win_probability": 0.4,
            },
        ]
    )


def test_add_season_labels_assigns_october_games_to_start_year():
    games = pd.DataFrame(
        [
            {"gameDateTimeEst": "2025-10-22 19:00:00"},
            {"gameDateTimeEst": "2026-04-10 19:00:00"},
            {"gameDateTimeEst": "2024-12-30 19:00:00"},
        ]
    )

    labeled = add_season_labels(games)

    assert labeled["season"].tolist() == [2025, 2025, 2024]


def test_conference_of_maps_current_franchises():
    assert conference_of(1610612738) == "East"
    assert conference_of(1610612747) == "West"
    assert conference_of(9999) == "Unknown"
    assert len(EASTERN_CONFERENCE_TEAM_IDS) == 15
    assert len(WESTERN_CONFERENCE_TEAM_IDS) == 15
    assert EASTERN_CONFERENCE_TEAM_IDS.isdisjoint(WESTERN_CONFERENCE_TEAM_IDS)


def test_simulate_season_returns_win_totals_within_schedule_bounds():
    probabilities = _synthetic_probabilities()

    wins, teams = simulate_season(
        probabilities, season=2025, n_simulations=200, random_state=1
    )

    assert wins.shape == (200, len(teams))
    assert set(teams) == {
        1610612738,
        1610612747,
        1610612760,
        1610612765,
    }
    # Team 2738 plays games 1, 2, 4 (three appearances); team 2760 plays
    # games 2, 3, 4 (three appearances); teams 2747 and 2765 play one game each.
    games_by_team = {1610612738: 3, 1610612747: 1, 1610612760: 3, 1610612765: 1}
    for index, team_id in enumerate(teams):
        assert wins[:, index].max() <= games_by_team[team_id]
    # Every simulation must produce a valid total win count per game.
    per_simulation_games = wins.sum(axis=1)
    assert (per_simulation_games == 4).all()


def test_simulate_season_is_reproducible_with_fixed_seed():
    probabilities = _synthetic_probabilities()

    wins_a, _ = simulate_season(probabilities, 2025, n_simulations=50, random_state=7)
    wins_b, _ = simulate_season(probabilities, 2025, n_simulations=50, random_state=7)

    np.testing.assert_array_equal(wins_a, wins_b)


def test_simulate_season_rejects_missing_season():
    probabilities = _synthetic_probabilities()

    try:
        simulate_season(probabilities, season=1999)
    except ValueError:
        pass
    else:
        raise AssertionError("Expected a ValueError for a season without games.")


def test_conference_rank_matrix_ranks_within_conference():
    wins = np.array(
        [
            [8, 6, 3, 7],  # teams 0..3 in simulation 0
            [5, 9, 2, 4],  # simulation 1
        ]
    )
    # Force two East and two West teams so each conference has a rank 1 and 2.
    teams = sorted(
        list(EASTERN_CONFERENCE_TEAM_IDS)[:2]
        + list(WESTERN_CONFERENCE_TEAM_IDS)[:2]
    )
    teams = teams[:4]
    # Rewrite wins rows to match the actual team order.
    wins = wins[:, :4]

    ranks = _conference_rank_matrix(wins, teams)

    assert ranks.shape == wins.shape
    for simulation in range(wins.shape[0]):
        for conference in ("East", "West"):
            conference_indices = [
                index
                for index, team_id in enumerate(teams)
                if conference_of(team_id) == conference
            ]
            conference_ranks = sorted(ranks[simulation, conference_indices])
            assert conference_ranks == [1, 2]


def test_summarize_team_wins_reports_distribution_and_playoff_odds():
    probabilities = _synthetic_probabilities()
    wins, teams = simulate_season(
        probabilities, season=2025, n_simulations=500, random_state=3
    )

    summary = summarize_team_wins(wins, teams, probabilities, 2025)

    assert len(summary) == len(teams)
    assert {"teamId", "mean_wins", "median_wins", "p5_wins", "p95_wins"} <= set(
        summary.columns
    )
    assert summary["mean_wins"].between(0, 2).all()
    assert (summary["p5_wins"] <= summary["median_wins"]).all()
    assert (summary["median_wins"] <= summary["p95_wins"]).all()


def test_direct_playoff_probability_is_bounded():
    probabilities = _synthetic_probabilities()
    wins, teams = simulate_season(
        probabilities, season=2025, n_simulations=100, random_state=5
    )

    summary = summarize_team_wins(wins, teams, probabilities, 2025)

    probabilities_by_team = dict(
        zip(summary["teamId"], summary["direct_playoff_probability"])
    )
    for team_id in teams:
        probability = probabilities_by_team[team_id]
        assert probability is None or 0.0 <= probability <= 1.0


def test_actual_season_standings_counts_wins_correctly():
    probabilities = _synthetic_probabilities()

    standings = actual_season_standings(probabilities, 2025)

    by_team = dict(zip(standings["teamId"], standings["wins"]))
    # Game 1: home East (2738) wins; Game 2: home East (2738) wins;
    # Game 3: away West (2760) wins; Game 4: home West (2760) wins.
    assert by_team[1610612738] == 2
    assert by_team[1610612760] == 2
    assert by_team[1610612747] == 0
    assert by_team[1610612765] == 0
    assert standings["conference"].isin(["East", "West"]).all()


def test_validate_season_reports_mae_correlation_and_playoff_overlap():
    probabilities = _synthetic_probabilities()

    validation = validate_season(
        probabilities, season=2025, n_simulations=300, random_state=9
    )

    assert validation["season"] == 2025
    assert validation["mean_absolute_error_wins"] >= 0.0
    assert -1.0 <= validation["projected_vs_actual_correlation"] <= 1.0
    assert 0.0 <= validation["playoff_field_overlap"] <= 1.0
    assert len(validation["actual_playoff_teams"]) == 4
    assert len(validation["projected_playoff_teams"]) == 4


def test_project_season_returns_standings_records():
    probabilities = _synthetic_probabilities()

    projection = project_season(
        probabilities, season=2025, n_simulations=200, random_state=11
    )

    assert projection["season"] == 2025
    assert projection["n_simulations"] == 200
    assert len(projection["projected_standings"]) == 4


def test_summarize_team_wins_adds_seed_probabilities_when_ranks_available():
    probabilities = _synthetic_probabilities()
    wins, teams = simulate_season(
        probabilities, season=2025, n_simulations=500, random_state=3
    )

    summary = summarize_team_wins(wins, teams, probabilities, 2025)

    seed_columns = [f"p_seed_{seed}" for seed in range(1, 7)]
    assert {"mean_conference_seed", "median_conference_seed"} <= set(summary.columns)
    assert set(seed_columns) <= set(summary.columns)
    assert "out_of_playoffs_probability" in summary.columns
    # With two East and two West teams, every team has a playoff seed
    # (ranks 1..2), so out-of-playoffs probability must be zero.
    assert (summary["out_of_playoffs_probability"] == 0.0).all()
    # Seed probabilities per team must sum to 1 (seeds 1..6 + out).
    for _, row in summary.iterrows():
        seed_mass = sum(float(row[column]) for column in seed_columns)
        seed_mass += float(row["out_of_playoffs_probability"])
        np.testing.assert_allclose(seed_mass, 1.0)


def test_summarize_team_wins_without_ranks_omits_seed_columns():
    probabilities = _synthetic_probabilities()
    wins, teams = simulate_season(
        probabilities, season=2025, n_simulations=100, random_state=5
    )

    summary = summarize_team_wins(wins, teams)

    assert "p_seed_1" not in summary.columns
    assert "out_of_playoffs_probability" not in summary.columns
    assert summary["direct_playoff_probability"].isna().all()


def test_build_seedings_table_picks_most_likely_seed_occupants():
    probabilities = _synthetic_probabilities()
    wins, teams = simulate_season(
        probabilities, season=2025, n_simulations=500, random_state=7
    )
    summary = summarize_team_wins(wins, teams, probabilities, 2025)

    seedings = build_seedings_table(summary)

    # Only two teams per conference exist in the synthetic schedule, so the
    # table only covers seeds 1..2 (teams beyond the field are not assigned).
    assert seedings
    for slot in seedings:
        assert slot["conference"] in ("East", "West")
        assert 1 <= slot["seed"] <= 2
        assert slot["teamId"] in {
            1610612738,
            1610612765,
            1610612747,
            1610612760,
        }
        assert 0.0 <= slot["probability"] <= 1.0
    # Exactly two seed slots per conference.
    east_slots = [slot for slot in seedings if slot["conference"] == "East"]
    west_slots = [slot for slot in seedings if slot["conference"] == "West"]
    assert len(east_slots) == 2
    assert len(west_slots) == 2


def test_build_league_summary_reports_aggregate_strength():
    probabilities = _synthetic_probabilities()
    wins, teams = simulate_season(
        probabilities, season=2025, n_simulations=300, random_state=11
    )
    summary = summarize_team_wins(wins, teams, probabilities, 2025)

    league = build_league_summary(summary)

    assert league["n_teams"] == len(teams)
    assert league["league_mean_wins"] == float(summary["mean_wins"].mean())
    assert league["league_median_wins"] == float(summary["mean_wins"].median())
    assert league["best_team"]["mean_wins"] == float(summary["mean_wins"].max())
    assert league["worst_team"]["mean_wins"] == float(summary["mean_wins"].min())
    assert set(league["conference_mean_wins"]) == {"East", "West"}
    assert league["best_team"]["mean_wins"] >= league["worst_team"]["mean_wins"]


def test_project_season_includes_seedings_and_league_summary():
    probabilities = _synthetic_probabilities()

    projection = project_season(
        probabilities, season=2025, n_simulations=200, random_state=11
    )

    assert "projected_seedings" in projection
    assert "league_summary" in projection
    assert projection["league_summary"]["n_teams"] == 4
    assert all(
        "p_seed_1" in row for row in projection["projected_standings"]
    )


def test_validate_seasons_iterates_available_seasons():
    probabilities = _synthetic_probabilities()

    results = validate_seasons(
        probabilities, seasons=[2025], n_simulations=50, random_state=13
    )

    assert set(results) == {"2025"}
    assert results["2025"]["mean_absolute_error_wins"] >= 0.0


def test_write_metrics_persists_json(tmp_path):
    metrics_path = tmp_path / "season_simulation_metrics.json"

    write_metrics({"season": 2025}, path=metrics_path)

    assert metrics_path.exists()
    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert payload["season"] == 2025


def test_load_pregame_probabilities_returns_valid_ensemble_values(monkeypatch):
    """Check the probability loader via a monkeypatched cheap pipeline.

    The real full-dataset path (historical Elo replay) is validated by the
    CLI run; this test verifies the loader's column contract and the
    ensemble-averaging rule on a small substitute pipeline.
    """
    import src.simulate_season as simulate_module

    small_features = pd.DataFrame(
        [
            {
                "gameId": 1,
                "gameDateTimeEst": "2025-10-22 19:00:00",
                "teamId": 1610612738,
                "home": 1,
                "win": 1,
                "teamScore_rolling_10": 105,
            },
            {
                "gameId": 1,
                "gameDateTimeEst": "2025-10-22 19:00:00",
                "teamId": 1610612747,
                "home": 0,
                "win": 0,
                "teamScore_rolling_10": 101,
            },
        ]
    )

    class FakeModel:
        def predict_proba(self, frame):
            return np.full((len(frame), 2), 0.6)

    def fake_build(features):
        games = small_features[small_features["home"] == 1][
            ["gameId", "gameDateTimeEst", "teamId", "win"]
        ].rename(
            columns={"teamId": "homeTeamId", "win": "target"}
        )
        away = small_features[small_features["home"] == 0][
            ["gameId", "teamId"]
        ].rename(columns={"teamId": "awayTeamId"})
        games = games.merge(away, on="gameId", how="inner")
        games["rest_days"] = 1.0
        return games, ["rest_days"]

    def fake_elo(games):
        games["elo_probability"] = 0.5
        games["elo_delta"] = 50.0
        return games

    monkeypatch.setattr(simulate_module, "build_game_dataset", fake_build)
    monkeypatch.setattr(simulate_module, "add_elo_rating_deltas", fake_elo)

    real_bundle = {"model": FakeModel(), "predictors": ["rest_days"]}
    monkeypatch.setattr(simulate_module.pickle, "load", lambda handle: real_bundle)

    probabilities = load_pregame_probabilities()

    assert list(probabilities.columns) == [
        "gameId",
        "gameDateTimeEst",
        "season",
        "homeTeamId",
        "awayTeamId",
        "target",
        "elo_probability",
        "boosted_probability",
        "home_win_probability",
    ]
    assert probabilities["season"].tolist() == [2025]
    # Ensemble = (boosted + elo) / 2 = (0.6 + 0.5) / 2.
    np.testing.assert_allclose(
        probabilities["home_win_probability"], np.array([0.55])
    )

def test_load_team_names_uses_the_name_a_franchise_had_that_season(tmp_path):
    import sqlite3

    from src.simulate_season import load_team_names

    db_path = tmp_path / "nba.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE team_histories (teamId INTEGER, teamCity TEXT, teamName TEXT, "
            "teamAbbrev TEXT, seasonFounded INTEGER, seasonActiveTill INTEGER, league TEXT)"
        )
        connection.executemany(
            "INSERT INTO team_histories VALUES (?, ?, ?, ?, ?, ?, 'NBA')",
            [
                (1610612760, "Seattle", "SuperSonics", "SEA  ", 1967, 2007),
                (1610612760, "Oklahoma City", "Thunder", "OKC  ", 2008, 2100),
            ],
        )
        connection.commit()

    assert load_team_names(2005, db_path=db_path)[1610612760]["teamName"] == "Seattle SuperSonics"
    current = load_team_names(db_path=db_path)[1610612760]
    assert current == {"teamName": "Oklahoma City Thunder", "teamAbbreviation": "OKC"}
    assert load_team_names(db_path=tmp_path / "missing" / "nba.db") == {}


def test_project_season_attaches_team_names_everywhere():
    probabilities = _synthetic_probabilities()
    names = {
        1610612738: {"teamName": "Boston Celtics", "teamAbbreviation": "BOS"},
        1610612760: {"teamName": "Oklahoma City Thunder", "teamAbbreviation": "OKC"},
    }

    projection = project_season(
        probabilities, 2025, n_simulations=50, random_state=1, team_names=names
    )

    by_id = {row["teamId"]: row for row in projection["projected_standings"]}
    assert by_id[1610612738]["teamName"] == "Boston Celtics"
    # Teams without a name entry keep their id only.
    assert "teamName" not in by_id[1610612747]
    assert all(
        "teamName" in slot
        for slot in projection["projected_seedings"]
        if slot["teamId"] in names
    )
    best = projection["league_summary"]["best_team"]
    if best["teamId"] in names:
        assert best["teamName"] == names[best["teamId"]]["teamName"]
