"""Minimal, dependency-free HTTP API over the validated tool layer.

This is the first piece of the website/API milestone (roadmap item 13). It
exposes the deterministic analytical tools and the natural-language assistant
over HTTP using only the Python standard library, so no new dependency is
required in the project environment. The API never fabricates answers: every
response is produced by ``src.tools.execute_tool`` or
``src.assistant.answer_question``.

Endpoints:

* ``GET  /``  (and ``/app``, ``/index.html``) -> the browser front-end page
* ``GET  /<page>``            -> a front-end page path (``APP_PAGES``, e.g.
  ``/players?id=2544``) serves the same page with that page's title,
  description and Open Graph tags, so links are shareable and indexable;
  ``/sitemap.xml`` and ``/robots.txt`` list them
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

Serving details: text responses are gzip-compressed when the client accepts
it; static files carry an ``ETag`` (``If-None-Match`` gets a 304); read-only
tool results are memoised until the database or the feature file changes
(``CACHEABLE_TOOLS``); and a per-client token bucket limits ``/ask``, the
slow simulation tools and everything else (``RateLimiter``). Loopback clients
are exempt unless ``BACKBOARD_TRUST_PROXY=1``, which keys clients by
``X-Forwarded-For`` behind a reverse proxy; ``BACKBOARD_RATE_LIMIT=off``
turns the limits off. LLM-mode questions are capped per day
(``BACKBOARD_LLM_DAILY_LIMIT``, default 300); past the cap a question is
answered by the deterministic planner, with a notice.

The HTTP parsing is deliberately separated from ``handle_request``, a pure
function that takes ``(method, path, body)`` and returns ``(status_code, dict)``,
so the routing and error handling are fully testable without binding a socket.
A real socket server (``ThreadingHTTPServer``) is provided for local runs and
integration tests, with CORS headers so a future browser front end can call it.
"""

import argparse
import gzip
import hashlib
import html
import json
import os
import re
import sqlite3
import threading
import time
import urllib.parse
from collections import OrderedDict
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from src.tools import (
        FEATURES_PATH, PRODUCTION_MODEL, TEAM_DB_PATH, enable_disk_cache, execute_tool, list_tools,
    )
    from src.nl_agent import answer as answer_question
    from src.live_data import ingest_schedule
except ImportError:  # pragma: no cover - direct-script support
    from tools import (
        FEATURES_PATH, PRODUCTION_MODEL, TEAM_DB_PATH, enable_disk_cache, execute_tool, list_tools,
    )
    from nl_agent import answer as answer_question
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
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
    ".xml": "application/xml; charset=utf-8",
    ".webmanifest": "application/manifest+json",
}
_COMPRESSIBLE = ("text/", "application/json", "application/xml", "image/svg+xml",
                 "application/manifest+json")
_COMPRESS_MIN_BYTES = 1024
# Media is large and rarely changes: browsers may keep it for a week. Code and
# styles revalidate every time (cheap with the ETag), so a deploy shows at once.
_LONG_CACHE_SUFFIXES = {".mp4", ".webm", ".jpg", ".jpeg", ".png", ".webp", ".woff2", ".ico"}


# ---------------------------------------------------------------------------
# Front-end pages (keep in step with ROUTES in web/js/main.js)
# ---------------------------------------------------------------------------

