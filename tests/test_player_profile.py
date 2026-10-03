"""Player pages: season rows, rates, highs and game logs on a small synthetic database."""

import sqlite3

import pandas as pd
import pytest

from src import player_profile as pp

GSW, LAL, BOS = 1610612744, 1610612747, 1610612738
PLAYER = 7

PS_COLUMNS = [
    "personId", "gameId", "gameDateTimeEst", "gameType", "playerteamId", "opponentteamId",
    "playerteamCity", "playerteamName", "opponentteamCity", "opponentteamName", "home", "win",
    "numMinutes", "points", "fieldGoalsMade", "fieldGoalsAttempted", "threePointersMade",
    "threePointersAttempted", "freeThrowsMade", "freeThrowsAttempted", "reboundsOffensive",
    "reboundsDefensive", "reboundsTotal", "assists", "steals", "blocks", "turnovers",
    "foulsPersonal", "plusMinusPoints", "startingPosition", "comment",
]
TS_COLUMNS = [
    "teamId", "opponentTeamId", "gameId", "gameDateTimeEst", "gameType", "numMinutes", "teamScore",
    "opponentScore", "fieldGoalsMade", "fieldGoalsAttempted", "threePointersAttempted",
    "freeThrowsAttempted", "reboundsOffensive", "reboundsDefensive", "reboundsTotal", "turnovers", "win",
]
NAMES = {GSW: ("Golden State", "Warriors"), LAL: ("Los Angeles", "Lakers"), BOS: ("Boston", "Celtics")}


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "nba.db"
    with sqlite3.connect(path) as c:
        c.execute("""CREATE TABLE players (personId INTEGER, firstName TEXT, lastName TEXT, birthDate TEXT,
            school TEXT, country TEXT, heightInches REAL, bodyWeightLbs REAL, jersey TEXT, guard INTEGER,
            forward INTEGER, center INTEGER, draftYear REAL, draftRound REAL, draftNumber REAL,
            fromYear REAL, toYear REAL, nbaFlag REAL)""")
        c.execute("INSERT INTO players VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (PLAYER, "Test", "Guard", "1990-05-01", "State", "USA", 75, 190, "3", 1, 0, 0,
                   2012, 1, 9, 2012, None, 1))
        c.execute(f"CREATE TABLE player_statistics ({', '.join(PS_COLUMNS)})")
        c.execute(f"CREATE TABLE team_statistics ({', '.join(TS_COLUMNS)})")
        c.execute("""CREATE TABLE games (gameId INTEGER, gameDateTimeEst TEXT, gameType TEXT, hometeamId INTEGER,
            awayteamId INTEGER, hometeamName TEXT, awayteamName TEXT, homeScore REAL, awayScore REAL, winner INTEGER)""")
        c.execute("""CREATE TABLE team_histories (teamId INTEGER, teamCity TEXT, teamName TEXT, teamAbbrev TEXT,
            seasonFounded INTEGER, seasonActiveTill INTEGER, league TEXT)""")
        c.executemany("INSERT INTO team_histories VALUES (?,?,?,?,?,?,?)", [
            (GSW, "Golden State", "Warriors", "GSW", 1971, 2100, "NBA"),
            (LAL, "Los Angeles", "Lakers", "LAL", 1960, 2100, "NBA"),
            (BOS, "Boston", "Celtics", "BOS", 1946, 2100, "NBA"),
        ])

        def game(gid, date, home, away, home_score, away_score, gtype="Regular Season"):
            c.execute("INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (gid, date, gtype, home, away, NAMES[home][1], NAMES[away][1], home_score, away_score,
                       home if home_score > away_score else away))
            for team, opp, own, other in ((home, away, home_score, away_score), (away, home, away_score, home_score)):
                c.execute(f"INSERT INTO team_statistics VALUES ({', '.join('?' * len(TS_COLUMNS))})",
                          (team, opp, gid, date, gtype, 240, own, other, 40, 88, 30, 20, 10, 34, 44, 14,
                           int(own > other)))

        def line(gid, date, team, opp, home, win, minutes, pts, start="G", comment=None, team_id=True,
                 gtype="Regular Season"):
            c.execute(f"INSERT INTO player_statistics VALUES ({', '.join('?' * len(PS_COLUMNS))})", (
                PLAYER, gid, date, gtype, team if team_id else None, opp if team_id else None,
                NAMES[team][0], NAMES[team][1], NAMES[opp][0], NAMES[opp][1], home, win,
                minutes, pts, 8, 16, 3, 7, 4, 5, 1, 5, 6, 7, 2, 1, 3, 2, 5, start, comment))

        # 2023-24: three games for GSW, then two for LAL (one with no team id).
        game(1, "2023-10-25 19:00:00", GSW, BOS, 110, 100)
        game(2, "2023-10-27 19:00:00", BOS, GSW, 120, 105)
        game(3, "2023-10-28 19:00:00", GSW, LAL, 101, 99)
        game(4, "2024-01-10 19:00:00", LAL, BOS, 100, 90)
        game(5, "2024-01-14 19:00:00", BOS, LAL, 95, 97)
        game(6, "2024-01-16 19:00:00", LAL, GSW, 88, 92)   # the player did not play
        game(7, "2024-04-25 19:00:00", LAL, BOS, 105, 100, "Playoffs")
        line(1, "2023-10-25 19:00:00", GSW, BOS, 1, 1, "30", 23)
        line(2, "2023-10-27 19:00:00", GSW, BOS, 0, 0, "32", 41)
        line(3, "2023-10-28 19:00:00", GSW, LAL, 1, 1, "28", 12, start=None)
        line(4, "2024-01-10 19:00:00", LAL, BOS, 1, 1, "35", 30, team_id=False)
        line(5, "2024-01-14 19:00:00", LAL, BOS, 0, 1, "33", 18)
        line(6, "2024-01-16 19:00:00", LAL, GSW, 1, 0, None, 0, comment="DNP - Coach's Decision")
        line(7, "2024-04-25 19:00:00", LAL, BOS, 1, 1, "38", 33, gtype="Playoffs")
        # A 1960-61 game: no minutes logged, but a real stat line.
        game(8, "1960-11-01 19:00:00", BOS, LAL, 120, 110)
        line(8, "1960-11-01 19:00:00", BOS, LAL, 1, 1, "0", 25, start=None)
        c.commit()
    pp._build_team_season_totals.cache_clear()
    pp.starts_recorded_seasons.cache_clear()
    pp.latest_database_season.cache_clear()
    pp._team_history.cache_clear()
    yield path
    pp._build_team_season_totals.cache_clear()
    pp.starts_recorded_seasons.cache_clear()
    pp.latest_database_season.cache_clear()
    pp._team_history.cache_clear()


