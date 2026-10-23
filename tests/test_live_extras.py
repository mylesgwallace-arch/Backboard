"""Live scores, the injury report and odds snapshots, all offline."""

import json
import sqlite3

import pandas as pd
import pytest

from src import injuries
from src import live_games
from src import odds_history
from src.live_results import FetchError

BOS, NYK, LAL = 1610612738, 1610612752, 1610612747

SCHEMA = """
CREATE TABLE games (gameId INTEGER, gameDateTimeEst TEXT, hometeamId INTEGER, awayteamId INTEGER,
    homeScore INTEGER, awayScore INTEGER, winner INTEGER, gameType TEXT, gameLabel TEXT);
CREATE TABLE team_histories (teamId INTEGER, teamCity TEXT, teamName TEXT, teamAbbrev TEXT,
    seasonFounded INTEGER, seasonActiveTill INTEGER, league TEXT);
CREATE TABLE players (personId INTEGER, firstName TEXT, lastName TEXT, nbaFlag REAL);
CREATE TABLE player_statistics (personId INTEGER, playerteamId INTEGER, gameDateTimeEst TEXT);
"""


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "nba.db"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.executemany("INSERT INTO team_histories VALUES (?, ?, ?, ?, 1946, 2100, 'NBA')", [
        (BOS, "Boston", "Celtics", "BOS"), (NYK, "New York", "Knicks", "NYK"),
        (LAL, "Los Angeles", "Lakers", "LAL"),
    ])
    conn.execute("INSERT INTO games VALUES (7, '2026-01-10 19:30:00', ?, ?, NULL, NULL, NULL, "
                 "'Regular Season', NULL)", (BOS, NYK))
    conn.executemany("INSERT INTO players VALUES (?, ?, ?, 1)", [
        (1628369, "Jayson", "Tatum"), (1630217, "Jalen", "Brunson"),
    ])
    conn.commit()
    conn.close()
    return path


# ---- live scores ----------------------------------------------------------

def espn_event(espn_id, home, away, state, period=0, clock=720.0, home_score=0, away_score=0):
    return {
        "id": str(espn_id), "date": "2026-01-11T00:30Z", "season": {"type": 2},
        "competitions": [{
            "competitors": [
                {"homeAway": "home", "team": {"abbreviation": home}, "score": str(home_score)},
                {"homeAway": "away", "team": {"abbreviation": away}, "score": str(away_score)},
            ],
            "status": {"period": period, "clock": clock, "displayClock": "5:00",
                       "type": {"state": state, "completed": state == "post",
                                "shortDetail": f"{state} detail"}},
        }],
    }


class FakeSession:
    def __init__(self, events, fail=False):
        self.events, self.fail, self.calls = events, fail, 0

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls += 1
        if self.fail:
            import requests
            raise requests.ConnectionError("offline")
        raw = json.dumps({"events": self.events}).encode()

        class Response:
            content = raw

            def raise_for_status(self):
                return None
        return Response()


def test_minutes_remaining_by_period_and_overtime():
    assert live_games.minutes_remaining(1, 720) == 48
    assert live_games.minutes_remaining(3, 300) == 17
    assert live_games.minutes_remaining(4, 0) == 0
    assert live_games.minutes_remaining(5, 120) == 2  # overtime: only its clock is left
    assert live_games.minutes_remaining(0, 0) == 48


def test_parse_live_event_reads_state_and_skips_non_nba():
    game = live_games.parse_live_event(espn_event(1, "BOS", "NY", "in", 3, 300, 70, 64))
    assert game["home_id"] == BOS and game["away_id"] == NYK
    assert (game["state"], game["period"], game["home_score"]) == ("in", 3, 70)
    assert game["date"] == "2026-01-10"  # 00:30 UTC is the evening before in New York
    pre = live_games.parse_live_event(espn_event(2, "BOS", "NY", "pre"))
    assert pre["home_score"] is None
    assert live_games.parse_live_event(espn_event(3, "BOS", "XYZ", "in")) is None


def test_live_scoreboard_scores_in_game_probability_and_caches(db, monkeypatch):
    live_games.clear_cache()
    monkeypatch.setattr(live_games.league_views, "frozen_matchup_probabilities",
                        lambda pairs, as_of, inputs, **k: pd.DataFrame({"home_win_probability": [0.6] * len(pairs)}))
    session = FakeSession([
        espn_event(11, "BOS", "NY", "in", 4, 60, 100, 90),
        espn_event(12, "LAL", "BOS", "pre"),
    ])
    board = live_games.live_scoreboard("2026-01-10", None, None, session=session, db_path=db, sd=13.5)
    live, upcoming = board["games"]
    assert live["state"] == "in" and upcoming["state"] == "pre"
    assert live["pregame_home_win_probability"] == 0.6
    assert live["minutes_remaining"] == 1.0
    assert live["live_home_win_probability"] > 0.99
    assert len(live["history"]) == 1
    assert upcoming["live_home_win_probability"] is None
    assert board["live_games"] == 1
    live_games.live_scoreboard("2026-01-10", None, None, session=session, db_path=db, sd=13.5)
    assert session.calls == 1  # second call inside the cache window
    live_games.clear_cache()


def test_live_scoreboard_raises_fetch_error_offline(db, monkeypatch):
    live_games.clear_cache()
    monkeypatch.setattr("src.live_results.time.sleep", lambda s: None)
    with pytest.raises(FetchError):
        live_games.live_scoreboard("2026-01-10", None, None, session=FakeSession([], fail=True), db_path=db)


