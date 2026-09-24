"""Read-only, guarded SQL access to ``nba.db``.

The database holds far more than the narrow factual tools expose (every box
score since 1946, advanced player/team metrics since 1996-97, schedules and
playoff labels). This module lets a caller -- a person at the CLI, the web
dashboard, or a future LLM agent -- ask arbitrary questions of it, while
making writes impossible:

1. The connection is opened with SQLite's ``mode=ro`` URI flag, so the
   engine itself refuses to write.
2. ``PRAGMA query_only = ON`` is set as a second engine-level guard.
3. An authorizer callback allows only read actions (SELECT, column reads,
   functions, recursive CTEs); anything else -- INSERT/UPDATE/DELETE, DDL,
   ATTACH, PRAGMA, transactions -- is denied at prepare time.
4. The statement text must be a single ``SELECT`` or ``WITH`` statement.

Results are capped (``max_rows``) and a progress handler aborts queries that
run longer than ``timeout_seconds``, so one bad query cannot hang the API.
"""

import argparse
import json
import re
import sqlite3
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "database" / "nba.db"

DEFAULT_MAX_ROWS = 200
HARD_MAX_ROWS = 5000
DEFAULT_TIMEOUT_SECONDS = 20.0

_ALLOWED_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
}
# SQLITE_RECURSIVE (recursive CTEs) exists on modern SQLite builds only.
if hasattr(sqlite3, "SQLITE_RECURSIVE"):
    _ALLOWED_ACTIONS.add(sqlite3.SQLITE_RECURSIVE)

_LEADING_KEYWORD = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)


class QueryRejected(ValueError):
    """Raised when a statement is not an allowed read-only query."""


def _strip_sql_comments(sql):
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", " ", sql)


def _mask_string_literals(sql):
    """Blank out quoted literals/identifiers so their contents can't trip checks."""
    return re.sub(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"", "''", sql)


def check_read_only_statement(sql):
    """Validate that ``sql`` is one SELECT/WITH statement; return it trimmed."""
    if not isinstance(sql, str) or not sql.strip():
        raise QueryRejected("SQL must be a non-empty string.")
    cleaned = _strip_sql_comments(sql).strip()
    cleaned = cleaned.rstrip().rstrip(";").rstrip()
    if not cleaned:
        raise QueryRejected("SQL contains only comments.")
    if ";" in _mask_string_literals(cleaned):
        raise QueryRejected("Only a single SQL statement is allowed.")
    if not _LEADING_KEYWORD.match(cleaned):
        raise QueryRejected("Only read-only SELECT (or WITH ... SELECT) queries are allowed.")
    return cleaned


def _authorizer(action, arg1, arg2, db_name, trigger):
    if action in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _connect_read_only(db_path):
    path = Path(db_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}")
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON")
    return connection


def run_query(sql, params=None, max_rows=DEFAULT_MAX_ROWS,
              timeout_seconds=DEFAULT_TIMEOUT_SECONDS, db_path=DB_PATH):
    """Execute one read-only query and return columns, rows and metadata."""
    statement = check_read_only_statement(sql)
    max_rows = int(max_rows)
    if max_rows < 1:
        raise QueryRejected("max_rows must be at least 1.")
    max_rows = min(max_rows, HARD_MAX_ROWS)
    if params is None:
        params = []
    if not isinstance(params, (list, tuple, dict)):
        raise QueryRejected("params must be a list (positional ?) or an object (named :name).")

    connection = _connect_read_only(db_path)
    started = time.monotonic()
    deadline = started + float(timeout_seconds)

    def _progress():
        # Returning non-zero aborts the running statement.
        return 1 if time.monotonic() > deadline else 0

    try:
        connection.set_authorizer(_authorizer)
        connection.set_progress_handler(_progress, 10_000)
        try:
            cursor = connection.execute(statement, params)
            rows = cursor.fetchmany(max_rows + 1)
        except sqlite3.DatabaseError as exc:
            message = str(exc)
            if "interrupted" in message.lower():
                raise QueryRejected(
                    f"Query exceeded the {timeout_seconds:g}s time limit and was stopped."
                )
            if "not authorized" in message.lower():
                raise QueryRejected(
                    "Query was denied: only reads of existing tables are allowed "
                    "(no writes, schema changes, PRAGMA or ATTACH)."
                )
            raise QueryRejected(f"SQL error: {message}")
        columns = [description[0] for description in cursor.description or []]
    finally:
        connection.close()

    truncated = len(rows) > max_rows
    rows = [list(row) for row in rows[:max_rows]]
    return {
        "sql": statement,
        "params": params,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "max_rows": max_rows,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "source": "nba.db (read-only connection)",
    }


def describe_database(table=None, db_path=DB_PATH):
    """List tables with row counts and columns (or one table's columns)."""
    connection = _connect_read_only(db_path)
    try:
        names = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
        ]
        if table is not None:
            if table not in names:
                raise QueryRejected(
                    f"Unknown table '{table}'. Tables: {', '.join(names)}."
                )
            names = [table]
        tables = []
        for name in names:
            columns = [
                {"name": row[1], "type": row[2] or ""}
                for row in connection.execute(f'PRAGMA table_info("{name}")')
            ]
            count = connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            tables.append({"table": name, "row_count": int(count),
                           "column_count": len(columns), "columns": columns})
    finally:
        connection.close()
    return {"tables": tables, "source": "nba.db (read-only connection)"}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Run a read-only SQL query against nba.db (writes are impossible)."
    )
    parser.add_argument("sql", nargs="?", help="A single SELECT/WITH statement.")
    parser.add_argument("--params", default=None,
                        help="JSON list/object of bound parameters.")
    parser.add_argument("--max-rows", type=int, default=DEFAULT_MAX_ROWS)
    parser.add_argument("--describe", nargs="?", const="*", default=None,
                        help="Describe all tables, or one table by name.")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.describe is not None:
        table = None if args.describe == "*" else args.describe
        print(json.dumps(describe_database(table), indent=2))
        return 0
    if not args.sql:
        raise SystemExit("Provide a SQL statement, or use --describe.")
    params = json.loads(args.params) if args.params else None
    try:
        result = run_query(args.sql, params=params, max_rows=args.max_rows)
    except QueryRejected as exc:
        raise SystemExit(f"Rejected: {exc}")
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
