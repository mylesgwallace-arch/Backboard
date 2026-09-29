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
* ``POST /ask``               -> ``{"question": "...", "mode"?: "deterministic"|"llm",
  "context"?: {...}}`` -> grounded multi-tool answer (``src.nl_agent``)
* ``POST /ingest``            -> ``{"source": "...", "dry_run": true}``
  -> provenanced schedule ingestion (``src.live_data``); defaults to dry-run so
  the UI cannot mutate the database without an explicit opt-in.

The HTTP parsing is deliberately separated from ``handle_request``, a pure
function that takes ``(method, path, body)`` and returns ``(status_code, dict)``,
so the routing and error handling are fully testable without binding a socket.
A real socket server (``ThreadingHTTPServer``) is provided for local runs and
integration tests.

Safety limits (``ServerConfig``). The server runs on the user's machine and
can write to the local database, so a web page on another site must not be
able to drive it:

* cross-origin POSTs are refused (403) unless the origin was allowed with
  ``--allow-origin``; CORS headers are sent only to allowed origins (the
  bundled front end is same-origin and needs none);
* POST bodies must be ``application/json`` (415 otherwise -- this also forces
  a CORS preflight for any cross-site request) and at most 64 KiB (413);
* ``/ingest`` with ``dry_run: false`` writes only when the server was started
  with ``--allow-ingest-writes`` (403 otherwise);
* questions are at most 1,000 characters; ``/ask`` and ``/tools`` are rate
  limited per client (429 with ``Retry-After``), with a tighter limit for the
  paid LLM mode.
"""

import argparse
import json
import re
import threading
import time
import urllib.parse
from collections import deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from src.tools import PRODUCTION_MODEL, execute_tool, list_tools
    from src.nl_agent import answer as answer_question
    from src.live_data import ingest_schedule
except ImportError:  # pragma: no cover - direct-script support
    from tools import PRODUCTION_MODEL, execute_tool, list_tools
    from nl_agent import answer as answer_question
    from live_data import ingest_schedule


ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "web"
WEB_HTML = WEB_DIR / "index.html"

SERVICE_NAME = "backboard"
MAX_BODY_BYTES = 64 * 1024
MAX_QUESTION_CHARS = 1000


@dataclass
class ServerConfig:
    """Per-server safety settings (see the module docstring)."""

    allowed_origins: frozenset = frozenset()
    allow_ingest_writes: bool = False
    # (requests, window seconds) per client and bucket.
    rate_limits: dict = field(default_factory=lambda: {
        "ask": (30, 60.0), "ask_llm": (6, 60.0), "tools": (240, 60.0),
    })


class RateLimiter:
    """Sliding-window request counter per (client, bucket); thread-safe."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._events = {}
        self._lock = threading.Lock()

    def check(self, client, bucket, limit, window):
        """``None`` if allowed (and counted), else seconds until a slot frees."""
        now = self._clock()
        with self._lock:
            events = self._events.setdefault((client, bucket), deque())
            while events and events[0] <= now - window:
                events.popleft()
            if len(events) >= limit:
                return max(1, int(events[0] + window - now + 0.999))
            events.append(now)
            return None


DEFAULT_CONFIG = ServerConfig()
RATE_LIMITER = RateLimiter()

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


def _rate_limited(client, bucket, config, limiter):
    if client is None or bucket not in config.rate_limits:
        return None
    limit, window = config.rate_limits[bucket]
    retry_after = limiter.check(client, bucket, limit, window)
    if retry_after is None:
        return None
    return 429, {"error": "rate_limited", "retry_after_seconds": retry_after,
                 "message": f"Too many requests; try again in {retry_after} s."}


