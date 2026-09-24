"""Natural-language layer: planning, grounded rendering, and the LLM tool loop.

Planning uses the real read-only database for team/player names (fast);
tool execution is monkeypatched so no model runs. The LLM loop is driven by a
scripted fake client shaped like the Anthropic SDK's responses, so it is
tested without network access or credentials.
"""

from types import SimpleNamespace

import pytest

from src import nl_agent
from src.nl_agent import answer, plan_question, tool_schemas

CELTICS, LAKERS, KNICKS = 1610612738, 1610612747, 1610612752


def tools_of(question, context=None):
    return [step["tool"] for step in plan_question(question, context)]


def test_single_and_multi_tool_plans():
    assert tools_of("Who is favored in Celtics vs Lakers?") == ["predict_matchup"]
    assert sorted(tools_of("What is the predicted score of Celtics vs Lakers, and who wins?")) == [
        "predict_margin", "predict_matchup"]
    assert sorted(set(tools_of("Compare the Knicks and Celtics: 2025 records, head-to-head, and who is "
                               "favored if the Knicks host the Celtics?"))) == [
        "head_to_head", "predict_matchup", "team_record"]
    assert tools_of("How good is the prediction model overall?") == ["validation_report"]


def test_home_and_away_follow_the_wording():
    visit = plan_question("Who's favored when the Celtics visit the Knicks?")[0]["parameters"]
    assert (visit["home_team_id"], visit["away_team_id"]) == (KNICKS, CELTICS)
    host = plan_question("Who is favored if the Knicks host the Celtics?")[0]["parameters"]
    assert (host["home_team_id"], host["away_team_id"]) == (KNICKS, CELTICS)


def test_dates_seasons_and_players_are_extracted():
    step = plan_question("As of January 15, 2026, what did the rest-of-season projection look like "
                         "for the Spurs?")[0]
    assert step["tool"] == "project_rest_of_season"
    assert step["parameters"]["as_of"] == "2026-01-15"
    assert step["parameters"]["season"] == 2025
    stats = plan_question("How many points per game did Stephen Curry average in 2015-16?")[0]
    assert stats == {"tool": "player_season_stats", "parameters": {"person_id": 201939, "season": 2015},
                     "focus": None}


def test_ambiguous_references_ask_instead_of_guessing():
    with pytest.raises(ValueError, match="Los Angeles"):
        plan_question("Who is favored in Los Angeles vs Denver?")
    with pytest.raises(ValueError, match="Curry' matches"):
        plan_question("How many points did Curry average in 2015?")
    with pytest.raises(ValueError, match="could not map"):
        plan_question("What is the meaning of life?")


def test_follow_up_reuses_context_teams():
    steps = plan_question("What would the model have predicted for that matchup on 2026-04-12?",
                          context={"teams": [CELTICS, LAKERS]})
    assert steps[0]["tool"] == "predict_matchup"
    assert steps[0]["parameters"] == {"home_team_id": CELTICS, "away_team_id": LAKERS,
                                      "game_date": "2026-04-12"}


def _fake_execute(tool, parameters):
    data = {
        "predict_matchup": {"home_team_id": CELTICS, "away_team_id": LAKERS,
                            "prediction": {"model": "elo_boosted_ensemble", "home_win_probability": 0.61,
                                           "away_win_probability": 0.39}},
        "team_record": {"team_id": parameters.get("team_id"), "season": 2025, "wins": 56,
                        "losses": 26, "games": 82},
    }[tool]
    return {"tool": tool, "status": "success", "model": "m", "operation": "op",
            "assumptions": [], "limitations": ["A probability is not a guarantee."],
            "parameters": parameters, "data": data}


def test_deterministic_answer_is_sectioned_and_grounded(monkeypatch):
    monkeypatch.setattr(nl_agent, "execute_tool", _fake_execute)
    result = answer("What was Boston's 2025 record, and who is favored in Celtics vs Lakers?")
    tools = [step["tool"] for step in result["plan"]]
    assert tools[0] == "team_record" and tools[-1] == "predict_matchup"
    assert result["status"] == "success"
    assert result["grounding"]["grounded"]
    assert "Facts (from the database)" in result["answer"]
    assert "Model output" in result["answer"]
    assert "61.0%" in result["answer"] and "56-26" in result["answer"]
    # Legacy keys for existing /ask callers.
    assert result["tool"] == "team_record"
    assert result["envelope"]["data"]["wins"] == 56
    assert result["context"]["teams"] == [CELTICS, LAKERS]


def test_unsupported_question_is_a_clear_error():
    result = answer("Tell me a joke about basketball")
    assert result["status"] == "error"
    assert result["tool"] is None
    assert "could not answer" in result["answer"]


