"""Focused regression coverage for the deterministic tool/orchestration layer.

The tests verify tool selection/routing, structured result envelopes, invalid
or unsupported requests, preservation of the validated model/simulation path,
and correct handling of unavailable/low-confidence player diagnostics. They use
monkeypatched executors and a temporary SQLite database so no heavy pipeline or
real DB state is required.
"""

import json
import sqlite3

import pandas as pd
import pytest

from src.tools import (
    execute_tool,
    list_tools,
    parse_args,
    validate_parameters,
    clear_probability_cache,
)


def test_list_tools_exposes_expected_capabilities():
    tools = {tool["name"]: tool for tool in list_tools()}

    assert {
        "predict_matchup",
        "simulate_season",
        "team_projection",
        "player_impact",
        "player_scenario",
        "team_record",
        "head_to_head",
        "resolve_team_name",
        "list_teams",
        "team_form",
        "team_elo_rating",
    } <= set(tools)
    for tool in tools.values():
        assert tool["description"]
        assert tool["model"]
        assert tool["parameters"] is not None


def test_list_teams_returns_the_30_current_franchises():
    envelope = execute_tool("list_teams", {})

    assert envelope["status"] == "success"
    data = envelope["data"]
    assert data["count"] == 30
    assert len(data["teams"]) == 30
    team = data["teams"][0]
    assert set(team) == {"team_id", "city", "name", "full_name", "abbreviation"}
    full_names = {team["full_name"] for team in data["teams"]}
    assert "Boston Celtics" in full_names
    assert "Los Angeles Lakers" in full_names
    # No historical/relocated or international franchises leak in.
    abbreviations = {team["abbreviation"] for team in data["teams"]}
    assert "TRI" not in abbreviations


def test_execute_tool_unknown_tool_returns_structured_error():
    result = execute_tool("does_not_exist", {})

    assert result["status"] == "error"
    assert result["error"]["type"] == "UnknownTool"
    assert "does_not_exist" in result["error"]["message"]
    assert "predict_matchup" in result["error"]["message"]
    assert result["data"] is None


def test_validate_parameters_rejects_unknown_and_requires_required():
    schema = [
        {"name": "season", "type": "int", "required": True,
         "description": "season"},
        {"name": "n_simulations", "type": "int", "required": False,
         "description": "sims"},
    ]

    with pytest.raises(Exception) as unknown:
        validate_parameters(schema, {"season": 2025, "bogus": 1})
    assert "Unknown parameter" in str(unknown.value)

    with pytest.raises(Exception) as missing:
        validate_parameters(schema, {})
    assert "Missing required parameter 'season'" in str(missing.value)


def test_validate_parameters_coerces_integer_and_requires_strings():
    schema = [
        {"name": "season", "type": "int", "required": True, "description": "s"},
        {"name": "team", "type": "str", "required": True, "description": "t"},
    ]

    cleaned = validate_parameters(
        schema, {"season": "2025", "team": "Boston Celtics"}
    )

    assert cleaned["season"] == 2025
    assert cleaned["team"] == "Boston Celtics"

    with pytest.raises(Exception):
        validate_parameters(schema, {"season": "abc", "team": "x"})
    with pytest.raises(Exception):
        validate_parameters(schema, {"season": 2025, "team": 123})


def test_predict_matchup_routes_to_production_model_and_preserves_result(monkeypatch):
    captured = {}

    def fake_predict_matchup(**kwargs):
        captured.update(kwargs)
        return {
            "model": "elo_boosted_ensemble",
            "home_team_id": 1610612738,
            "away_team_id": 1610612747,
            "home_win_probability": 0.61,
            "away_win_probability": 0.39,
            "game_date": "2026-04-12",
            "matchup_summary": "The model favors the home team with a 61.0% win chance.",
        }

    monkeypatch.setattr("src.tools.predict_matchup", fake_predict_matchup)
    monkeypatch.setattr(
        "src.tools.resolve_team_name_to_id",
        lambda name: 1610612738 if "Celtics" in name else 1610612747,
    )

    result = execute_tool(
        "predict_matchup",
        {"home_team": "Boston Celtics", "away_team": "Los Angeles Lakers"},
    )

    assert result["status"] == "success"
    assert result["tool"] == "predict_matchup"
    assert result["model"] == "elo_boosted_ensemble"
    assert result["data"]["home_team_id"] == 1610612738
    assert result["data"]["away_team_id"] == 1610612747
    # The production result is preserved verbatim under data["prediction"].
    assert result["data"]["prediction"]["home_win_probability"] == 0.61
    assert result["data"]["prediction"]["model"] == "elo_boosted_ensemble"
    assert captured["home_team_id"] == 1610612738
    assert captured["away_team_id"] == 1610612747


