"""The read-only SQL tool must answer SELECTs and make writes impossible."""

import sqlite3

import pytest

from src.db_query import (
    QueryRejected,
    check_read_only_statement,
    describe_database,
    run_query,
)


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "nba.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE games (gameId INTEGER, winner INTEGER, label TEXT)")
        connection.executemany(
            "INSERT INTO games VALUES (?, ?, ?)",
            [(1, 10, "a;b"), (2, 20, "O'Neal"), (3, 10, "c")],
        )
        connection.commit()
    return db_path


def _games(db_path):
    with sqlite3.connect(db_path) as connection:
        return connection.execute("SELECT * FROM games ORDER BY gameId").fetchall()


def test_select_returns_columns_and_rows(db):
    result = run_query(
        "SELECT winner, COUNT(*) AS n FROM games GROUP BY winner ORDER BY winner",
        db_path=db,
    )

    assert result["columns"] == ["winner", "n"]
    assert result["rows"] == [[10, 2], [20, 1]]
    assert result["truncated"] is False


def test_with_cte_and_bound_parameters_are_allowed(db):
    result = run_query(
        "WITH w AS (SELECT * FROM games WHERE winner = ?) SELECT COUNT(*) FROM w",
        params=[10],
        db_path=db,
    )
    assert result["rows"] == [[2]]


def test_semicolons_inside_string_literals_are_not_statement_breaks(db):
    result = run_query("SELECT label FROM games WHERE label = 'a;b'", db_path=db)
    assert result["rows"] == [["a;b"]]


def test_row_cap_truncates_and_reports_it(db):
    result = run_query("SELECT gameId FROM games ORDER BY gameId", max_rows=2, db_path=db)
    assert result["rows"] == [[1], [2]]
    assert result["truncated"] is True


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM games",
        "UPDATE games SET winner = 0",
        "INSERT INTO games VALUES (9, 9, 'x')",
        "DROP TABLE games",
        "CREATE TABLE t (x INTEGER)",
        "SELECT 1; DELETE FROM games",
        "PRAGMA writable_schema = ON",
        "ATTACH DATABASE 'other.db' AS other",
        "VACUUM",
        "-- comment only",
        "",
    ],
)
def test_write_and_multi_statement_sql_is_rejected(db, sql):
    before = _games(db)
    with pytest.raises(QueryRejected):
        run_query(sql, db_path=db)
    assert _games(db) == before


def test_writes_hidden_inside_a_with_clause_are_denied_by_the_engine(db):
    before = _games(db)
    with pytest.raises(QueryRejected):
        run_query(
            "WITH x AS (SELECT 1) INSERT INTO games(gameId) SELECT * FROM x",
            db_path=db,
        )
    assert _games(db) == before


def test_pragma_functions_are_denied(db):
    with pytest.raises(QueryRejected):
        run_query("SELECT * FROM pragma_table_info('games')", db_path=db)


def test_check_strips_trailing_semicolon_and_comments():
    assert check_read_only_statement("SELECT 1; -- trailing") == "SELECT 1"
    assert check_read_only_statement("/* hi */ select 2 ;") == "select 2"


def test_describe_database_lists_tables_and_columns(db):
    result = describe_database(db_path=db)
    assert result["tables"][0]["table"] == "games"
    assert result["tables"][0]["row_count"] == 3
    assert [column["name"] for column in result["tables"][0]["columns"]] == [
        "gameId", "winner", "label",
    ]
    with pytest.raises(QueryRejected):
        describe_database("missing", db_path=db)
