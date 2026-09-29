"""Hypothetical roster moves ("what if X were traded to Y?") on the strength layer.

The strength layer (``src/strength_layer.py``) already converts rosters into
team strength: its ``roster`` signal is the minutes-weighted, shrunk
previous-season plus-minus of the players on each team at the cutoff, and its
weight was fitted on 2015-2021 remaining games. A hypothetical move is just
another transaction: the player is removed from his current team and added to
the destination one second before the cutoff. Both projections use the same
random seed and strength shocks, so the difference between them is the move's
effect, not simulation noise.

What the effect means and does not mean:

* It is the change in the roster signal times its fitted weight at that point
  of the season (large preseason, small after mid-season -- by then the
  model trusts season-to-date margin, which a mid-season trade cannot move).
* A player's value is last season's plus-minus per 48 minutes (shrunk), so it
  carries his old team's context; fit, usage and injuries are not modeled.
* ``validate_transaction_effects`` checks the transaction component on its
  own: for every team-season it compares the projected effect of the real
  offseason transactions with what actually happened
  (``models/roster_moves_validation.json``).
"""

import argparse
import datetime
import json
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from src.forward_projection import (
        BACKTEST_SEASONS, CALIBRATION_SEASONS, load_model_inputs, project_from_date,
        resolve_strength_layer, season_schedule,
    )
    from src.provenance import with_provenance
    from src.strength_layer import (
        _RUNTIME_CACHE, _load_transactions, add_player_values, interpolate_weights,
        load_player_seasons, opening_rosters, replacement_value, roster_values,
    )
except ImportError:  # pragma: no cover - direct-script support
    from forward_projection import (
        BACKTEST_SEASONS, CALIBRATION_SEASONS, load_model_inputs, project_from_date,
        resolve_strength_layer, season_schedule,
    )
    from provenance import with_provenance
    from strength_layer import (
        _RUNTIME_CACHE, _load_transactions, add_player_values, interpolate_weights,
        load_player_seasons, opening_rosters, replacement_value, roster_values,
    )


ROOT = Path(__file__).resolve().parents[1]
VALIDATION_PATH = ROOT / "models" / "roster_moves_validation.json"
TRANSACTION_COLUMNS = ["event_id", "event_timestamp", "team_id", "person_id",
                       "change_type", "source", "source_url"]


def _feed():
    if "transactions" not in _RUNTIME_CACHE:
        _RUNTIME_CACHE["transactions"] = _load_transactions()
    return _RUNTIME_CACHE["transactions"]


def _player_seasons(season, layer):
    key = (int(season), "roster_moves")
    if key not in _RUNTIME_CACHE:
        _RUNTIME_CACHE[key] = load_player_seasons([int(season) - 1, int(season)])
    return add_player_values(_RUNTIME_CACHE[key], layer.get("shrink_minutes"))


def current_teams(season, cutoff, player_seasons, transactions, team_ids):
    """``{person_id: team_id}`` for everyone on a roster at ``cutoff``."""
    rosters = opening_rosters(season, cutoff, player_seasons, transactions, team_ids)
    return {player["person_id"]: team for team, players in rosters.items() for player in players}


def move_events(moves, cutoff, teams_now):
    """Transaction rows for ``moves`` (``[{"person_id", "to_team_id"}]``), just before cutoff."""
    # Python datetime arithmetic: pandas Timestamp - Timedelta warns under numpy 2.5.
    stamp = pd.Timestamp(
        pd.Timestamp(cutoff).tz_localize("UTC").to_pydatetime() - datetime.timedelta(seconds=1)
    )
    rows = []
    for index, move in enumerate(moves):
        person, destination = int(move["person_id"]), int(move["to_team_id"])
        origin = teams_now.get(person)
        if origin is not None and origin != destination:
            rows.append({"event_id": f"hypothetical-{index}-remove", "event_timestamp": stamp,
                         "team_id": origin, "person_id": person, "change_type": "remove",
                         "source": "hypothetical", "source_url": ""})
        rows.append({"event_id": f"hypothetical-{index}-add", "event_timestamp": stamp,
                     "team_id": destination, "person_id": person, "change_type": "add",
                     "source": "hypothetical", "source_url": ""})
    return pd.DataFrame(rows, columns=TRANSACTION_COLUMNS)


