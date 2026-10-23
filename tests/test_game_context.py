"""Missing players, schedule spots and the injury/fatigue overlay (no real DB)."""

import sqlite3

import numpy as np
import pandas as pd
import pytest

from src import game_context as gc

BOS, DEN = 1610612738, 1610612743
CITY = {BOS: "Boston", DEN: "Denver"}
BOS_PLAYERS = [101, 102, 103, 104, 105]
DEN_PLAYERS = [201, 202, 203, 204, 205]

# id, date, home, away, home score, away score (None = not played yet)
GAMES = [
    (1, "2025-10-22 19:30:00", BOS, DEN, 110, 100),
    (2, "2025-10-23 19:30:00", DEN, BOS, 105, 99),
    (3, "2025-10-25 19:30:00", BOS, DEN, 101, 98),
    (4, "2025-10-26 19:30:00", DEN, BOS, 112, 108),
    (5, "2025-10-28 19:30:00", BOS, DEN, 120, 99),
    (6, "2025-10-30 19:30:00", DEN, BOS, 100, 102),
    (7, "2025-11-01 19:30:00", BOS, DEN, None, None),
]

RATING = {
    "coefficients": {stat: (1.0 if stat == "points" else 0.0) for stat in gc.BOX_STATS},
    "intercept_per_slot": -20.0,
    "replacement": -5.0,
}
MODEL = {
    "box_rating": RATING,
    "overlay": {"missing_value": 0.1, **{name: 0.0 for name in gc.FATIGUE_FEATURES}},
    "validation": {}, "beats_plain_model": True,
}


def lineup(game_id, team):
    """Who played: BOS's 101 sits game 6; BOS's 102 is traded to DEN after game 5."""
    players = list(BOS_PLAYERS if team == BOS else DEN_PLAYERS)
    if team == BOS and game_id == 6:
        players = [p for p in players if p not in (101, 102)]
    if team == DEN and game_id == 6:
        players.append(102)
    return players


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "nba.db"
    conn = sqlite3.connect(path)
    stats = ", ".join(gc.BOX_STATS)
    conn.executescript(f"""
        CREATE TABLE games (gameId INTEGER, gameDateTimeEst TEXT, hometeamId INTEGER, awayteamId INTEGER,
            hometeamCity TEXT, homeScore INTEGER, awayScore INTEGER, winner INTEGER, gameType TEXT);
        CREATE TABLE player_statistics (personId INTEGER, playerteamId INTEGER, gameId INTEGER,
            gameDateTimeEst TEXT, firstName TEXT, lastName TEXT, numMinutes REAL, gameType TEXT, {stats});
    """)
    for game_id, stamp, home, away, hs, as_ in GAMES:
        winner = None if hs is None else (home if hs > as_ else away)
        conn.execute("INSERT INTO games VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Regular Season')",
                     (game_id, stamp, home, away, CITY[home], hs, as_, winner))
        if hs is None:
            continue
        for team in (home, away):
            for person in lineup(game_id, team):
                values = [20 if stat == "points" else 0 for stat in gc.BOX_STATS]
                conn.execute(
                    f"INSERT INTO player_statistics VALUES (?, ?, ?, ?, 'P', ?, 30.0, 'Regular Season', "
                    f"{', '.join('?' * len(values))})",
                    (person, team, game_id, stamp, str(person), *values))
    conn.commit()
    conn.close()
    return path


def test_schedule_spots_flags_back_to_backs_travel_and_road_trips(db):
    schedule = gc.load_schedule(db, "2025-09-01", "2026-07-01")
    spots = gc.schedule_spots(schedule)
    second = spots[(2, BOS)]  # Boston at Denver the night after hosting
    assert second["back_to_back"] == 1 and second["rest_days"] == 1
    assert second["travel_km"] == pytest.approx(2840, abs=60)
    assert second["time_zones"] == 2
    assert second["road_trip_game"] == 1
    assert spots[(1, BOS)]["rest_days"] is None and spots[(1, BOS)]["travel_km"] == 0
    assert spots[(3, DEN)]["three_in_four"] == 1  # 22nd, 23rd, 25th
    assert spots[(6, DEN)]["three_in_four"] == 0  # 26th, 28th, 30th