def test_tool_schemas_mirror_the_registry():
    schemas = {schema["name"]: schema for schema in tool_schemas()}
    assert set(schemas) == set(nl_agent.TOOLS)
    matchup = schemas["predict_matchup"]["input_schema"]
    assert matchup["properties"]["home_team_id"]["type"] == "integer"
    assert matchup["additionalProperties"] is False
    assert schemas["simulate_season"]["input_schema"]["required"] == ["season"]
    assert schemas["team_strength"]["input_schema"]["properties"]["roster_adjusted"]["type"] == "boolean"


# ---------------------------------------------------------------------------
# LLM loop with a scripted client
# ---------------------------------------------------------------------------

class ScriptedClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def _text(text):
    return SimpleNamespace(type="text", text=text)


def _tool_use(name, arguments, block_id="tu_1"):
    return SimpleNamespace(type="tool_use", name=name, input=arguments, id=block_id)


def _response(stop_reason, *blocks):
    return SimpleNamespace(stop_reason=stop_reason, content=list(blocks))


def test_llm_loop_calls_tools_and_returns_grounded_answer(monkeypatch):
    monkeypatch.setattr(nl_agent, "execute_tool", _fake_execute)
    client = ScriptedClient([
        _response("tool_use", _text("Checking."), _tool_use(
            "predict_matchup", {"home_team_id": CELTICS, "away_team_id": LAKERS})),
        _response("end_turn", _text("Model output: Boston 61% vs Los Angeles 39%.")),
    ])
    result = answer("Who wins Celtics vs Lakers?", mode="llm", client=client)
    assert result["mode"] == "llm"
    assert result["plan"] == [{"tool": "predict_matchup",
                               "parameters": {"home_team_id": CELTICS, "away_team_id": LAKERS}}]
    assert result["grounding"]["grounded"]
    first_call = client.calls[0]
    assert first_call["model"] == "claude-opus-5"
    assert first_call["thinking"] == {"type": "adaptive"}
    assert first_call["fallbacks"] == "default"
    # The tool result went back as a user turn with the matching tool_use_id.
    tool_turn = client.calls[1]["messages"][-1]
    assert tool_turn["role"] == "user"
    assert tool_turn["content"][0]["tool_use_id"] == "tu_1"
    assert '"home_win_probability":0.61' in tool_turn["content"][0]["content"]


def test_llm_invented_numbers_get_one_retry_then_a_warning(monkeypatch):
    monkeypatch.setattr(nl_agent, "execute_tool", _fake_execute)
    client = ScriptedClient([
        _response("tool_use", _tool_use("predict_matchup", {"home_team_id": CELTICS,
                                                            "away_team_id": LAKERS})),
        _response("end_turn", _text("Boston 61% and they average 118 points.")),
        _response("end_turn", _text("Boston 61%, still 118 points.")),
    ])
    result = answer("Who wins Celtics vs Lakers?", mode="llm", client=client)
    retry_prompt = client.calls[2]["messages"][-1]["content"]
    assert "118" in retry_prompt
    assert not result["grounding"]["grounded"]
    assert "could not be traced" in result["answer"]


def test_llm_failures_fall_back_to_the_deterministic_planner(monkeypatch):
    monkeypatch.setattr(nl_agent, "execute_tool", _fake_execute)
    refusing = ScriptedClient([_response("refusal")])
    result = answer("Who is favored in Celtics vs Lakers?", mode="llm", client=refusing)
    assert result["mode"] == "deterministic"
    assert "declined" in result["fallback_reason"]

    def no_sdk():
        raise ImportError("No module named 'anthropic'")

    monkeypatch.setattr(nl_agent, "_default_client", no_sdk)
    result = answer("Who is favored in Celtics vs Lakers?", mode="llm")
    assert result["mode"] == "deterministic"
    assert "ImportError" in result["fallback_reason"]
    assert result["status"] == "success"


def test_what_if_swap_questions_become_one_era_swap_call():
    steps = plan_question("How would the 1993 Chicago Bulls change if they had 2016 Steph Curry "
                          "instead of B.J. Armstrong?")
    assert [s["tool"] for s in steps] == ["simulate_era_swap"]
    params = steps[0]["parameters"]
    # A bare year is the season ending that year: '1993' -> 1992-93, '2016' -> 2015-16.
    assert params == {"team_id": 1610612741, "season": 1992, "out_person_id": 769,
                      "in_person_id": 201939, "in_season": 2015}
    explicit = plan_question("What if the 1992-93 Bulls had 2015-16 Stephen Curry in place of "
                             "B.J. Armstrong?")[0]["parameters"]
    assert explicit["season"] == 1992 and explicit["in_season"] == 2015
    apostrophe = plan_question("What if the '96 Bulls had 2006 Kobe Bryant in place of Ron Harper?")
    assert apostrophe[0]["parameters"]["season"] == 1995


def test_what_if_without_the_replaced_player_asks():
    with pytest.raises(ValueError, match="name both players"):
        plan_question("What if the 1992-93 Bulls had 2015-16 Stephen Curry instead of somebody?")
