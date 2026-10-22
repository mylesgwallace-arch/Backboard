"""Offline coverage for ESPN box-score ingestion (no network)."""

import json
import sqlite3

import pytest

import src.live_boxscores as lb
import src.live_results as lr

NY, PHI = 1610612752, 1610612755

SCHEMA = """
CREATE TABLE games (gameId INTEGER, gameDateTimeEst TEXT, hometeamCity TEXT, hometeamName TEXT,
  hometeamId INTEGER, awayteamCity TEXT, awayteamName TEXT, awayteamId INTEGER, homeScore INTEGER,
  awayScore INTEGER, winner INTEGER, gameType TEXT, gameSubtype TEXT, gameLabel TEXT,
  gameSubLabel TEXT, seriesGameNumber TEXT, attendance REAL, arenaId INTEGER, arenaName TEXT,
  arenaCity TEXT, arenaState TEXT, officials TEXT, gameDate TEXT);
CREATE TABLE players (personId INTEGER, firstName TEXT, lastName TEXT, nbaFlag REAL);
CREATE TABLE team_statistics (gameId INTEGER, gameDateTimeEst TEXT, teamCity TEXT, teamName TEXT,
  teamId INTEGER, opponentTeamCity TEXT, opponentTeamName TEXT, opponentTeamId INTEGER, home INTEGER,
  win REAL, teamScore INTEGER, opponentScore INTEGER, assists REAL, blocks REAL, steals REAL,
  fieldGoalsAttempted REAL, fieldGoalsMade REAL, fieldGoalsPercentage REAL, threePointersAttempted REAL,
  threePointersMade REAL, threePointersPercentage REAL, freeThrowsAttempted REAL, freeThrowsMade REAL,
  freeThrowsPercentage REAL, reboundsDefensive REAL, reboundsOffensive REAL, reboundsTotal REAL,
  foulsPersonal REAL, turnovers REAL, plusMinusPoints REAL, numMinutes REAL, gameType TEXT,
  gameLabel TEXT, gameDate TEXT);
CREATE TABLE player_statistics (firstName TEXT, lastName TEXT, personId REAL, gameId INTEGER,
  gameDateTimeEst TEXT, playerteamCity TEXT, playerteamName TEXT, opponentteamCity TEXT,
  opponentteamName TEXT, gameType TEXT, gameLabel TEXT, win REAL, home REAL, numMinutes TEXT,
  points REAL, assists REAL, blocks REAL, steals REAL, fieldGoalsAttempted REAL, fieldGoalsMade REAL,
  fieldGoalsPercentage REAL, threePointersAttempted REAL, threePointersMade REAL,
  threePointersPercentage REAL, freeThrowsAttempted REAL, freeThrowsMade REAL,
  freeThrowsPercentage REAL, reboundsDefensive REAL, reboundsOffensive REAL, reboundsTotal REAL,
  foulsPersonal REAL, turnovers REAL, plusMinusPoints REAL, playerteamId REAL, opponentteamId REAL,
  comment TEXT, startingPosition TEXT, gameDate TEXT);
"""
STAT_NAMES = ["MIN", "PTS", "FG", "3PT", "FT", "REB", "AST", "TO", "STL", "BLK", "OREB", "DREB", "PF", "+/-"]


