"""Standings, scoreboard, schedules, report card and the in-game model (no real DB)."""

import json
import math
import sqlite3

import numpy as np
import pandas as pd
import pytest

from src import league_views as lv

BOS, NYK, LAL, DEN = 1610612738, 1610612752, 1610612747, 1610612743  # East, East, West, West

SCHEMA = """
CREATE TABLE games (gameId INTEGER, gameDateTimeEst TEXT, hometeamId INTEGER, awayteamId INTEGER,
    homeScore INTEGER, awayScore INTEGER, winner INTEGER, gameType TEXT, gameLabel TEXT);
CREATE TABLE team_histories (teamId INTEGER, teamCity TEXT, teamName TEXT, teamAbbrev TEXT,
    seasonFounded INTEGER, seasonActiveTill INTEGER, league TEXT);
"""

GAMES = [
    # id, date, home, away, home score, away score, winner
    (1, "2025-10-22 19:30:00", BOS, NYK, 110, 100, BOS),
    (2, "2025-10-23 19:30:00", LAL, BOS, 101, 99, LAL),
    (3, "2025-10-24 19:30:00", NYK, LAL, 120, 90, NYK),
    (4, "2025-10-25 19:30:00", BOS, DEN, 95, 105, DEN),
    (5, "2025-10-26 19:30:00", DEN, NYK, None, None, None),  # not played yet
]


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "nba.db"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.executemany(
        "INSERT INTO games VALUES (?, ?, ?, ?, ?, ?, ?, 'Regular Season', NULL)", GAMES)
    conn.executemany("INSERT INTO team_histories VALUES (?, ?, ?, ?, 1946, 2100, 'NBA')", [
        (BOS, "Boston", "Celtics", "BOS"), (NYK, "New York", "Knicks", "NYK"),
        (LAL, "Los Angeles", "Lakers", "LAL"), (DEN, "Denver", "Nuggets", "DEN"),
    ])
    conn.commit()
    conn.close()
    return path


def probabilities_frame():
    rows = [
        (1, "2025-10-22 19:30:00", 2025, BOS, NYK, 1.0, 0.60, 0.70),
        (2, "2025-10-23 19:30:00", 2025, LAL, BOS, 1.0, 0.45, 0.40),  # home won at 40%: miss
        (3, "2025-10-24 19:30:00", 2025, NYK, LAL, 1.0, 0.55, 0.52),
        (4, "2025-10-25 19:30:00", 2025, BOS, DEN, 0.0, 0.50, 0.85),  # big miss
    ]
    return pd.DataFrame(rows, columns=["gameId", "gameDateTimeEst", "season", "homeTeamId", "awayTeamId",
                                       "target", "elo_probability", "home_win_probability"])


def fake_frozen(pairs, as_of, inputs, **kwargs):
    return pd.DataFrame({"home_win_probability": [0.25] * len(pairs)})


def test_standings_records_games_behind_and_streaks(db):
    table = lv.standings(2025, db_path=db)
    east = {row["abbreviation"]: row for row in table["conferences"]["East"]}
    assert table["games_counted"] == 4
    assert (east["BOS"]["wins"], east["BOS"]["losses"]) == (1, 2)
    assert (east["NYK"]["wins"], east["NYK"]["losses"]) == (1, 1)
    assert east["NYK"]["conference_rank"] == 1 and east["NYK"]["games_behind"] == 0
    assert east["BOS"]["games_behind"] == 0.5
    assert east["BOS"]["streak"] == "L2"
    assert east["BOS"]["home"] == "1-1" and east["BOS"]["road"] == "0-1"
    assert east["BOS"]["conference_record"] == "1-0"
    assert east["BOS"]["point_differential"] == pytest.approx((110 + 99 + 95 - 100 - 101 - 105) / 3, abs=0.05)


def test_standings_as_of_cuts_later_results(db):
    table = lv.standings(2025, as_of="2025-10-22", db_path=db)
    assert table["games_counted"] == 1
    east = {row["abbreviation"]: row for row in table["conferences"]["East"]}
    assert east["BOS"]["wins"] == 1 and east["BOS"]["streak"] == "W1"


def test_scoreboard_uses_recorded_pregame_for_played_and_frozen_for_the_rest(db, monkeypatch):
    monkeypatch.setattr(lv, "frozen_matchup_probabilities", fake_frozen)
    played = lv.scoreboard("2025-10-23", inputs=None, probabilities=probabilities_frame(), db_path=db)
    game = played["games"][0]
    assert game["probability_source"] == "pregame"
    assert game["home_win_probability"] == 0.40
    assert game["status"] == "final" and game["winner_team_id"] == LAL
    assert game["model_correct"] is False and game["upset"] is False  # 40% is not under 40%
    assert played["previous_date"] == "2025-10-22" and played["next_date"] == "2025-10-24"

    upcoming = lv.scoreboard("2025-10-26", inputs=None, probabilities=probabilities_frame(), db_path=db)
    game = upcoming["games"][0]
    assert game["probability_source"] == "frozen" and game["home_win_probability"] == 0.25
    assert game["status"] == "scheduled" and game["favorite_team_id"] == NYK
    assert upcoming["summary"] == {"games": 1, "final": 0, "scheduled": 1, "model_correct": 0, "upsets": 0}


