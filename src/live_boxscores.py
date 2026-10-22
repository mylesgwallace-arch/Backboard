"""Box scores for newly played games, from ESPN's game summary JSON.

``live_results.py`` keeps the ``games`` table current (schedule, scores,
winners). The win-probability model, though, reads rolling form built from
``team_statistics`` and ``player_statistics``. This module fills those two
tables for games the refresh has recorded a result for, so the feature
pipeline (``build_features.py``) and Elo can move forward with the season.

Same rules as the rest of the live-data path:

* Provenance: one ``data_ingestion_log`` row per run (entity ``box_scores``).
* Never overwrites: a game that already has team rows is skipped.
* All-or-nothing writes: every summary is fetched and validated first; the
  rows are inserted in a single transaction. A game whose box score fails
  validation (scores disagree with ``games``, missing team, bad numbers) is
  skipped and counted, never half-written.
* Only the columns the feature pipeline and Elo read are filled; the other
  ``team_statistics`` columns stay NULL.
* ESPN athlete ids are not NBA person ids. A player is matched to an existing
  ``players`` row by normalised name (using recent teams to break ties); an
  unmatched player gets the stable synthetic id ``-ESPN athlete id`` so his
  rolling history stays consistent from game to game.
"""

import hashlib
import json
import re
import sqlite3
import time
import unicodedata

import requests

try:
    from src.live_data import DB_PATH, ensure_schema
    from src.live_results import FetchError, ESPN_SUMMARY_URL, REQUEST_PAUSE_SECONDS
except ImportError:  # pragma: no cover - direct-script support
    from live_data import DB_PATH, ensure_schema
    from live_results import FetchError, ESPN_SUMMARY_URL, REQUEST_PAUSE_SECONDS


LOG_ENTITY = "box_scores"
BOX_GAME_TYPES = ("Regular Season", "Playoffs", "Play-in Tournament", "Emirates NBA Cup", "NBA Cup")
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
TEAM_MINUTES = 240.0


class BoxScoreError(Exception):
    """A box score failed validation; the game is skipped."""


# ---- fetching ----------------------------------------------------------

def fetch_summary(espn_id, base_url=ESPN_SUMMARY_URL, session=None):
    """One game summary. Returns ``(payload, raw_bytes)`` or raises ``FetchError``."""
    getter = session.get if session is not None else requests.get
    last_error = None
    for attempt in range(3):
        try:
            response = getter(base_url, params={"event": str(espn_id)}, timeout=30,
                              headers={"User-Agent": "backboard-live-data/1.0"})
            response.raise_for_status()
            raw = response.content
            payload = json.loads(raw)
            if "boxscore" not in payload:
                raise ValueError("no boxscore in response")
            return payload, raw
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            time.sleep(0.5 * (attempt + 1))
    raise FetchError(f"could not fetch summary for event {espn_id}: {last_error}")


# ---- parsing -----------------------------------------------------------

def _made_attempted(text):
    made, attempted = str(text).split("-")
    return int(made), int(attempted)


def _pct(made, attempted):
    return round(made / attempted, 3) if attempted else None


def _team_stats(statistics):
    by_name = {item["name"]: item["displayValue"] for item in statistics}
    fgm, fga = _made_attempted(by_name["fieldGoalsMade-fieldGoalsAttempted"])
    tpm, tpa = _made_attempted(by_name["threePointFieldGoalsMade-threePointFieldGoalsAttempted"])
    ftm, fta = _made_attempted(by_name["freeThrowsMade-freeThrowsAttempted"])
    return {
        "fieldGoalsMade": fgm, "fieldGoalsAttempted": fga, "fieldGoalsPercentage": _pct(fgm, fga),
        "threePointersMade": tpm, "threePointersAttempted": tpa, "threePointersPercentage": _pct(tpm, tpa),
        "freeThrowsMade": ftm, "freeThrowsAttempted": fta, "freeThrowsPercentage": _pct(ftm, fta),
        "reboundsTotal": int(by_name["totalRebounds"]),
        "reboundsOffensive": int(by_name["offensiveRebounds"]),
        "reboundsDefensive": int(by_name["defensiveRebounds"]),
        "assists": int(by_name["assists"]), "steals": int(by_name["steals"]),
        "blocks": int(by_name["blocks"]), "turnovers": int(by_name.get("totalTurnovers", by_name["turnovers"])),
        "foulsPersonal": int(by_name["fouls"]),
    }


