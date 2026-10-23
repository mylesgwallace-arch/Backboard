"""Daily schedule + results refresh from ESPN's public scoreboard JSON.

``src/live_data.py`` ingests a schedule file. This module adds the other half
of the live-data milestone: it pulls ESPN's scoreboard, one request per day,
and brings the ``games`` table up to date with

* final scores and winners for games that have been played, and
* scheduled games that the database does not know yet (so a new season is
  usable before a schedule file is ingested).

Rules (they mirror ``live_data.py``):

* Provenance: every refresh writes one ``data_ingestion_log`` row with the
  source, row counts, a SHA-256 over the fetched bytes and a status.
* Results are write-once. A result is written only where ``winner`` is NULL.
  If ESPN disagrees with a stored result, the row is left alone and counted as
  a conflict; nothing already recorded is ever changed.
* All-or-nothing. Every day is fetched into memory first; any fetch failure
  aborts before the database is opened for writing. The database changes run
  in one transaction that is rolled back if a sanity check fails.
* ESPN game ids are not NBA game ids. A game that already exists is matched by
  Eastern date plus home and away team. A game that does not exist is inserted
  with ``ESPN_ID_OFFSET + espn_event_id`` so it can be recognised later; when a
  schedule file with the real NBA id arrives, ``live_data.ingest_schedule``
  replaces the placeholder and keeps its result and box score (see
  ``replace_espn_placeholders``).
* This does not touch box scores. Team strength features and Elo come from
  ``team_statistics`` and stay as of the last box-score load; the data status
  tool reports how many played games they are missing.
"""

import argparse
import hashlib
import json
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

try:
    from src.live_data import DB_PATH, ensure_schema, derive_game_type
except ImportError:  # pragma: no cover - direct-script support
    from live_data import DB_PATH, ensure_schema, derive_game_type


ESPN_SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
)
ESPN_SUMMARY_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary"
)
LOG_ENTITY = "games_espn"
EASTERN = ZoneInfo("America/New_York")

# Placeholder game ids for games first seen on ESPN. NBA ids are below 100M.
ESPN_ID_OFFSET = 4_000_000_000

# ESPN abbreviation -> NBA teamId (``team_histories.teamId``).
ESPN_TEAM_IDS = {
    "ATL": 1610612737, "BOS": 1610612738, "CLE": 1610612739, "NO": 1610612740,
    "CHI": 1610612741, "DAL": 1610612742, "DEN": 1610612743, "GS": 1610612744,
    "HOU": 1610612745, "LAC": 1610612746, "LAL": 1610612747, "MIA": 1610612748,
    "MIL": 1610612749, "MIN": 1610612750, "BKN": 1610612751, "NY": 1610612752,
    "ORL": 1610612753, "IND": 1610612754, "PHI": 1610612755, "PHX": 1610612756,
    "POR": 1610612757, "SAC": 1610612758, "SA": 1610612759, "OKC": 1610612760,
    "TOR": 1610612761, "UTAH": 1610612762, "MEM": 1610612763, "WSH": 1610612764,
    "DET": 1610612765, "CHA": 1610612766,
}

# Only these ESPN season types are NBA games we store.
ESPN_SEASON_TYPES = {1: "Preseason", 2: "Regular Season", 3: "Playoffs", 5: "Play-in Tournament"}

REQUEST_PAUSE_SECONDS = 0.25
FETCH_RETRIES = 3
DEFAULT_LOOKAHEAD_DAYS = 14
OVERLAP_DAYS = 3  # re-read recent days so late finals and corrections are seen
FULL_SEASON_GAMES = 1230  # 30 teams x 82 games / 2
# A schedule is usable once 90% of it is on file. ESPN lists some games (NBA Cup
# knockout nights) as TBD until the field is known, so it is rarely 1,230 early.
MIN_SCHEDULE_GAMES = int(FULL_SEASON_GAMES * 0.9)


class RefreshError(Exception):
    """A refresh failed before or during the write; nothing was committed."""


class FetchError(RefreshError):
    """A request to the ESPN feed failed after retries."""


# ---- fetching ----------------------------------------------------------