def test_scoreboard_attaches_margin_and_survives_a_margin_failure(db, monkeypatch):
    monkeypatch.setattr(lv, "frozen_matchup_probabilities", fake_frozen)

    def margin(home, away, cutoff):
        if home == DEN:
            raise ValueError("no history")
        return {"predicted_home_margin": 3.5, "predicted_total_points": 220.0}

    board = lv.scoreboard("2025-10-22", None, probabilities_frame(), margin_fn=margin, db_path=db)
    assert board["games"][0]["predicted_home_margin"] == 3.5
    board = lv.scoreboard("2025-10-26", None, probabilities_frame(), margin_fn=margin, db_path=db)
    assert "predicted_home_margin" not in board["games"][0]


def test_team_schedule_counts_results_and_expected_wins(db, monkeypatch):
    monkeypatch.setattr(lv, "frozen_matchup_probabilities", fake_frozen)
    schedule = lv.team_schedule(NYK, 2025, None, probabilities_frame(), "2025-10-26", db_path=db)
    assert schedule["record"] == "1-1"
    assert [g["result"] for g in schedule["games"] if g["played"]] == ["L", "W"]
    left = schedule["remaining"]
    assert left["games"] == 1 and left["road"] == 1
    assert left["expected_wins"] == pytest.approx(0.8, abs=0.05)  # 1 - 0.25 home probability
    with pytest.raises(ValueError):
        lv.team_schedule(BOS, 2019, None, probabilities_frame(), "2019-10-26", db_path=db)


def test_strength_of_schedule_ranks_by_remaining_opponents(db, monkeypatch):
    monkeypatch.setattr(lv, "frozen_matchup_probabilities", fake_frozen)
    monkeypatch.setattr(lv, "strength_table", lambda inputs, as_of: [
        {"teamId": BOS, "win_probability_vs_average": 0.6}, {"teamId": NYK, "win_probability_vs_average": 0.5},
        {"teamId": LAL, "win_probability_vs_average": 0.4}, {"teamId": DEN, "win_probability_vs_average": 0.7},
    ])
    sos = lv.strength_of_schedule(2025, None, "2025-10-26", db_path=db)
    rows = {row["abbreviation"]: row for row in sos["teams"]}
    assert sos["ranked_by"] == "remaining_opponent_strength"
    assert rows["NYK"]["remaining_opponent_strength"] == 0.7  # only Denver left
    assert rows["DEN"]["remaining_opponent_strength"] == 0.5
    assert rows["NYK"]["difficulty_rank"] == 1
    assert rows["BOS"]["remaining_games"] == 0 and rows["BOS"]["remaining_opponent_strength"] is None
    assert rows["BOS"]["played_opponent_strength"] == pytest.approx((0.5 + 0.4 + 0.7) / 3, abs=1e-4)


def test_report_card_grades_calls_against_results(db):
    card = lv.report_card(2025, probabilities_frame(), db_path=db, split_start="2025-10-23")
    overall = card["overall"]
    assert overall["games"] == 4 and overall["correct"] == 2 and overall["accuracy"] == 0.5
    expected_brier = np.mean([(0.7 - 1) ** 2, (0.4 - 1) ** 2, (0.52 - 1) ** 2, (0.85 - 0) ** 2])
    assert overall["brier"] == pytest.approx(expected_brier, abs=1e-4)
    assert card["baselines"]["always_home"] == 0.75
    assert card["games_in_holdout"] == 3 and card["all_out_of_sample"] is False
    assert card["biggest_misses"][0]["gameId"] == 4
    assert card["biggest_misses"][0]["winner_pregame_probability"] == pytest.approx(0.15)
    assert card["biggest_misses"][0]["home_score"] == 95
    tiers = {t["tier"]: t for t in card["confidence_tiers"]}
    assert tiers["Lock"]["games"] == 1 and tiers["Lock"]["favorite_won"] == 0.0
    assert sum(t["games"] for t in card["confidence_tiers"]) == 4
    assert card["cumulative"][-1] == {"date": "2025-10-25", "games": 4, "accuracy": 0.5}
    assert card["current_correct_streak"] == 0
    with pytest.raises(ValueError):
        lv.report_card(2019, probabilities_frame(), db_path=db)


def test_in_game_probability_starts_at_pregame_and_ends_decided():
    sd = 13.5
    assert lv.in_game_win_probability(0.64, 0, 48, sd) == pytest.approx(0.64, abs=1e-6)
    assert lv.in_game_win_probability(0.5, 0, 24, sd) == pytest.approx(0.5)
    assert lv.in_game_win_probability(0.5, 1, 0, sd) == 1.0
    assert lv.in_game_win_probability(0.5, -1, 0, sd) == 0.0
    # A lead is worth more as the clock runs down.
    early = lv.in_game_win_probability(0.5, 6, 36, sd)
    late = lv.in_game_win_probability(0.5, 6, 3, sd)
    assert 0.5 < early < late < 1.0


def test_normal_ppf_inverts_the_cdf():
    for p in (0.01, 0.2, 0.5, 0.77, 0.999):
        assert lv._normal_cdf(lv._normal_ppf(p)) == pytest.approx(p, abs=1e-7)
    assert math.isfinite(lv._normal_ppf(0.0)) and math.isfinite(lv._normal_ppf(1.0))
