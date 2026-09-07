"""Deterministic analytical tool layer and orchestration routing.

This module is the stable programmatic surface underneath the future
natural-language AI interface. It exposes the project's *validated* analytical
capabilities as named, parameterized, deterministic tools and routes structured
tool calls to them.

Design rules:

* Every tool wraps an existing validated implementation. Prediction routes to
  the frozen production ``predict_matchup`` (``elo_boosted_ensemble``);
  simulation routes to ``load_pregame_probabilities`` + ``project_season``;
  player diagnostics route to the existing association-only estimates. No new
  model is trained and no prediction/simulation logic is duplicated here.
* The orchestration layer never bypasses the validated production-model path
  and never fabricates answers: it only dispatches to deterministic tools and
  returns their structured output.
* Every result carries the tool name, a description of the operation performed,
  the model/data that produced it, assumptions, limitations, and the actual
  structured result.
* Player-impact diagnostics are preserved strictly as association-only
  estimates. When a diagnostic cannot be produced (no prior history) or is
  low-confidence, the result is marked ``unavailable``/``low_confidence``
  rather than converted into a causal claim.
"""

import argparse
import json
import sqlite3
from pathlib import Path

import pandas as pd

try:
    from src.main import (
        FEATURES_PATH,
        METRICS_PATH,
        MODEL_PATH,
        NBA_TEAM_ID_MAX,
        NBA_TEAM_ID_MIN,
        TEAM_DB_PATH,
        predict_matchup,
        resolve_team_name_to_id,
    )
    from src.simulate_season import load_pregame_probabilities, project_season
    from src.player_impact import summarize_player_impact
    from src.player_scenario import analyze_single_player_scenario
except ImportError:  # pragma: no cover - direct-script support
    from main import (
        FEATURES_PATH,
        METRICS_PATH,
        MODEL_PATH,
        NBA_TEAM_ID_MAX,
        NBA_TEAM_ID_MIN,
        TEAM_DB_PATH,
        predict_matchup,
        resolve_team_name_to_id,
    )
    from simulate_season import load_pregame_probabilities, project_season
    from player_impact import summarize_player_impact
    from player_scenario import analyze_single_player_scenario


ROOT = Path(__file__).resolve().parents[1]

PRODUCTION_MODEL = "elo_boosted_ensemble"

# Cache of pregame probabilities so repeated simulation tool calls within one
# process do not re-run the (expensive) chronological Elo replay every time.
PROBABILITIES_CACHE = {}


class ToolUnavailable(Exception):
    """Raised when a tool cannot produce a result with available data."""


class ToolError(Exception):
    """Raised for invalid tool requests (unknown tool or bad parameters)."""


def _cached_probabilities(
    features_path=FEATURES_PATH,
    model_path=MODEL_PATH,
    metrics_path=METRICS_PATH,
):
    key = (str(features_path), str(model_path), str(metrics_path))
    if key not in PROBABILITIES_CACHE:
        PROBABILITIES_CACHE[key] = load_pregame_probabilities(
            features_path=features_path,
            model_path=model_path,
            metrics_path=metrics_path,
        )
    return PROBABILITIES_CACHE[key]


def clear_probability_cache():
    """Clear the in-process pregame probability cache (mainly for tests)."""
    PROBABILITIES_CACHE.clear()


# ---------------------------------------------------------------------------
# Parameter schema
# ---------------------------------------------------------------------------

INT_TYPE = "int"
STR_TYPE = "str"

PLAYOFF_LIMITATION = (
    "Season projections are descriptive outputs of the validated per-game model; "
    "they are not causal claims and do not account for roster changes or "
    "coaching decisions not present in pregame features."
)


def _team_id_or_name(parameters, id_key, name_key):
    """Resolve a team reference to a numeric teamId from id or current name."""
    team_id = parameters.get(id_key)
    if team_id is None:
        team_name = parameters.get(name_key)
        if not team_name:
            raise ValueError(f"Provide either '{id_key}' or '{name_key}'.")
        team_id = resolve_team_name_to_id(team_name)
    return int(team_id)


def _resolve_home_away(parameters):
    home_team_id = _team_id_or_name(parameters, "home_team_id", "home_team")
    away_team_id = _team_id_or_name(parameters, "away_team_id", "away_team")
    if home_team_id == away_team_id:
        raise ValueError("Home and away teams must be different.")
    return home_team_id, away_team_id