SITE_NAME = "Backboard"
DEFAULT_DESCRIPTION = (
    "Backboard: NBA win probabilities, team and player analytics, season "
    "simulations and a what-if Sandbox, from a validated prediction engine "
    "that shows its work."
)
APP_PAGES = {
    "/dashboard": ("Backboard — NBA Analytics", DEFAULT_DESCRIPTION),
    "/tonight": ("Tonight", "Every game today with the model's pick, the predicted score, "
                            "live win probability and the final results."),
    "/matchups": ("Matchups", "Win probability, projected score and the case for each side "
                              "in any NBA matchup."),
    "/teams": ("Teams", "Elo, form, records, schedule, projections and head-to-head history "
                        "for every NBA franchise."),
    "/players": ("Players", "Choose a player: every season, game log, league percentiles, "
                            "comparables and outlook."),
    "/compare": ("Compare players", "Two players head to head: ratings, seasons, career arcs "
                                    "and the most similar players in history."),
    "/leaders": ("League leaders", "Who leads the NBA in scoring, rebounding, shooting and the "
                                   "box-impact model."),
    "/standings": ("Standings", "Conference standings with games behind, streaks, home and road "
                                "records and schedule difficulty."),
    "/sandbox": ("Sandbox", "Trade anyone, sign anyone from any era and simulate the season "
                            "with and without the moves."),
    "/gallery": ("Scenario gallery", "Ready-made Sandbox scenarios and the ones you saved, "
                                     "one click to open."),
    "/simulator": ("Season simulator", "Monte Carlo season simulations: projected standings, "
                                       "seeds, the play-in and the bracket."),
    "/playoffs": ("Playoffs", "Playoff and title odds from the real bracket or a simulated rest "
                              "of season, and how they moved."),
    "/report-card": ("Model report card", "How the model's pregame picks have done: accuracy, "
                                          "calibration and its biggest misses."),
    "/assistant": ("Ask", "Ask an NBA question in plain words; every number is traced to the "
                          "tool and model that produced it."),
    "/current-season": ("Current season", "Where every team stands now: strength, roster "
                                          "changes, injuries and the season projection."),
    "/head-to-head": ("Head-to-head", "Any two franchises' regular-season record, all-time and "
                                      "season by season."),
    "/league-predictions": ("League predictions", "The projected playoff picture and full "
                                                  "standings for the season."),
    "/player-impact": ("Player impact", "How a team has done with and without a player: an "
                                        "association-only diagnostic."),
    "/what-if": ("What-if lab", "Put any player's season, translated to another era, on any "
                                "team and see the projected change."),
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


def _etag(path):
    stat = path.stat()
    return '"' + hashlib.sha1(f"{stat.st_mtime_ns}-{stat.st_size}".encode()).hexdigest()[:20] + '"'


# ---------------------------------------------------------------------------
# Page paths, titles and the sitemap
# ---------------------------------------------------------------------------

def page_route(url_path):
    """The ``APP_PAGES`` key for a front-end page path, or None."""
    route = urllib.parse.urlparse(url_path).path.rstrip("/") or "/"
    return route if route in APP_PAGES else None


def _lookup_name(sql, value, db_path=None):
    try:
        uri = f"file:{db_path or TEAM_DB_PATH}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            row = connection.execute(sql, (int(value),)).fetchone()
    except (sqlite3.Error, ValueError, TypeError):
        return None
    return " ".join(str(part) for part in row if part).strip() if row else None


def page_meta(url_path, db_path=None):
    """``(title, description)`` for a front-end page, naming the linked player or team."""
    parsed = urllib.parse.urlparse(url_path)
    route = page_route(url_path) or "/dashboard"
    title, description = APP_PAGES[route]
    if route == "/dashboard":
        return title, description
    query = urllib.parse.parse_qs(parsed.query)
    subject = None
    if route == "/players" and query.get("id"):
        subject = _lookup_name("SELECT firstName, lastName FROM players WHERE personId = ?",
                               query["id"][0], db_path)
        if subject:
            description = f"{subject}: every season, game log, league percentiles, comparables and outlook."
    elif route == "/teams" and query.get("team"):
        subject = _lookup_name("SELECT teamCity, teamName FROM team_histories "
                               "WHERE teamId = ? AND seasonActiveTill >= 2100",
                               query["team"][0], db_path)
        if subject:
            description = f"{subject}: Elo, form, records, schedule, projections and head-to-head history."
    full = f"{subject} · {title}" if subject else title
    return f"{full} · {SITE_NAME}", description


def render_index(url_path="/", base_url="", db_path=None):
    """index.html with the page's title, description and Open Graph tags filled in."""
    page = WEB_HTML.read_text(encoding="utf-8")
    title, description = page_meta(url_path, db_path)
    title_html = html.escape(title)
    description_html = html.escape(description, quote=True)
    page = re.sub(r"<title>.*?</title>", lambda _: f"<title>{title_html}</title>", page, count=1, flags=re.S)
    page = re.sub(r'<meta name="description" content="[^"]*">',
                  lambda _: f'<meta name="description" content="{description_html}">', page, count=1)
    parsed = urllib.parse.urlparse(url_path)
    canonical = base_url + (parsed.path if page_route(url_path) else "/")
    if parsed.query and page_route(url_path):
        canonical += "?" + parsed.query
    canonical_html = html.escape(canonical, quote=True)
    tags = "\n  ".join([
        f'<meta property="og:site_name" content="{SITE_NAME}">',
        '<meta property="og:type" content="website">',
        f'<meta property="og:title" content="{title_html}">',
        f'<meta property="og:description" content="{description_html}">',
        f'<meta property="og:image" content="{html.escape(base_url)}/media/zine/still-166.jpg">',
        f'<meta property="og:url" content="{canonical_html}">',
        '<meta name="twitter:card" content="summary_large_image">',
        f'<link rel="canonical" href="{canonical_html}">',
    ])
    return page.replace("<!-- page-meta -->", tags, 1)


def sitemap_xml(base_url):
    today = date.today().isoformat()
    urls = "".join(
        f"  <url><loc>{html.escape(base_url + path)}</loc><lastmod>{today}</lastmod></url>\n"
        for path in APP_PAGES
    )
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + urls + "</urlset>\n")