def test_predict_matchup_rejects_missing_team_with_structured_error(monkeypatch):
    monkeypatch.setattr("src.tools.predict_matchup", lambda **kwargs: None)

    result = execute_tool("predict_matchup", {"home_team": "Boston Celtics"})

    assert result["status"] == "error"
    assert "away" in result["error"]["message"]
    assert result["data"] is None


def test_simulate_season_routes_to_validated_simulator(monkeypatch):
    fake_projection = {
        "season": 2025,
        "n_simulations": 100,
        "random_state": 42,
        "teams": 30,
        "projected_standings": [
            {
                "teamId": 1610612738,
                "conference": "East",
                "mean_wins": 57.1,
                "direct_playoff_probability": 1.0,
                "p_seed_1": 0.66,
            }
        ],
        "projected_seedings": [],
        "league_summary": {"n_teams": 30, "league_mean_wins": 41.0},
    }
    monkeypatch.setattr(
        "src.tools.project_season",
        lambda probabilities, season, n_simulations=1000, random_state=42: fake_projection,
    )
    monkeypatch.setattr(
        "src.tools.load_pregame_probabilities",
        lambda **kwargs: pd.DataFrame(),
    )
    clear_probability_cache()

    result = execute_tool(
        "simulate_season", {"season": 2025, "n_simulations": 100}
    )

    assert result["status"] == "success"
    assert result["data"]["model"] == "elo_boosted_ensemble"
    assert result["data"]["leakage_safe"] is True
    assert result["data"]["projection"]["season"] == 2025
    assert result["data"]["projection"]["projected_standings"][0]["mean_wins"] == 57.1


def test_team_projection_filters_to_requested_team(monkeypatch):
    fake_projection = {
        "season": 2025,
        "n_simulations": 100,
        "random_state": 42,
        "teams": 2,
        "projected_standings": [
            {"teamId": 1610612738, "conference": "East", "mean_wins": 57.1,
             "direct_playoff_probability": 1.0, "p_seed_1": 0.66},
            {"teamId": 1610612760, "conference": "West", "mean_wins": 63.8,
             "direct_playoff_probability": 1.0, "p_seed_1": 0.98},
        ],
    }
    monkeypatch.setattr(
        "src.tools.project_season",
        lambda probabilities, season, n_simulations=1000, random_state=42: fake_projection,
    )
    monkeypatch.setattr(
        "src.tools.load_pregame_probabilities",
        lambda **kwargs: pd.DataFrame(),
    )
    clear_probability_cache()

    result = execute_tool(
        "team_projection",
        {"team_id": 1610612760, "season": 2025, "n_simulations": 100},
    )

    assert result["status"] == "success"
    assert result["data"]["team_id"] == 1610612760
    assert result["data"]["projection"]["mean_wins"] == 63.8
    assert result["data"]["projection"]["p_seed_1"] == 0.98


def test_team_projection_unavailable_for_unknown_team(monkeypatch):
    fake_projection = {
        "season": 2025,
        "n_simulations": 100,
        "random_state": 42,
        "teams": 1,
        "projected_standings": [
            {"teamId": 1610612738, "conference": "East", "mean_wins": 57.1},
        ],
    }
    monkeypatch.setattr(
        "src.tools.project_season",
        lambda probabilities, season, n_simulations=1000, random_state=42: fake_projection,
    )
    monkeypatch.setattr(
        "src.tools.load_pregame_probabilities",
        lambda **kwargs: pd.DataFrame(),
    )
    clear_probability_cache()

    result = execute_tool(
        "team_projection",
        {"team_id": 999999, "season": 2025, "n_simulations": 100},
    )

    assert result["status"] == "unavailable"
    assert result["data"] is None
    assert "999999" in result["error"]["message"]