# ---------------------------------------------------------------------------
# Tool executors (all deterministic wrappers over existing validated code)
# ---------------------------------------------------------------------------

def _execute_predict_matchup(parameters):
    """Route a single-game prediction to the frozen production model."""
    home_team_id, away_team_id = _resolve_home_away(parameters)
    result = predict_matchup(
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        game_date=parameters.get("game_date"),
    )
    return {
        "home_team_id": home_team_id,
        "away_team_id": away_team_id,
        "prediction": result,
    }


def _run_projection(parameters):
    probabilities = _cached_probabilities()
    return project_season(
        probabilities,
        parameters["season"],
        n_simulations=parameters.get("n_simulations", 1000),
        random_state=parameters.get("random_state", 42),
    )


def _execute_simulate_season(parameters):
    """Project a full season using the validated leakage-safe Monte Carlo engine."""
    projection = _run_projection(parameters)
    return {
        "model": PRODUCTION_MODEL,
        "mode": "season_simulation",
        "leakage_safe": True,
        "projection": projection,
    }


def _execute_team_projection(parameters):
    """Return a single team's projected season row (wins + seed odds)."""
    team_id = _team_id_or_name(parameters, "team_id", "team")
    projection = _run_projection(parameters)
    rows = projection["projected_standings"]
    match = next((row for row in rows if row["teamId"] == team_id), None)
    if match is None:
        raise ToolUnavailable(
            f"Team {team_id} has no projected row in season {parameters['season']}."
        )
    return {
        "team_id": team_id,
        "season": projection["season"],
        "n_simulations": projection["n_simulations"],
        "projection": match,
    }


def _execute_player_impact(parameters):
    """Return the association-only player-impact diagnostic for one player."""
    try:
        diagnostic = summarize_player_impact(
            parameters["person_id"],
            before=parameters.get("before"),
            window=parameters.get("window", 10),
        )
    except ValueError as exc:
        raise ToolUnavailable(str(exc))
    prior_games = int(diagnostic.get("prior_games", 0))
    return {
        "diagnostic": diagnostic,
        "confidence": (
            "low" if prior_games < 5 else "moderate"
        ),
        "association_only": True,
    }


def _execute_player_scenario(parameters):
    """Describe a player's estimated impact in a matchup without altering the model."""
    home_team_id, away_team_id = _resolve_home_away(parameters)
    try:
        result = analyze_single_player_scenario(
            home_team_id,
            away_team_id,
            parameters["person_id"],
            game_date=parameters.get("game_date"),
            window=parameters.get("window", 10),
        )
    except ValueError as exc:
        raise ToolUnavailable(str(exc))
    return {
        "home_team_id": home_team_id,
        "away_team_id": away_team_id,
        "scenario": result,
        "association_only": True,
    }


def _execute_team_record(parameters):
    """Query actual regular-season win/loss record from the database."""
    team_id = _team_id_or_name(parameters, "team_id", "team")
    season = parameters.get("season")
    with sqlite3.connect(TEAM_DB_PATH) as connection:
        rows = connection.execute(
            """
            SELECT gameDateTimeEst, hometeamId, awayteamId, winner
            FROM games
            WHERE gameType = 'Regular Season'
              AND (hometeamId = ? OR awayteamId = ?)
            """,
            (team_id, team_id),
        ).fetchall()
    wins = 0
    losses = 0
    games = 0
    for game_date, home_id, away_id, winner in rows:
        timestamp = pd.Timestamp(game_date)
        game_season = timestamp.year - (timestamp.month < 10)
        if season is not None and game_season != season:
            continue
        games += 1
        if winner == team_id:
            wins += 1
        else:
            losses += 1
    return {
        "team_id": team_id,
        "season": season,
        "games": games,
        "wins": wins,
        "losses": losses,
        "source": "nba.db games (Regular Season)",
    }


