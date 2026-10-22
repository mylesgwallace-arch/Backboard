"""Offline coverage for the ESPN schedule + results refresh (no network).

Verifies: finals are parsed with Eastern dates and winners; results are
written only where missing; a disagreeing result is a conflict, never an
overwrite; unknown games are inserted as ESPN placeholders and replaced by a
later NBA-id schedule ingest; any fetch failure leaves the database untouched;
a failed sanity check rolls everything back; and results ingested for later
dates do not reach the as-of-date projection helpers.
"""

import json
import sqlite3
from datetime import date

import pytest

import src.live_data as live_data
import src.live_results as lr

NY, PHI, BOS, LAL = 1610612752, 1610612755, 1610612738, 1610612747

SCHEMA = """
CREATE TABLE games (
    gameId INTEGER, gameDateTimeEst TEXT, hometeamCity TEXT, hometeamName TEXT,
    hometeamId INTEGER, awayteamCity TEXT, awayteamName TEXT, awayteamId INTEGER,
    homeScore INTEGER, awayScore INTEGER, winner INTEGER, gameType TEXT,
    gameSubtype TEXT, gameLabel TEXT, gameSubLabel TEXT, seriesGameNumber TEXT,
    attendance REAL, arenaId INTEGER, arenaName TEXT, arenaCity TEXT,
    arenaState TEXT, officials TEXT, gameDate TEXT);
CREATE TABLE team_histories (teamId INTEGER, teamCity TEXT, teamName TEXT,
    teamAbbrev TEXT, seasonFounded INTEGER, seasonActiveTill INTEGER, league TEXT);
"""


def make_db(path, games=()):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.executemany(
        "INSERT INTO team_histories VALUES (?,?,?,?,?,?,?)",
        [(NY, "New York", "Knicks", "NYK", 1946, 2026, "NBA"),
         (PHI, "Philadelphia", "76ers", "PHI", 1949, 2026, "NBA"),
         (BOS, "Boston", "Celtics", "BOS", 1946, 2026, "NBA"),
         (LAL, "Los Angeles", "Lakers", "LAL", 1947, 2026, "NBA")],
    )
    conn.executemany(
        "INSERT INTO games (gameId, gameDateTimeEst, hometeamId, awayteamId, homeScore, "
        "awayScore, winner, gameType) VALUES (?,?,?,?,?,?,?,'Regular Season')", games)
    conn.commit()
    conn.close()
    return str(path)


def event(espn_id, utc, home, away, home_score=None, away_score=None, final=True,
          season_type=2, note=None):
    sides = [
        {"homeAway": "home", "team": {"abbreviation": home}, "score": str(home_score)},
        {"homeAway": "away", "team": {"abbreviation": away}, "score": str(away_score)},
    ]
    return {
        "id": str(espn_id), "date": utc, "season": {"type": season_type},
        "competitions": [{
            "competitors": sides,
            "status": {"type": {"name": "STATUS_FINAL" if final else "STATUS_SCHEDULED",
                                "completed": final}},
            "venue": {"fullName": "Arena", "address": {"city": "Town", "state": "NY"}},
            "notes": [{"headline": note}] if note else [],
        }],
    }


class FakeSession:
    """Serves ESPN-shaped JSON by ``dates`` parameter; counts calls."""

    def __init__(self, by_day=None, fail_on=None):
        self.by_day = by_day or {}
        self.fail_on = fail_on
        self.calls = []

    def get(self, url, params=None, timeout=None, headers=None):
        day = params["dates"]
        self.calls.append(day)
        if day == self.fail_on:
            raise lr.requests.ConnectionError("boom")
        body = json.dumps({"events": self.by_day.get(day, [])}).encode()
        return type("R", (), {"content": body, "raise_for_status": lambda self: None})()


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(lr.time, "sleep", lambda *_: None)


def run(db, session, since, through, **kw):
    return lr.refresh(db, since=since, through=through, session=session, pause=0, **kw)


def rows(db):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            "SELECT gameId, gameDateTimeEst, hometeamId, awayteamId, homeScore, awayScore, winner, gameType "
            "FROM games ORDER BY gameDateTimeEst").fetchall()
    finally:
        conn.close()


def test_parse_final_uses_eastern_date_and_winner():
    # 00:00Z on the 16th is 7pm Eastern on the 15th.
    game = lr.parse_event(event(1, "2025-01-16T00:00Z", "PHI", "NY", 119, 125))
    assert game["date"] == "2025-01-15"
    assert game["timestamp"] == "2025-01-15 19:00:00"
    assert game["final"] and game["winner"] == NY
    assert game["home_id"] == PHI and game["away_id"] == NY


def test_parse_skips_non_nba_and_unfinished_results():
    assert lr.parse_event(event(2, "2026-02-15T20:00Z", "STARS", "WORLD", 1, 2)) is None
    assert lr.parse_event(event(3, "2026-02-15T20:00Z", "PHI", "NY", season_type=4)) is None
    scheduled = lr.parse_event(event(4, "2026-10-22T23:30Z", "NY", "BOS", final=False))
    assert scheduled["final"] is False and scheduled["winner"] is None