# ---------------------------------------------------------------------------
# Tool result memo, rate limits and the LLM budget
# ---------------------------------------------------------------------------

# Read-only tools whose result depends only on their parameters and the data
# on disk. A result is kept until the database or the feature file changes,
# or the day rolls over (several tools default to "today").
CACHEABLE_TOOLS = {
    "list_teams", "team_record", "head_to_head", "team_form", "team_elo_rating", "team_strength",
    "standings", "scoreboard", "team_schedule", "strength_of_schedule", "model_report_card",
    "stat_leaders", "player_cards", "similar_players", "player_profile", "player_game_log",
    "player_outlook", "player_season_stats", "team_season_roster", "validation_report",
    "playoff_odds", "simulate_season", "team_projection", "project_rest_of_season",
    "odds_history", "injury_report", "predict_matchup", "predict_margin", "resolve_player",
}
TOOL_CACHE_SIZE = 256
_TOOL_CACHE = OrderedDict()
_TOOL_CACHE_LOCK = threading.Lock()


def _data_version():
    stamps = []
    for path in (TEAM_DB_PATH, FEATURES_PATH):
        try:
            stamps.append(Path(path).stat().st_mtime_ns)
        except OSError:
            stamps.append(None)
    return (*stamps, date.today().isoformat())


def cached_execute(tool_name, parameters):
    """``execute_tool`` with a memo for read-only tools (successful results only)."""
    if tool_name not in CACHEABLE_TOOLS:
        return execute_tool(tool_name, parameters)
    try:
        key = (tool_name, json.dumps(parameters, sort_keys=True), _data_version())
    except (TypeError, ValueError):
        return execute_tool(tool_name, parameters)
    with _TOOL_CACHE_LOCK:
        if key in _TOOL_CACHE:
            _TOOL_CACHE.move_to_end(key)
            return _TOOL_CACHE[key]
    envelope = execute_tool(tool_name, parameters)
    if envelope.get("status") == "success":
        with _TOOL_CACHE_LOCK:
            _TOOL_CACHE[key] = envelope
            while len(_TOOL_CACHE) > TOOL_CACHE_SIZE:
                _TOOL_CACHE.popitem(last=False)
    return envelope


def clear_tool_cache():
    with _TOOL_CACHE_LOCK:
        _TOOL_CACHE.clear()


class RateLimiter:
    """Token bucket per client: ``per_minute`` sustained, up to ``burst`` at once."""

    def __init__(self, per_minute, burst=None):
        self.rate = per_minute / 60.0
        self.capacity = float(burst if burst is not None else max(1, per_minute // 2))
        self.buckets = {}
        self.lock = threading.Lock()

    def allow(self, key, now=None):
        """Returns ``(allowed, retry_after_seconds)``."""
        now = time.monotonic() if now is None else now
        with self.lock:
            tokens, stamp = self.buckets.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - stamp) * self.rate)
            if tokens >= 1.0:
                self.buckets[key] = (tokens - 1.0, now)
                if len(self.buckets) > 10000:  # forget idle clients
                    self.buckets = {k: v for k, v in self.buckets.items() if now - v[1] < 600}
                return True, 0
            self.buckets[key] = (tokens, now)
            return False, max(1, int((1.0 - tokens) / self.rate + 0.999))


HEAVY_TOOLS = {"simulate_season", "team_projection", "project_rest_of_season", "playoff_odds",
               "sandbox_simulate", "simulate_era_swap", "player_scenario", "player_impact",
               "strength_of_schedule", "query_database"}
