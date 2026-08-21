"""Source-provenanced live-data ingestion for the NBA analytics repository.

This module is the concrete first step of the live-data milestone: a scheduled,
provenanced refresh of the repository's schedule data so the validated feature
pipeline, prediction model, simulator, and tool layer operate on current data.

Design rules:

* Provenance is first-class. Every ingestion records its source URL/type,
  fetch timestamp, row counts, a SHA-256 checksum of the source bytes, and a
  validation status in ``data_ingestion_log``.
* The ingestion is leakage-safe with respect to results: it upserts schedule
  rows by ``gameId`` and NEVER overwrites an existing ``winner``, ``homeScore``,
  ``awayScore``, ``attendance``, or ``officials`` value. A live schedule feed
  carries no results, so newly inserted games stay unplayed (NULL results) and
  already-played games keep their historical outcome.
* The ingestion is source-agnostic: it accepts an ``http(s)://`` URL (fetched
  with ``requests``) or a local file path. Validation and the upsert are
  identical for both, and the whole path is testable offline.
* Structural problems (missing required columns, unparseable source) fail fast
  with a clear error. Per-row problems (bad types, home == away, duplicate
  gameId within the source) skip only that row and are counted, so one bad row
  never aborts a scheduled refresh.
"""

import argparse
import hashlib
import sqlite3
from pathlib import Path

import pandas as pd
import requests


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "database" / "nba.db"

# Target columns written into the ``games`` table (metadata only -- results are
# never touched by an upsert). ``gameType`` and ``gameDate`` are derived.
GAMES_COLUMNS = [
    "gameId",
    "gameDateTimeEst",
    "hometeamId",
    "awayteamId",
    "hometeamName",
    "hometeamCity",
    "awayteamName",
    "awayteamCity",
    "arenaName",
    "arenaCity",
    "arenaState",
    "gameLabel",
    "gameSubLabel",
    "gameSubtype",
    "seriesGameNumber",
    "gameType",
    "gameDate",
]

# Columns whose existing values must be preserved on conflict (never overwritten
# by a schedule-only refresh).
PROTECTED_COLUMNS = ["winner", "homeScore", "awayScore", "attendance", "officials"]

REQUIRED_SOURCE_COLUMNS = ["gameid", "gamedatetimeest", "hometeamid", "awayteamid"]


class IngestionError(Exception):
    """Raised for structural ingestion failures (bad source, schema, etc.)."""


def ensure_schema(conn):
    """Idempotently add the provenance log and a unique gameId index."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS data_ingestion_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_url TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            rows_in_source INTEGER,
            rows_loaded INTEGER,
            rows_skipped INTEGER,
            validation_status TEXT NOT NULL,
            checksum_sha256 TEXT,
            notes TEXT
        )
        """
    )
    # Enforce gameId uniqueness so upserts are deterministic. The historical
    # loader did not add a constraint; this is a safe integrity improvement.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_games_gameid ON games(gameId)"
    )


def _sha256_of_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load_source(source: str):
    """Load a schedule source into a DataFrame.

    Returns ``(df, source_type, source_url, raw_bytes)``. ``source_type`` is
    ``"http"`` for ``http(s)://`` URLs and ``"file"`` otherwise.
    """
    if source.startswith("http://") or source.startswith("https://"):
        response = requests.get(source, timeout=30)
        response.raise_for_status()
        raw = response.content
        df = pd.read_csv(pd.io.common.BytesIO(raw), low_memory=False)
        return df, "http", source, raw
    path = Path(source)
    if not path.exists():
        raise IngestionError(f"Local source file not found: {source}")
    raw = path.read_bytes()
    df = pd.read_csv(path, low_memory=False)
    return df, "file", str(path.resolve()), raw


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df


def derive_game_type(labels):
    """Derive a ``gameType`` from schedule label columns (assumption: league
    schedule feeds lack an explicit type column)."""
    blob = " ".join(str(x or "") for x in labels).lower()
    if "preseason" in blob:
        return "Preseason"
    if "all-star" in blob or "all star" in blob:
        return "All-Star Game"
    if "play-in" in blob or "playin" in blob:
        return "Play-in Tournament"
    if "cup" in blob:
        return "NBA Cup"
    if "final" in blob or "playoff" in blob:
        return "Playoffs"
    return "Regular Season"