def fetch_day(day, base_url=ESPN_SCOREBOARD_URL, session=None):
    """Fetch one scoreboard day. Returns ``(payload_dict, raw_bytes)``."""
    getter = session.get if session is not None else requests.get
    last_error = None
    for attempt in range(FETCH_RETRIES):
        try:
            response = getter(
                base_url, params={"dates": day.strftime("%Y%m%d")}, timeout=30,
                headers={"User-Agent": "backboard-live-data/1.0"},
            )
            response.raise_for_status()
            raw = response.content
            payload = json.loads(raw)
            if not isinstance(payload, dict) or "events" not in payload:
                raise RefreshError(f"unexpected response shape for {day}")
            return payload, raw
        except (requests.RequestException, ValueError, RefreshError) as exc:
            last_error = exc
            time.sleep(0.5 * (attempt + 1))
    raise FetchError(f"could not fetch {day} from {base_url}: {last_error}")


def fetch_days(days, base_url=ESPN_SCOREBOARD_URL, pause=REQUEST_PAUSE_SECONDS,
               session=None):
    """Fetch every day or raise; returns ``(list_of_payloads, sha256_hex)``."""
    digest = hashlib.sha256()
    payloads = []
    for index, day in enumerate(days):
        payload, raw = fetch_day(day, base_url, session)
        digest.update(raw)
        payloads.append(payload)
        if pause and index + 1 < len(days):
            time.sleep(pause)
    return payloads, digest.hexdigest()


# ---- parsing -----------------------------------------------------------

def _eastern_timestamp(iso_utc):
    parsed = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(EASTERN).strftime("%Y-%m-%d %H:%M:%S")


def parse_event(event):
    """One ESPN event -> a normalised game dict, or ``None`` if not stored.

    Skipped: non-NBA matchups (All-Star teams, international opponents),
    unknown season types, and malformed events.
    """
    try:
        competition = event["competitions"][0]
        season_type = event["season"]["type"]
        if season_type not in ESPN_SEASON_TYPES:
            return None
        sides = {c["homeAway"]: c for c in competition["competitors"]}
        home_id = ESPN_TEAM_IDS.get(sides["home"]["team"]["abbreviation"])
        away_id = ESPN_TEAM_IDS.get(sides["away"]["team"]["abbreviation"])
        if home_id is None or away_id is None or home_id == away_id:
            return None
        timestamp = _eastern_timestamp(event["date"])
        status = competition["status"]["type"]
        final = bool(status.get("completed")) and status.get("name") == "STATUS_FINAL"
        home_score = away_score = winner = None
        if final:
            home_score = int(sides["home"]["score"])
            away_score = int(sides["away"]["score"])
            if home_score == away_score:
                final = False  # not a real final; treat as unplayed
                home_score = away_score = None
            else:
                winner = home_id if home_score > away_score else away_id
        notes = [n.get("headline") for n in competition.get("notes") or []]
        venue = competition.get("venue") or {}
        address = venue.get("address") or {}
        label = notes[0] if notes else None
        game_type = ESPN_SEASON_TYPES[season_type]
        if season_type == 2:
            game_type = derive_game_type([label])
        return {
            "espn_id": int(event["id"]),
            "timestamp": timestamp,
            "date": timestamp[:10],
            "home_id": home_id,
            "away_id": away_id,
            "final": final,
            "home_score": home_score,
            "away_score": away_score,
            "winner": winner,
            "game_type": game_type,
            "label": label,
            "arena": venue.get("fullName"),
            "arena_city": address.get("city"),
            "arena_state": address.get("state"),
        }
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def parse_payloads(payloads):
    """All stored games across payloads, de-duplicated by ESPN event id."""
    games, malformed, seen = [], 0, set()
    for payload in payloads:
        for event in payload.get("events", []):
            game = parse_event(event)
            if game is None:
                malformed += 1
                continue
            if game["espn_id"] in seen:
                continue
            seen.add(game["espn_id"])
            games.append(game)
    return games, malformed


# ---- database ----------------------------------------------------------

def _has_table(conn, name):
    return bool(conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone()[0])