def handle_request(method, path, body=None, client=None, config=None, limiter=None):
    """Pure request router. Returns ``(status_code, response_dict)``.

    No socket is touched here; the HTTP server adapter calls this and serializes
    the result. This keeps all routing logic unit-testable in isolation.
    ``client`` (e.g. the remote address) turns on per-client rate limiting;
    ``config`` defaults to ``DEFAULT_CONFIG``.
    """
    config = config or DEFAULT_CONFIG
    limiter = limiter or RATE_LIMITER
    if method not in ("GET", "POST", "OPTIONS"):
        return 405, {"error": "method_not_allowed", "method": method}
    if body is not None and len(body) > MAX_BODY_BYTES:
        return 413, {"error": "body_too_large", "max_bytes": MAX_BODY_BYTES}

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
        limited = _rate_limited(client, "tools", config, limiter)
        if limited:
            return limited
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
        if not isinstance(data["question"], str) or len(data["question"]) > MAX_QUESTION_CHARS:
            return 400, {"error": "question_too_long",
                         "message": f"Questions are limited to {MAX_QUESTION_CHARS} characters."}
        bucket = "ask_llm" if data.get("mode") == "llm" else "ask"
        limited = _rate_limited(client, bucket, config, limiter)
        if limited:
            return limited
        options = {}
        if data.get("mode") in ("deterministic", "llm"):
            options["mode"] = data["mode"]
        if isinstance(data.get("context"), dict):
            options["context"] = data["context"]
        return 200, answer_question(data["question"], **options)

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
        if not dry_run and not config.allow_ingest_writes:
            return 403, {"error": "ingest_writes_disabled",
                         "message": "This server only runs dry-run ingestion. Start it with "
                                    "--allow-ingest-writes, or run python src/live_data.py "
                                    "--source <file> locally."}
        manifest = ingest_schedule(data["source"], dry_run=dry_run)
        return 200, manifest

    return 404, {"error": "not_found", "path": route}


class _Handler(BaseHTTPRequestHandler):
    # Set per server (see ``make_server``); class default for tests.
    config = DEFAULT_CONFIG

    # Quieter default logging for a local service.
    def log_message(self, *args):
        pass

    def _origin(self):
        return self.headers.get("Origin")

    def _same_origin(self, origin):
        host = self.headers.get("Host")
        return bool(host) and origin in (f"http://{host}", f"https://{host}")

    def _origin_allowed(self, origin):
        return origin is None or self._same_origin(origin) or origin in self.config.allowed_origins

    def _cors_headers(self):
        origin = self._origin()
        if origin and origin in self.config.allowed_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send(self, status, payload, extra_headers=None):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self._cors_headers()
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        origin = self._origin()
        if origin and origin not in self.config.allowed_origins and not self._same_origin(origin):
            self._send(403, {"error": "origin_not_allowed", "origin": origin})
            return
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
        origin = self._origin()
        if not self._origin_allowed(origin):
            self._send(403, {"error": "origin_not_allowed", "origin": origin,
                             "message": "Cross-origin requests are not accepted; start the "
                                        "server with --allow-origin to allow one."})
            return
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type != "application/json":
            self._send(415, {"error": "unsupported_media_type",
                             "message": "POST bodies must be Content-Type: application/json."})
            return
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY_BYTES:
            self._send(413, {"error": "body_too_large", "max_bytes": MAX_BODY_BYTES})
            return
        body = self.rfile.read(length) if length else None
        status, payload = handle_request("POST", self.path, body, client=self.client_address[0],
                                         config=self.config)
        extra = ({"Retry-After": str(payload["retry_after_seconds"])}
                 if status == 429 else None)
        self._send(status, payload, extra)


def make_server(host, port, config=None):
    """A ``ThreadingHTTPServer`` whose handler uses ``config``."""
    handler = type("Handler", (_Handler,), {"config": config or DEFAULT_CONFIG})
    return ThreadingHTTPServer((host, port), handler)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Serve the NBA analytics tool layer over HTTP (stdlib only)."
    )
    parser.add_argument("--host", default="127.0.0.1",
                        help="Bind host (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8000,
                        help="Bind port (default: 8000).")
    parser.add_argument("--allow-origin", action="append", default=[],
                        help="Allow cross-origin requests from this origin (repeatable), "
                             "e.g. http://localhost:4173.")
    parser.add_argument("--allow-ingest-writes", action="store_true",
                        help="Let POST /ingest with dry_run=false write to the database.")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    config = ServerConfig(allowed_origins=frozenset(args.allow_origin),
                          allow_ingest_writes=args.allow_ingest_writes)
    server = make_server(args.host, args.port, config)
    print(f"{SERVICE_NAME} API listening on http://{args.host}:{args.port}")
    print(f"  ingest writes: {'enabled' if config.allow_ingest_writes else 'disabled (dry-run only)'}"
          f"; cross-origin: {', '.join(sorted(config.allowed_origins)) or 'none'}")
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