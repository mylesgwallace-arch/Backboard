"""Data integrity checks on a tiny synthetic database and feature file."""

import sqlite3

import pandas as pd

from src import data_checks as dc

HOME, AWAY = 1610612738, 1610612747


def _db(path, games, team_rows, player_rows):
    with sqlite3.connect(path) as connection:
        pd.DataFrame(games).to_sql("games", connection, index=False)
        pd.DataFrame(team_rows).to_sql("team_statistics", connection, index=False)
        pd.DataFrame(player_rows).to_sql("player_statistics", connection, index=False)
    return path


def _game(game_id, home_score, away_score, winner, game_type="Regular Season"):
    return {"gameId": game_id, "gameDateTimeEst": "2024-11-01 19:00:00", "hometeamId": HOME,
            "awayteamId": AWAY, "homeScore": home_score, "awayScore": away_score,
            "winner": winner, "gameType": game_type}


def _team(game_id, team_id, score):
    return {"gameId": game_id, "teamId": team_id, "teamScore": score,
            "gameDateTimeEst": "2024-11-01 19:00:00"}


def _player(game_id, team_id, minutes):
    return {"gameId": game_id, "playerteamId": team_id, "numMinutes": minutes,
            "gameDateTimeEst": "2024-11-01 19:00:00", "gameType": "Regular Season"}


def _status(results):
    return {row["check"]: row["status"] for row in results}


def test_clean_database_passes(tmp_path):
    path = _db(tmp_path / "ok.db", [_game(1, 110, 100, HOME)],
               [_team(1, HOME, 110), _team(1, AWAY, 100)], [_player(1, HOME, 30.0)])
    statuses = _status(dc.database_checks(path))
    assert set(statuses.values()) == {"pass"}


def test_each_defect_is_caught(tmp_path):
    games = [
        _game(1, 110, 100, AWAY),          # winner disagrees with the score
        _game(2, 100, 100, HOME),          # tie
        _game(2, 100, 100, HOME),          # duplicate gameId
        _game(3, 101, 99, HOME),
    ]
    teams = [_team(1, HOME, 110), _team(1, AWAY, 100), _team(3, HOME, 95), _team(3, AWAY, 99),
             _team(3, 0, 0)]              # score mismatch + a teamId-0 placeholder row
    players = [_player(3, HOME, -4.0), _player(3, 999, 10.0)]
    statuses = _status(dc.database_checks(_db(tmp_path / "bad.db", games, teams, players)))
    assert statuses["games.duplicate_game_id"] == "fail"
    assert statuses["games.winner_disagrees_with_score"] == "fail"
    assert statuses["games.tied_final_score"] == "warn"
    assert statuses["team_statistics.score_disagrees_with_game"] == "fail"
    assert statuses["team_statistics.team_not_in_game"] == "warn"
    assert statuses["player_statistics.negative_minutes"] == "fail"
    assert statuses["player_statistics.team_not_in_game"] == "fail"


def test_feature_null_shares_and_pairing(tmp_path):
    rows = []
    for game in range(1, 5):
        for team in (HOME, AWAY):
            rows.append({"gameId": game, "teamId": team, "gameDateTimeEst": "2024-11-01",
                         "rest_days": 2, "win_rate_rolling_10": 0.5 if game > 1 else None,
                         "player_points_per_minute_rolling_10": None,
                         "teamScore_rolling_10": None})
    rows.append({"gameId": 9, "teamId": HOME, "gameDateTimeEst": "2024-11-01", "rest_days": -1})
    path = tmp_path / "features.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    statuses = _status(dc.feature_checks(path))
    assert statuses["features.game_without_two_teams"] == "fail"   # 1 of 5 games > 1%
    assert statuses["features.negative_rest_days"] == "fail"
    assert statuses["features.null_share.win_rate_rolling_10"] == "warn"
    assert statuses["features.null_share.teamScore_rolling_10"] == "fail"
    # Known and documented: a warning, not a failure.
    assert statuses["features.null_share.player_points_per_minute_rolling_10"] == "warn"
