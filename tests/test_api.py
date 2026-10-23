"""Focused regression coverage for the HTTP API over the tool layer.

The tests exercise the pure ``handle_request`` router (no socket) plus one real
socket integration test, verifying that the API exposes ``execute_tool`` and the
natural-language assistant without fabricating answers. Heavy model-backed tools
are mocked; a cheap, real DB-backed tool (``resolve_team_name`` via /ask) is used
to prove the end-to-end path works against the repository database.
"""

import json
import threading
import urllib.error
import urllib.request

import pytest

import src.api as api


def test_health_reports_production_model():
    status, payload = api.handle_request("GET", "/health", None)
    assert status == 200
    assert payload["status"] == "ok"
    assert payload["model"] == "elo_boosted_ensemble"
    assert payload["service"] == "backboard"


def test_tools_endpoint_lists_registry():
    status, payload = api.handle_request("GET", "/tools", None)
    assert status == 200
    names = {tool["name"] for tool in payload["tools"]}
    assert {
        "predict_matchup", "simulate_season", "team_projection",
        "player_impact", "player_scenario", "team_record", "head_to_head",
        "resolve_team_name", "list_teams", "team_form", "team_elo_rating",
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


def test_ingest_routes_to_live_data_with_safe_default(monkeypatch):
    captured = {}

    def fake_ingest(source, db_path=None, dry_run=False):
        captured["source"] = source
        captured["dry_run"] = dry_run
        return {
            "source": source,
            "source_type": "file",
            "dry_run": dry_run,
            "rows_loaded": 0,
            "rows_skipped": 0,
            "validation_status": "passed",
            "checksum_sha256": "deadbeef",
            "notes": "mocked",
        }

    monkeypatch.setattr("src.api.ingest_schedule", fake_ingest)

    # Default dry_run is True (safe: no DB mutation without opt-in).
    status, payload = api.handle_request(
        "POST", "/ingest", json.dumps({"source": "data/raw/x.csv"}).encode("utf-8")
    )
    assert status == 200
    assert captured["source"] == "data/raw/x.csv"
    assert captured["dry_run"] is True
    assert payload["validation_status"] == "passed"

    # Explicit dry_run: false is honored.
    status, _ = api.handle_request(
        "POST", "/ingest",
        json.dumps({"source": "data/raw/x.csv", "dry_run": False}).encode("utf-8"),
    )
    assert captured["dry_run"] is False


def test_ingest_requires_source():
    status, payload = api.handle_request("POST", "/ingest", b"{}")
    assert status == 400
    assert payload["error"] == "missing_field"


def test_static_front_end_is_served():
    # The front-end shell must exist and load the app's JS entry point. The
    # actual API calls live in the JS modules under web/js/, not inline HTML.
    assert api.WEB_HTML.exists()
    html = api.WEB_HTML.read_text(encoding="utf-8")
    assert "POST /ask" not in html  # JS uses fetch('/ask'), not literal text
    assert 'type="module"' in html

    api_js = api.WEB_DIR / "js" / "api.js"
    assert api_js.exists()
    js = api_js.read_text(encoding="utf-8")
    assert "/ask" in js
    assert "/tools/" in js
    assert "/ingest" in js


def test_socket_serves_front_end_page():
    server = api.ThreadingHTTPServer(("127.0.0.1", 0), api._Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as resp:
            assert resp.status == 200
            assert resp.headers.get("Content-Type").startswith("text/html")
            body = resp.read().decode("utf-8")
            assert "NBA" in body
    finally:
        server.shutdown()


def test_socket_serves_static_css_and_js_assets():
    server = api.ThreadingHTTPServer(("127.0.0.1", 0), api._Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/styles.css") as resp:
            assert resp.status == 200
            assert resp.headers.get("Content-Type").startswith("text/css")

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/js/api.js") as resp:
            assert resp.status == 200
            assert resp.headers.get("Content-Type").startswith("text/javascript")
    finally:
        server.shutdown()


@pytest.mark.parametrize("header, expected", [
    (None, None),
    ("bytes=0-99", (0, 99)),
    ("bytes=0-", (0, 1023)),
    ("bytes=1000-5000", (1000, 1023)),
    ("bytes=-4", (1020, 1023)),
    ("bytes=-5000", (0, 1023)),
    ("BYTES=10-19", (10, 19)),
    ("bytes=1024-", "unsatisfiable"),
    ("bytes=-0", "unsatisfiable"),
    ("bytes=5-2", None),          # invalid spec: ignored, whole file
    ("bytes=0-1,4-5", None),      # multi-range: ignored, whole file
    ("bytes=--5", None),
    ("bytes=-", None),
    ("items=0-5", None),
])
def test_parse_byte_range(header, expected):
    assert api._parse_byte_range(header, 1024) == expected


def test_parse_byte_range_on_empty_file():
    assert api._parse_byte_range("bytes=0-", 0) == "unsatisfiable"
    assert api._parse_byte_range(None, 0) is None


def test_socket_serves_video_with_byte_ranges(tmp_path, monkeypatch):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(bytes(range(256)) * 4)  # 1024 bytes
    monkeypatch.setattr(api, "WEB_DIR", tmp_path)
    server = api.ThreadingHTTPServer(("127.0.0.1", 0), api._Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}/clip.mp4"
    try:
        with urllib.request.urlopen(url) as resp:
            assert resp.status == 200
            assert resp.headers.get("Content-Type") == "video/mp4"
            assert resp.headers.get("Accept-Ranges") == "bytes"
            assert len(resp.read()) == 1024

        request = urllib.request.Request(url, headers={"Range": "bytes=10-19"})
        with urllib.request.urlopen(request) as resp:
            assert resp.status == 206
            assert resp.headers.get("Content-Range") == "bytes 10-19/1024"
            assert resp.read() == bytes(range(10, 20))

        request = urllib.request.Request(url, headers={"Range": "bytes=-4"})
        with urllib.request.urlopen(request) as resp:
            assert resp.status == 206
            assert resp.headers.get("Content-Range") == "bytes 1020-1023/1024"

        request = urllib.request.Request(url, headers={"Range": "bytes=5-2"})
        with urllib.request.urlopen(request) as resp:
            assert resp.status == 200
            assert len(resp.read()) == 1024

        request = urllib.request.Request(url, headers={"Range": "bytes=5000-"})
        try:
            urllib.request.urlopen(request)
            raise AssertionError("expected HTTP 416")
        except urllib.error.HTTPError as err:
            assert err.code == 416
            assert err.headers.get("Content-Range") == "bytes */1024"
    finally:
        server.shutdown()


def test_static_asset_path_blocks_traversal_outside_web_dir():
    assert api._static_asset_path("/../src/api.py") is None
    assert api._static_asset_path("/../../etc/passwd") is None
    assert api._static_asset_path("/does-not-exist.js") is None
    assert api._static_asset_path("/styles.css") is not None


# ---- page paths, caching headers, memo and rate limits --------------------

def _serve():
    server = api.ThreadingHTTPServer(("127.0.0.1", 0), api._Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def test_every_front_end_route_has_a_server_page():
    from pathlib import Path
    import re
    main_js = (Path(api.WEB_DIR) / "js" / "main.js").read_text(encoding="utf-8")
    routes = set(re.findall(r'\{ path: "(/[a-z0-9-]+)"', main_js))
    assert routes, "ROUTES not found in main.js"
    assert routes == set(api.APP_PAGES)


def test_page_paths_serve_the_app_with_their_own_title(tmp_path):
    import sqlite3
    db = tmp_path / "nba.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE players (personId INTEGER, firstName TEXT, lastName TEXT)")
        conn.execute("INSERT INTO players VALUES (2544, 'LeBron', 'James')")
        conn.execute("CREATE TABLE team_histories (teamId INTEGER, teamCity TEXT, teamName TEXT, "
                     "seasonActiveTill INTEGER)")
    page = api.render_index("/players?id=2544", "https://example.org", db_path=db)
    assert "<title>LeBron James · Players · Backboard</title>" in page
    assert 'content="LeBron James: every season' in page
    assert '<link rel="canonical" href="https://example.org/players?id=2544">' in page
    assert api.page_meta("/standings")[0] == "Standings · Backboard"
    assert api.page_meta("/players?id=999", db_path=db)[0] == "Players · Backboard"
    assert api.page_meta("/dashboard")[0] == "Backboard — NBA Analytics"


def test_socket_serves_page_paths_sitemap_and_gzip():
    server, base = _serve()
    try:
        with urllib.request.urlopen(f"{base}/standings") as resp:
            assert resp.headers.get("Content-Type").startswith("text/html")
            assert "<title>Standings · Backboard</title>" in resp.read().decode("utf-8")
        with urllib.request.urlopen(f"{base}/sitemap.xml") as resp:
            text = resp.read().decode("utf-8")
            assert f"<loc>{base}/tonight</loc>" in text
        request = urllib.request.Request(f"{base}/styles.css", headers={"Accept-Encoding": "gzip"})
        with urllib.request.urlopen(request) as resp:
            assert resp.headers.get("Content-Encoding") == "gzip"
            import gzip
            assert b":root" in gzip.decompress(resp.read())
            etag = resp.headers.get("ETag")
        request = urllib.request.Request(f"{base}/styles.css", headers={"If-None-Match": etag})
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        assert caught.value.code == 304
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(f"{base}/not-a-page")
        assert caught.value.code == 404
    finally:
        server.shutdown()


def test_read_only_tool_results_are_memoised_until_data_changes(monkeypatch):
    calls = []

    def fake_execute(name, parameters):
        calls.append(name)
        return {"status": "success", "data": {"n": len(calls)}}

    monkeypatch.setattr(api, "execute_tool", fake_execute)
    api.clear_tool_cache()
    version = ["v1"]
    monkeypatch.setattr(api, "_data_version", lambda: tuple(version))
    first = api.cached_execute("standings", {"season": 2025})
    again = api.cached_execute("standings", {"season": 2025})
    assert first is again and calls == ["standings"]
    api.cached_execute("sandbox_simulate", {})
    api.cached_execute("sandbox_simulate", {})
    assert calls.count("sandbox_simulate") == 2  # not cacheable
    version[0] = "v2"
    api.cached_execute("standings", {"season": 2025})
    assert calls.count("standings") == 2
    api.clear_tool_cache()


def test_rate_limiter_refills_over_time():
    limiter = api.RateLimiter(per_minute=60, burst=2)
    assert limiter.allow("a", now=0.0) == (True, 0)
    assert limiter.allow("a", now=0.0) == (True, 0)
    allowed, retry = limiter.allow("a", now=0.0)
    assert not allowed and retry == 1
    assert limiter.allow("b", now=0.0)[0]  # per client
    assert limiter.allow("a", now=1.0)[0]


def test_client_keys_exempt_loopback_unless_behind_a_proxy(monkeypatch):
    monkeypatch.delenv("BACKBOARD_TRUST_PROXY", raising=False)
    assert api.client_key("127.0.0.1") is None
    assert api.client_key("203.0.113.9") == "203.0.113.9"
    monkeypatch.setenv("BACKBOARD_TRUST_PROXY", "1")
    assert api.client_key("127.0.0.1", "198.51.100.7, 10.0.0.1") == "198.51.100.7"
    assert api.limiter_for("/ask") is api.LIMITERS["ask"]
    assert api.limiter_for("/tools/sandbox_simulate") is api.LIMITERS["heavy"]
    assert api.limiter_for("/tools/standings") is api.LIMITERS["default"]


def test_rate_limited_post_gets_429(monkeypatch):
    monkeypatch.setenv("BACKBOARD_TRUST_PROXY", "1")
    monkeypatch.setitem(api.LIMITERS, "default", api.RateLimiter(per_minute=1, burst=1))
    monkeypatch.setattr(api, "execute_tool", lambda name, params: {"status": "success", "data": {}})
    server, base = _serve()
    try:
        def post():
            request = urllib.request.Request(
                f"{base}/tools/list_teams", data=b"{}", method="POST",
                headers={"Content-Type": "application/json", "X-Forwarded-For": "198.51.100.7"})
            return urllib.request.urlopen(request)
        with post() as resp:
            assert resp.status == 200
        with pytest.raises(urllib.error.HTTPError) as caught:
            post()
        assert caught.value.code == 429 and caught.value.headers.get("Retry-After")
    finally:
        server.shutdown()


def test_llm_questions_fall_back_past_the_daily_limit(monkeypatch):
    seen = []
    monkeypatch.setattr(api, "answer_question", lambda q, **options: seen.append(options) or {"answer": "ok"})
    monkeypatch.setattr(api, "LLM_QUESTIONS", api.DailyCounter())
    monkeypatch.setenv("BACKBOARD_LLM_DAILY_LIMIT", "1")
    body = json.dumps({"question": "Who wins?", "mode": "llm"}).encode()
    status, first = api.handle_request("POST", "/ask", body)
    status, second = api.handle_request("POST", "/ask", body)
    assert seen[0]["mode"] == "llm" and "notice" not in first
    assert seen[1]["mode"] == "deterministic" and second["notice"] == api.LLM_LIMIT_NOTICE


def test_client_disconnects_are_not_reported(capsys):
    server = api.Server(("127.0.0.1", 0), api._Handler)
    try:
        for exc in (ConnectionAbortedError("aborted"), BrokenPipeError(), ConnectionResetError()):
            try:
                raise exc
            except OSError:
                server.handle_error(None, ("127.0.0.1", 1))
        assert capsys.readouterr().err == ""
        try:
            raise ValueError("a real bug")
        except ValueError:
            server.handle_error(None, ("127.0.0.1", 1))
        assert "a real bug" in capsys.readouterr().err
    finally:
        server.server_close()