def test_season_of_starts_in_october_and_keeps_the_bubble_in_2019():
    assert pp.season_of("2023-10-24") == 2023
    assert pp.season_of("2024-04-14") == 2023
    assert pp.season_of("2020-08-14") == 2019   # bubble restart
    assert pp.season_of("2020-10-11") == 2019   # 2020 Finals
    assert pp.season_of("2020-12-22") == 2020   # 2020-21 opener


def test_age_is_taken_on_february_first():
    assert pp.age_on("1990-05-01", 2023) == 33   # Feb 1, 2024
    assert pp.age_on("1990-01-15", 2023) == 34
    assert pp.age_on(None, 2023) is None


def test_games_recover_a_missing_team_id_and_skip_did_not_play(db):
    games = pp.load_games(PLAYER, db)
    regular = games[(games["kind"] == "regular") & (games["season"] == 2023)]
    assert list(regular["gameId"]) == [1, 2, 3, 4, 5]          # game 6 was a DNP
    assert regular.loc[regular["gameId"] == 4, "teamId"].iloc[0] == LAL
    assert regular.loc[regular["gameId"] == 4, "oppId"].iloc[0] == BOS
    early = games[games["season"] == 1960]
    assert len(early) == 1 and not early["mp_logged"].iloc[0]  # counted, minutes unknown
    assert early["stl"].isna().all()                             # not recorded in 1960