def _player_rows(team_block):
    """Players of one team: list of dicts (played and did-not-play)."""
    rows = []
    for group in team_block.get("statistics", []):
        names = group["names"]
        for entry in group.get("athletes", []):
            athlete = entry["athlete"]
            row = {
                "espn_id": int(athlete["id"]),
                "name": athlete["displayName"],
                "starter": bool(entry.get("starter")),
                "position": (athlete.get("position") or {}).get("abbreviation"),
                "dnp_reason": None, "minutes": None, "points": 0, "assists": 0, "rebounds": 0,
                "steals": 0, "blocks": 0, "turnovers": 0, "offReb": 0, "defReb": 0, "fouls": 0,
                "plusMinus": None, "fgm": 0, "fga": 0, "tpm": 0, "tpa": 0, "ftm": 0, "fta": 0,
            }
            if entry.get("didNotPlay") or not entry.get("stats"):
                row["dnp_reason"] = entry.get("reason") or "DNP"
                rows.append(row)
                continue
            stats = dict(zip(names, entry["stats"]))
            row["minutes"] = int(stats["MIN"])
            row["points"] = int(stats["PTS"])
            row["assists"] = int(stats["AST"])
            row["rebounds"] = int(stats["REB"])
            row["steals"] = int(stats["STL"])
            row["blocks"] = int(stats["BLK"])
            row["turnovers"] = int(stats["TO"])
            row["offReb"] = int(stats["OREB"])
            row["defReb"] = int(stats["DREB"])
            row["fouls"] = int(stats["PF"])
            row["plusMinus"] = float(str(stats["+/-"]).replace("+", "")) if stats.get("+/-") not in (None, "--") else None
            row["fgm"], row["fga"] = _made_attempted(stats["FG"])
            row["tpm"], row["tpa"] = _made_attempted(stats["3PT"])
            row["ftm"], row["fta"] = _made_attempted(stats["FT"])
            rows.append(row)
    return rows


def parse_summary(payload, game):
    """Validate a summary against its ``games`` row and return stat rows.

    ``game`` is a dict with ``home_id``, ``away_id``, ``home_score`` and
    ``away_score``. Returns ``{"teams": {side: dict}, "players": {side: [..]}}``.
    Raises ``BoxScoreError`` when anything does not line up.
    """
    try:
        header = payload["header"]["competitions"][0]
        if header["status"]["type"]["name"] != "STATUS_FINAL":
            raise BoxScoreError("game is not final")
        scores = {c["homeAway"]: int(c["score"]) for c in header["competitors"]}
        if scores.get("home") != game["home_score"] or scores.get("away") != game["away_score"]:
            raise BoxScoreError(
                f"score {scores} does not match games table "
                f"{game['home_score']}-{game['away_score']}")
        teams, players = {}, {}
        blocks = payload["boxscore"]["teams"]
        player_blocks = {b["team"]["id"]: b for b in payload["boxscore"]["players"]}
        for block in blocks:
            side = block.get("homeAway")
            if side not in ("home", "away"):
                raise BoxScoreError("team side missing")
            team_id = block["team"]["id"]
            teams[side] = {"espn_team_id": team_id, **_team_stats(block["statistics"])}
            players[side] = _player_rows(player_blocks[team_id])
        if set(teams) != {"home", "away"}:
            raise BoxScoreError("expected two teams")
        for side in ("home", "away"):
            played = [p for p in players[side] if p["minutes"] is not None]
            if len(played) < 5:
                raise BoxScoreError(f"only {len(played)} players with minutes for {side}")
            points = sum(p["points"] for p in played)
            if points != scores[side]:
                raise BoxScoreError(f"{side} player points {points} != team score {scores[side]}")
        return {"teams": teams, "players": players}
    except BoxScoreError:
        raise
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise BoxScoreError(f"unreadable box score: {exc!r}")


# ---- player id matching ------------------------------------------------

def normalise_name(name):
    text = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    words = [w for w in re.sub(r"[^a-z\s]", " ", text).split() if w not in SUFFIXES]
    return " ".join(words)


