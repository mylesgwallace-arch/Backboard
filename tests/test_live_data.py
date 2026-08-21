"""Focused regression coverage for the source-provenanced live-data ingestion.

The tests verify: structural validation fails fast on missing columns; the
leakage-safe upsert inserts new games, preserves existing results, and skips
invalid rows; provenance is recorded in ``data_ingestion_log``; the SHA-256
checksum is deterministic; and the HTTP fetch path works via a mocked
``requests.get`` (no real network).
"""

import sqlite3
import textwrap

import pandas as pd
import pytest

import src.live_data as live_data


GAMES_SCHEMA = """
CREATE TABLE games (
    gameId INTEGER,
    gameDateTimeEst TEXT,
    hometeamCity TEXT,
    hometeamName TEXT,
    hometeamId INTEGER,
    awayteamCity TEXT,
    awayteamName TEXT,
    awayteamId INTEGER,
    homeScore INTEGER,
    awayScore INTEGER,
    winner INTEGER,
    gameType TEXT,
    gameSubtype TEXT,
    gameLabel TEXT,
    gameSubLabel TEXT,
    seriesGameNumber TEXT,
    attendance REAL,
    arenaId INTEGER,
    arenaName TEXT,
    arenaCity TEXT,
    arenaState TEXT,
    officials TEXT,
    gameDate TEXT
);
"""


def make_db(tmp_path):
    db_path = tmp_path / "nba.db"
    conn = sqlite3.connect(db_path)
    conn.execute(GAMES_SCHEMA)
    # An existing, already-played game that a refresh must NOT overwrite.
    conn.execute(
        """
        INSERT INTO games (
            gameId, gameDateTimeEst, hometeamId, awayteamId,
            hometeamName, awayteamName, homeScore, awayScore, winner, gameType
        ) VALUES (42500405, '2026-06-13 20:30:00', 1610612759, 1610612752,
                  'Spurs', 'Knicks', 90, 94, 1610612752, 'Playoffs')
        """
    )
    conn.commit()
    conn.close()
    return db_path


def write_fixture(tmp_path, header, rows):
    path = tmp_path / "schedule.csv"
    content = header + "\n" + "\n".join(rows) + "\n"
    path.write_text(content)
    return path


def test_ingest_inserts_new_and_preserves_existing_results(tmp_path):
    db_path = make_db(tmp_path)
    # Header uses the 25_26 casing (homeTeamId). One row overlaps the existing
    # played game; one is a brand-new upcoming game; one is invalid (home==away).
    header = (
        "gameId,gameDateTimeEst,homeTeamId,awayTeamId,homeTeamName,"
        "awayTeamName,gameLabel,gameSubLabel"
    )
    rows = [
        "42500405,2026-06-13 20:30:00,1610612759,1610612752,Spurs,Knicks,"
        "NBA Finals,Game 5",
        "42509999,2026-10-20 19:00:00,1610612738,1610612747,Celtics,Lakers,"
        "Regular Season,",
        "42500000,2026-10-21 19:00:00,1610612738,1610612738,Celtics,Celtics,"
        "Regular Season,",
    ]
    source = write_fixture(tmp_path, header, rows)

    manifest = live_data.ingest_schedule(str(source), db_path=str(db_path))

    assert manifest["validation_status"] == "passed_with_skips"
    assert manifest["rows_loaded"] == 2
    assert manifest["rows_skipped"] == 1

    conn = sqlite3.connect(db_path)
    # Existing played game keeps its result.
    existing = conn.execute(
        "SELECT homeScore, awayScore, winner FROM games WHERE gameId=42500405"
    ).fetchone()
    assert existing == (90, 94, 1610612752)
    # New game inserted with NULL results (unplayed).
    new = conn.execute(
        "SELECT homeScore, awayScore, winner, gameType FROM games "
        "WHERE gameId=42509999"
    ).fetchone()
    assert new[0] is None and new[1] is None and new[2] is None
    assert new[3] == "Regular Season"
    # Invalid row not present.
    assert conn.execute(
        "SELECT COUNT(*) FROM games WHERE gameId=42500000"
    ).fetchone()[0] == 0
    # Provenance logged.
    log = conn.execute(
        "SELECT source_type, rows_loaded, rows_skipped, validation_status "
        "FROM data_ingestion_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert log == ("file", 2, 1, "passed_with_skips")
    conn.close()


def test_dry_run_writes_nothing(tmp_path):
    db_path = make_db(tmp_path)
    header = "gameId,gameDateTimeEst,homeTeamId,awayTeamId"
    rows = ["42509999,2026-10-20 19:00:00,1610612738,1610612747"]
    source = write_fixture(tmp_path, header, rows)

    manifest = live_data.ingest_schedule(
        str(source), db_path=str(db_path), dry_run=True
    )

    assert manifest["dry_run"] is True
    conn = sqlite3.connect(db_path)
    # No new game was written, and the provenance table was never created.
    assert conn.execute(
        "SELECT COUNT(*) FROM games WHERE gameId=42509999"
    ).fetchone()[0] == 0
    table_exists = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master "
        "WHERE type='table' AND name='data_ingestion_log'"
    ).fetchone()[0]
    assert table_exists == 0
    conn.close()