def test_a_traded_season_has_a_combined_row_then_each_stint(db):
    profile = pp.player_profile(PLAYER, db)
    rows = [r for r in profile["seasons"]["regular"] if r["season"] == 2023]
    assert [r["team"] for r in rows] == ["TOT", "GSW", "LAL"]
    total, gsw, lal = rows
    assert (total["g"], gsw["g"], lal["g"]) == (5, 3, 2)
    assert total["pts"] == gsw["pts"] + lal["pts"] == 124
    assert total["age"] == 33
    assert profile["seasons"]["playoffs"][0]["pts"] == 33
    assert profile["last_team"]["abbreviation"] == "LAL"
    assert profile["career"]["regular"]["g"] == 6


def test_rates_follow_the_published_formulas(db):
    profile = pp.player_profile(PLAYER, db)
    gsw = next(r for r in profile["seasons"]["regular"] if r["season"] == 2023 and r["team"] == "GSW")
    # 3 games x (16 FGA, 5 FTA, 76 pts) -> TS = 76 / (2 * (48 + 0.44 * 15))
    assert gsw["ts_pct"] == pytest.approx(76 / (2 * (48 + 0.44 * 15)), abs=1e-3)
    assert gsw["efg_pct"] == pytest.approx((24 + 0.5 * 9) / 48, abs=1e-3)
    # Usage: player plays / team plays while on the floor (90 of 240 team minutes per game).
    on_court = 90 / (3 * 240 / 5)
    team_plays = 3 * (88 + 0.44 * 20 + 14) * on_court
    assert gsw["usg_pct"] == pytest.approx((48 + 0.44 * 15 + 9) / team_plays, abs=1e-3)
    assert gsw["tov_pct"] == pytest.approx(9 / (48 + 0.44 * 15 + 9), abs=1e-3)


def test_team_rates_stay_blank_when_team_box_scores_are_missing():
    sums = {"fgm": 10, "fga": 20, "tpm": 0, "tpa": 0, "ftm": 5, "fta": 6, "orb": None, "drb": None,
            "trb": 12, "ast": 4, "stl": None, "blk": None, "tov": None, "pts": 25,
            "minutes_complete": True}
    ctx = {key: None for key in pp.CONTEXT_KEYS}
    ctx["fgm"] = 40.0
    rates = pp._advanced(sums, ctx, 1960)
    assert rates["ts_pct"] == pytest.approx(25 / (2 * (20 + 0.44 * 6)), abs=1e-3)
    assert rates["ast_pct"] == pytest.approx(4 / 30, abs=1e-3)
    for key in ("usg_pct", "orb_pct", "drb_pct", "trb_pct", "stl_pct", "blk_pct", "poss"):
        assert rates[key] is None


def test_career_highs_and_milestones(db):
    profile = pp.player_profile(PLAYER, db)
    high = profile["highs"]["regular"]["pts"]
    assert (high["value"], high["date"], high["opponent"]) == (41, "2023-10-27", "BOS")
    miles = profile["milestones"]["regular"]
    assert miles["games_40_points"] == 1
    assert miles["games_20_points"] == 4   # 23, 41, 30 and the 1960 game's 25


def test_game_log_has_rest_days_and_location_splits(db):
    log = pp.player_game_log(PLAYER, 2023, db_path=db)
    assert [g["n"] for g in log["games"]] == [1, 2, 3, 4, 5]
    assert [g["rest_days"] for g in log["games"]] == [None, 1, 0, 73, 3]
    home = next(s for s in log["splits"]["location"] if s["label"] == "Home")
    assert home["g"] == 3 and home["pts"] == pytest.approx((23 + 12 + 30) / 3, abs=0.05)
    assert log["games"][3]["team"] == "LAL" and log["games"][3]["score"] == "100-90"
    with pytest.raises(ValueError):
        pp.player_game_log(PLAYER, 2010, db_path=db)