LIMITERS = {
    "ask": RateLimiter(per_minute=20, burst=6),
    "heavy": RateLimiter(per_minute=30, burst=10),
    "default": RateLimiter(per_minute=300, burst=120),
}


def limiter_for(route):
    """The limiter for a POST route."""
    if route == "/ask":
        return LIMITERS["ask"]
    if route.startswith("/tools/") and route[len("/tools/"):] in HEAVY_TOOLS:
        return LIMITERS["heavy"]
    return LIMITERS["default"]


def rate_limits_enabled():
    return os.environ.get("BACKBOARD_RATE_LIMIT", "on").lower() not in ("off", "0", "false")


def client_key(client_ip, forwarded_for=None):
    """The rate-limit key for a client, or None when it is exempt (loopback, no proxy)."""
    if os.environ.get("BACKBOARD_TRUST_PROXY") == "1":
        return (forwarded_for or "").split(",")[0].strip() or client_ip
    if client_ip in ("127.0.0.1", "::1"):
        return None
    return client_ip


class DailyCounter:
    """A count that starts over each calendar day (the LLM question budget)."""

    def __init__(self):
        self.day, self.count = None, 0
        self.lock = threading.Lock()

    def take(self, limit, today=None):
        today = today or date.today().isoformat()
        with self.lock:
            if self.day != today:
                self.day, self.count = today, 0
            if self.count >= limit:
                return False
            self.count += 1
            return True


LLM_QUESTIONS = DailyCounter()
LLM_LIMIT_NOTICE = ("Today's limit of plain-English (LLM) answers has been reached, so this "
                    "answer comes from the deterministic planner.")


def llm_daily_limit():
    try:
        return int(os.environ.get("BACKBOARD_LLM_DAILY_LIMIT", "300"))
    except ValueError:
        return 300


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

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
        envelope = cached_execute(tool_name, parameters)
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
        options = {}
        if data.get("mode") in ("deterministic", "llm"):
            options["mode"] = data["mode"]
        if isinstance(data.get("context"), dict):
            options["context"] = data["context"]
        notice = None
        wants_llm = (options.get("mode") or os.environ.get("BACKBOARD_NL_MODE") or "").lower() == "llm"
        if wants_llm and not LLM_QUESTIONS.take(llm_daily_limit()):
            options["mode"] = "deterministic"
            notice = LLM_LIMIT_NOTICE
        result = answer_question(data["question"], **options)
        if notice and isinstance(result, dict):
            result["notice"] = notice
        return 200, result

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

    def _accepts_gzip(self):
        return "gzip" in (self.headers.get("Accept-Encoding") or "").lower()

    def _write(self, status, body, content_type, extra_headers=None):
        """Send a whole (non-range) response, gzip-compressed when worthwhile."""
        headers = dict(extra_headers or {})
        compressible = content_type.startswith(_COMPRESSIBLE)
        if compressible and self._accepts_gzip() and len(body) >= _COMPRESS_MIN_BYTES:
            body = gzip.compress(body, compresslevel=6)
            headers["Content-Encoding"] = "gzip"
        if compressible:
            headers["Vary"] = "Accept-Encoding"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send(self, status, payload, extra_headers=None):
        raw = json.dumps(payload).encode("utf-8")
        headers = {
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
            "Cache-Control": "no-store",
        }
        headers.update(extra_headers or {})
        self._write(status, raw, "application/json", headers)

    def do_OPTIONS(self):
        self._send(204, {})

    def _base_url(self):
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or "localhost"
        scheme = self.headers.get("X-Forwarded-Proto") or "http"
        return f"{scheme}://{host}"

    def _serve_html(self):
        if not WEB_HTML.exists():
            self._send(404, {"error": "not_found",
                             "message": "web/index.html not found."})
            return
        body = render_index(self.path, self._base_url()).encode("utf-8")
        self._write(200, body, "text/html; charset=utf-8", {"Cache-Control": "no-cache"})

    def _serve_static(self, path):
        content_type = _STATIC_CONTENT_TYPES.get(
            path.suffix.lower(), "application/octet-stream"
        )
        size = path.stat().st_size
        etag = _etag(path)
        cache = ("public, max-age=604800" if path.suffix.lower() in _LONG_CACHE_SUFFIXES
                 else "no-cache")
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", cache)
            self.end_headers()
            return
        byte_range = _parse_byte_range(self.headers.get("Range"), size)
        if byte_range == "unsatisfiable":
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            return
        if byte_range is None:
            self._write(200, path.read_bytes(), content_type,
                        {"Accept-Ranges": "bytes", "ETag": etag, "Cache-Control": cache})
            return
        start, end = byte_range
        with path.open("rb") as handle:
            handle.seek(start)
            body = handle.read(end - start + 1) if size else b""
        self.send_response(206)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", cache)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        if route in ("/", "/app", "/index.html") or page_route(parsed.path):
            self._serve_html()
            return
        if route == "/sitemap.xml":
            self._write(200, sitemap_xml(self._base_url()).encode("utf-8"),
                        "application/xml; charset=utf-8", {"Cache-Control": "no-cache"})
            return
        if route == "/robots.txt":
            text = f"User-agent: *\nAllow: /\nSitemap: {self._base_url()}/sitemap.xml\n"
            self._write(200, text.encode("utf-8"), "text/plain; charset=utf-8")
            return
        static_path = _static_asset_path(parsed.path)
        if static_path is not None:
            self._serve_static(static_path)
            return
        status, payload = handle_request("GET", self.path, None)
        self._send(status, payload)

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else None
        route = urllib.parse.urlparse(self.path).path.rstrip("/") or "/"
        key = client_key(self.client_address[0], self.headers.get("X-Forwarded-For"))
        if key is not None and rate_limits_enabled():
            allowed, retry_after = limiter_for(route).allow(key)
            if not allowed:
                self._send(429, {"error": "rate_limited",
                                 "message": f"Too many requests; try again in {retry_after} s.",
                                 "retry_after": retry_after},
                           {"Retry-After": str(retry_after)})
                return
        status, payload = handle_request("POST", self.path, body)
        self._send(status, payload)