def test_player_impact_marks_association_only_and_confidence(monkeypatch):
    monkeypatch.setattr(
        "src.tools.summarize_player_impact",
        lambda person_id, before=None, window=10: {
            "person_id": person_id,
            "prior_games": 8,
            "player_net_rating": 4.2,
            "estimated_net_rating_change": 1.2,
            "note": "descriptive association estimate",
        },
    )

    result = execute_tool("player_impact", {"person_id": 203500})

    assert result["status"] == "success"
    assert result["data"]["association_only"] is True
    assert result["data"]["confidence"] == "moderate"
    assert result["data"]["diagnostic"]["person_id"] == 203500
    # Limitations explicitly prevent causal interpretation.
    assert any("NOT a causal forecast" in limit for limit in result["limitations"])


def test_player_impact_low_confidence_with_few_prior_games(monkeypatch):
    monkeypatch.setattr(
        "src.tools.summarize_player_impact",
        lambda person_id, before=None, window=10: {
            "person_id": person_id,
            "prior_games": 2,
            "player_net_rating": 9.0,
            "estimated_net_rating_change": 0.8,
            "note": "descriptive association estimate",
        },
    )

    result = execute_tool("player_impact", {"person_id": 203500})

    assert result["status"] == "success"
    assert result["data"]["confidence"] == "low"


def test_player_impact_unavailable_without_prior_history(monkeypatch):
    monkeypatch.setattr(
        "src.tools.summarize_player_impact",
        lambda person_id, before=None, window=10: (_ for _ in ()).throw(
            ValueError("No prior regular-season appearances found for personId=999999.")
        ),
    )

    result = execute_tool("player_impact", {"person_id": 999999})

    assert result["status"] == "unavailable"
    assert result["data"] is None
    assert "No prior regular-season appearances" in result["error"]["message"]


def test_player_scenario_preserves_production_model(monkeypatch):
    monkeypatch.setattr(
        "src.tools.analyze_single_player_scenario",
        lambda *args, **kwargs: {
            "model": "elo_boosted_ensemble",
            "base_prediction": {"home_win_probability": 0.61},
            "player_impact": {"person_id": 203500},
            "feature_translation": {"status": "unsupported"},
            "scenario_summary": "The production model remains the validated baseline.",
        },
    )
    monkeypatch.setattr(
        "src.tools.resolve_team_name_to_id",
        lambda name: 1610612738 if "Celtics" in name else 1610612747,
    )

    result = execute_tool(
        "player_scenario",
        {
            "home_team": "Boston Celtics",
            "away_team": "Los Angeles Lakers",
            "person_id": 203500,
        },
    )

    assert result["status"] == "success"
    assert result["data"]["association_only"] is True
    assert (
        result["data"]["scenario"]["base_prediction"]["home_win_probability"]
        == 0.61
    )
    assert (
        result["data"]["scenario"]["feature_translation"]["status"]
        == "unsupported"
    )


def test_team_record_queries_database_factually(tmp_path, monkeypatch):
    db_path = tmp_path / "nba.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE games (
                gameId INTEGER, gameDateTimeEst TEXT, gameType TEXT,
                hometeamId INTEGER, awayteamId INTEGER, winner INTEGER
            )
            """
        )
        connection.executemany(
            "INSERT INTO games VALUES (?, ?, ?, ?, ?, ?)",
            [
                (1, "2025-10-22 19:00:00", "Regular Season", 1610612738, 1610612747, 1610612738),
                (2, "2025-10-24 19:00:00", "Regular Season", 1610612747, 1610612738, 1610612747),
                (3, "2025-10-26 19:00:00", "Regular Season", 1610612738, 1610612760, 1610612760),
                (4, "2026-04-12 19:00:00", "Regular Season", 1610612760, 1610612738, 1610612738),
            ],
        )
        connection.commit()
    monkeypatch.setattr("src.tools.TEAM_DB_PATH", db_path)

    result = execute_tool(
        "team_record", {"team_id": 1610612738, "season": 2025}
    )

    assert result["status"] == "success"
    # Game 1 and game 4 (played in October 2025 and April 2026, both part of
    # the 2025 season per the feature-pipeline season-labeling rule) are
    # Celtics wins; games 2 and 3 are losses.
    assert result["data"]["wins"] == 2
    assert result["data"]["losses"] == 2
    assert result["data"]["games"] == 4
    assert result["data"]["source"] == "nba.db games (Regular Season)"


def test_head_to_head_queries_database_factually(tmp_path, monkeypatch):
    db_path = tmp_path / "nba.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE games (
                gameId INTEGER, gameDateTimeEst TEXT, gameType TEXT,
                hometeamId INTEGER, awayteamId INTEGER, winner INTEGER
            )
            """
        )
        connection.executemany(
            "INSERT INTO games VALUES (?, ?, ?, ?, ?, ?)",
            [
                (1, "2025-10-22 19:00:00", "Regular Season", 1610612738, 1610612747, 1610612738),
                (2, "2025-11-01 19:00:00", "Regular Season", 1610612747, 1610612738, 1610612747),
                (3, "2025-12-01 19:00:00", "Regular Season", 1610612738, 1610612747, 1610612738),
            ],
        )
        connection.commit()
    monkeypatch.setattr("src.tools.TEAM_DB_PATH", db_path)

    result = execute_tool(
        "head_to_head",
        {"team_a_id": 1610612738, "team_b_id": 1610612747, "season": 2025},
    )

    assert result["status"] == "success"
    assert result["data"]["games"] == 3
    assert result["data"]["team_a_wins"] == 2
    assert result["data"]["team_b_wins"] == 1