def test_missing_required_column_fails_fast(tmp_path):
    db_path = make_db(tmp_path)
    header = "gameId,gameDateTimeEst,homeTeamId"
    rows = ["42509999,2026-10-20 19:00:00,1610612738"]
    source = write_fixture(tmp_path, header, rows)

    with pytest.raises(live_data.IngestionError, match="missing required"):
        live_data.ingest_schedule(str(source), db_path=str(db_path))


def test_checksum_is_deterministic(tmp_path):
    db_path = make_db(tmp_path)
    header = "gameId,gameDateTimeEst,homeTeamId,awayTeamId"
    rows = ["42509999,2026-10-20 19:00:00,1610612738,1610612747"]
    source = write_fixture(tmp_path, header, rows)

    first = live_data.ingest_schedule(str(source), db_path=str(db_path))
    # Re-ingest the same file: same checksum, idempotent load (upsert).
    second = live_data.ingest_schedule(str(source), db_path=str(db_path))

    assert first["checksum_sha256"] == second["checksum_sha256"]
    assert second["rows_loaded"] == 1  # upsert, not duplicate insert
    conn = sqlite3.connect(db_path)
    assert conn.execute(
        "SELECT COUNT(*) FROM games WHERE gameId=42509999"
    ).fetchone()[0] == 1
    conn.close()


def test_http_source_ingests_via_mocked_requests(tmp_path, monkeypatch):
    db_path = make_db(tmp_path)
    csv_text = textwrap.dedent(
        """\
        gameId,gameDateTimeEst,homeTeamId,awayTeamId,gameLabel
        42509999,2026-10-20 19:00:00,1610612738,1610612747,Regular Season
        """
    )

    class FakeResponse:
        content = csv_text.encode("utf-8")
        status_code = 200

        def raise_for_status(self):
            return None

    monkeypatch.setattr(
        "src.live_data.requests.get", lambda url, timeout=30: FakeResponse()
    )

    manifest = live_data.ingest_schedule(
        "https://example.com/schedule.csv", db_path=str(db_path)
    )

    assert manifest["source_type"] == "http"
    assert manifest["rows_loaded"] == 1
    conn = sqlite3.connect(db_path)
    assert conn.execute(
        "SELECT COUNT(*) FROM games WHERE gameId=42509999"
    ).fetchone()[0] == 1
    conn.close()


def test_derive_game_type_covers_known_labels():
    assert live_data.derive_game_type(["Preseason", ""]) == "Preseason"
    assert live_data.derive_game_type(["NBA Finals", "Game 5"]) == "Playoffs"
    assert live_data.derive_game_type(["Play-in", ""]) == "Play-in Tournament"
    assert live_data.derive_game_type(["Emirates NBA Cup", ""]) == "NBA Cup"
    assert live_data.derive_game_type(["Regular Season", ""]) == "Regular Season"


def test_get_ingestion_log_returns_rows(tmp_path):
    db_path = make_db(tmp_path)
    header = "gameId,gameDateTimeEst,homeTeamId,awayTeamId"
    rows = ["42509999,2026-10-20 19:00:00,1610612738,1610612747"]
    source = write_fixture(tmp_path, header, rows)
    live_data.ingest_schedule(str(source), db_path=str(db_path))

    log = live_data.get_ingestion_log(db_path=str(db_path))
    assert len(log) == 1
    assert log[0]["entity"] == "games"
    assert log[0]["rows_loaded"] == 1