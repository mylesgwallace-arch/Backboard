"""Roster bookkeeping: window players, transactions, adjusted model inputs."""

import sqlite3

import numpy as np
import pandas as pd
import pytest

from src import roster_state as rs

TEAM_A, TEAM_B = 1610612737, 1610612738


def _db(tmp_path):
    """Two teams, 12 games each (A plays B every day), three players per team."""
    db_path = tmp_path / "nba.db"
    games, team_rows, player_rows = [], [], []
    roster = {TEAM_A: [1, 2, 3], TEAM_B: [4, 5, 6]}
    for index in range(12):
        game_id = 100 + index
        date = (pd.Timestamp("2025-03-01 19:00") + pd.DateOffset(days=index)).strftime("%Y-%m-%d %H:%M:%S")
        games.append((game_id, date, "Regular Season", TEAM_A, TEAM_B))
        for team in (TEAM_A, TEAM_B):
            team_rows.append((game_id, date, team, "Regular Season"))
            for person in roster[team]:
                minutes = 0 if (person == 3 and index % 2) else 20 + person
                player_rows.append((game_id, date, team, person, f"P{person}", "X",
                                    str(minutes), 10 * person + index, person, person + 1,
                                    "Regular Season"))
    # Player 7 has history with some other team, earlier.
    for index in range(12):
        date = (pd.Timestamp("2024-12-01 19:00") + pd.DateOffset(days=index)).strftime("%Y-%m-%d %H:%M:%S")
        games.append((500 + index, date, "Regular Season", 1610612739, 1610612740))
        player_rows.append((500 + index, date, 1610612739, 7, "P7", "X", "30", 25, 5, 6,
                            "Regular Season"))
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE games (gameId INTEGER, gameDateTimeEst TEXT, gameType TEXT, "
                           "hometeamId INTEGER, awayteamId INTEGER)")
        connection.execute("CREATE TABLE team_statistics (gameId INTEGER, gameDateTimeEst TEXT, "
                           "teamId INTEGER, gameType TEXT)")
        connection.execute("CREATE TABLE player_statistics (gameId INTEGER, gameDateTimeEst TEXT, "
                           "playerteamId INTEGER, personId INTEGER, firstName TEXT, lastName TEXT, "
                           "numMinutes TEXT, points INTEGER, assists INTEGER, reboundsTotal INTEGER, "
                           "gameType TEXT)")
        connection.execute("CREATE TABLE players (personId INTEGER, firstName TEXT, lastName TEXT)")
        connection.executemany("INSERT INTO games VALUES (?, ?, ?, ?, ?)", games)
        connection.executemany("INSERT INTO team_statistics VALUES (?, ?, ?, ?)", team_rows)
        connection.executemany("INSERT INTO player_statistics VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               player_rows)
        connection.execute("INSERT INTO players VALUES (7, 'P7', 'X')")
        connection.commit()
    return db_path


def _features(db_path):
    """Feature rows built from the fixture exactly as build_features defines them."""
    with sqlite3.connect(db_path) as connection:
        rows = rs._player_rows(connection, game_ids=list(range(100, 112)))
    out = []
    for team in (TEAM_A, TEAM_B):
        team_games = sorted(rows.loc[rows["teamId"] == team, "gameId"].unique())
        for position, game in enumerate(team_games):
            window = team_games[max(0, position - 10):position]
            window_rows = rows[(rows["teamId"] == team) & rows["gameId"].isin(window)]
            feature = {"gameId": game, "teamId": team,
                       "gameDateTimeEst": rows.loc[rows["gameId"] == game, "gameDateTimeEst"].iloc[0]}
            for name, column in rs.SUM_FEATURES.items():
                feature[name] = (window_rows.groupby("personId")[column].mean().sum()
                                 if not window_rows.empty else np.nan)
            active = window_rows[window_rows["minutes"] > 0]
            feature["active_players_rolling_10"] = active["personId"].nunique()
            previous = window[-1] if window else None
            feature["active_players_last_game"] = int(
                ((window_rows["gameId"] == previous) & (window_rows["minutes"] > 0)).sum()
            ) if previous else 0
            out.append(feature)
    frame = pd.DataFrame(out)
    frame["gameDateTimeEst"] = pd.to_datetime(frame["gameDateTimeEst"])
    return frame


def _events(rows):
    frame = pd.DataFrame(rows, columns=["event_id", "event_timestamp", "team_id", "person_id",
                                        "change_type"])
    frame["source"] = "test"
    frame["source_url"] = "https://example.test"
    frame["event_timestamp"] = pd.to_datetime(frame["event_timestamp"], utc=True)
    return frame


def test_no_transactions_leaves_production_snapshot_unchanged(tmp_path):
    db_path = _db(tmp_path)
    features = _features(db_path)
    adjusted, state, applied = rs.adjusted_snapshots(
        "2025-06-01", features, db_path=db_path, transactions=_events([])
    )
    production = features.sort_values("gameDateTimeEst").groupby("teamId").tail(1).set_index("teamId")
    for column in rs.ROSTER_FEATURES:
        np.testing.assert_allclose(adjusted[column].to_numpy(dtype=float),
                                   production.loc[adjusted.index, column].to_numpy(dtype=float))
    assert applied == []
    assert all(not team["arrived"] and not team["departed"] for team in state.values())


def test_trade_moves_the_players_window_contribution(tmp_path):
    db_path = _db(tmp_path)
    features = _features(db_path)
    events = _events([
        ("t1-remove", "2025-05-01", TEAM_A, 2, "remove"),
        ("t1-add", "2025-05-01", TEAM_B, 2, "add"),
    ])
    adjusted, state, applied = rs.adjusted_snapshots("2025-06-01", features, db_path=db_path,
                                                      transactions=events)
    production = features.sort_values("gameDateTimeEst").groupby("teamId").tail(1).set_index("teamId")
    departed = state[TEAM_A]["departed"]
    assert [player["person_id"] for player in departed] == [2]
    assert departed[0]["now_with_team_id"] == TEAM_B
    # Player 2 scored 20 + game index over the window (games 1-10 -> mean 25.5).
    assert departed[0]["player_points_rolling_10"] == pytest.approx(25.5)
    assert adjusted.at[TEAM_A, "player_points_rolling_10"] == pytest.approx(
        production.at[TEAM_A, "player_points_rolling_10"] - 25.5)
    arrived = state[TEAM_B]["arrived"]
    assert [player["person_id"] for player in arrived] == [2]
    # His last 10 rows are games 2-11 -> mean points 26.5.
    assert arrived[0]["player_points_rolling_10"] == pytest.approx(26.5)
    assert adjusted.at[TEAM_B, "active_players_rolling_10"] == production.at[TEAM_B, "active_players_rolling_10"] + 1
    assert adjusted.at[TEAM_A, "active_players_rolling_10"] == production.at[TEAM_A, "active_players_rolling_10"] - 1
    assert len(applied) == 2


def test_signing_elsewhere_implies_departure_and_history_comes_along(tmp_path):
    db_path = _db(tmp_path)
    features = _features(db_path)
    events = _events([
        ("s1", "2025-07-01", TEAM_B, 1, "add"),   # A's player 1 signs with B
        ("s2", "2025-07-02", TEAM_A, 7, "add"),   # outside player 7 joins A
    ])
    _, state, _ = rs.adjusted_snapshots("2025-08-01", features, db_path=db_path, transactions=events)
    assert {p["person_id"] for p in state[TEAM_A]["departed"]} == {1}
    arrived = {p["person_id"]: p for p in state[TEAM_A]["arrived"]}
    assert arrived[7]["player_points_rolling_10"] == pytest.approx(25.0)
    assert arrived[7]["name"] == "P7 X"


def test_events_before_the_snapshot_or_after_the_cutoff_are_ignored(tmp_path):
    db_path = _db(tmp_path)
    features = _features(db_path)
    events = _events([
        ("old", "2025-02-01", TEAM_A, 2, "remove"),
        ("future", "2025-09-01", TEAM_A, 3, "remove"),
    ])
    _, state, applied = rs.adjusted_snapshots("2025-08-01", features, db_path=db_path,
                                              transactions=events)
    assert applied == []
    assert not state[TEAM_A]["departed"]


def test_missing_production_value_stays_missing(tmp_path):
    db_path = _db(tmp_path)
    features = _features(db_path)
    last = features.sort_values("gameDateTimeEst").groupby("teamId").tail(1).index
    features.loc[last, "player_points_rolling_10"] = np.nan
    events = _events([("w", "2025-05-01", TEAM_A, 2, "remove")])
    adjusted, _, _ = rs.adjusted_snapshots("2025-06-01", features, db_path=db_path, transactions=events)
    assert np.isnan(adjusted.at[TEAM_A, "player_points_rolling_10"])


def test_load_transactions_merges_files_and_keeps_high_confidence(tmp_path):
    first = tmp_path / "a.csv"
    second = tmp_path / "b.csv"
    base = "event_id,event_timestamp,team_id,person_id,change_type,source,source_url"
    first.write_text(base + ",confidence_level\n"
                     "e1,2025-07-01,1610612737,1,add,feed,https://x.test,high\n"
                     "e2,2025-07-02,1610612737,2,add,feed,https://x.test,low\n", encoding="utf-8")
    second.write_text(base + "\ne3,2025-07-03,1610612738,3,remove,manual,https://y.test\n",
                      encoding="utf-8")
    events = rs.load_transactions([first, second, tmp_path / "missing.csv"])
    assert list(events["event_id"]) == ["e1", "e3"]