def team_block(abbr, side, tid, points_per_player):
    stats = [
        ("fieldGoalsMade-fieldGoalsAttempted", "40-88"),
        ("threePointFieldGoalsMade-threePointFieldGoalsAttempted", "10-30"),
        ("freeThrowsMade-freeThrowsAttempted", "16-20"), ("totalRebounds", "40"),
        ("offensiveRebounds", "10"), ("defensiveRebounds", "30"), ("assists", "25"),
        ("steals", "7"), ("blocks", "5"), ("turnovers", "12"), ("totalTurnovers", "12"), ("fouls", "20"),
    ]
    athletes = []
    for n in range(5):
        athletes.append({
            "athlete": {"id": str(tid * 10 + n), "displayName": "Player %s%s" % (abbr, "ABCDE"[n]),
                        "position": {"abbreviation": "G"}},
            "starter": True, "didNotPlay": False,
            "stats": ["30", str(points_per_player), "4-9", "1-3", "2-2", "5", "3", "1", "1", "0",
                      "1", "4", "2", "+3"]})
    athletes.append({"athlete": {"id": str(tid * 10 + 9), "displayName": "Bench " + abbr},
                     "starter": False, "didNotPlay": True, "reason": "COACH DECISION", "stats": []})
    return (
        {"team": {"id": str(tid), "abbreviation": abbr}, "homeAway": side,
         "statistics": [{"name": n, "displayValue": v} for n, v in stats]},
        {"team": {"id": str(tid)}, "statistics": [{"names": STAT_NAMES, "athletes": athletes}]},
    )


def summary(home_points=20, away_points=19, final=True):
    h_team, h_players = team_block("PHI", "home", 20, home_points)
    a_team, a_players = team_block("NY", "away", 18, away_points)
    return {
        "boxscore": {"teams": [a_team, h_team], "players": [a_players, h_players]},
        "header": {"competitions": [{
            "status": {"type": {"name": "STATUS_FINAL" if final else "STATUS_IN_PROGRESS"}},
            "competitors": [{"homeAway": "home", "score": str(home_points * 5)},
                            {"homeAway": "away", "score": str(away_points * 5)}]}]},
    }


class FakeSession:
    def __init__(self, payloads):
        self.payloads, self.calls = payloads, []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append(params["event"])
        body = json.dumps(self.payloads[params["event"]]).encode()
        return type("R", (), {"content": body, "raise_for_status": lambda self: None})()


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(lb.time, "sleep", lambda *_: None)


def make_db(tmp_path, home_score=100, away_score=95, winner=PHI, with_map=True):
    path = str(tmp_path / "t.db")
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO games (gameId, gameDateTimeEst, hometeamCity, hometeamName, hometeamId, awayteamCity, "
        "awayteamName, awayteamId, homeScore, awayScore, winner, gameType) VALUES "
        "(?, '2026-10-22 19:00:00', 'Philadelphia', '76ers', ?, 'New York', 'Knicks', ?, ?, ?, ?, 'Regular Season')",
        (lr.ESPN_ID_OFFSET + 77, PHI, NY, home_score, away_score, winner))
    conn.execute("INSERT INTO players VALUES (1, 'Player', 'PHIA', 1), (2, 'Someone', 'Else', 1)")
    if with_map:
        lr.ensure_map_schema(conn)
        conn.execute("INSERT INTO espn_game_map VALUES (?, 77)", (lr.ESPN_ID_OFFSET + 77,))
    conn.commit()
    conn.close()
    return path


def ingest(path, payload, **kw):
    return lb.ingest_box_scores(path, session=FakeSession({"77": payload}), pause=0, **kw)


def count(path, sql):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql).fetchone()[0]
    finally:
        conn.close()


def test_box_score_fills_team_and_player_rows(tmp_path):
    path = make_db(tmp_path)
    manifest = ingest(path, summary())
    assert manifest["games_loaded"] == 1 and manifest["games_skipped"] == 0
    assert count(path, "SELECT COUNT(*) FROM team_statistics") == 2
    conn = sqlite3.connect(path)
    home = conn.execute(
        "SELECT home, win, teamScore, opponentScore, plusMinusPoints, fieldGoalsPercentage, reboundsTotal, "
        "turnovers FROM team_statistics WHERE teamId = ?", (PHI,)).fetchone()
    assert home == (1, 1.0, 100, 95, 5.0, 0.455, 40, 12)
    assert conn.execute("SELECT COUNT(*) FROM player_statistics").fetchone()[0] == 12  # 10 played + 2 DNP
    assert conn.execute("SELECT COUNT(*) FROM player_statistics WHERE comment IS NOT NULL").fetchone()[0] == 2
    assert conn.execute("SELECT personId FROM player_statistics WHERE lastName = 'PHIA'").fetchone()[0] == 1.0
    assert conn.execute("SELECT COUNT(*) FROM player_statistics WHERE personId < 0").fetchone()[0] == 11
    log = conn.execute("SELECT entity, validation_status FROM data_ingestion_log").fetchone()
    assert log == (lb.LOG_ENTITY, "passed")
    conn.close()