def test_resolve_team_name_returns_structured_result(monkeypatch):
    monkeypatch.setattr(
        "src.tools.resolve_team_name_to_id",
        lambda name: 1610612738,
    )

    result = execute_tool("resolve_team_name", {"team": "Boston Celtics"})

    assert result["status"] == "success"
    assert result["data"]["team_id"] == 1610612738
    assert result["data"]["team"] == "Boston Celtics"


def test_team_form_returns_latest_rolling_snapshot(monkeypatch):
    monkeypatch.setattr("src.tools.resolve_team_name_to_id", lambda name: 1610612738)
    fake_features = pd.DataFrame(
        {
            "teamId": [1610612738],
            "gameDateTimeEst": ["2026-04-12"],
        }
    )
    monkeypatch.setattr("src.tools.pd.read_csv", lambda path: fake_features)
    monkeypatch.setattr(
        "src.tools.lookup_last_team_row",
        lambda features, team_id, as_of=None: {
            "gameDateTimeEst": pd.Timestamp("2026-04-12"),
            "win_rate_rolling_10": 0.8,
            "teamScore_rolling_10": 120.2,
            "opponentScore_rolling_10": 108.5,
            "plusMinusPoints_rolling_10": 11.7,
            "rest_days": 1.9,
            "active_players_last_game": 12.0,
        },
    )

    result = execute_tool("team_form", {"team": "Boston Celtics"})

    assert result["status"] == "success"
    assert result["data"]["team_id"] == 1610612738
    assert result["data"]["snapshot_date"] == "2026-04-12"
    assert result["data"]["win_rate_rolling_10"] == 0.8
    assert result["data"]["plusMinusPoints_rolling_10"] == 11.7


def test_team_form_unavailable_without_feature_rows(monkeypatch):
    monkeypatch.setattr("src.tools.resolve_team_name_to_id", lambda name: 999999)
    monkeypatch.setattr("src.tools.pd.read_csv", lambda path: pd.DataFrame({"teamId": [], "gameDateTimeEst": []}))

    def _raise(*args, **kwargs):
        raise ValueError("No feature rows found for teamId=999999.")

    monkeypatch.setattr("src.tools.lookup_last_team_row", _raise)

    result = execute_tool("team_form", {"team_id": 999999})

    assert result["status"] == "unavailable"
    assert "No feature rows found" in result["error"]["message"]


def test_team_elo_rating_returns_current_rating(monkeypatch):
    monkeypatch.setattr("src.tools.resolve_team_name_to_id", lambda name: 1610612738)
    monkeypatch.setattr("src.tools.pd.read_csv", lambda path: pd.DataFrame({"a": [1]}))
    monkeypatch.setattr(
        "src.tools.build_game_dataset",
        lambda features: (pd.DataFrame({"gameDateTimeEst": []}), None),
    )
    monkeypatch.setattr(
        "src.tools.load_elo_config",
        lambda: {"initial_rating": 1500.0, "k_factor": 20.0, "home_advantage": 65.0},
    )
    monkeypatch.setattr(
        "src.tools.compute_elo_ratings_as_of",
        lambda games, cutoff=None, **kwargs: (
            {1610612738: 1682.3},
            {1610612738},
        ),
    )

    result = execute_tool("team_elo_rating", {"team": "Boston Celtics"})

    assert result["status"] == "success"
    assert result["data"]["team_id"] == 1610612738
    assert result["data"]["elo_rating"] == 1682.3
    assert result["data"]["as_of"] is None