# A browser that leaves a page, cancels a fetch or seeks a video closes the
# socket mid-response. That is normal, so the server drops those quietly
# instead of printing a traceback for each.
CLIENT_DISCONNECTS = (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        import sys
        if isinstance(sys.exc_info()[1], CLIENT_DISCONNECTS):
            return
        super().handle_error(request, client_address)


def warm_caches():
    """Load the slow, shared inputs once so the first page view is fast.

    Runs in a daemon thread at server start: the player-page league tables,
    the Sandbox engine, the model inputs, the next-season schedule and
    (slowest, about a minute) every game's pregame probability for
    replay-mode simulations. Each cache is single-flight, so a request that
    arrives meanwhile waits for the build instead of starting a second one.
    A failure only means the first request pays the cost instead.
    """
    try:
        from src import player_profile, sandbox
        from src.tools import _cached_model_inputs, _cached_probabilities
    except ImportError:  # pragma: no cover - direct-script support
        import player_profile
        import sandbox
        from tools import _cached_model_inputs, _cached_probabilities

    def next_season_setup():
        season = sandbox.seasons_available()["next"]
        sandbox.season_setup(season, "next", None, _cached_model_inputs())

    steps = [
        ("player tables", player_profile.team_season_totals),
        ("league reference", player_profile.league_reference),
        ("sandbox engine", sandbox.engine),
        ("model inputs", _cached_model_inputs),
        ("next-season schedule", next_season_setup),
        ("season probabilities", _cached_probabilities),
    ]
    for label, step in steps:
        try:
            step()
        except Exception as exc:  # pragma: no cover - best effort
            print(f"  warm-up skipped {label}: {exc}")
    print("  caches warm: player pages and the Sandbox are ready")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Serve the NBA analytics tool layer over HTTP (stdlib only)."
    )
    parser.add_argument("--host", default="127.0.0.1",
                        help="Bind host (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8000,
                        help="Bind port (default: 8000).")
    parser.add_argument("--no-warm", action="store_true",
                        help="Do not pre-load model caches in the background at start.")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    server = Server((args.host, args.port), _Handler)
    print(f"{SERVICE_NAME} API listening on http://{args.host}:{args.port}")
    print(f"  production model: {PRODUCTION_MODEL}")
    print("  endpoints: GET / and the page paths (front end), GET /health, GET /tools, "
          "POST /tools/<name>, POST /ask, POST /ingest, GET /sitemap.xml")
    enable_disk_cache(ROOT / "data" / "cache")
    if not args.no_warm:
        print("  warming caches in the background (about a minute; seconds when data/cache is current)")
        threading.Thread(target=warm_caches, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