def test_rotation_needs_three_recent_appearances_and_fifteen_minutes():
    window = [{1: 30.0, 2: 10.0}, {1: 28.0, 2: 12.0, 3: 30.0}, {1: 31.0, 2: 11.0, 3: 30.0}]
    assert set(gc.rotation_before(window)) == {1}


def test_historical_absences_skip_traded_players(db):
    schedule = gc.load_schedule(db, "2025-09-01", "2026-07-01")
    players = gc.load_player_games(db, "2025-09-01")
    timeline = gc.PlayerTimeline(gc.player_states(players, RATING))
    absences = gc.historical_absences(schedule, players, timeline)
    missing = absences[(6, BOS)]
    assert [m["person_id"] for m in missing] == [101]  # 102 moved to Denver, not missing
    assert missing[0]["value"] > 0
    assert absences[(6, DEN)] == []


def test_player_value_is_minutes_share_times_rating_above_replacement(db):
    players = gc.load_player_games(db, "2025-09-01")
    states = gc.player_states(players, RATING)
    row = states[(states["personId"] == 101) & (states["gameId"] == 5)].iloc[0]
    minutes = 5 * 30.0
    raw = 20 / 30 * 48 - 20.0
    shrunk = (minutes * raw + gc.RATING_PRIOR_MINUTES * -5.0) / (minutes + gc.RATING_PRIOR_MINUTES)
    assert row["rating"] == pytest.approx(shrunk)
    assert row["value"] == pytest.approx(30 / 48 * (shrunk + 5.0))


def test_overlay_fit_recovers_a_known_effect():
    rng = np.random.default_rng(1)
    x = rng.normal(size=(20000, 2))
    offset = rng.normal(scale=0.5, size=20000)
    y = (rng.random(20000) < 1 / (1 + np.exp(-(offset + 0.4 * x[:, 0])))).astype(float)
    theta = gc.fit_overlay(x, offset, y)
    assert theta[0] == pytest.approx(0.4, abs=0.05)
    assert theta[1] == pytest.approx(0.0, abs=0.05)


def test_adjust_splits_the_change_between_injuries_and_schedule():
    model = {"overlay": {"missing_value": 0.2, "back_to_back": -0.3, **{
        name: 0.0 for name in gc.FATIGUE_FEATURES if name != "back_to_back"}}}
    none = gc.adjust(0.6, {name: 0.0 for name in gc.OVERLAY_FEATURES}, model)
    assert none["home_win_probability"] == pytest.approx(0.6) and none["change"] == 0
    both = gc.adjust(0.6, {"missing_value": 2.0, "back_to_back": 1.0}, model)
    assert both["from_injuries"] > 0 > both["from_schedule"]
    assert both["from_injuries"] + both["from_schedule"] == pytest.approx(both["change"], abs=2e-4)


def test_day_context_uses_the_injury_report_for_upcoming_games(db):
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE injuries (teamId INTEGER, personId INTEGER, playerName TEXT, status TEXT, "
                 "is_out INTEGER, injury TEXT, reported_at TEXT)")
    conn.executemany("INSERT INTO injuries VALUES (?, ?, ?, ?, ?, ?, '2025-11-01 09:00:00')", [
        (BOS, 103, "P 103", "Out", 1, "Knee"),
        (BOS, 104, "P 104", "Questionable", 0, "Ankle"),  # assumed to play
        (DEN, 999, "Two-way", "Out", 1, None),  # never played: listed, worth 0
    ])
    conn.commit()
    conn.close()
    games = pd.DataFrame([{"gameId": 7, "gameDateTimeEst": pd.Timestamp("2025-11-01 19:30"),
                           "homeTeamId": BOS, "awayTeamId": DEN, "home_win_probability": 0.6,
                           "played": False}])
    result = gc.day_context("2025-11-01", games, MODEL, db_path=db)
    game = result["games"][0]
    assert [m["person_id"] for m in game["home"]["missing"]] == [103]
    assert game["home"]["missing"][0]["value"] > 0
    assert [m["name"] for m in game["away"]["listed_not_in_rotation"]] == ["Two-way"]
    assert game["adjusted"]["home_win_probability"] < 0.6
    assert game["adjusted"]["from_injuries"] < 0
    assert result["injury_report_at"] == "2025-11-01 09:00:00"