def test_team_elo_rating_unavailable_for_unseen_team(monkeypatch):
    monkeypatch.setattr("src.tools.resolve_team_name_to_id", lambda name: 999999)
    monkeypatch.setattr("src.tools.pd.read_csv", lambda path: pd.DataFrame({"a": [1]}))
    monkeypatch.setattr(
        "src.tools.build_game_dataset",
        lambda features: (pd.DataFrame({"gameDateTimeEst": []}), None),
    )
    monkeypatch.setattr(
        "src.tools.load_elo_config",
        lambda: {"initial_rating": 1500.0, "k_factor": 20.0, "home_advantage": 65.0},
    )
    monkeypatch.setattr(
        "src.tools.compute_elo_ratings_as_of",
        lambda games, cutoff=None, **kwargs: ({}, set()),
    )

    result = execute_tool("team_elo_rating", {"team_id": 999999})

    assert result["status"] == "unavailable"
    assert "cannot compute an Elo rating" in result["error"]["message"]


def test_envelope_carries_operation_model_assumptions_limitations():
    tools = {tool["name"]: tool for tool in list_tools()}
    for name in ("predict_matchup", "simulate_season", "team_projection",
                 "player_impact", "player_scenario", "team_record",
                 "head_to_head", "resolve_team_name"):
        assert tools[name]["description"]
        assert tools[name]["model"]
        assert isinstance(tools[name]["assumptions"], list)
        assert isinstance(tools[name]["limitations"], list)

def test_new_phase_one_tools_are_registered_with_metadata():
    tools = {tool["name"]: tool for tool in list_tools()}
    for name in ("resolve_player", "query_database", "describe_database",
                 "project_rest_of_season"):
        assert name in tools
        assert tools[name]["description"]
        assert tools[name]["limitations"]
    # person_id is no longer the only way to name a player.
    impact_params = {p["name"]: p for p in tools["player_impact"]["parameters"]}
    assert impact_params["person_id"]["required"] is False
    assert "player" in impact_params


def test_player_impact_accepts_a_player_name(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "src.tools.resolve_player_id",
        lambda name, season=None: captured.setdefault("id", 201939),
    )
    monkeypatch.setattr(
        "src.tools.summarize_player_impact",
        lambda person_id, before=None, window=10: {
            "person_id": person_id, "prior_games": 10,
            "player_net_rating": 5.0, "estimated_net_rating_change": 3.5,
        },
    )

    result = execute_tool("player_impact", {"player": "Stephen Curry"})

    assert result["status"] == "success"
    assert result["data"]["diagnostic"]["person_id"] == 201939


def test_player_impact_ambiguous_name_is_a_structured_error(monkeypatch):
    def ambiguous(name, season=None):
        raise ValueError("Player name 'Curry' matches 6 players: ...")

    monkeypatch.setattr("src.tools.resolve_player_id", ambiguous)

    result = execute_tool("player_impact", {"player": "Curry"})

    assert result["status"] == "error"
    assert "matches 6 players" in result["error"]["message"]


def test_player_impact_requires_id_or_name():
    result = execute_tool("player_impact", {})
    assert result["status"] == "error"
    assert "person_id" in result["error"]["message"]


def test_resolve_player_unavailable_when_nothing_matches(monkeypatch):
    monkeypatch.setattr(
        "src.tools.find_players",
        lambda name, season=None, limit=10: {"match_count": 0, "candidates": []},
    )
    result = execute_tool("resolve_player", {"name": "Nobody"})
    assert result["status"] == "unavailable"


def test_query_database_rejects_writes_as_structured_error():
    result = execute_tool("query_database", {"sql": "DELETE FROM games"})
    assert result["status"] == "error"
    assert "read-only" in result["error"]["message"]


