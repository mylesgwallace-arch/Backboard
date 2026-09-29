"""Focused regression coverage for the deterministic natural-language layer.

The tests verify question -> tool mapping, structured answer rendering that is
derived only from tool envelopes (never fabricated), ambiguity handling, and
pass-through of association-only player-impact language. They use the real
read-only repository database for team/player extraction and monkeypatched
``execute_tool`` so no heavy pipeline runs.
"""

import pytest

from src.assistant import (
    answer_question,
    extract_player_ids,
    extract_team_ids,
    load_team_labels,
    render_envelope,
    resolve_intent,
)


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

@pytest.mark.requires_data
def test_extract_team_ids_handles_city_name_and_alias():
    assert extract_team_ids("Who is favored in Celtics vs Lakers?") == [
        1610612738,
        1610612747,
    ]
    assert extract_team_ids("What does the simulator project for OKC?") == [
        1610612760,
    ]
    assert extract_team_ids("Boston vs Oklahoma City") == [
        1610612738,
        1610612760,
    ]


@pytest.mark.requires_data
def test_extract_team_ids_prefers_full_franchise_phrase():
    # "Los Angeles" alone is ambiguous (Lakers/Clippers); the full franchise
    # phrase must win and the ambiguous city phrase must not be used.
    assert extract_team_ids("Los Angeles Lakers vs Denver Nuggets") == [
        1610612747,
        1610612743,
    ]


@pytest.mark.requires_data
def test_extract_player_ids_finds_nba_player():
    matches = extract_player_ids("What does the diagnostic say about Steven Adams?")
    assert 203500 in matches


@pytest.mark.requires_data
def test_load_team_labels_covers_current_franchises():
    labels = load_team_labels()
    assert labels[1610612738] == "Boston Celtics"
    assert labels[1610612747] == "Los Angeles Lakers"
    assert len(labels) == 30


# ---------------------------------------------------------------------------
# Intent routing
# ---------------------------------------------------------------------------

@pytest.mark.requires_data
def test_routes_head_to_head_before_record():
    tool, params = resolve_intent(
        "What is the head to head record between Boston and LA Lakers?"
    )
    assert tool == "head_to_head"
    assert params["team_a_id"] == 1610612738
    assert params["team_b_id"] == 1610612747
    assert params["season"] == 2025


@pytest.mark.requires_data
def test_routes_team_record():
    tool, params = resolve_intent("What was OKC's record in the 2024 season?")
    assert tool == "team_record"
    assert params["team_id"] == 1610612760
    assert params["season"] == 2024


@pytest.mark.requires_data
def test_routes_predict_matchup():
    tool, params = resolve_intent("Who is favored in Celtics vs Lakers?")
    assert tool == "predict_matchup"
    assert params["home_team_id"] == 1610612738
    assert params["away_team_id"] == 1610612747


@pytest.mark.requires_data
def test_routes_predict_matchup_will_win():
    tool, _ = resolve_intent("Who will win between the Celtics and the Lakers?")
    assert tool == "predict_matchup"


@pytest.mark.requires_data
def test_routes_season_projection():
    tool, params = resolve_intent("What are the projected playoff teams?")
    assert tool == "simulate_season"
    assert params["season"] == 2025


@pytest.mark.requires_data
def test_routes_team_projection_seed():
    tool, params = resolve_intent(
        "What is Boston's probability of getting the 1 seed?"
    )
    assert tool == "team_projection"
    assert params["team_id"] == 1610612738
    assert params["season"] == 2025


@pytest.mark.requires_data
def test_routes_team_projection_wins():
    tool, params = resolve_intent(
        "How many wins does the simulator project for OKC?"
    )
    assert tool == "team_projection"
    assert params["team_id"] == 1610612760


@pytest.mark.requires_data
def test_routes_player_impact():
    tool, params = resolve_intent(
        "What does the player-impact diagnostic say about Steven Adams?"
    )
    assert tool == "player_impact"
    assert params["person_id"] == 203500


@pytest.mark.requires_data
def test_routes_player_scenario():
    tool, params = resolve_intent(
        "How does Steven Adams change the Celtics vs Lakers scenario?"
    )
    assert tool == "player_scenario"
    assert params["home_team_id"] == 1610612738
    assert params["away_team_id"] == 1610612747
    assert params["person_id"] == 203500