class PlayerIndex:
    """Match ESPN players to NBA ``personId`` values by normalised name."""

    def __init__(self, conn):
        self.names = {}
        recent_team = {}
        for person_id, team_id, _ in conn.execute(
            "SELECT personId, playerteamId, MAX(gameDateTimeEst) FROM player_statistics "
            "WHERE gameDateTimeEst >= '2024-09-01' AND playerteamId IS NOT NULL "
            "GROUP BY personId"
        ):
            recent_team[int(person_id)] = int(team_id)
        self.recent_team = recent_team
        for person_id, first, last, nba_flag in conn.execute(
            "SELECT personId, firstName, lastName, nbaFlag FROM players"
        ):
            key = normalise_name(f"{first or ''} {last or ''}")
            self.names.setdefault(key, []).append((int(person_id), nba_flag or 0))

    def person_id(self, espn_athlete_id, name, team_id):
        candidates = self.names.get(normalise_name(name), [])
        if len(candidates) == 1:
            return candidates[0][0]
        if candidates:
            on_team = [pid for pid, _ in candidates if self.recent_team.get(pid) == team_id]
            if len(on_team) == 1:
                return on_team[0]
            recent = [pid for pid, _ in candidates if pid in self.recent_team]
            if len(recent) == 1:
                return recent[0]
        return -int(espn_athlete_id)  # unmatched or ambiguous: stable synthetic id


# ---- database ----------------------------------------------------------

def pending_games(conn, limit=None):
    """Played games with an ESPN id and no team rows yet, oldest first."""
    placeholders = ",".join("?" for _ in BOX_GAME_TYPES)
    sql = (
        "SELECT g.gameId, m.espnId, g.gameDateTimeEst, g.hometeamId, g.awayteamId, "
        "g.hometeamCity, g.hometeamName, g.awayteamCity, g.awayteamName, "
        "g.homeScore, g.awayScore, g.winner, g.gameType, g.gameLabel "
        "FROM games g JOIN espn_game_map m ON m.gameId = g.gameId "
        f"WHERE g.winner IS NOT NULL AND g.gameType IN ({placeholders}) "
        "AND NOT EXISTS (SELECT 1 FROM team_statistics t WHERE t.gameId = g.gameId) "
        "ORDER BY g.gameDateTimeEst, g.gameId"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    keys = ["game_id", "espn_id", "timestamp", "home_id", "away_id", "home_city", "home_name",
            "away_city", "away_name", "home_score", "away_score", "winner", "game_type", "label"]
    return [dict(zip(keys, row)) for row in conn.execute(sql, BOX_GAME_TYPES)]


def _minutes_text(minutes):
    return None if minutes is None else f"{minutes}.0"


def insert_game(conn, game, parsed, index):
    """Insert team and player rows for one validated game."""
    sides = {
        "home": (game["home_id"], game["away_id"], game["home_city"], game["home_name"],
                 game["away_city"], game["away_name"], game["home_score"], game["away_score"], 1),
        "away": (game["away_id"], game["home_id"], game["away_city"], game["away_name"],
                 game["home_city"], game["home_name"], game["away_score"], game["home_score"], 0),
    }
    for side, (team_id, opp_id, city, name, opp_city, opp_name, score, opp_score, home) in sides.items():
        s = parsed["teams"][side]
        conn.execute(
            "INSERT INTO team_statistics (gameId, gameDateTimeEst, teamCity, teamName, teamId, "
            "opponentTeamCity, opponentTeamName, opponentTeamId, home, win, teamScore, opponentScore, "
            "assists, blocks, steals, fieldGoalsAttempted, fieldGoalsMade, fieldGoalsPercentage, "
            "threePointersAttempted, threePointersMade, threePointersPercentage, freeThrowsAttempted, "
            "freeThrowsMade, freeThrowsPercentage, reboundsDefensive, reboundsOffensive, reboundsTotal, "
            "foulsPersonal, turnovers, plusMinusPoints, numMinutes, gameType, gameLabel, gameDate) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (game["game_id"], game["timestamp"], city, name, team_id, opp_city, opp_name, opp_id,
             home, 1.0 if game["winner"] == team_id else 0.0, score, opp_score,
             s["assists"], s["blocks"], s["steals"], s["fieldGoalsAttempted"], s["fieldGoalsMade"],
             s["fieldGoalsPercentage"], s["threePointersAttempted"], s["threePointersMade"],
             s["threePointersPercentage"], s["freeThrowsAttempted"], s["freeThrowsMade"],
             s["freeThrowsPercentage"], s["reboundsDefensive"], s["reboundsOffensive"],
             s["reboundsTotal"], s["foulsPersonal"], s["turnovers"], float(score - opp_score),
             TEAM_MINUTES, game["game_type"], game["label"], game["timestamp"]),
        )
        won = 1.0 if game["winner"] == team_id else 0.0
        for p in parsed["players"][side]:
            first, _, last = p["name"].partition(" ")
            pct = lambda m, a: round(m / a, 3) if a else None
            conn.execute(
                "INSERT INTO player_statistics (firstName, lastName, personId, gameId, gameDateTimeEst, "
                "playerteamCity, playerteamName, opponentteamCity, opponentteamName, gameType, gameLabel, "
                "win, home, numMinutes, points, assists, blocks, steals, fieldGoalsAttempted, "
                "fieldGoalsMade, fieldGoalsPercentage, threePointersAttempted, threePointersMade, "
                "threePointersPercentage, freeThrowsAttempted, freeThrowsMade, freeThrowsPercentage, "
                "reboundsDefensive, reboundsOffensive, reboundsTotal, foulsPersonal, turnovers, "
                "plusMinusPoints, playerteamId, opponentteamId, comment, startingPosition, gameDate) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (first, last, float(index.person_id(p["espn_id"], p["name"], team_id)), game["game_id"],
                 game["timestamp"], city, name, opp_city, opp_name, game["game_type"], game["label"],
                 won, float(home), _minutes_text(p["minutes"]), float(p["points"]), float(p["assists"]),
                 float(p["blocks"]), float(p["steals"]), float(p["fga"]), float(p["fgm"]),
                 pct(p["fgm"], p["fga"]), float(p["tpa"]), float(p["tpm"]), pct(p["tpm"], p["tpa"]),
                 float(p["fta"]), float(p["ftm"]), pct(p["ftm"], p["fta"]), float(p["defReb"]),
                 float(p["offReb"]), float(p["rebounds"]), float(p["fouls"]), float(p["turnovers"]),
                 p["plusMinus"], float(team_id), float(opp_id), p["dnp_reason"],
                 p["position"] if p["starter"] else None, game["timestamp"]),
            )