def _with_events(feed, events):
    if events.empty:
        return feed
    frame = pd.concat([feed, events], ignore_index=True)
    frame["event_timestamp"] = pd.to_datetime(frame["event_timestamp"], utc=True)
    return frame.sort_values(["event_timestamp", "event_id"]).reset_index(drop=True)


def project_with_moves(season, as_of, inputs, moves, n_simulations=1000, random_state=42,
                       schedule=None, team_names=None, strength_layer=None):
    """Baseline vs moved projection for ``moves`` at ``as_of``.

    Returns per-team before/after/delta for every team a move touches, the
    moved players' previous-season numbers, and the roster-signal weight used.
    """
    layer = resolve_strength_layer(strength_layer)
    if layer is None or "roster" not in layer["signals"]:
        raise ValueError("Roster moves need the strength layer with a roster signal "
                         "(models/strength_layer.json).")
    if not moves:
        raise ValueError("Provide at least one move.")
    schedule = season_schedule(inputs, season) if schedule is None else schedule
    cutoff = pd.Timestamp(as_of)
    teams = sorted(int(team) for team in set(schedule["homeTeamId"]) | set(schedule["awayTeamId"]))
    for move in moves:
        if int(move["to_team_id"]) not in teams:
            raise ValueError(f"Team {move['to_team_id']} has no games in season {season}.")
    feed = _feed()
    players = _player_seasons(season, layer)
    teams_now = current_teams(season, cutoff, players, feed, teams)
    events = move_events(moves, cutoff, teams_now)
    moved_feed = _with_events(feed, events)

    common = dict(n_simulations=n_simulations, random_state=random_state, schedule=schedule,
                  team_names=team_names, strength_layer=layer)
    before = project_from_date(season, cutoff, inputs, layer_transactions=feed, **common)
    after = project_from_date(season, cutoff, inputs, layer_transactions=moved_feed, **common)

    values_before = roster_values(season, cutoff, players, feed, teams)
    values_after = roster_values(season, cutoff, players, moved_feed, teams)
    weight = float(interpolate_weights(layer, before["fraction_completed"])[
        layer["signals"].index("roster")])
    touched = sorted({int(team) for team in events["team_id"]})
    rows_before = {row["teamId"]: row for row in before["projected_standings"]}
    rows_after = {row["teamId"]: row for row in after["projected_standings"]}
    team_effects = []
    for team in touched:
        b, a = rows_before[team], rows_after[team]
        team_effects.append({
            "teamId": team,
            "teamName": b.get("teamName"),
            "roster_value_before": round(values_before[team], 3),
            "roster_value_after": round(values_after[team], 3),
            "log_odds_shift_vs_average": round(weight * (values_after[team] - values_before[team]), 4),
            "mean_wins_before": b["mean_wins"],
            "mean_wins_after": a["mean_wins"],
            "mean_wins_change": round(a["mean_wins"] - b["mean_wins"], 2),
            "p5_p95_after": [a["p5_wins"], a["p95_wins"]],
            "direct_playoff_probability_before": b["direct_playoff_probability"],
            "direct_playoff_probability_after": a["direct_playoff_probability"],
            "top_six_or_better_change": round(
                a["direct_playoff_probability"] - b["direct_playoff_probability"], 3),
        })
    previous = players[players["season"] == int(season) - 1].set_index("personId")
    moved_players = []
    for move in moves:
        person = int(move["person_id"])
        row = previous.loc[person] if person in previous.index else None
        moved_players.append({
            "person_id": person,
            "from_team_id": teams_now.get(person),
            "to_team_id": int(move["to_team_id"]),
            "previous_season_minutes": None if row is None else round(float(row["minutes"]), 0),
            "previous_season_plus_minus": None if row is None else round(float(row["plus_minus"]), 0),
            "previous_season_value_per_48": None if row is None else round(float(row["value"]), 3),
            "counted_at_replacement_level": row is None or float(row["minutes"]) == 0,
        })
    warnings = []
    if any(player["counted_at_replacement_level"] for player in moved_players):
        warnings.append("A moved player has no previous-season NBA minutes; he counts at "
                        "replacement level, so the move has little or no effect.")
    if before["fraction_completed"] >= 0.25:
        warnings.append("After a quarter of the season the fitted roster weight is small "
                        f"({weight:.3f}); the projection leans on season-to-date margin, which "
                        "a hypothetical trade cannot change, so the effect is likely understated.")
    return {
        "season": int(season),
        "as_of": str(cutoff.date()),
        "fraction_completed": before["fraction_completed"],
        "roster_signal_weight": round(weight, 4),
        "replacement_value_per_48": round(replacement_value(players, int(season) - 1), 3),
        "moves": moved_players,
        "team_effects": team_effects,
        "n_simulations": int(n_simulations),
        "random_state": int(random_state),
        "method": "Paired projections (same seed and strength shocks) with and without the "
                  "moves; the moves change only the strength layer's roster signal.",
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Validation: do projected transaction effects show up in real results?
# ---------------------------------------------------------------------------

def _expected_wins(season, cutoff, inputs, schedule, layer, transactions):
    projection = project_from_date(season, cutoff, inputs, n_simulations=200, random_state=7,
                                   schedule=schedule, strength_layer=layer,
                                   layer_transactions=transactions, strength_sd=0.0)
    return {row["teamId"]: row["mean_wins"] for row in projection["projected_standings"]}


def validate_transaction_effects(inputs, seasons, layer=None):
    """Per team-season: projected effect of the real offseason moves vs the outcome.

    ``effect`` = preseason projection with the real transaction feed minus the
    same projection with no transactions (everyone still on last season's
    team). ``residual`` = actual wins minus the no-transaction projection. A
    useful transaction signal gives a positive slope of residual on effect
    (1.0 = the size is right) and lowers the MAE.
    """
    layer = resolve_strength_layer(layer)
    feed = _feed()
    empty = feed.iloc[0:0]
    rows = []
    for season in seasons:
        schedule = season_schedule(inputs, season)
        cutoff = schedule["gameDateTimeEst"].min().normalize()
        with_moves = _expected_wins(season, cutoff, inputs, schedule, layer, feed)
        without = _expected_wins(season, cutoff, inputs, schedule, layer, empty)
        actual = {}
        for home, away, target in zip(schedule["homeTeamId"], schedule["awayTeamId"],
                                      schedule["target"]):
            winner = int(home if target == 1 else away)
            actual[winner] = actual.get(winner, 0) + 1
        for team in with_moves:
            rows.append({"season": int(season), "teamId": int(team),
                         "effect": with_moves[team] - without[team],
                         "residual": actual.get(team, 0) - without[team],
                         "error_with_moves": abs(actual.get(team, 0) - with_moves[team]),
                         "error_without_moves": abs(actual.get(team, 0) - without[team])})
    frame = pd.DataFrame(rows)
    effect, residual = frame["effect"].to_numpy(), frame["residual"].to_numpy()
    slope = float(np.sum(effect * residual) / np.sum(effect * effect))
    big = frame[frame["effect"].abs() >= 3]
    return {
        "seasons": [int(season) for season in seasons],
        "team_seasons": int(len(frame)),
        "slope_through_origin": slope,
        "correlation": float(np.corrcoef(effect, residual)[0, 1]),
        "mae_with_moves": float(frame["error_with_moves"].mean()),
        "mae_without_moves": float(frame["error_without_moves"].mean()),
        "large_effects": {
            "threshold_wins": 3,
            "count": int(len(big)),
            "direction_correct_share": float((np.sign(big["effect"]) == np.sign(big["residual"])).mean())
            if len(big) else None,
        },
        "mean_abs_effect": float(np.mean(np.abs(effect))),
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--validate", action="store_true",
                        help="Check projected transaction effects against real outcomes and "
                             "write models/roster_moves_validation.json.")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if not args.validate:
        raise SystemExit("Use the project_roster_move tool, or pass --validate.")
    inputs = load_model_inputs()
    report = {
        "method": validate_transaction_effects.__doc__.strip().splitlines()[0],
        "calibration_seasons": validate_transaction_effects(inputs, CALIBRATION_SEASONS),
        "holdout_seasons": validate_transaction_effects(inputs, BACKTEST_SEASONS),
    }
    VALIDATION_PATH.write_text(json.dumps(with_provenance(report), indent=2) + "\n",
                               encoding="utf-8")
    for label in ("calibration_seasons", "holdout_seasons"):
        row = report[label]
        print(f"{label}: slope {row['slope_through_origin']:.2f} | corr {row['correlation']:.2f} "
              f"| MAE {row['mae_without_moves']:.2f} -> {row['mae_with_moves']:.2f} "
              f"| large effects {row['large_effects']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