def test_results_fill_empty_rows_only(tmp_path):
    db = make_db(tmp_path / "t.db", [
        (10, "2025-01-15 19:00:00", PHI, NY, None, None, None),
        (11, "2025-01-14 19:00:00", BOS, LAL, 100, 90, BOS),
    ])
    session = FakeSession({
        "20250115": [event(1, "2025-01-16T00:00Z", "PHI", "NY", 119, 125)],
        "20250114": [event(2, "2025-01-15T00:00Z", "BOS", "LAL", 100, 90)],
    })
    manifest = run(db, session, date(2025, 1, 14), date(2025, 1, 15))
    assert manifest["results_written"] == 1 and manifest["already_current"] == 1
    assert manifest["inserted"] == 0 and manifest["conflicts"] == 0
    data = {r[0]: r for r in rows(db)}
    assert data[10][4:7] == (119, 125, NY)
    assert data[11][4:7] == (100, 90, BOS)


def test_conflicting_result_is_logged_not_overwritten(tmp_path):
    db = make_db(tmp_path / "t.db", [(10, "2025-01-15 19:00:00", PHI, NY, 100, 99, PHI)])
    session = FakeSession({"20250115": [event(1, "2025-01-16T00:00Z", "PHI", "NY", 119, 125)]})
    manifest = run(db, session, date(2025, 1, 15), date(2025, 1, 15))
    assert manifest["conflicts"] == 1 and manifest["validation_status"] == "passed_with_skips"
    assert rows(db)[0][4:7] == (100, 99, PHI)


def test_unknown_game_is_inserted_with_placeholder_id_and_provenance(tmp_path):
    db = make_db(tmp_path / "t.db")
    session = FakeSession({"20261022": [event(777, "2026-10-22T23:30Z", "NY", "BOS", final=False)]})
    manifest = run(db, session, date(2026, 10, 22), date(2026, 10, 22))
    assert manifest["inserted"] == 1
    game = rows(db)[0]
    assert game[0] == lr.ESPN_ID_OFFSET + 777 and game[6] is None
    log = live_data.get_ingestion_log(db)[0]
    assert log["entity"] == lr.LOG_ENTITY and log["checksum_sha256"] == manifest["checksum_sha256"]
    assert lr.last_refresh(db)["status"] == "passed"
    # Running again changes nothing and does not duplicate the game.
    run(db, session, date(2026, 10, 22), date(2026, 10, 22))
    assert len(rows(db)) == 1


def test_schedule_file_replaces_placeholder(tmp_path):
    db = make_db(tmp_path / "t.db")
    session = FakeSession({"20261022": [event(777, "2026-10-22T23:30Z", "NY", "BOS", final=False)]})
    run(db, session, date(2026, 10, 22), date(2026, 10, 22))
    csv = tmp_path / "sched.csv"
    csv.write_text(
        "gameId,gameDateTimeEst,hometeamId,awayteamId\n"
        f"22600001,2026-10-22 19:30:00,{NY},{BOS}\n")
    live_data.ingest_schedule(str(csv), db_path=db)
    data = rows(db)
    assert [r[0] for r in data] == [22600001]


def test_fetch_failure_aborts_before_any_write(tmp_path):
    db = make_db(tmp_path / "t.db", [(10, "2025-01-15 19:00:00", PHI, NY, None, None, None)])
    session = FakeSession(
        {"20250115": [event(1, "2025-01-16T00:00Z", "PHI", "NY", 119, 125)]}, fail_on="20250116")
    with pytest.raises(lr.RefreshError):
        run(db, session, date(2025, 1, 15), date(2025, 1, 16))
    assert rows(db)[0][6] is None
    assert live_data.get_ingestion_log(db) == []


def test_failed_sanity_check_rolls_everything_back(tmp_path, monkeypatch):
    db = make_db(tmp_path / "t.db", [(10, "2025-01-15 19:00:00", PHI, NY, None, None, None)])
    session = FakeSession({"20250115": [event(1, "2025-01-16T00:00Z", "PHI", "NY", 119, 125)]})

    def boom(conn, before):
        raise lr.RefreshError("forced")

    monkeypatch.setattr(lr, "sanity_check", boom)
    with pytest.raises(lr.RefreshError):
        run(db, session, date(2025, 1, 15), date(2025, 1, 15))
    assert rows(db)[0][6] is None


def test_dry_run_writes_nothing(tmp_path):
    db = make_db(tmp_path / "t.db", [(10, "2025-01-15 19:00:00", PHI, NY, None, None, None)])
    session = FakeSession({"20250115": [event(1, "2025-01-16T00:00Z", "PHI", "NY", 119, 125)]})
    manifest = run(db, session, date(2025, 1, 15), date(2025, 1, 15), dry_run=True)
    assert manifest["results_written"] == 1 and rows(db)[0][6] is None
    assert lr.last_refresh(db) is None