def normalize_timestamp(value):
    """Return 'YYYY-MM-DD HH:MM:SS' when parseable, else the original string."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    parsed = pd.to_datetime(str(value), errors="coerce")
    if pd.isna(parsed):
        return str(value)
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def validate_structure(df: pd.DataFrame):
    """Fail fast if required source columns are missing."""
    missing = [col for col in REQUIRED_SOURCE_COLUMNS if col not in df.columns]
    if missing:
        raise IngestionError(
            f"Source is missing required column(s): {', '.join(missing)}. "
            f"Found columns: {', '.join(df.columns)}"
        )


def build_games_row(normalized_row):
    """Map one normalized source row to the games columns (metadata only).

    Returns ``(gameId, {column: value})`` or raises ``ValueError`` for a row
    that must be skipped.
    """
    try:
        game_id = int(float(normalized_row["gameid"]))
    except (ValueError, TypeError):
        raise ValueError("gameId is not an integer")
    if game_id <= 0:
        raise ValueError("gameId must be positive")

    home_id = normalized_row.get("hometeamid")
    away_id = normalized_row.get("awayteamid")
    try:
        home_id = int(float(home_id))
        away_id = int(float(away_id))
    except (ValueError, TypeError):
        raise ValueError("team ids are not integers")
    if home_id == away_id:
        raise ValueError("home and away team are the same")

    timestamp = normalize_timestamp(normalized_row.get("gamedatetimeest"))
    if timestamp is None:
        raise ValueError("gameDateTimeEst is missing or unparseable")

    def pick(*names):
        for name in names:
            value = normalized_row.get(name)
            if value is not None and not (isinstance(value, float) and pd.isna(value)):
                return None if (isinstance(value, str) and value == "") else value
        return None

    values = {
        "gameId": game_id,
        "gameDateTimeEst": timestamp,
        "hometeamId": home_id,
        "awayteamId": away_id,
        "hometeamName": pick("hometeamname"),
        "hometeamCity": pick("hometeamcity"),
        "awayteamName": pick("awayteamname"),
        "awayteamCity": pick("awayteamcity"),
        "arenaName": pick("arenename"),
        "arenaCity": pick("arenacity"),
        "arenaState": pick("arenastate"),
        "gameLabel": pick("gamelabel"),
        "gameSubLabel": pick("gamesublabel"),
        "gameSubtype": pick("gamesubtype"),
        "seriesGameNumber": pick("seriesgamennumber"),
        "gameType": derive_game_type(
            [normalized_row.get("gamelabel"), normalized_row.get("gamesublabel")]
        ),
        "gameDate": timestamp,
    }
    return game_id, values


def ingest_schedule(source: str, db_path=DB_PATH, dry_run: bool = False):
    """Ingest a schedule source into the ``games`` table with provenance.

    Returns a manifest dict describing what was (or would be) done. On
    ``dry_run``, no database writes occur.
    """
    df, source_type, source_url, raw = load_source(source)
    normalized = _normalize_columns(df)
    validate_structure(normalized)

    checksum = _sha256_of_bytes(raw)
    rows = []
    skipped = 0
    seen_ids = set()
    for _, raw_row in normalized.iterrows():
        row = {col: raw_row.get(col) for col in normalized.columns}
        try:
            game_id, values = build_games_row(row)
        except ValueError:
            skipped += 1
            continue
        if game_id in seen_ids:
            skipped += 1
            continue
        seen_ids.add(game_id)
        rows.append(values)

    validation_status = "passed" if skipped == 0 else "passed_with_skips"

    if dry_run:
        return {
            "source": source_url,
            "source_type": source_type,
            "dry_run": True,
            "rows_in_source": len(normalized),
            "rows_loaded": len(rows),
            "rows_skipped": skipped,
            "validation_status": validation_status,
            "checksum_sha256": checksum,
            "notes": "Dry run: no changes written to the database.",
        }

    conn = sqlite3.connect(db_path)
    try:
        ensure_schema(conn)
        insert_cols = GAMES_COLUMNS
        placeholders = ", ".join("?" for _ in insert_cols)
        col_list = ", ".join(insert_cols)
        update_cols = [c for c in insert_cols if c != "gameId"]
        update_clause = ", ".join(f"{c}=excluded.{c}" for c in update_cols)
        sql = (
            f"INSERT INTO games ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT(gameId) DO UPDATE SET {update_clause}"
        )
        conn.executemany(
            sql, [[values[c] for c in insert_cols] for values in rows]
        )
        conn.execute(
            """
            INSERT INTO data_ingestion_log (
                entity, source_type, source_url, fetched_at, rows_in_source,
                rows_loaded, rows_skipped, validation_status, checksum_sha256,
                notes
            ) VALUES ('games', ?, ?, datetime('now'), ?, ?, ?, ?, ?, ?)
            """,
            (
                source_type,
                source_url,
                len(normalized),
                len(rows),
                skipped,
                validation_status,
                checksum,
                "Leakage-safe upsert: existing results preserved.",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "source": source_url,
        "source_type": source_type,
        "dry_run": False,
        "rows_in_source": len(normalized),
        "rows_loaded": len(rows),
        "rows_skipped": skipped,
        "validation_status": validation_status,
        "checksum_sha256": checksum,
        "notes": "Leakage-safe upsert complete; existing results preserved.",
    }


def get_ingestion_log(db_path=DB_PATH, limit: int = 20):
    """Return recent ingestion log rows as a list of dicts."""
    conn = sqlite3.connect(db_path)
    try:
        table_exists = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='table' AND name='data_ingestion_log'"
        ).fetchone()[0]
        if not table_exists:
            return []
        rows = conn.execute(
            """
            SELECT id, entity, source_type, source_url, fetched_at,
                   rows_in_source, rows_loaded, rows_skipped,
                   validation_status, checksum_sha256, notes
            FROM data_ingestion_log
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        columns = [
            "id", "entity", "source_type", "source_url", "fetched_at",
            "rows_in_source", "rows_loaded", "rows_skipped",
            "validation_status", "checksum_sha256", "notes",
        ]
        return [dict(zip(columns, row)) for row in rows]
    finally:
        conn.close()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Source-provenanced schedule ingestion for nba.db."
    )
    parser.add_argument(
        "--source",
        type=str,
        help="Schedule source: an http(s):// URL or a local CSV path.",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=str(DB_PATH),
        help="Path to nba.db (default: the repository database).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and plan the ingestion without writing to the database.",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="Print the data_ingestion_log and exit.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.history:
        for row in get_ingestion_log(args.db):
            print(row)
        return 0
    if not args.source:
        raise SystemExit("Provide --source, or use --history.")
    manifest = ingest_schedule(args.source, db_path=args.db, dry_run=args.dry_run)
    print(f"Ingestion manifest: {manifest['validation_status']}")
    for key in (
        "source", "source_type", "rows_in_source", "rows_loaded",
        "rows_skipped", "checksum_sha256", "notes",
    ):
        print(f"  {key}: {manifest[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())