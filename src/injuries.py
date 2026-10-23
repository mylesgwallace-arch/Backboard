"""The league injury report, from ESPN's public injuries JSON.

Same rules as the rest of the live-data path (``live_results.py``):

* Provenance: one ``data_ingestion_log`` row per run (entity ``injuries``).
* All-or-nothing: the whole report is fetched and parsed first; the
  ``injuries`` table is then replaced in one transaction. A fetch or parse
  failure leaves the previous report in place.
* The report is a snapshot (who is listed right now), not a history: each
  successful run replaces it and records ``reported_at``.
* ESPN athlete ids are not NBA person ids. Players are matched to ``players``
  rows by normalised name (``live_boxscores.PlayerIndex``); an unmatched
  player keeps ``personId`` NULL and is shown by name only.

The win-probability model does not read this table: it has no injury
feature. The Sandbox can apply listed injuries as what-if moves.
"""

import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone

import requests

try:
    from src.live_data import DB_PATH, ensure_schema
    from src.live_boxscores import PlayerIndex
    from src.live_results import EASTERN, FetchError
except ImportError:  # pragma: no cover - direct-script support
    from live_data import DB_PATH, ensure_schema
    from live_boxscores import PlayerIndex
    from live_results import EASTERN, FetchError


ESPN_INJURIES_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
LOG_ENTITY = "injuries"
# Statuses that mean the player will not play; "Day-To-Day" and
# "Questionable" are listed but not treated as out.
OUT_STATUSES = {"out", "injured reserve", "suspension", "suspended", "out for season"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS injuries (
    teamId INTEGER NOT NULL,
    personId INTEGER,
    playerName TEXT NOT NULL,
    position TEXT,
    status TEXT NOT NULL,
    is_out INTEGER NOT NULL,
    injury TEXT,
    detail TEXT,
    return_date TEXT,
    listed_on TEXT,
    espn_athlete_id INTEGER,
    reported_at TEXT NOT NULL
)
"""


class InjuryError(Exception):
    """The injury report could not be parsed; nothing is written."""


def fetch_report(base_url=ESPN_INJURIES_URL, session=None, retries=3):
    """Fetch the league report. Returns ``(payload, raw_bytes)`` or raises ``FetchError``."""
    getter = session.get if session is not None else requests.get
    last_error = None
    for attempt in range(retries):
        try:
            response = getter(base_url, timeout=30, headers={"User-Agent": "backboard-live-data/1.0"})
            response.raise_for_status()
            raw = response.content
            payload = json.loads(raw)
            if not isinstance(payload, dict) or "injuries" not in payload:
                raise ValueError("no injuries list in response")
            return payload, raw
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            time.sleep(0.5 * (attempt + 1))
    raise FetchError(f"could not fetch the injury report: {last_error}")


def _team_lookup(conn):
    """Nickname / city+nickname / abbreviation -> teamId for the 30 current franchises."""
    lookup = {}
    for team_id, city, name, abbrev in conn.execute(
            "SELECT teamId, teamCity, teamName, teamAbbrev FROM team_histories WHERE seasonActiveTill >= 2100"):
        for key in (name, f"{city} {name}", abbrev):
            if key:
                lookup[str(key).strip().lower()] = int(team_id)
    return lookup


def _resolve_team(block, lookup):
    names = [block.get("displayName"), block.get("abbreviation"),
             (block.get("team") or {}).get("abbreviation"), (block.get("team") or {}).get("displayName")]
    for name in names:
        if not name:
            continue
        key = str(name).strip().lower()
        if key in lookup:
            return lookup[key]
        # "LA Clippers" vs "Los Angeles Clippers": match on the nickname.
        for nickname, team_id in lookup.items():
            if len(nickname) > 3 and key.endswith(" " + nickname):
                return team_id
    return None


def parse_report(payload, conn):
    """Payload -> list of row dicts. Raises ``InjuryError`` on an unusable report."""
    lookup = _team_lookup(conn)
    index = PlayerIndex(conn)
    rows = []
    blocks = payload.get("injuries")
    if not isinstance(blocks, list):
        raise InjuryError("injuries is not a list")
    for block in blocks:
        team_id = _resolve_team(block, lookup)
        if team_id is None:
            continue
        for item in block.get("injuries") or []:
            athlete = item.get("athlete") or {}
            name = athlete.get("displayName") or athlete.get("fullName")
            status = (item.get("status") or (item.get("type") or {}).get("description") or "").strip()
            if not name or not status:
                raise InjuryError(f"injury entry without a player or status for team {team_id}")
            details = item.get("details") or {}
            espn_id = athlete.get("id")
            person_id = index.person_id(int(espn_id or 0), name, team_id)
            injury = " ".join(str(part) for part in (details.get("side"), details.get("type"))
                              if part and str(part).lower() not in ("not specified",)) or None
            rows.append({
                "teamId": team_id,
                "personId": person_id if person_id and person_id > 0 else None,
                "playerName": name,
                "position": (athlete.get("position") or {}).get("abbreviation"),
                "status": status,
                "is_out": int(status.lower() in OUT_STATUSES),
                "injury": injury,
                "detail": item.get("shortComment") or details.get("detail"),
                "return_date": details.get("returnDate"),
                "listed_on": (item.get("date") or "")[:10] or None,
                "espn_athlete_id": int(espn_id) if espn_id else None,
            })
    return rows


def ingest_injuries(db_path=DB_PATH, base_url=ESPN_INJURIES_URL, session=None, dry_run=False, now=None):
    """Fetch, parse and (unless ``dry_run``) replace the injury table. Returns a manifest."""
    payload, raw = fetch_report(base_url, session)
    reported_at = (now or datetime.now(timezone.utc)).astimezone(EASTERN).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(db_path)
    try:
        rows = parse_report(payload, conn)
        manifest = {
            "players_listed": len(rows),
            "players_out": sum(row["is_out"] for row in rows),
            "players_unmatched": sum(1 for row in rows if row["personId"] is None),
            "teams": len({row["teamId"] for row in rows}),
            "reported_at": reported_at,
            "checksum_sha256": hashlib.sha256(raw).hexdigest(),
            "dry_run": dry_run,
        }
        if dry_run:
            return manifest
        try:
            conn.execute("BEGIN")
            conn.execute(SCHEMA)
            conn.execute("DELETE FROM injuries")
            conn.executemany(
                "INSERT INTO injuries (teamId, personId, playerName, position, status, is_out, injury, "
                "detail, return_date, listed_on, espn_athlete_id, reported_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(r["teamId"], r["personId"], r["playerName"], r["position"], r["status"], r["is_out"],
                  r["injury"], r["detail"], r["return_date"], r["listed_on"], r["espn_athlete_id"],
                  reported_at) for r in rows],
            )
            ensure_schema(conn)
            conn.execute(
                "INSERT INTO data_ingestion_log (entity, source_type, source_url, fetched_at, "
                "rows_in_source, rows_loaded, rows_skipped, validation_status, checksum_sha256, notes) "
                "VALUES (?, 'http', ?, datetime('now'), ?, ?, 0, 'passed', ?, ?)",
                (LOG_ENTITY, base_url, len(rows), len(rows), manifest["checksum_sha256"],
                 json.dumps({"players_unmatched": manifest["players_unmatched"]})),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return manifest
    finally:
        conn.close()


def injury_report(db_path=DB_PATH, team_id=None):
    """The stored report, grouped by team. Empty (``reported_at`` None) before the first run."""
    conn = sqlite3.connect(db_path)
    try:
        exists = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='injuries'").fetchone()[0]
        if not exists:
            return {"reported_at": None, "teams": [], "players_listed": 0}
        query = ("SELECT teamId, personId, playerName, position, status, is_out, injury, detail, "
                 "return_date, listed_on, reported_at FROM injuries")
        params = ()
        if team_id is not None:
            query += " WHERE teamId = ?"
            params = (int(team_id),)
        rows = conn.execute(query + " ORDER BY teamId, is_out DESC, playerName", params).fetchall()
        names = {int(t): f"{c} {n}" for t, c, n in conn.execute(
            "SELECT teamId, teamCity, teamName FROM team_histories WHERE seasonActiveTill >= 2100")}
    finally:
        conn.close()
    teams = {}
    reported_at = None
    for (team, person, name, position, status, is_out, injury, detail, return_date, listed_on,
         reported) in rows:
        reported_at = reported
        teams.setdefault(team, {"teamId": team, "teamName": names.get(team, str(team)), "players": []})
        teams[team]["players"].append({
            "person_id": person, "name": name, "position": position, "status": status,
            "out": bool(is_out), "injury": injury, "detail": detail, "return_date": return_date,
            "listed_on": listed_on,
        })
    return {
        "reported_at": reported_at,
        "players_listed": len(rows),
        "teams": sorted(teams.values(), key=lambda t: t["teamName"]),
    }
