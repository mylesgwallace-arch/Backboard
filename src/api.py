"""Minimal, dependency-free HTTP API over the validated tool layer.

This is the first piece of the website/API milestone (roadmap item 13). It
exposes the deterministic analytical tools and the natural-language assistant
over HTTP using only the Python standard library, so no new dependency is
required in the project environment. The API never fabricates answers: every
response is produced by ``src.tools.execute_tool`` or
``src.assistant.answer_question``.

Endpoints:

* ``GET  /``  (and ``/app``, ``/index.html``) -> the browser front-end page
* ``GET  /<path>``            -> any other static asset under ``web/`` (CSS,
  vanilla-JS ES modules, etc.), served with an explicit content-type map so
  the dashboard front end can be a normal multi-file static app with no
  build step
* ``GET  /health``            -> server + production model status
* ``GET  /tools``             -> tool registry (``list_tools``)
* ``POST /tools/{tool_name}`` -> ``{"parameters": {...}}`` -> tool envelope
* ``POST /ask``               -> ``{"question": "..."}``   -> assistant answer
* ``POST /ingest``            -> ``{"source": "...", "dry_run": true}``
  -> provenanced schedule ingestion (``src.live_data``); defaults to dry-run so
  the UI cannot mutate the database without an explicit opt-in.

The HTTP parsing is deliberately separated from ``handle_request``, a pure
function that takes ``(method, path, body)`` and returns ``(status_code, dict)``,
so the routing and error handling are fully testable without binding a socket.
A real socket server (``ThreadingHTTPServer``) is provided for local runs and
integration tests, with CORS headers so a future browser front end can call it.
"""

import argparse
import json
import re
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from src.tools import PRODUCTION_MODEL, execute_tool, list_tools
    from src.assistant import answer_question
    from src.live_data import ingest_schedule
except ImportError:  # pragma: no cover - direct-script support
    from tools import PRODUCTION_MODEL, execute_tool, list_tools
    from assistant import answer_question
    from live_data import ingest_schedule


ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "web"
WEB_HTML = WEB_DIR / "index.html"

SERVICE_NAME = "backboard"

# The front end is a static, no-build-step, multi-file app (HTML/CSS/vanilla
# JS ES modules) under web/. Content types are mapped explicitly rather than
# relying on the OS mimetypes registry, which is inconsistent across
# platforms (notably Windows) for .js/.css.
_STATIC_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
}


_BYTE_RANGE = re.compile(r"bytes=(\d*)-(\d*)", re.ASCII | re.IGNORECASE)


def _parse_byte_range(header, size):
    """Parse a single ``Range: bytes=start-end`` header against ``size``.

    Returns ``(start, end)`` (inclusive), ``None`` to serve the whole file
    (no header, a multi-range or malformed header, or an invalid spec such
    as ``bytes=5-2``, all of which RFC 9110 lets a server ignore), or
    ``"unsatisfiable"`` when the range starts past the end of the file.
    Browsers send ranges for <video>; Safari will not play a video from a
    server that ignores them.
    """
    match = _BYTE_RANGE.fullmatch(header.strip()) if header else None
    if match is None:
        return None
    start_text, end_text = match.groups()
    if not start_text:  # suffix range: the last N bytes
        if not end_text:
            return None
        length = int(end_text)
        if length == 0 or size == 0:
            return "unsatisfiable"
        return max(0, size - length), size - 1
    start = int(start_text)
    if end_text and int(end_text) < start:
        return None
    if start >= size:
        return "unsatisfiable"
    end = min(int(end_text), size - 1) if end_text else size - 1
    return start, end


def _static_asset_path(url_path):
    """Resolve a GET path to a file under web/, or None if not a static asset.

    Guards against path traversal by requiring the resolved path to stay
    inside ``WEB_DIR``.
    """
    candidate = urllib.parse.unquote(url_path).lstrip("/")
    if not candidate:
        return None
    target = (WEB_DIR / candidate).resolve()
    try:
        target.relative_to(WEB_DIR.resolve())
    except ValueError:
        return None
    if target.is_file():
        return target
    return None


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

    # Source-provenanced live-data ingestion (defaults to a safe dry-run).
    if method == "POST" and route == "/ingest":
        try:
            data = _parse_json_body(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return 400, {"error": "invalid_json",
                         "message": "Request body must be valid JSON."}
        if not isinstance(data, dict) or not data.get("source"):
            return 400, {"error": "missing_field",
                         "message": "Provide a JSON body with a 'source'."}
        dry_run = bool(data.get("dry_run", True))
        manifest = ingest_schedule(data["source"], dry_run=dry_run)
        return 200, manifest

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

    def _serve_html(self):
        if not WEB_HTML.exists():
            self._send(404, {"error": "not_found",
                             "message": "web/index.html not found."})
            return
        body = WEB_HTML.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, path):
        content_type = _STATIC_CONTENT_TYPES.get(
            path.suffix.lower(), "application/octet-stream"
        )
        size = path.stat().st_size
        byte_range = _parse_byte_range(self.headers.get("Range"), size)
        if byte_range == "unsatisfiable":
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            return
        if byte_range is None:
            start, end, status = 0, size - 1, 200
        else:
            (start, end), status = byte_range, 206
        with path.open("rb") as handle:
            handle.seek(start)
            body = handle.read(end - start + 1) if size else b""
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        if route in ("/", "/app", "/index.html"):
            self._serve_html()
            return
        static_path = _static_asset_path(parsed.path)
        if static_path is not None:
            self._serve_static(static_path)
            return
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
    print("  endpoints: GET / (front end), GET /health, GET /tools, "
          "POST /tools/<name>, POST /ask, POST /ingest")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())