def replace_espn_placeholders(conn, source_rows):
    """Swap ESPN placeholder games for the real NBA-id rows that replace them.

    Call after the schedule rows were upserted. For each source row, a
    placeholder with the same Eastern date, home and away team hands over its
    result (only where the real row has none), its box-score rows and its ESPN
    id, then is deleted. Returns the number of placeholders replaced.
    """
    replaced = 0
    for row in source_rows:
        new_id = int(row["gameId"])
        if new_id >= ESPN_ID_OFFSET:
            continue
        for old_id, winner, home_score, away_score in conn.execute(
            "SELECT gameId, winner, homeScore, awayScore FROM games WHERE gameId >= ? "
            "AND substr(gameDateTimeEst, 1, 10) = ? AND hometeamId = ? AND awayteamId = ?",
            (ESPN_ID_OFFSET, str(row["gameDateTimeEst"])[:10],
             int(row["hometeamId"]), int(row["awayteamId"])),
        ).fetchall():
            if winner is not None:
                conn.execute(
                    "UPDATE games SET winner = ?, homeScore = ?, awayScore = ? "
                    "WHERE gameId = ? AND winner IS NULL", (winner, home_score, away_score, new_id))
            for table in ("team_statistics", "player_statistics"):
                if _has_table(conn, table):
                    conn.execute(f"UPDATE {table} SET gameId = ? WHERE gameId = ?", (new_id, old_id))
            if _has_table(conn, "espn_game_map"):
                conn.execute("DELETE FROM espn_game_map WHERE gameId = ?", (old_id,))
                _remember(conn, new_id, old_id - ESPN_ID_OFFSET)
            conn.execute("DELETE FROM games WHERE gameId = ?", (old_id,))
            replaced += 1
    return replaced


def _team_names(conn):
    names = {}
    for team_id, city, name in conn.execute(
        "SELECT teamId, teamCity, teamName FROM team_histories "
        "WHERE teamId BETWEEN 1610612737 AND 1610612766 "
        "ORDER BY seasonActiveTill"
    ):
        names[int(team_id)] = (city, name)  # latest era wins
    return names


