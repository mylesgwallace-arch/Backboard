"""Player name -> personId resolution against a small temporary database."""

import sqlite3

import pytest

from src.player_lookup import (
    find_players,
    normalize_player_name,
    resolve_player_id,
)


@pytest.fixture
def players_db(tmp_path):
    db_path = tmp_path / "nba.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE players (
                personId INTEGER, firstName TEXT, lastName TEXT,
                fromYear REAL, toYear REAL, nbaFlag REAL,
                guard INTEGER, forward INTEGER, center INTEGER,
                draftYear REAL, draftRound REAL, draftNumber REAL
            )
            """
        )
        connection.executemany(
            "INSERT INTO players VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (201939, "Stephen", "Curry", 2009, None, 1, 1, 0, 0, 2009, 1, 7),
                (203552, "Seth", "Curry", 2013, None, 1, 1, 0, 0, -1, -1, -1),
                (209, "Dell", "Curry", 1986, 2001, 1, 1, 1, 0, 1986, 1, 15),
                (56, "Gary", "Payton", 1990, 2006, 1, 1, 0, 0, 1990, 1, 2),
                (1627780, "Gary", "Payton II", 2016, None, 1, 1, 0, 0, -1, -1, -1),
                (1628983, "Shai", "Gilgeous-Alexander", 2018, None, 1, 1, 0, 0, 2018, 1, 11),
                (203999, "Nikola", "Jokić", 2015, None, 1, 0, 0, 1, 2014, 2, 41),
            ],
        )
        connection.execute(
            """
            CREATE TABLE player_statistics (
                personId INTEGER, gameId INTEGER, gameType TEXT, numMinutes TEXT
            )
            """
        )
        connection.execute("CREATE TABLE games (gameId INTEGER, gameType TEXT)")
        rows = [(201939, game, "Regular Season", "34") for game in range(1, 6)]
        rows += [(209, game, "Regular Season", "20") for game in range(6, 9)]
        # A null gameType is resolved through the games table.
        rows += [(203552, 100, None, "10")]
        connection.executemany("INSERT INTO player_statistics VALUES (?, ?, ?, ?)", rows)
        connection.execute("INSERT INTO games VALUES (100, 'Regular Season')")
        connection.commit()
    return db_path


def test_normalize_strips_accents_punctuation_and_suffixes():
    assert normalize_player_name("Nikola Jokić") == "nikola jokic"
    assert normalize_player_name("Gary Payton II") == "gary payton"
    assert normalize_player_name("Shai Gilgeous-Alexander") == "shai gilgeous alexander"
    assert normalize_player_name("  D'Angelo  Russell Jr. ") == "dangelo russell"


def test_exact_name_resolves_to_single_player(players_db):
    result = find_players("stephen curry", db_path=players_db)

    assert result["match_type"] == "exact"
    assert result["ambiguous"] is False
    assert result["person_id"] == 201939
    assert result["candidates"][0]["regular_season_games"] == 5
    assert result["candidates"][0]["positions"] == ["G"]


def test_prefix_tokens_find_nickname_style_input(players_db):
    result = find_players("steph curry", db_path=players_db)

    assert result["match_type"] == "prefix"
    assert result["person_id"] == 201939


def test_surname_only_is_ambiguous_and_never_picks(players_db):
    result = find_players("Curry", db_path=players_db)

    assert result["ambiguous"] is True
    assert result["person_id"] is None
    assert {c["person_id"] for c in result["candidates"]} == {201939, 203552, 209}
    # Ranked by career games: Stephen (5) before Dell (3) before Seth (1).
    assert [c["person_id"] for c in result["candidates"]] == [201939, 209, 203552]


def test_season_filter_separates_namesakes(players_db):
    both = find_players("Gary Payton", db_path=players_db)
    assert both["ambiguous"] is True

    result = find_players("Gary Payton", season=1995, db_path=players_db)
    assert result["season_filter_applied"] is True
    assert result["person_id"] == 56


def test_accented_and_hyphenated_names_resolve(players_db):
    assert find_players("Nikola Jokic", db_path=players_db)["person_id"] == 203999
    assert find_players("gilgeous alexander", db_path=players_db)["person_id"] == 1628983


def test_resolve_player_id_raises_with_candidates_when_ambiguous(players_db):
    with pytest.raises(ValueError) as excinfo:
        resolve_player_id("Curry", db_path=players_db)
    assert "matches 3 players" in str(excinfo.value)
    assert "201939" in str(excinfo.value)

    with pytest.raises(ValueError) as missing:
        resolve_player_id("Nobody Atall", db_path=players_db)
    assert "No player found" in str(missing.value)

    assert resolve_player_id("Stephen Curry", db_path=players_db) == 201939