@pytest.mark.requires_data
def test_routes_resolve_team_name():
    tool, params = resolve_intent("What is the team id for the Boston Celtics?")
    assert tool == "resolve_team_name"
    assert params["team"] == "Boston Celtics"


# ---------------------------------------------------------------------------
# Ambiguity and unsupported questions
# ---------------------------------------------------------------------------

@pytest.mark.requires_data
def test_rejects_ambiguous_city_only_question():
    # "Los Angeles" is ambiguous (Lakers and Clippers), so it resolves to
    # neither team, leaving only Denver -> the matchup cannot be completed.
    with pytest.raises(ValueError, match="Two different teams are required"):
        resolve_intent("Who is favored in Los Angeles vs Denver?")


@pytest.mark.requires_data
def test_rejects_missing_team_for_matchup():
    with pytest.raises(ValueError, match="Two different teams are required"):
        resolve_intent("Who is favored?")


@pytest.mark.requires_data
def test_rejects_unsupported_question():
    with pytest.raises(ValueError, match="could not map"):
        resolve_intent("What is the meaning of life?")


def test_rejects_empty_question():
    with pytest.raises(ValueError, match="empty"):
        resolve_intent("   ")


# ---------------------------------------------------------------------------
# Rendering (derived only from the envelope, never fabricated)
# ---------------------------------------------------------------------------

def test_render_predict_matchup_uses_envelope_values():
    envelope = {
        "status": "success",
        "tool": "predict_matchup",
        "data": {
            "home_team_id": 1610612738,
            "away_team_id": 1610612747,
            "prediction": {
                "model": "elo_boosted_ensemble",
                "home_win_probability": 0.693,
                "away_win_probability": 0.307,
                "home_team_prediction": "favorite",
            },
        },
    }
    labels = {1610612738: "Boston Celtics", 1610612747: "Los Angeles Lakers"}
    text = render_envelope(envelope, labels=labels)

    assert "Boston Celtics" in text
    assert "Los Angeles Lakers" in text
    assert "69.3%" in text
    assert "30.7%" in text
    assert "elo_boosted_ensemble" in text


def test_render_player_impact_preserves_association_language():
    envelope = {
        "status": "success",
        "tool": "player_impact",
        "data": {
            "confidence": "moderate",
            "association_only": True,
            "diagnostic": {
                "person_id": 203500,
                "prior_games": 8,
                "player_net_rating": 4.2,
                "estimated_net_rating_change": 1.2,
                "direction": "addition",
            },
        },
    }
    text = render_envelope(envelope)

    assert "association-only" in text
    assert "not a causal forecast" in text
    assert "moderate" in text
    assert "+1.20" in text


def test_render_error_and_unavailable():
    error_text = render_envelope(
        {"status": "error", "error": {"message": "boom"}}
    )
    assert "boom" in error_text

    unavailable_text = render_envelope(
        {"status": "unavailable", "error": {"message": "no data"}}
    )
    assert "not available" in unavailable_text


# ---------------------------------------------------------------------------
# End-to-end through the real tool layer (monkeypatched execute)
# ---------------------------------------------------------------------------

@pytest.mark.requires_data
def test_answer_question_returns_structured_result(monkeypatch):
    def fake_execute(tool_name, parameters):
        return {
            "status": "success",
            "tool": tool_name,
            "model": "elo_boosted_ensemble",
            "parameters": parameters,
            "assumptions": [],
            "limitations": [],
            "data": {
                "home_team_id": 1610612738,
                "away_team_id": 1610612747,
                "prediction": {
                    "model": "elo_boosted_ensemble",
                    "home_win_probability": 0.7,
                    "away_win_probability": 0.3,
                    "home_team_prediction": "favorite",
                },
            },
        }

    monkeypatch.setattr("src.assistant.execute_tool", fake_execute)

    result = answer_question("Who is favored in Celtics vs Lakers?")

    assert result["tool"] == "predict_matchup"
    assert result["status"] == "success"
    assert "70.0%" in result["answer"]
    assert result["envelope"]["data"]["prediction"]["home_win_probability"] == 0.7


@pytest.mark.requires_data
def test_answer_question_unsupported_is_graceful(monkeypatch):
    result = answer_question("Tell me a joke about basketball")
    assert result["tool"] is None
    assert result["status"] == "error"
    assert "could not answer" in result["answer"]