def _execute_head_to_head(parameters):
    """Query actual regular-season head-to-head record between two teams."""
    team_a = _team_id_or_name(parameters, "team_a_id", "team_a")
    team_b = _team_id_or_name(parameters, "team_b_id", "team_b")
    season = parameters.get("season")
    with sqlite3.connect(TEAM_DB_PATH) as connection:
        rows = connection.execute(
            """
            SELECT gameDateTimeEst, hometeamId, awayteamId, winner
            FROM games
            WHERE gameType = 'Regular Season'
              AND ((hometeamId = ? AND awayteamId = ?)
                OR (hometeamId = ? AND awayteamId = ?))
            """,
            (team_a, team_b, team_b, team_a),
        ).fetchall()
    team_a_wins = 0
    team_b_wins = 0
    games = 0
    for game_date, home_id, away_id, winner in rows:
        timestamp = pd.Timestamp(game_date)
        game_season = timestamp.year - (timestamp.month < 10)
        if season is not None and game_season != season:
            continue
        games += 1
        if winner == team_a:
            team_a_wins += 1
        elif winner == team_b:
            team_b_wins += 1
    return {
        "team_a_id": team_a,
        "team_b_id": team_b,
        "season": season,
        "games": games,
        "team_a_wins": team_a_wins,
        "team_b_wins": team_b_wins,
        "source": "nba.db games (Regular Season)",
    }


def _execute_resolve_team_name(parameters):
    """Resolve a current franchise name or city to a numeric teamId."""
    team_id = resolve_team_name_to_id(parameters["team"])
    return {"team": parameters["team"], "team_id": int(team_id)}


def _execute_list_teams(parameters):
    """List the 30 current NBA franchises for UI selection (teamId + names)."""
    with sqlite3.connect(TEAM_DB_PATH) as connection:
        rows = connection.execute(
            """
            SELECT teamId, teamCity, teamName, teamAbbrev
            FROM team_histories
            WHERE seasonActiveTill >= 2100
              AND teamId BETWEEN ? AND ?
            ORDER BY teamCity, teamName
            """,
            (NBA_TEAM_ID_MIN, NBA_TEAM_ID_MAX),
        ).fetchall()
    teams = [
        {
            "team_id": int(team_id),
            "city": city,
            "name": name,
            "full_name": f"{city} {name}",
            "abbreviation": (abbrev or "").strip(),
        }
        for team_id, city, name, abbrev in rows
    ]
    return {"teams": teams, "count": len(teams)}


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

