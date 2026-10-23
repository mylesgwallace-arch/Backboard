"""Player forecasts, betting lines, lottery odds, rivalries and awards (no real DB)."""

import sqlite3

import numpy as np
import pandas as pd
import pytest

from src import awards, game_context, history_views, lottery, market_lines, player_forecast

BOS, NYK = 1610612738, 1610612752


# ---------------------------------------------------------------------------
# Lottery
# ---------------------------------------------------------------------------

def test_lottery_matrix_matches_the_published_odds():
    matrix = lottery.slot_pick_matrix()
    assert matrix.sum(axis=1) == pytest.approx(np.ones(14))
    assert matrix.sum(axis=0) == pytest.approx(np.ones(14))
    # The NBA's table for the worst record: 14.0, 13.4, 12.7, 12.0, 47.9.
    assert matrix[0, :5] == pytest.approx([0.140, 0.134, 0.127, 0.120, 0.479], abs=0.0006)
    # The 14th team: 0.5% at the first pick, otherwise keeps the 14th.
    assert matrix[13, 0] == pytest.approx(0.005)
    assert matrix[13, 13] == pytest.approx(0.976, abs=0.001)
    # No team falls more than four places.
    for slot in range(14):
        assert matrix[slot, slot + 5:].sum() == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Betting lines
# ---------------------------------------------------------------------------

def test_moneylines_become_vig_free_probabilities():
    assert market_lines.moneyline_probability(-200) == pytest.approx(2 / 3)
    assert market_lines.moneyline_probability(150) == pytest.approx(0.4)
    p = market_lines.market_probability(-200, 170)
    assert p == pytest.approx((2 / 3) / (2 / 3 + 100 / 270))
    # Spread only: a 5.5-point home favourite.
    assert market_lines.market_probability(home_spread=-5.5) > 0.6
    assert market_lines.market_probability() is None


def espn_event(state="pre", odds=None):
    return {
        "id": "401", "date": "2025-11-02T00:30Z", "season": {"type": 2},
        "competitions": [{
            "status": {"type": {"state": state}},
            "competitors": [{"homeAway": "home", "team": {"abbreviation": "BOS"}},
                            {"homeAway": "away", "team": {"abbreviation": "NY"}}],
            "odds": [odds] if odds else [],
        }],
    }


def test_parse_event_odds_reads_both_espn_layouts():
    old = market_lines.parse_event_odds(espn_event(odds={
        "provider": {"name": "Book"}, "spread": -4.5, "overUnder": 221.5,
        "homeTeamOdds": {"moneyLine": -180}, "awayTeamOdds": {"moneyLine": 155}}))
    assert old["homeTeamId"] == BOS and old["awayTeamId"] == NYK
    assert old["date"] == "2025-11-01"  # 00:30 UTC is the evening before in New York
    assert old["home_moneyline"] == -180 and old["home_spread"] == -4.5
    assert 0.6 < old["market_home_probability"] < 0.65
    new = market_lines.parse_event_odds(espn_event(odds={
        "moneyline": {"home": {"close": {"odds": "+120"}}, "away": {"close": {"odds": "-140"}}},
        "pointSpread": {"home": {"close": {"line": "+2.5"}}}}))
    assert new["home_moneyline"] == 120 and new["home_spread"] == 2.5
    assert new["market_home_probability"] < 0.5
    assert market_lines.parse_event_odds(espn_event()) is None