def ingest_box_scores(db_path=DB_PATH, limit=None, base_url=ESPN_SUMMARY_URL, session=None,
                      pause=REQUEST_PAUSE_SECONDS, dry_run=False):
    """Fetch and store box scores for played games that lack them."""
    conn = sqlite3.connect(db_path)
    try:
        has_map = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'espn_game_map'").fetchone()[0]
        games = pending_games(conn, limit) if has_map else []
        digest = hashlib.sha256()
        fetched, skipped = [], []
        for number, game in enumerate(games):
            try:
                payload, raw = fetch_summary(game["espn_id"], base_url, session)
                digest.update(raw)
                fetched.append((game, parse_summary(payload, game)))
            except (BoxScoreError, FetchError) as exc:
                skipped.append({"gameId": game["game_id"], "espnId": game["espn_id"], "reason": str(exc)})
            if pause and number + 1 < len(games):
                time.sleep(pause)
        manifest = {
            "games_pending": len(games), "games_loaded": len(fetched),
            "games_skipped": len(skipped), "skipped": skipped[:10],
            "checksum_sha256": digest.hexdigest(), "dry_run": dry_run,
        }
        if not fetched:
            return manifest
        index = PlayerIndex(conn)
        unmatched = 0
        try:
            conn.execute("BEGIN")
            for game, parsed in fetched:
                insert_game(conn, game, parsed, index)
            unmatched = conn.execute(
                "SELECT COUNT(DISTINCT personId) FROM player_statistics WHERE personId < 0").fetchone()[0]
            if dry_run:
                conn.rollback()
            else:
                ensure_schema(conn)
                conn.execute(
                    "INSERT INTO data_ingestion_log (entity, source_type, source_url, fetched_at, "
                    "rows_in_source, rows_loaded, rows_skipped, validation_status, checksum_sha256, notes) "
                    "VALUES (?, 'http', ?, datetime('now'), ?, ?, ?, ?, ?, ?)",
                    (LOG_ENTITY, base_url, len(games), len(fetched), len(skipped),
                     "passed" if not skipped else "passed_with_skips", manifest["checksum_sha256"],
                     json.dumps({"skipped": skipped[:10], "players_with_synthetic_id": unmatched})),
                )
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        manifest["players_with_synthetic_id"] = unmatched
        return manifest
    finally:
        conn.close()
