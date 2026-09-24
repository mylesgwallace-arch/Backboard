"""Player name -> personId resolution over the repository's ``players`` table.

The analytics tools that take a player (``player_impact``, ``player_scenario``)
key on the NBA ``personId``. This module turns a human-typed name into that id
without guessing:

* An exact normalized full-name match wins. Otherwise every token the user
  typed must be a prefix of a token of the player's name ("steph curry" finds
  Stephen Curry, "curry" finds every Curry).
* When more than one player matches, the result is flagged ``ambiguous`` and
  every candidate is returned with the facts needed to choose (career span,
  games played). Nothing is silently picked for the caller, unlike
  ``roster_change_data.resolve_person_id``, which keeps its first-match
  behavior for its own batch-normalization job.
* An optional ``season`` narrows candidates to players active that season
  (``fromYear <= season <= toYear``), which separates, e.g., the two
  Gary Paytons.
"""

import argparse
import json
import re
import sqlite3
import unicodedata
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "database" / "nba.db"

NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
DEFAULT_LIMIT = 10


def normalize_player_name(value):
    """Lowercase, strip accents/punctuation and generational suffixes."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("'", "").replace(".", "")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    tokens = [token for token in text.split() if token]
    while len(tokens) > 1 and tokens[-1] in NAME_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def _load_players(connection):
    rows = connection.execute(
        """
        SELECT personId, firstName, lastName, fromYear, toYear, nbaFlag,
               guard, forward, center, draftYear, draftRound, draftNumber
        FROM players
        """
    ).fetchall()
    players = []
    for row in rows:
        (person_id, first, last, from_year, to_year, nba_flag, guard, forward,
         center, draft_year, draft_round, draft_number) = row
        full_name = " ".join(part for part in (first or "", last or "") if part).strip()
        players.append(
            {
                "person_id": int(person_id),
                "full_name": full_name,
                "normalized": normalize_player_name(full_name),
                "from_year": None if from_year is None else int(from_year),
                "to_year": None if to_year is None else int(to_year),
                "nba_flag": bool(nba_flag) if nba_flag is not None else False,
                "positions": [
                    label
                    for label, flag in (("G", guard), ("F", forward), ("C", center))
                    if flag
                ],
                "draft_year": (
                    int(draft_year) if draft_year not in (None, -1, -1.0) else None
                ),
                "draft_round": (
                    int(draft_round) if draft_round not in (None, -1, -1.0) else None
                ),
                "draft_number": (
                    int(draft_number) if draft_number not in (None, -1, -1.0) else None
                ),
            }
        )
    return players


def _games_played(connection, person_ids):
    """Regular-season appearances (minutes > 0) per personId."""
    if not person_ids:
        return {}
    placeholders = ", ".join("?" for _ in person_ids)
    rows = connection.execute(
        f"""
        SELECT player_statistics.personId, COUNT(*)
        FROM player_statistics
        LEFT JOIN games ON games.gameId = player_statistics.gameId
        WHERE player_statistics.personId IN ({placeholders})
          AND COALESCE(player_statistics.gameType, games.gameType) = 'Regular Season'
          AND CAST(player_statistics.numMinutes AS REAL) > 0
        GROUP BY player_statistics.personId
        """,
        list(person_ids),
    ).fetchall()
    return {int(person_id): int(count) for person_id, count in rows}


def _active_in(player, season):
    from_year = player["from_year"]
    to_year = player["to_year"] if player["to_year"] is not None else 9999
    if from_year is None:
        return False
    return from_year <= season <= to_year


def find_players(name, season=None, db_path=DB_PATH, limit=DEFAULT_LIMIT):
    """Return ranked player candidates for a typed name.

    Result: ``{"query", "normalized_query", "season", "match_type",
    "ambiguous", "person_id", "candidates"}``. ``person_id`` is set only when
    exactly one candidate remains; otherwise it is ``None`` and the caller
    must choose from ``candidates``.
    """
    query = normalize_player_name(name)
    if not query:
        raise ValueError("Player name cannot be empty.")
    query_tokens = query.split()

    with sqlite3.connect(db_path) as connection:
        players = _load_players(connection)
        exact = [player for player in players if player["normalized"] == query]
        match_type = "exact"
        matches = exact
        if not matches:
            match_type = "prefix"

            def token_prefix_match(player):
                name_tokens = player["normalized"].split()
                return all(
                    any(token.startswith(query_token) for token in name_tokens)
                    for query_token in query_tokens
                )

            matches = [player for player in players if token_prefix_match(player)]

        season_filtered = False
        if season is not None and matches:
            active = [player for player in matches if _active_in(player, int(season))]
            if active:
                matches = active
                season_filtered = True

        games = _games_played(connection, [player["person_id"] for player in matches])

    for player in matches:
        player["regular_season_games"] = games.get(player["person_id"], 0)
        player.pop("normalized", None)

    # NBA players first, then by career games (the player people usually
    # mean), then most recent career, then personId for a stable order.
    matches.sort(
        key=lambda player: (
            not player["nba_flag"],
            -player["regular_season_games"],
            -(player["to_year"] or 9999),
            player["person_id"],
        )
    )
    total = len(matches)
    candidates = matches[: max(int(limit), 1)]
    unique = total == 1
    return {
        "query": name,
        "normalized_query": query,
        "season": None if season is None else int(season),
        "season_filter_applied": season_filtered,
        "match_type": match_type if total else "none",
        "match_count": total,
        "ambiguous": total > 1,
        "person_id": candidates[0]["person_id"] if unique else None,
        "candidates": candidates,
    }


def resolve_player_id(name, season=None, db_path=DB_PATH):
    """Return a single personId for ``name`` or raise ``ValueError``.

    Raises when nothing matches or when the name is ambiguous, listing the
    candidates so the caller can retry with a fuller name or a season.
    """
    result = find_players(name, season=season, db_path=db_path, limit=5)
    if result["match_count"] == 0:
        raise ValueError(f"No player found matching '{name}'.")
    if result["ambiguous"]:
        described = "; ".join(
            f"{candidate['full_name']} (personId {candidate['person_id']}, "
            f"{candidate['from_year']}-{candidate['to_year'] or 'present'})"
            for candidate in result["candidates"]
        )
        shown = (
            f" (top {len(result['candidates'])} shown)"
            if result["match_count"] > len(result["candidates"])
            else ""
        )
        raise ValueError(
            f"Player name '{name}' matches {result['match_count']} players{shown}: "
            f"{described}. Use a fuller name, a season, or a personId."
        )
    return result["person_id"]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Resolve a player name to NBA personId candidates."
    )
    parser.add_argument("name", help="Player name, e.g. 'Stephen Curry' or 'curry'.")
    parser.add_argument("--season", type=int, help="Restrict to players active that season.")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    print(json.dumps(find_players(args.name, season=args.season, limit=args.limit), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
