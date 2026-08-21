"""Focused regression coverage for the HTTP API over the tool layer.

The tests exercise the pure ``handle_request`` router (no socket) plus one real
socket integration test, verifying that the API exposes ``execute_tool`` and the
natural-language assistant without fabricating answers. Heavy model-backed tools
are mocked; a cheap, real DB-backed tool (``resolve_team_name`` via /ask) is used
to prove the end-to-end path works against the repository database.
"""

import json
import threading
import urllib.request

import pytest

import src.api as api


def test_health_reports_production_model():
    status, payload = api.handle_request("GET", "/health", None)
    assert status == 200
    assert payload["status"] == "ok"
    assert payload["model"] == "elo_boosted_ensemble"
    assert payload["service"] == "nba-sports-ai"


def test_tools_endpoint_lists_registry():
    status, payload = api.handle_request("GET", "/tools", None)
    assert status == 200
    names = {tool["name"] for tool in payload["tools"]}
    assert {
        "predict_matchup", "simulate_season", "team_projection",
        "player_impact", "player_scenario", "team_record", "head_to_head",
        "resolve_team_name",
    } <= names


def test_tool_execution_routes_to_execute_tool(monkeypatch):
    captured = {}

    def fake_execute(tool_name, parameters=None):
        captured["tool_name"] = tool_name
        captured["parameters"] = parameters
        return {
            "tool": tool_name,
            "status": "success",
            "operation": "test",
            "model": "elo_boosted_ensemble",
            "assumptions": [],
            "limitations": [],
            "parameters": parameters,
            "data": {"echo": True},
        }

    monkeypatch.setattr("src.api.execute_tool", fake_execute)

    body = json.dumps(
        {"parameters": {"home_team_id": 1610612738, "away_team_id": 1610612747}}
    ).encode("utf-8")
    status, payload = api.handle_request("POST", "/tools/predict_matchup", body)

    assert status == 200
    assert captured["tool_name"] == "predict_matchup"
    # The bare body object is also accepted as parameters.
    assert captured["parameters"] == {
        "home_team_id": 1610612738, "away_team_id": 1610612747
    }
    assert payload["data"]["echo"] is True


def test_unknown_tool_returns_400_with_error_envelope(monkeypatch):
    def fake_execute(tool_name, parameters=None):
        return {
            "tool": tool_name,
            "status": "error",
            "operation": None,
            "model": None,
            "assumptions": [],
            "limitations": [],
            "error": {"type": "UnknownTool",
                      "message": f"Unknown tool '{tool_name}'."},
            "data": None,
        }

    monkeypatch.setattr("src.api.execute_tool", fake_execute)
    status, payload = api.handle_request(
        "POST", "/tools/does_not_exist", b"{}"
    )
    assert status == 400
    assert payload["status"] == "error"
    assert payload["error"]["type"] == "UnknownTool"


def test_ask_routes_to_assistant(monkeypatch):
    captured = {}

    def fake_ask(question):
        captured["question"] = question
        return {
            "question": question,
            "tool": "resolve_team_name",
            "parameters": {"team": question},
            "status": "success",
            "answer": "Boston Celtics has teamId 1610612738.",
            "envelope": {"status": "success"},
        }

    monkeypatch.setattr("src.api.answer_question", fake_ask)
    status, payload = api.handle_request(
        "POST", "/ask", json.dumps({"question": "Boston Celtics"}).encode("utf-8")
    )
    assert status == 200
    assert captured["question"] == "Boston Celtics"
    assert payload["answer"].startswith("Boston Celtics")


def test_ask_requires_question_field():
    status, payload = api.handle_request("POST", "/ask", b"{}")
    assert status == 400
    assert payload["error"] == "missing_field"


def test_invalid_json_returns_400():
    status, payload = api.handle_request("POST", "/tools/x", b"{not json")
    assert status == 400
    assert payload["error"] == "invalid_json"


def test_unknown_route_returns_404():
    status, payload = api.handle_request("GET", "/nope", None)
    assert status == 404
    assert payload["error"] == "not_found"


def test_method_not_allowed():
    status, _ = api.handle_request("DELETE", "/tools", None)
    assert status == 405


def test_real_ask_resolves_team_without_model_load():
    # Real, cheap end-to-end path: resolve_team_name hits the DB only.
    status, payload = api.handle_request(
        "POST", "/ask",
        json.dumps({"question": "What is the team id for the Boston Celtics?"}).encode("utf-8"),
    )
    assert status == 200
    assert payload["tool"] == "resolve_team_name"
    assert "1610612738" in payload["answer"]


def test_socket_integration_server():
    server = api.ThreadingHTTPServer(("127.0.0.1", 0), api._Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health") as resp:
            assert resp.status == 200
            assert resp.headers.get("Content-Type") == "application/json"
            assert resp.headers.get("Access-Control-Allow-Origin") == "*"
            data = json.loads(resp.read())
            assert data["model"] == "elo_boosted_ensemble"
    finally:
        server.shutdown()