def test_project_rest_of_season_highlights_requested_team(monkeypatch):
    fake_projection = {
        "season": 2024,
        "as_of": "2025-01-15",
        "projected_standings": [
            {"teamId": 1610612738, "mean_wins": 58.5, "current_wins": 28},
            {"teamId": 1610612760, "mean_wins": 66.0, "current_wins": 34},
        ],
    }
    captured = {}

    def fake_project(season, as_of, inputs, **kwargs):
        captured.update({"season": season, "as_of": as_of, **kwargs})
        return fake_projection

    monkeypatch.setattr("src.tools.project_from_date", fake_project)
    monkeypatch.setattr("src.tools._cached_model_inputs", lambda: object())

    result = execute_tool(
        "project_rest_of_season",
        {"season": 2024, "as_of": "2025-01-15", "team_id": 1610612738},
    )

    assert result["status"] == "success"
    assert result["data"]["team_projection"]["mean_wins"] == 58.5
    assert captured["as_of"] == "2025-01-15"
    assert captured["n_simulations"] == 1000


def test_phase_two_tools_are_registered():
    tools = {tool["name"]: tool for tool in list_tools()}
    for name in ("predict_margin", "playoff_odds", "validation_report"):
        assert name in tools
        assert tools[name]["limitations"]


def test_predict_margin_reports_production_probability_alongside(monkeypatch):
    monkeypatch.setattr("src.tools._cached_model_inputs", lambda: object())
    monkeypatch.setattr("src.tools._cached_margin_bundle", lambda: {})
    monkeypatch.setattr(
        "src.tools.predict_margin",
        lambda home, away, inputs, bundle, game_date=None: {
            "home_team_id": home, "away_team_id": away, "predicted_home_margin": 4.2,
        },
    )
    monkeypatch.setattr(
        "src.tools.frozen_matchup_probabilities",
        lambda pairs, cutoff, inputs: pd.DataFrame({"home_win_probability": [0.63]}),
    )

    result = execute_tool("predict_margin", {"home_team_id": 1610612738,
                                             "away_team_id": 1610612747})

    assert result["status"] == "success"
    assert result["data"]["predicted_home_margin"] == 4.2
    assert result["data"]["production_home_win_probability"] == 0.63
    assert result["data"]["production_model"] == "elo_boosted_ensemble"


def test_playoff_odds_uses_real_bracket_once_postseason_exists(monkeypatch):
    games = pd.DataFrame({"gameDateTimeEst": [pd.Timestamp("2025-04-15 19:00")]})
    monkeypatch.setattr("src.tools._cached_model_inputs", lambda: object())
    monkeypatch.setattr("src.tools.load_team_names", lambda season: {})
    monkeypatch.setattr("src.tools.load_postseason_games", lambda season: (games, None))
    called = {}
    monkeypatch.setattr(
        "src.tools.actual_bracket_odds",
        lambda season, inputs, team_names=None: called.setdefault(
            "actual", {"mode": "actual", "teams": [{"teamId": 1610612760, "champion": 0.5}]}
        ),
    )
    monkeypatch.setattr(
        "src.tools.season_playoff_odds",
        lambda *args, **kwargs: called.setdefault("simulated", {"mode": "sim", "teams": []}),
    )

    result = execute_tool("playoff_odds", {"season": 2024, "team_id": 1610612760})
    assert result["data"]["mode"] == "actual"
    assert result["data"]["team_odds"]["champion"] == 0.5

    execute_tool("playoff_odds", {"season": 2024, "as_of": "2025-01-01"})
    assert "simulated" in called


def test_playoff_odds_needs_as_of_without_a_postseason(monkeypatch):
    monkeypatch.setattr("src.tools._cached_model_inputs", lambda: object())
    monkeypatch.setattr("src.tools.load_team_names", lambda season: {})
    monkeypatch.setattr("src.tools.load_postseason_games",
                        lambda season: (pd.DataFrame({"gameDateTimeEst": []}), None))
    result = execute_tool("playoff_odds", {"season": 2026})
    assert result["status"] == "error"
    assert "as_of" in result["error"]["message"]


def test_validation_report_rejects_unknown_component():
    result = execute_tool("validation_report", {"component": "vibes"})
    assert result["status"] == "error"
    assert "production_model" in result["error"]["message"]