TOOLS = {
    "predict_matchup": {
        "name": "predict_matchup",
        "description": (
            "Predict the winner of a single NBA game using the frozen validated "
            "production model (elo_boosted_ensemble)."
        ),
        "category": "prediction",
        "model": "elo_boosted_ensemble",
        "parameters": [
            {"name": "home_team_id", "type": INT_TYPE, "required": False,
             "description": "Numeric teamId of the home team."},
            {"name": "home_team", "type": STR_TYPE, "required": False,
             "description": "Current franchise name or city of the home team."},
            {"name": "away_team_id", "type": INT_TYPE, "required": False,
             "description": "Numeric teamId of the away team."},
            {"name": "away_team", "type": STR_TYPE, "required": False,
             "description": "Current franchise name or city of the away team."},
            {"name": "game_date", "type": STR_TYPE, "required": False,
             "description": "Optional cutoff date (YYYY-MM-DD); only pregame "
                            "features on or before this date are used."},
        ],
        "assumptions": [
            "Both teams are current NBA franchises.",
            "The prediction uses only pregame information available before the "
            "game date (chronological Elo replay + rolling team features).",
            "The served model is the frozen validated production model recorded "
            "in models/baseline_metrics.json.",
        ],
        "limitations": [
            "A single-game probability is not a guarantee of the outcome.",
            "The model does not incorporate player availability or roster "
            "changes beyond the pregame rolling features it was validated on.",
        ],
        "execute": _execute_predict_matchup,
    },
    "simulate_season": {
        "name": "simulate_season",
        "description": (
            "Project a full NBA season with a leakage-safe Monte Carlo "
            "simulation built on the validated production model, including "
            "projected standings, conference seedings, the direct-playoff "
            "field, and league summary."
        ),
        "category": "simulation",
        "model": "elo_boosted_ensemble (Monte Carlo season engine)",
        "parameters": [
            {"name": "season", "type": INT_TYPE, "required": True,
             "description": "NBA season start year, e.g. 2025."},
            {"name": "n_simulations", "type": INT_TYPE, "required": False,
             "description": "Number of Monte Carlo simulations (default 1000)."},
            {"name": "random_state", "type": INT_TYPE, "required": False,
             "description": "Random seed for reproducible runs (default 42)."},
        ],
        "assumptions": [
            "Every game probability is leakage-safe: formed only from "
            "information available before that game.",
            "The schedule is the repository's full historical schedule, and "
            "the projection applies to that schedule's structure.",
        ],
        "limitations": [PLAYOFF_LIMITATION],
        "execute": _execute_simulate_season,
    },
    "team_projection": {
        "name": "team_projection",
        "description": (
            "Project a single team's season: expected wins, projected "
            "conference seed, exact-seed probabilities, and direct-playoff "
            "probability, from the validated Monte Carlo season engine."
        ),
        "category": "simulation",
        "model": "elo_boosted_ensemble (Monte Carlo season engine)",
        "parameters": [
            {"name": "team_id", "type": INT_TYPE, "required": False,
             "description": "Numeric teamId of the team."},
            {"name": "team", "type": STR_TYPE, "required": False,
             "description": "Current franchise name or city of the team."},
            {"name": "season", "type": INT_TYPE, "required": True,
             "description": "NBA season start year, e.g. 2025."},
            {"name": "n_simulations", "type": INT_TYPE, "required": False,
             "description": "Number of Monte Carlo simulations (default 1000)."},
            {"name": "random_state", "type": INT_TYPE, "required": False,
             "description": "Random seed for reproducible runs (default 42)."},
        ],
        "assumptions": [
            "The team is one of the 30 current NBA franchises.",
            "All game probabilities are leakage-safe pregame probabilities.",
        ],
        "limitations": [PLAYOFF_LIMITATION],
        "execute": _execute_team_projection,
    },
    "player_impact": {
        "name": "player_impact",
        "description": (
            "Return the association-only player-impact diagnostic for a single "
            "player from their prior regular-season appearances."
        ),
        "category": "player_diagnostic",
        "model": "player-impact association diagnostic (descriptive)",
        "parameters": [
            {"name": "person_id", "type": INT_TYPE, "required": True,
             "description": "NBA player personId."},
            {"name": "before", "type": STR_TYPE, "required": False,
             "description": "Optional cutoff date (YYYY-MM-DD); only prior "
                            "appearances are used."},
            {"name": "window", "type": INT_TYPE, "required": False,
             "description": "Recent-game window for the estimate (default 10)."},
        ],
        "assumptions": [
            "The diagnostic uses only appearances strictly before the cutoff.",
            "It is a minutes-weighted net-rating proxy, not a replacement-level "
            "estimate.",
        ],
        "limitations": [
            "This is a descriptive association estimate, NOT a causal forecast "
            "of roster or trade impact.",
            "It is not a feature of the validated production prediction model.",
            "Results with fewer than 5 prior games are low-confidence.",
        ],
        "execute": _execute_player_impact,
    },
    "player_scenario": {
        "name": "player_scenario",
        "description": (
            "Describe a single player's estimated impact in a matchup: the "
            "production model probability plus the association-only player "
            "impact diagnostic, with the model probability unchanged."
        ),
        "category": "player_diagnostic",
        "model": "elo_boosted_ensemble + association-only player diagnostic",
        "parameters": [
            {"name": "home_team_id", "type": INT_TYPE, "required": False,
             "description": "Numeric teamId of the home team."},
            {"name": "home_team", "type": STR_TYPE, "required": False,
             "description": "Current franchise name or city of the home team."},
            {"name": "away_team_id", "type": INT_TYPE, "required": False,
             "description": "Numeric teamId of the away team."},
            {"name": "away_team", "type": STR_TYPE, "required": False,
             "description": "Current franchise name or city of the away team."},
            {"name": "person_id", "type": INT_TYPE, "required": True,
             "description": "NBA player personId to evaluate."},
            {"name": "game_date", "type": STR_TYPE, "required": False,
             "description": "Optional cutoff date (YYYY-MM-DD)."},
            {"name": "window", "type": INT_TYPE, "required": False,
             "description": "Recent-game window for the player estimate "
                            "(default 10)."},
        ],
        "assumptions": [
            "The production model probability is never modified by the player "
            "diagnostic.",
            "Only pregame information is used.",
        ],
        "limitations": [
            "The player-impact estimate is association-only and is NOT "
            "translated into the model's feature matrix.",
            "It is not a causal forecast of a roster change.",
        ],
        "execute": _execute_player_scenario,
    },
    "team_record": {
        "name": "team_record",
        "description": (
            "Query the actual regular-season win/loss record for a team from "
            "the repository database."
        ),
        "category": "database_query",
        "model": "nba.db games table (factual query)",
        "parameters": [
            {"name": "team_id", "type": INT_TYPE, "required": False,
             "description": "Numeric teamId of the team."},
            {"name": "team", "type": STR_TYPE, "required": False,
             "description": "Current franchise name or city of the team."},
            {"name": "season", "type": INT_TYPE, "required": False,
             "description": "Optional NBA season start year to restrict the "
                            "record to that season."},
        ],
        "assumptions": [
            "Only games with gameType 'Regular Season' are counted.",
            "Season labeling follows the feature-pipeline rule (start year).",
        ],
        "limitations": [
            "This is a factual database query, not a model prediction.",
            "The database may not yet contain a complete record for the most "
            "recent in-progress season.",
        ],
        "execute": _execute_team_record,
    },
    "head_to_head": {
        "name": "head_to_head",
        "description": (
            "Query the actual regular-season head-to-head record between two "
            "teams from the repository database."
        ),
        "category": "database_query",
        "model": "nba.db games table (factual query)",
        "parameters": [
            {"name": "team_a_id", "type": INT_TYPE, "required": False,
             "description": "Numeric teamId of the first team."},
            {"name": "team_a", "type": STR_TYPE, "required": False,
             "description": "Current franchise name or city of the first team."},
            {"name": "team_b_id", "type": INT_TYPE, "required": False,
             "description": "Numeric teamId of the second team."},
            {"name": "team_b", "type": STR_TYPE, "required": False,
             "description": "Current franchise name or city of the second team."},
            {"name": "season", "type": INT_TYPE, "required": False,
             "description": "Optional NBA season start year to restrict the "
                            "head-to-head record."},
        ],
        "assumptions": [
            "Only games with gameType 'Regular Season' are counted.",
            "Season labeling follows the feature-pipeline rule (start year).",
        ],
        "limitations": [
            "This is a factual database query, not a model prediction.",
        ],
        "execute": _execute_head_to_head,
    },
    "resolve_team_name": {
        "name": "resolve_team_name",
        "description": (
            "Resolve a current NBA franchise name or city to a numeric teamId."
        ),
        "category": "utility",
        "model": "nba.db team_histories table (factual lookup)",
        "parameters": [
            {"name": "team", "type": STR_TYPE, "required": True,
             "description": "Current franchise name or city, e.g. "
                            "'Boston Celtics' or 'Boston'."},
        ],
        "assumptions": [
            "The name refers to one of the 30 current NBA franchises.",
        ],
        "limitations": [
            "Historical franchise names are not resolved to current teamIds.",
        ],
        "execute": _execute_resolve_team_name,
    },
    "list_teams": {
        "name": "list_teams",
        "description": (
            "List the 30 current NBA franchises with teamId, city, name, and "
            "abbreviation (for populating team selection UIs)."
        ),
        "category": "utility",
        "model": "nba.db team_histories table (factual lookup)",
        "parameters": [],
        "assumptions": [
            "Only the 30 currently active NBA franchises are included.",
        ],
        "limitations": [
            "Historical or relocated franchise names are not included.",
        ],
        "execute": _execute_list_teams,
    },
}