def test_second_run_does_not_duplicate(tmp_path):
    path = make_db(tmp_path)
    ingest(path, summary())
    again = ingest(path, summary())
    assert again["games_pending"] == 0
    assert count(path, "SELECT COUNT(*) FROM team_statistics") == 2


def test_score_mismatch_is_skipped_not_half_written(tmp_path):
    path = make_db(tmp_path, home_score=101)  # games table disagrees with the box score
    manifest = ingest(path, summary())
    assert manifest["games_loaded"] == 0 and manifest["games_skipped"] == 1
    assert "does not match" in manifest["skipped"][0]["reason"]
    assert count(path, "SELECT COUNT(*) FROM team_statistics") == 0


def test_player_points_must_add_up_to_team_score(tmp_path):
    path = make_db(tmp_path)
    bad = summary()
    bad["boxscore"]["players"][1]["statistics"][0]["athletes"][0]["stats"][1] = "99"
    assert ingest(path, bad)["games_skipped"] == 1
    assert count(path, "SELECT COUNT(*) FROM player_statistics") == 0


def test_not_final_summary_is_skipped(tmp_path):
    path = make_db(tmp_path)
    assert ingest(path, summary(final=False))["games_skipped"] == 1


def test_dry_run_writes_nothing(tmp_path):
    path = make_db(tmp_path)
    manifest = ingest(path, summary(), dry_run=True)
    assert manifest["games_loaded"] == 1
    assert count(path, "SELECT COUNT(*) FROM team_statistics") == 0


def test_no_map_table_means_nothing_pending(tmp_path):
    path = make_db(tmp_path, with_map=False)
    assert lb.ingest_box_scores(path, session=FakeSession({}), pause=0)["games_pending"] == 0


def test_name_matching_normalises_and_breaks_ties(tmp_path):
    path = make_db(tmp_path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO players VALUES (10, 'Tim', 'Hardaway Jr.', 1), (11, 'Tim', 'Hardaway Jr.', 1)")
    conn.execute("INSERT INTO player_statistics (personId, playerteamId, gameDateTimeEst) "
                 "VALUES (11, ?, '2026-04-01 19:00:00')", (PHI,))
    index = lb.PlayerIndex(conn)
    conn.close()
    assert lb.normalise_name("Nikola Jokic Jr.") == "nikola jokic"
    assert index.person_id(5, "Tim Hardaway", PHI) == 11       # two matches; one played for this team
    assert index.person_id(5, "Someone Else", NY) == 2          # single match
    assert index.person_id(321, "Rookie Nobody", NY) == -321    # unmatched -> stable synthetic id


def test_schedule_file_replacement_keeps_result_and_box_score(tmp_path):
    import src.live_data as live_data
    path = make_db(tmp_path)
    ingest(path, summary())
    csv = tmp_path / "sched.csv"
    csv.write_text("gameId,gameDateTimeEst,hometeamId,awayteamId\n22600005,2026-10-22 19:00:00,%d,%d\n" % (PHI, NY))
    live_data.ingest_schedule(str(csv), db_path=path)
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT gameId, winner, homeScore FROM games").fetchall() == [(22600005, PHI, 100)]
    assert conn.execute("SELECT DISTINCT gameId FROM team_statistics").fetchall() == [(22600005,)]
    assert conn.execute("SELECT DISTINCT gameId FROM player_statistics").fetchall() == [(22600005,)]
    assert conn.execute("SELECT gameId, espnId FROM espn_game_map").fetchall() == [(22600005, 77)]
    conn.close()