def test_default_window_starts_before_latest_result(tmp_path):
    db = make_db(tmp_path / "t.db", [(11, "2025-01-14 19:00:00", BOS, LAL, 100, 90, BOS)])
    conn = sqlite3.connect(db)
    since, through = lr.plan_window(conn, today=date(2025, 1, 20), lookahead_days=5)
    conn.close()
    assert since == date(2025, 1, 11)
    assert through == date(2025, 4, 30)  # season incomplete: backfill to its end


def test_default_window_backfills_incomplete_season_then_stays_short(tmp_path):
    db = make_db(tmp_path / "t.db", [(11, "2026-10-20 19:00:00", BOS, LAL, 100, 90, BOS)])
    conn = sqlite3.connect(db)
    _, through = lr.plan_window(conn, today=date(2026, 10, 21))
    assert through == date(2027, 4, 30)  # 1 of 1,230 games loaded
    conn.executemany(
        "INSERT INTO games (gameId, gameDateTimeEst, hometeamId, awayteamId, gameType) "
        "VALUES (?, '2026-11-01 19:00:00', ?, ?, 'Regular Season')",
        [(1000 + n, BOS, LAL) for n in range(lr.MIN_SCHEDULE_GAMES)])
    _, through = lr.plan_window(conn, today=date(2026, 10, 21))
    assert through == date(2026, 11, 4)
    conn.close()


def test_partial_schedule_projection_is_refused(tmp_path):
    from src.forward_projection import season_schedule

    class Inputs:  # only the attribute season_schedule reads
        import pandas as pd
        games = pd.DataFrame({"season": [], "gameId": []})

    db = make_db(tmp_path / "t.db", [(10, "2026-10-22 19:00:00", PHI, NY, None, None, None)])
    with pytest.raises(ValueError, match="Only 1 of 1230"):
        season_schedule(Inputs, 2026, db)


def test_espn_team_ids_match_database_abbreviations():
    # The nba.db team_histories abbreviations for the 30 current teams.
    expected = {
        "ATL": 1610612737, "BOS": 1610612738, "CLE": 1610612739, "NOP": 1610612740,
        "CHI": 1610612741, "DAL": 1610612742, "DEN": 1610612743, "GSW": 1610612744,
        "HOU": 1610612745, "LAC": 1610612746, "LAL": 1610612747, "MIA": 1610612748,
        "MIL": 1610612749, "MIN": 1610612750, "BKN": 1610612751, "NYK": 1610612752,
        "ORL": 1610612753, "IND": 1610612754, "PHI": 1610612755, "PHX": 1610612756,
        "POR": 1610612757, "SAC": 1610612758, "SAS": 1610612759, "OKC": 1610612760,
        "TOR": 1610612761, "UTA": 1610612762, "MEM": 1610612763, "WAS": 1610612764,
        "DET": 1610612765, "CHA": 1610612766,
    }
    assert sorted(lr.ESPN_TEAM_IDS.values()) == sorted(expected.values())
    assert len(set(lr.ESPN_TEAM_IDS)) == 30


def test_new_results_do_not_leak_into_as_of_projection_inputs(tmp_path):
    """schedule_from_database marks only games with a result as played; a
    refresh that adds later results cannot change the earlier cutoff view."""
    from src.forward_projection import schedule_from_database
    db = make_db(tmp_path / "t.db", [
        (10, "2026-10-22 19:00:00", PHI, NY, None, None, None),
        (11, "2026-12-01 19:00:00", BOS, LAL, None, None, None),
    ])
    conn = sqlite3.connect(db)
    conn.execute("UPDATE games SET gameType='Regular Season'")
    conn.commit(); conn.close()
    before = schedule_from_database(2026, db)
    session = FakeSession({"20261022": [event(1, "2026-10-22T23:00Z", "PHI", "NY", 110, 100)]})
    run(db, session, date(2026, 10, 22), date(2026, 10, 22))
    after = schedule_from_database(2026, db)
    assert before["target"].isna().all()
    assert after.loc[after["gameDateTimeEst"] >= "2026-12-01", "target"].isna().all()
    assert after.loc[after["gameId"] == 10, "target"].iloc[0] == 1.0


def test_freshness_flags_stale_in_season_only(tmp_path):
    db = make_db(tmp_path / "t.db", [(11, "2025-01-14 19:00:00", BOS, LAL, 100, 90, BOS),
                                     (12, "2025-01-20 19:00:00", BOS, LAL, 100, 90, BOS)])
    winter, summer = date(2026, 1, 20), date(2026, 8, 1)
    never = lr.freshness(db, winter, "2025-01-15")
    assert never["stale"] and never["last_refresh_at"] is None
    assert never["results_ahead_of_model_inputs"] == 1
    assert not lr.freshness(db, summer)["stale"]  # offseason is never stale
    run(db, FakeSession(), date(2025, 1, 14), date(2025, 1, 14))
    fresh = lr.freshness(db, winter)
    assert not fresh["stale"] and fresh["days_since_refresh"] == 0
    conn = sqlite3.connect(db)
    conn.execute("UPDATE data_ingestion_log SET fetched_at = datetime('now', '-5 days')")
    conn.commit(); conn.close()
    old = lr.freshness(db, winter)
    assert old["stale"] and "5 days" in old["stale_reason"]