# ---------------------------------------------------------------------------
# Routing / orchestration
# ---------------------------------------------------------------------------

def validate_parameters(schema, parameters):
    """Validate and clean a parameters dict against a tool's schema."""
    parameters = parameters or {}
    unknown = set(parameters) - {spec["name"] for spec in schema}
    if unknown:
        raise ToolError(
            f"Unknown parameter(s) for this tool: {sorted(unknown)}."
        )
    cleaned = {}
    for spec in schema:
        name = spec["name"]
        if name not in parameters or parameters[name] is None:
            if spec.get("required"):
                raise ToolError(
                    f"Missing required parameter '{name}' for this tool."
                )
            continue
        value = parameters[name]
        if spec["type"] == INT_TYPE:
            # Accept ints/floats and numeric strings (a future NL layer may
            # pass parameter values as strings).
            if isinstance(value, bool):
                raise ToolError(f"Parameter '{name}' must be an integer.")
            if isinstance(value, str):
                try:
                    cleaned[name] = int(value)
                    continue
                except (TypeError, ValueError):
                    raise ToolError(f"Parameter '{name}' must be an integer.")
            if isinstance(value, (int, float)):
                cleaned[name] = int(value)
                continue
            raise ToolError(f"Parameter '{name}' must be an integer.")
        else:
            if not isinstance(value, str):
                raise ToolError(f"Parameter '{name}' must be a string.")
            cleaned[name] = value
    return cleaned


