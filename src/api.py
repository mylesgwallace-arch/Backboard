"""Minimal, dependency-free HTTP API over the validated tool layer.

This is the first piece of the website/API milestone (roadmap item 13). It
exposes the deterministic analytical tools and the natural-language assistant
over HTTP using only the Python standard library, so no new dependency is
required in the project environment. The API never fabricates answers: every
response is produced by ``src.tools.execute_tool`` or
``src.assistant.answer_question``.

Endpoints:

* ``GET  /health``            -> server + production model status
* ``GET  /tools``             -> tool registry (``list_tools``)
* ``POST /tools/{tool_name}`` -> ``{"parameters": {...}}`` -> tool envelope
* ``POST /ask``               -> ``{"question": "..."}``   -> assistant answer

The HTTP parsing is deliberately separated from ``handle_request``, a pure
function that takes ``(method, path, body)`` and returns ``(status_code, dict)``,
so the routing and error handling are fully testable without binding a socket.
A real socket server (``ThreadingHTTPServer``) is provided for local runs and
integration tests, with CORS headers so a future browser front end can call it.
"""

import argparse
import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from src.tools import PRODUCTION_MODEL, execute_tool, list_tools
    from src.assistant import answer_question
except ImportError:  # pragma: no cover - direct-script support
    from tools import PRODUCTION_MODEL, execute_tool, list_tools
    from assistant import answer_question


SERVICE_NAME = "nba-sports-ai"


def _parse_json_body(body):
    """Decode a bytes body to JSON, or return None for empty/missing bodies."""
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def _extract_parameters(body):
    """Return the tool parameters dict from a request body.

    Accepts either ``{"parameters": {...}}`` or a bare ``{...}`` object.
    """
    data = _parse_json_body(body)
    if isinstance(data, dict) and "parameters" in data:
        params = data["parameters"]
        return params if isinstance(params, dict) else {}
    if isinstance(data, dict):
        return data
    return {}


def handle_request(method, path, body=None):
    """Pure request router. Returns ``(status_code, response_dict)``.

    No socket is touched here; the HTTP server adapter calls this and serializes
    the result. This keeps all routing logic unit-testable in isolation.
    """
    if method not in ("GET", "POST", "OPTIONS"):
        return 405, {"error": "method_not_allowed", "method": method}

    parsed = urllib.parse.urlparse(path)
    route = parsed.path.rstrip("/") or "/"

    # Health / root.
    if method == "GET" and route in ("/", "/health"):
        return 200, {
            "status": "ok",
            "service": SERVICE_NAME,
            "model": PRODUCTION_MODEL,
        }

    # Tool registry.
    if method == "GET" and route == "/tools":
        return 200, {"tools": list_tools()}

    # Single tool execution.
    if method == "POST" and route.startswith("/tools/"):
        tool_name = route[len("/tools/"):]
        try:
            parameters = _extract_parameters(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return 400, {"error": "invalid_json",
                         "message": "Request body must be valid JSON."}
        envelope = execute_tool(tool_name, parameters)
        # Envelope already carries its own status; map error -> HTTP 400.
        status = 400 if envelope.get("status") == "error" else 200
        return status, envelope

    # Natural-language question.
    if method == "POST" and route == "/ask":
        try:
            data = _parse_json_body(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return 400, {"error": "invalid_json",
                         "message": "Request body must be valid JSON."}
        if not isinstance(data, dict) or not data.get("question"):
            return 400, {"error": "missing_field",
                         "message": "Provide a JSON body with a 'question'."}
        return 200, answer_question(data["question"])

    return 404, {"error": "not_found", "path": route}


class _Handler(BaseHTTPRequestHandler):
    # Quieter default logging for a local service.
    def log_message(self, *args):
        pass

    def _send(self, status, payload):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        status, payload = handle_request("GET", self.path, None)
        self._send(status, payload)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else None
        status, payload = handle_request("POST", self.path, body)
        self._send(status, payload)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Serve the NBA analytics tool layer over HTTP (stdlib only)."
    )
    parser.add_argument("--host", default="127.0.0.1",
                        help="Bind host (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8000,
                        help="Bind port (default: 8000).")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    server = ThreadingHTTPServer((args.host, args.port), _Handler)
    print(f"{SERVICE_NAME} API listening on http://{args.host}:{args.port}")
    print(f"  production model: {PRODUCTION_MODEL}")
    print("  endpoints: GET /health, GET /tools, "
          "POST /tools/<name>, POST /ask")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())