@pytest.fixture
def league_db(tmp_path):
    path = tmp_path / "nba.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE games (gameId INTEGER, gameDateTimeEst TEXT, hometeamId INTEGER, awayteamId INTEGER,
            hometeamCity TEXT, homeScore INTEGER, awayScore INTEGER, winner INTEGER, gameType TEXT, gameLabel TEXT);
        CREATE TABLE team_histories (teamId INTEGER, teamCity TEXT, teamName TEXT, teamAbbrev TEXT,
            seasonFounded INTEGER, seasonActiveTill INTEGER, league TEXT);
    """)
    conn.executemany("INSERT INTO team_histories VALUES (?, ?, ?, ?, 1946, 2100, 'NBA')", [
        (BOS, "Boston", "Celtics", "BOS"), (NYK, "New York", "Knicks", "NYK")])
    games = [
        (1, "2024-11-01 19:30:00", BOS, NYK, 110, 100, BOS, "Regular Season", None),
        (2, "2024-12-01 19:30:00", NYK, BOS, 120, 90, NYK, "Regular Season", None),
        (3, "2025-04-20 19:30:00", BOS, NYK, 101, 99, BOS, "Playoffs", "East First Round"),
        (4, "2025-04-22 19:30:00", BOS, NYK, 95, 99, NYK, "Playoffs", "East First Round"),
        (5, "2025-04-25 19:30:00", NYK, BOS, 90, 99, BOS, "Playoffs", "East First Round"),
        (6, "2025-11-01 19:30:00", BOS, NYK, None, None, None, "Regular Season", None),
    ]
    conn.executemany("INSERT INTO games VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, ?)", games)
    conn.commit()
    conn.close()
    return path


def test_rivalry_counts_series_streaks_and_the_models_record(league_db):
    probabilities = pd.DataFrame({"gameId": [1, 2, 3], "home_win_probability": [0.7, 0.3, 0.6]})
    result = history_views.rivalry(BOS, NYK, probabilities, db_path=league_db)
    summary = result["summary"]
    assert result["games"] == 5  # the unplayed game is left out
    assert (summary["a_wins"], summary["b_wins"]) == (3, 2)
    assert summary["playoffs"] == {"a_wins": 2, "b_wins": 1}
    assert summary["streaks"]["current"] == {"side": "A", "games": 1}
    assert summary["streaks"]["longest"] == {"A": 1, "B": 1}
    # Game 1: BOS home at 70%, won. Game 2: NYK home at 30% (BOS 70%), BOS lost. Game 3: right.
    assert summary["model"] == {"games": 3, "correct": 2, "accuracy": pytest.approx(0.6667, abs=1e-4),
                                "brier": pytest.approx((0.09 + 0.49 + 0.16) / 3, abs=1e-4)}
    assert result["series"] == [{"season": 2024, "label": "2024-25", "a_wins": 2, "b_wins": 1,
                                 "winner": "A", "round": "East First Round"}]
    assert result["meetings"][0]["gameId"] == 5  # newest first
    assert summary["biggest_win"]["B"]["margin"] == -30


def test_market_comparison_scores_model_against_market(league_db, monkeypatch):
    conn = sqlite3.connect(league_db)
    market_lines.ensure_schema(conn)
    conn.executemany("INSERT INTO market_lines VALUES (?, ?, ?, ?, 'Book', NULL, NULL, NULL, NULL, ?, 'x')", [
        (1, "2024-11-01", BOS, NYK, 0.55), (2, "2024-12-01", NYK, BOS, 0.60)])
    conn.commit()
    conn.close()
    probabilities = pd.DataFrame({"gameId": [1, 2], "home_win_probability": [0.70, 0.45]})
    monkeypatch.setattr(market_lines.league_views, "frozen_matchup_probabilities",
                        lambda pairs, as_of, inputs, **kw: pd.DataFrame({"home_win_probability": [0.5] * len(pairs)}))
    result = market_lines.comparison(2024, "2024-12-01", None, probabilities, db_path=league_db)
    card = result["scorecard"]
    assert card["games"] == 2
    # Game 1: home won, model 0.70 beats market 0.55. Game 2: home (NYK) won, market 0.60
    # beats model 0.45, and the two picked different winners.
    assert card["model_closer"] == 1 and card["market_closer"] == 1
    assert card["picks_disagreed"] == 1 and card["model_won_disagreements"] == 0
    assert result["slate"][0]["gap"] == pytest.approx(-0.15)
    assert result["running"][-1] == {"date": "2024-12-01", "model_closer": 1, "market_closer": 1}


# ---------------------------------------------------------------------------
# Player forecasts
# ---------------------------------------------------------------------------

def test_forecast_interval_is_mean_plus_scaled_spread():
    assert player_forecast.interval(25.0, [-1.0, 1.0]) == [20, 30]
    assert player_forecast.interval(0.2, [-1.6, 1.6]) == [0, 1]


def test_player_features_only_use_earlier_games():
    rows = pd.DataFrame({
        "personId": [1, 1, 1], "teamId": [BOS] * 3, "opponentId": [NYK] * 3, "gameId": [1, 2, 3],
        "gameDateTimeEst": pd.to_datetime(["2025-11-01", "2025-11-03", "2025-11-05"]),
        "numMinutes": [30.0, 30.0, 30.0], "points": [10.0, 20.0, 60.0], "reboundsTotal": [5.0] * 3,
        "assists": [2.0] * 3, "threePointersMade": [1.0] * 3, "season": [2025] * 3, "name": ["P"] * 3,
    })
    pregame = player_forecast.player_features(rows)
    assert np.isnan(pregame.loc[0, "rate_pts"])
    assert pregame.loc[1, "naive_pts"] == 10.0
    assert pregame.loc[2, "naive_pts"] == 15.0  # the 60-point game is not known yet
    after = player_forecast.player_features(rows, include_current=True)
    assert after.loc[2, "naive_pts"] == 30.0
    assert after.loc[2, "expected_minutes"] == pytest.approx(30.0)


def test_opponent_factor_is_shrunk_and_bounded():
    allowed = pd.DataFrame({
        "gameId": range(1, 21), "teamId": [BOS, NYK] * 10, "scorerId": [NYK, BOS] * 10,
        "gameDateTimeEst": pd.date_range("2025-11-01", periods=20), "season": [2025] * 20,
        "pts": [130.0, 90.0] * 10, "reb": [44.0] * 20, "ast": [25.0] * 20, "tpm": [12.0] * 20,
    })
    factors = player_forecast.opponent_factors(allowed, include_current=True).groupby("teamId").tail(1)
    bos = factors[factors["teamId"] == BOS].iloc[0]
    # BOS allows 130; the league average through BOS's last game (19 rows) is 111.05.
    league = (10 * 130 + 9 * 90) / 19
    assert bos["factor_pts"] == pytest.approx(1 + 0.5 * (130 / league - 1), abs=1e-6)
    assert bos["factor_reb"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Awards
# ---------------------------------------------------------------------------

def test_mip_needs_minutes_and_games_in_both_seasons():
    now = pd.DataFrame({"name": ["A", "B"], "teamId": [BOS, BOS], "games": [30, 30], "mpg": [30.0, 10.0],
                        "value_per_game": [3.0, 5.0], "value": [90.0, 150.0], "ppg": [1, 1], "rpg": [1, 1],
                        "apg": [1, 1]}, index=[1, 2])
    before = pd.DataFrame({"games": [40, 40], "value_per_game": [1.0, 0.0]}, index=[1, 2])
    ranked = awards._mip(now, before, team_games=30)
    assert [person for _, person, _, _ in ranked] == [1]  # B plays 10 minutes a game
    assert ranked[0][0] == pytest.approx(2.0)


def test_box_rating_value_adds_up_game_by_game():
    rating = {"coefficients": {stat: (1.0 if stat == "points" else 0.0) for stat in game_context.BOX_STATS},
              "intercept_per_slot": -20.0, "replacement": -4.0}
    rows = pd.DataFrame([{**{stat: 0.0 for stat in game_context.BOX_STATS}, "points": 24.0, "numMinutes": 48.0}])
    beta = np.array([rating["coefficients"][s] for s in game_context.BOX_STATS])
    value = rows[game_context.BOX_STATS].to_numpy() @ beta + rows["numMinutes"] * (-20.0 + 4.0) / 48.0
    # A full game at 24 points per 48 rates 24 - 20 = 4, which is 8 above replacement.
    assert value.iloc[0] == pytest.approx(8.0)