def list_tools():
    """Return the registry metadata (no callables) for tool discovery."""
    return [
        {
            "name": spec["name"],
            "description": spec["description"],
            "category": spec["category"],
            "model": spec["model"],
            "parameters": spec["parameters"],
            "assumptions": spec["assumptions"],
            "limitations": spec["limitations"],
        }
        for spec in TOOLS.values()
    ]


def success_envelope(tool, parameters, data):
    return {
        "tool": tool["name"],
        "status": "success",
        "operation": tool["description"],
        "model": tool["model"],
        "assumptions": tool["assumptions"],
        "limitations": tool["limitations"],
        "parameters": parameters,
        "data": data,
    }


def error_envelope(tool, error_type, message):
    return {
        "tool": tool["name"] if tool else None,
        "status": "error",
        "operation": tool["description"] if tool else None,
        "model": tool["model"] if tool else None,
        "assumptions": tool["assumptions"] if tool else [],
        "limitations": tool["limitations"] if tool else [],
        "error": {"type": error_type, "message": message},
        "data": None,
    }


def unavailable_envelope(tool, message):
    return {
        "tool": tool["name"],
        "status": "unavailable",
        "operation": tool["description"],
        "model": tool["model"],
        "assumptions": tool["assumptions"],
        "limitations": tool["limitations"],
        "error": {"type": "ToolUnavailable", "message": message},
        "data": None,
    }


def execute_tool(tool_name, parameters=None):
    """Route a structured tool call and return a structured result envelope.

    This is the single entry point the future natural-language layer will use.
    It never fabricates an answer: it validates the request, dispatches to the
    registered deterministic executor, and wraps the result with the tool's
    operation/model/assumptions/limitations metadata.
    """
    tool = TOOLS.get(tool_name)
    if tool is None:
        available = ", ".join(sorted(TOOLS))
        return error_envelope(
            None,
            "UnknownTool",
            f"Unknown tool '{tool_name}'. Available tools: {available}.",
        )
    try:
        cleaned = validate_parameters(tool["parameters"], parameters)
        data = tool["execute"](cleaned)
        return success_envelope(tool, cleaned, data)
    except ToolUnavailable as exc:
        return unavailable_envelope(tool, str(exc))
    except (ToolError, ValueError, TypeError) as exc:
        return error_envelope(tool, type(exc).__name__, str(exc))
    except Exception as exc:  # pragma: no cover - defensive boundary
        return error_envelope(
            tool, type(exc).__name__, f"Unexpected error: {exc}"
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Deterministic analytical tool layer for the validated NBA "
            "analytics engine. Route structured tool calls or list available "
            "tools."
        )
    )
    parser.add_argument(
        "--tool",
        type=str,
        help="Name of the tool to execute (see --list-tools).",
    )
    parser.add_argument(
        "--params",
        type=str,
        default="{}",
        help="JSON object of tool parameters, e.g. "
             '--params \'{"home_team": "Boston Celtics", "away_team": '
             '"Los Angeles Lakers"}\'.',
    )
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="Print the available tool registry metadata and exit.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.list_tools:
        print(json.dumps(list_tools(), indent=2))
        return 0
    if not args.tool:
        raise SystemExit("Use --tool NAME (see --list-tools) or --list-tools.")
    try:
        parameters = json.loads(args.params)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid --params JSON: {exc}")
    print(json.dumps(execute_tool(args.tool, parameters), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())