def test_margin_sd_reads_the_stored_report(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"margin": {"holdout_residual_sd": 12.0}}))
    assert live_games.margin_sd(path) == 12.0
    assert live_games.margin_sd(tmp_path / "missing.json") == live_games.DEFAULT_MARGIN_SD


# ---- injuries -------------------------------------------------------------

def injury_payload(extra_team=None):
    teams = [
        {"displayName": "Boston Celtics", "injuries": [
            {"status": "Out", "date": "2026-01-08T18:00Z", "shortComment": "Achilles; out for the season.",
             "athlete": {"id": "4065648", "displayName": "Jayson Tatum", "position": {"abbreviation": "F"}},
             "details": {"type": "Achilles", "side": "Right", "returnDate": "2026-10-01"}},
            {"status": "Day-To-Day", "athlete": {"id": "999", "displayName": "Unknown Rookie"},
             "details": {"type": "Ankle", "side": "Not Specified"}},
        ]},
        {"displayName": "New York Knicks", "injuries": [
            {"status": "Questionable", "athlete": {"id": "3934672", "displayName": "Jalen Brunson"}},
        ]},
    ]
    if extra_team:
        teams.append(extra_team)
    return {"injuries": teams}


class InjurySession(FakeSession):
    def __init__(self, payload):
        super().__init__([])
        self.payload = payload

    def get(self, url, params=None, timeout=None, headers=None):
        raw = json.dumps(self.payload).encode()

        class Response:
            content = raw

            def raise_for_status(self):
                return None
        return Response()


def test_injury_ingest_replaces_the_report_and_logs(db):
    manifest = injuries.ingest_injuries(db, session=InjurySession(injury_payload()))
    assert manifest["players_listed"] == 3 and manifest["players_out"] == 1
    assert manifest["players_unmatched"] == 1 and manifest["teams"] == 2
    report = injuries.injury_report(db)
    celtics = next(t for t in report["teams"] if t["teamId"] == BOS)
    tatum = celtics["players"][0]
    assert tatum["person_id"] == 1628369 and tatum["out"] is True
    assert tatum["injury"] == "Right Achilles" and tatum["return_date"] == "2026-10-01"
    rookie = celtics["players"][1]
    assert rookie["person_id"] is None and rookie["injury"] == "Ankle"
    # A second run replaces, never appends.
    injuries.ingest_injuries(db, session=InjurySession({"injuries": [injury_payload()["injuries"][1]]}))
    report = injuries.injury_report(db)
    assert report["players_listed"] == 1
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM data_ingestion_log WHERE entity='injuries'").fetchone()[0] == 2


def test_a_bad_entry_leaves_the_previous_report(db):
    injuries.ingest_injuries(db, session=InjurySession(injury_payload()))
    broken = injury_payload({"displayName": "Los Angeles Lakers", "injuries": [{"athlete": {}}]})
    with pytest.raises(injuries.InjuryError):
        injuries.ingest_injuries(db, session=InjurySession(broken))
    assert injuries.injury_report(db)["players_listed"] == 3


def test_dry_run_and_empty_report(db):
    assert injuries.injury_report(db) == {"reported_at": None, "teams": [], "players_listed": 0}
    manifest = injuries.ingest_injuries(db, session=InjurySession(injury_payload()), dry_run=True)
    assert manifest["dry_run"] and manifest["players_listed"] == 3
    assert injuries.injury_report(db)["players_listed"] == 0


def test_team_names_match_on_the_nickname(db):
    with sqlite3.connect(db) as conn:
        lookup = injuries._team_lookup(conn)
    assert injuries._resolve_team({"displayName": "LA Lakers"}, lookup) == LAL
    assert injuries._resolve_team({"displayName": "Seattle SuperSonics"}, lookup) is None


# ---- odds history ---------------------------------------------------------

def test_snapshots_store_replace_and_read_back(db, monkeypatch):
    def fake_odds(season, as_of, inputs, n_simulations, random_state, team_names):
        bump = 0.1 if as_of == "2026-01-10" else 0.0
        return {"model": "elo_boosted_ensemble", "games_completed": 500, "teams": [
            {"teamId": BOS, "p_made_playoffs": 0.9, "p_champion": 0.2 + bump, "mean_wins": 55.0},
            {"teamId": NYK, "p_made_playoffs": 0.7, "p_champion": 0.1, "mean_wins": 48.0},
        ]}

    monkeypatch.setattr(odds_history, "season_playoff_odds", fake_odds)
    assert odds_history.history(2025, db_path=db) == {"season": 2025, "dates": [], "teams": []}
    odds_history.snapshot(2025, "2026-01-03", None, db_path=db, n_simulations=10)
    odds_history.snapshot(2025, "2026-01-10", None, db_path=db, n_simulations=10)
    odds_history.snapshot(2025, "2026-01-10", None, db_path=db, n_simulations=10)  # replaces
    history = odds_history.history(2025, db_path=db)
    assert history["dates"] == ["2026-01-03", "2026-01-10"]
    boston = history["teams"][0]
    assert boston["teamId"] == BOS
    assert [p["p_champion"] for p in boston["points"]] == [0.2, 0.3]
    only = odds_history.history(2025, db_path=db, team_id=NYK)
    assert [t["teamId"] for t in only["teams"]] == [NYK]