def ensure_map_schema(conn):
    """ESPN event id for every game we have seen (needed to fetch box scores)."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS espn_game_map (gameId INTEGER PRIMARY KEY, espnId INTEGER NOT NULL)"
    )


def _remember(conn, game_id, espn_id):
    conn.execute("INSERT OR REPLACE INTO espn_game_map (gameId, espnId) VALUES (?, ?)", (game_id, espn_id))


def apply_games(conn, games):
    """Write ESPN games into ``games`` inside the caller's transaction.

    Returns a counter dict. Never changes a result that is already recorded.
    """
    counts = {"inserted": 0, "results_written": 0, "already_current": 0,
              "conflicts": 0, "ambiguous": 0, "unplayed_seen": 0}
    names = _team_names(conn)
    by_key = {}
    for game_id, stamp, home, away, winner in conn.execute(
        "SELECT gameId, gameDateTimeEst, hometeamId, awayteamId, winner FROM games "
        "WHERE gameDateTimeEst >= ?", (min(g["date"] for g in games),) if games else ("9999",),
    ):
        by_key.setdefault((str(stamp)[:10], home, away), []).append((game_id, winner))

    for game in games:
        key = (game["date"], game["home_id"], game["away_id"])
        existing = by_key.get(key, [])
        if len(existing) > 1:
            counts["ambiguous"] += 1
            continue
        if not existing:
            neighbours = [
                1 for offset in (-1, 1)
                for _ in by_key.get(
                    ((date.fromisoformat(game["date"]) + timedelta(days=offset)).isoformat(),
                     game["home_id"], game["away_id"]), [])
            ]
            if neighbours:  # same pairing a day away: could be a timezone shift
                counts["ambiguous"] += 1
                continue
            home_city, home_name = names.get(game["home_id"], (None, None))
            away_city, away_name = names.get(game["away_id"], (None, None))
            conn.execute(
                "INSERT INTO games (gameId, gameDateTimeEst, hometeamCity, hometeamName, "
                "hometeamId, awayteamCity, awayteamName, awayteamId, homeScore, awayScore, "
                "winner, gameType, gameLabel, arenaName, arenaCity, arenaState, gameDate) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ESPN_ID_OFFSET + game["espn_id"], game["timestamp"], home_city, home_name,
                 game["home_id"], away_city, away_name, game["away_id"],
                 game["home_score"], game["away_score"], game["winner"], game["game_type"],
                 game["label"], game["arena"], game["arena_city"], game["arena_state"],
                 game["timestamp"]),
            )
            counts["inserted"] += 1
            _remember(conn, ESPN_ID_OFFSET + game["espn_id"], game["espn_id"])
            continue
        game_id, stored_winner = existing[0]
        _remember(conn, game_id, game["espn_id"])
        if not game["final"]:
            counts["unplayed_seen"] += 1
        elif stored_winner is None:
            conn.execute(
                "UPDATE games SET homeScore = ?, awayScore = ?, winner = ? "
                "WHERE gameId = ? AND winner IS NULL",
                (game["home_score"], game["away_score"], game["winner"], game_id),
            )
            counts["results_written"] += 1
        elif int(stored_winner) == game["winner"]:
            counts["already_current"] += 1
        else:
            counts["conflicts"] += 1
    return counts


def sanity_check(conn, before_results):
    """Raise ``RefreshError`` if the write broke an invariant."""
    changed = 0
    for game_id, winner in before_results.items():
        row = conn.execute("SELECT winner FROM games WHERE gameId = ?", (game_id,)).fetchone()
        if row is None or row[0] != winner:
            changed += 1
    if changed:
        raise RefreshError(f"{changed} previously recorded result(s) would change; aborting")
    duplicates = conn.execute(
        "SELECT COUNT(*) FROM (SELECT 1 FROM games WHERE gameId >= ? "
        "GROUP BY substr(gameDateTimeEst, 1, 10), hometeamId, awayteamId HAVING COUNT(*) > 1)",
        (ESPN_ID_OFFSET,),
    ).fetchone()[0]
    if duplicates:
        raise RefreshError(f"{duplicates} duplicate ESPN game(s) after write; aborting")
    bad = conn.execute(
        "SELECT COUNT(*) FROM games WHERE winner IS NOT NULL AND winner NOT IN (hometeamId, awayteamId)"
    ).fetchone()[0]
    if bad:
        raise RefreshError(f"{bad} game(s) have a winner who did not play; aborting")


# ---- orchestration -----------------------------------------------------

def plan_window(conn, since=None, through=None, today=None,
                lookahead_days=DEFAULT_LOOKAHEAD_DAYS):
    """The first and last day to fetch."""
    today = today or datetime.now(EASTERN).date()
    if since is None:
        latest = conn.execute(
            "SELECT MAX(substr(gameDateTimeEst, 1, 10)) FROM games WHERE winner IS NOT NULL"
        ).fetchone()[0]
        since = (date.fromisoformat(latest) - timedelta(days=OVERLAP_DAYS)) if latest else today - timedelta(days=30)
    if through is None:
        through = today + timedelta(days=lookahead_days)
        # Until the whole upcoming regular season is on file, fetch the rest of
        # it (one-off backfill); afterwards a short lookahead is enough.
        season = today.year if today.month >= 7 else today.year - 1
        loaded = conn.execute(
            "SELECT COUNT(*) FROM games WHERE gameType = 'Regular Season' "
            "AND gameDateTimeEst >= ? AND gameDateTimeEst < ?",
            (f"{season}-09-01", f"{season + 1}-07-01"),
        ).fetchone()[0]
        if loaded < MIN_SCHEDULE_GAMES and (today.month >= 10 or today.month <= 4):
            through = max(through, date(season + 1, 4, 30))
    if through < since:
        raise RefreshError(f"empty window: {since} to {through}")
    return since, through


def refresh(db_path=DB_PATH, since=None, through=None, dry_run=False,
            base_url=ESPN_SCOREBOARD_URL, pause=REQUEST_PAUSE_SECONDS, session=None,
            today=None):
    """Fetch ESPN, then apply schedule and results in one transaction."""
    conn = sqlite3.connect(db_path)
    try:
        since, through = plan_window(conn, since, through, today)
        days = [since + timedelta(days=n) for n in range((through - since).days + 1)]
        payloads, checksum = fetch_days(days, base_url, pause, session)  # may raise; no write yet
        games, skipped = parse_payloads(payloads)
        manifest = {
            "source": base_url, "window": [str(since), str(through)],
            "days_fetched": len(days), "games_in_source": len(games),
            "events_skipped": skipped, "checksum_sha256": checksum, "dry_run": dry_run,
        }
        if not dry_run:
            ensure_schema(conn)
        before = {
            gid: w for gid, w in conn.execute(
                "SELECT gameId, winner FROM games WHERE winner IS NOT NULL "
                "AND gameDateTimeEst >= ?", (str(since),))
        }
        try:
            conn.execute("BEGIN")
            ensure_map_schema(conn)
            counts = apply_games(conn, games)
            sanity_check(conn, before)
            status = "passed" if not counts["conflicts"] and not counts["ambiguous"] else "passed_with_skips"
            if dry_run:
                conn.rollback()
            else:
                conn.execute(
                    "INSERT INTO data_ingestion_log (entity, source_type, source_url, fetched_at, "
                    "rows_in_source, rows_loaded, rows_skipped, validation_status, checksum_sha256, notes) "
                    "VALUES (?, 'http', ?, datetime('now'), ?, ?, ?, ?, ?, ?)",
                    (LOG_ENTITY, base_url, len(games),
                     counts["inserted"] + counts["results_written"],
                     counts["conflicts"] + counts["ambiguous"] + skipped, status, checksum,
                     json.dumps({"window": manifest["window"], **counts})),
                )
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        manifest.update(counts)
        manifest["validation_status"] = status
        return manifest
    finally:
        conn.close()


def last_refresh(db_path=DB_PATH):
    """Most recent successful refresh as ``{"fetched_at", "status"}`` or ``None``."""
    conn = sqlite3.connect(db_path)
    try:
        exists = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='data_ingestion_log'"
        ).fetchone()[0]
        if not exists:
            return None
        row = conn.execute(
            "SELECT fetched_at, validation_status FROM data_ingestion_log "
            "WHERE entity = ? AND validation_status LIKE 'passed%' ORDER BY id DESC LIMIT 1",
            (LOG_ENTITY,),
        ).fetchone()
        return None if row is None else {"fetched_at": row[0], "status": row[1]}
    finally:
        conn.close()


STALE_AFTER_DAYS = 2
SEASON_MONTHS = (10, 11, 12, 1, 2, 3, 4, 5, 6)  # preseason through the Finals


def freshness(db_path=DB_PATH, today=None, latest_feature_date=None):
    """How current the live data is, for the data status tool and the UI.

    ``stale`` is true only during the season (October to June) when no
    successful refresh has run in the last ``STALE_AFTER_DAYS`` days. Off-days
    and the All-Star break do not make data stale: staleness is about the
    refresh job, not about whether games were played.
    """
    today = today or datetime.now(EASTERN).date()
    last = last_refresh(db_path)
    days_since = None
    if last is not None:
        fetched = datetime.strptime(last["fetched_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        days_since = max(0, (datetime.now(timezone.utc) - fetched).days)
    in_season = today.month in SEASON_MONTHS
    stale, reason = False, None
    if in_season and last is None:
        stale, reason = True, "The live data refresh has never run, so results after the last data export are missing."
    elif in_season and days_since > STALE_AFTER_DAYS:
        stale, reason = True, f"The last successful refresh was {days_since} days ago."
    ahead = None
    if latest_feature_date:
        conn = sqlite3.connect(db_path)
        try:
            ahead = conn.execute(
                "SELECT COUNT(*) FROM games WHERE winner IS NOT NULL AND gameType = 'Regular Season' "
                "AND substr(gameDateTimeEst, 1, 10) > ?", (str(latest_feature_date)[:10],),
            ).fetchone()[0]
        finally:
            conn.close()
    return {
        "last_refresh_at": None if last is None else last["fetched_at"] + " UTC",
        "last_refresh_status": None if last is None else last["status"],
        "days_since_refresh": days_since,
        "in_season": in_season,
        "stale": stale,
        "stale_reason": reason,
        "results_ahead_of_model_inputs": ahead,
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="live_data refresh",
        description="Refresh schedule and results in nba.db from ESPN's scoreboard.",
    )
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--since", type=date.fromisoformat,
                        help="First day to fetch (default: 3 days before the latest stored result).")
    parser.add_argument("--through", type=date.fromisoformat,
                        help=f"Last day to fetch (default: today + {DEFAULT_LOOKAHEAD_DAYS} days).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and validate, then roll back without writing.")
    parser.add_argument("--no-box-scores", action="store_true",
                        help="Skip box scores and the feature rebuild (schedule and results only).")
    parser.add_argument("--no-injuries", action="store_true",
                        help="Skip the injury report.")
    parser.add_argument("--no-odds-snapshot", action="store_true",
                        help="Skip storing today's playoff/title odds for the odds history chart.")
    parser.add_argument("--no-market-lines", action="store_true",
                        help="Skip storing today's and tomorrow's betting lines.")
    parser.add_argument("--no-refit", action="store_true",
                        help="Skip the weekly refit of the injury overlay and player forecasts.")
    return parser.parse_args(argv)


def features_lag(db_path=DB_PATH, features_path=None):
    """True when box scores exist for a regular-season day the feature file lacks."""
    import csv
    try:
        from src.build_features import OUTPUT_PATH
    except ImportError:  # pragma: no cover - direct-script support
        from build_features import OUTPUT_PATH
    features_path = Path(features_path or OUTPUT_PATH)
    if not features_path.exists():
        return True
    with features_path.open("rb") as handle:
        header = handle.readline().decode("utf8").strip().split(",")
        handle.seek(0, 2)
        handle.seek(max(0, handle.tell() - 8192))
        last = handle.read().decode("utf8", "ignore").strip().splitlines()[-1]
    row = next(csv.reader([last]))
    latest_feature = row[header.index("gameDateTimeEst")][:10]
    conn = sqlite3.connect(db_path)
    try:
        newest = conn.execute(
            "SELECT MAX(substr(gameDateTimeEst, 1, 10)) FROM team_statistics WHERE gameType = 'Regular Season'"
        ).fetchone()[0]
    finally:
        conn.close()
    return bool(newest) and newest > latest_feature


def run_all(db_path=DB_PATH, since=None, through=None, dry_run=False, box_scores=True,
            features_path=None, injuries=True, odds_snapshot=True, market=True, refit=True):
    """Refresh schedule + results, then box scores, then rebuild features if needed.

    The injury report, the betting lines, the day's odds snapshot and the
    weekly refit of the overlay models run last. Each is optional and fails
    soft: its error is reported, and the results already written stay written.
    """
    manifest = refresh(db_path, since, through, dry_run)
    report = {"results": manifest}
    if injuries:
        report["injuries"] = _soft(lambda: _ingest_injuries(db_path, dry_run))
    if dry_run or not box_scores:
        return report
    try:
        from src.live_boxscores import ingest_box_scores
        from src.build_features import build_features
    except ImportError:  # pragma: no cover - direct-script support
        from live_boxscores import ingest_box_scores
        from build_features import build_features
    box = ingest_box_scores(db_path)
    report["box_scores"] = box
    if box["games_loaded"] or features_lag(db_path, features_path):
        build_features(db_path, features_path) if features_path else build_features(db_path)
        report["features_rebuilt"] = True
    else:
        report["features_rebuilt"] = False
    if market:
        report["market_lines"] = _soft(lambda: _store_market_lines(db_path))
    if odds_snapshot:
        report["odds_snapshot"] = _soft(lambda: _store_odds_snapshot(db_path))
    if refit:
        report["model_refit"] = _soft(lambda: _refit_overlays(db_path))
    if report.get("features_rebuilt"):
        report["caches_primed"] = _soft(_prime_caches)
    return report


def _soft(step):
    try:
        return step()
    except Exception as exc:  # optional extras never undo the refresh
        return {"error": f"{type(exc).__name__}: {exc}"}


def _ingest_injuries(db_path, dry_run):
    try:
        from src.injuries import ingest_injuries
    except ImportError:  # pragma: no cover - direct-script support
        from injuries import ingest_injuries
    return ingest_injuries(db_path, dry_run=dry_run)


def _store_market_lines(db_path):
    try:
        from src.market_lines import snapshot
    except ImportError:  # pragma: no cover - direct-script support
        from market_lines import snapshot
    return snapshot(db_path)


REFIT_DAYS = 7


def _refit_overlays(db_path, now=None):
    """Refit the injury/schedule overlay and the player-forecast backtest once a week.

    Doing it here (not on a page view) keeps the site fast after a refresh.
    """
    try:
        from src import game_context, player_forecast
        from src.simulate_season import load_pregame_probabilities
    except ImportError:  # pragma: no cover - direct-script support
        import game_context
        import player_forecast
        from simulate_season import load_pregame_probabilities
    now = now or datetime.now().timestamp()
    out = {}
    for name, path, run in (
        ("game_context", game_context.MODEL_FILE,
         lambda: game_context.fit(load_pregame_probabilities(), db_path=db_path)),
        ("player_forecast", player_forecast.BACKTEST_FILE, lambda: player_forecast.backtest(db_path=db_path)),
    ):
        try:
            fresh = now - path.stat().st_mtime < REFIT_DAYS * 86400
        except OSError:
            fresh = False
        if fresh:
            out[name] = "fresh"
            continue
        run()
        out[name] = "refitted"
    return out


def _prime_caches():
    """Recompute every game's pregame probability now and save it to disk.

    The server reads the saved copy (data/cache) when the feature file has not
    changed since, so after a refresh the first page view does not wait about
    a minute for this.
    """
    try:
        from src import tools
    except ImportError:  # pragma: no cover - direct-script support
        import tools
    from pathlib import Path
    started = datetime.now()
    tools.enable_disk_cache(Path(__file__).resolve().parents[1] / "data" / "cache")
    tools.clear_probability_cache()
    games = len(tools._cached_probabilities())
    return {"games": games, "seconds": round((datetime.now() - started).total_seconds(), 1)}


def _store_odds_snapshot(db_path, today=None):
    """Today's playoff/title odds for the season in progress (skipped in the offseason)."""
    try:
        from src.forward_projection import load_model_inputs
        from src.odds_history import snapshot
    except ImportError:  # pragma: no cover - direct-script support
        from forward_projection import load_model_inputs
        from odds_history import snapshot
    today = today or datetime.now(EASTERN).date()
    season = today.year if today.month >= 9 else today.year - 1
    conn = sqlite3.connect(db_path)
    try:
        remaining = conn.execute(
            "SELECT COUNT(*) FROM games WHERE gameType = 'Regular Season' AND winner IS NULL "
            "AND gameDateTimeEst >= ? AND gameDateTimeEst < ?",
            (str(today), f"{season + 1}-07-01"),
        ).fetchone()[0]
    finally:
        conn.close()
    if not remaining:
        return {"skipped": "no regular-season games left this season"}
    rows = snapshot(season, str(today), load_model_inputs(), db_path)
    return {"season": season, "as_of": str(today), "rows": rows}


def main(argv=None):
    args = parse_args(argv)
    try:
        report = run_all(args.db, args.since, args.through, args.dry_run, not args.no_box_scores,
                         injuries=not args.no_injuries, odds_snapshot=not args.no_odds_snapshot,
                         market=not args.no_market_lines, refit=not args.no_refit)
    except RefreshError as exc:
        print(f"Refresh failed, database unchanged: {exc}")
        return 1
    manifest = report["results"]
    print(f"Refresh {manifest['validation_status']}{' (dry run)' if manifest['dry_run'] else ''}")
    for key, value in manifest.items():
        print(f"  {key}: {value}")
    if "box_scores" in report:
        print("Box scores")
        for key, value in report["box_scores"].items():
            print(f"  {key}: {value}")
        print(f"  features_rebuilt: {report['features_rebuilt']}")
    for extra in ("injuries", "market_lines", "odds_snapshot", "model_refit", "caches_primed"):
        if extra in report:
            print(extra.replace("_", " ").capitalize())
            for key, value in report[extra].items():
                print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
