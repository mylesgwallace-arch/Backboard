"""Projection strength layer: stack team-strength signals on the frozen model.

Why this exists. ``forward_projection`` freezes the production
``elo_boosted_ensemble`` at a cutoff and simulates the rest of the season.
Its backtest shows two weaknesses:

* preseason, strength is last season's end-of-year state carried over
  unchanged (Elo never regresses between seasons and roster moves never touch
  Elo -- the roster-adjusted snapshots in ``roster_state`` only change the
  minor player box-score sums, which is why their backtest found no gain);
* at mid-season the projection is no better than carrying each team's current
  win percentage forward, i.e. the season-to-date results are under-used.

What it does. For every team at the cutoff it computes three signals:

``model``     the frozen model's own strength: logit of its average win
              probability against every other team (``strength_table``);
``margin``    season-to-date point margin per game (0 before any game);
``roster``    a preseason roster value -- last season's on-court plus-minus
              per 48 minutes of every player on the opening-day roster
              (last season's team, updated with the transaction feed up to
              the cutoff), minutes-weighted, with replacement level filling
              any minutes the roster does not cover.

Each remaining game keeps the frozen model's probability as an offset and
gets ``logit p' = logit p + sum_k w_k (x_k,home - x_k,away)``. The weights are
fitted per checkpoint (share of the season played) by logistic regression on
the remaining games of the calibration seasons only
(``forward_projection.CALIBRATION_SEASONS``, 2015-2021) and interpolated in
between. The frozen model, its pickle and ``predict_matchup`` are unchanged;
this layer only affects season projections that opt into it.

Leakage rules (tested): player values come from the previous season only;
transactions strictly before the cutoff; margins from games strictly before
the cutoff.
"""

import argparse
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from src.forward_projection import (
        BACKTEST_CHECKPOINTS, BACKTEST_SEASONS, CALIBRATION_SEASONS, _checkpoint_cutoff,
        frozen_matchup_probabilities, load_model_inputs, season_schedule, strength_table,
    )
    from src.main import TEAM_DB_PATH
except ImportError:  # pragma: no cover - direct-script support
    from forward_projection import (
        BACKTEST_CHECKPOINTS, BACKTEST_SEASONS, CALIBRATION_SEASONS, _checkpoint_cutoff,
        frozen_matchup_probabilities, load_model_inputs, season_schedule, strength_table,
    )
    from main import TEAM_DB_PATH


ROOT = Path(__file__).resolve().parents[1]
LAYER_PATH = ROOT / "models" / "strength_layer.json"
SIGNALS = ["model", "margin", "roster", "last_margin"]
# Candidate signal sets; the one with the lowest leave-one-season-out log loss
# on the calibration seasons is used. ``last_margin`` (last season's point
# margin) is the roster-free control for ``roster``.
CANDIDATE_SIGNAL_SETS = {
    "model": ["model"],
    "model+margin": ["model", "margin"],
    "model+margin+last_margin": ["model", "margin", "last_margin"],
    "model+margin+roster": ["model", "margin", "roster"],
    "all": ["model", "margin", "roster", "last_margin"],
}
# Plus-minus shrinkage: a player's value is PM / (minutes + shrink) * 48, so a
# player with few minutes is pulled toward 0 (league average). Chosen by
# preseason cross-validated log loss on the calibration seasons.
SHRINK_GRID = [250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0, 16000.0]
SHRINK_MINUTES = 4000.0
# A larger signal set is chosen only if its cross-validated mean log loss beats
# every smaller candidate's by more than this (collinear signals otherwise win
# by noise-level margins).
PARSIMONY_TOLERANCE = 0.0005
# Minutes below this in the previous season count as "fringe" for the
# replacement-level estimate.
REPLACEMENT_MAX_MINUTES = 500.0
MINUTES_PER_TEAM_GAME = 240.0
RIDGE = 1e-3


# ---------------------------------------------------------------------------
# Player values and rosters
# ---------------------------------------------------------------------------

def load_player_seasons(seasons, db_path=TEAM_DB_PATH):
    """Regular-season minutes and plus-minus per player-season.

    Returns one row per (personId, season) with total minutes, games with
    minutes, plus-minus, the team of the player's latest game that season
    (``last_team``) and the date of that game.
    """
    seasons = sorted({int(season) for season in seasons})
    start = f"{min(seasons)}-09-01"
    end = f"{max(seasons) + 1}-09-01"
    with sqlite3.connect(db_path) as connection:
        frame = pd.read_sql_query(
            """
            SELECT ps.personId, ps.playerteamId AS teamId, ps.gameDateTimeEst,
                   CAST(ps.numMinutes AS REAL) AS minutes,
                   CAST(ps.plusMinusPoints AS REAL) AS plus_minus
            FROM player_statistics ps
            JOIN games g ON g.gameId = ps.gameId
            WHERE g.gameType = 'Regular Season'
              AND ps.gameDateTimeEst >= ? AND ps.gameDateTimeEst < ?
              AND ps.playerteamId IS NOT NULL
            """,
            connection,
            params=(start, end),
        )
    frame["gameDateTimeEst"] = pd.to_datetime(frame["gameDateTimeEst"])
    frame["minutes"] = frame["minutes"].fillna(0.0)
    frame["plus_minus"] = frame["plus_minus"].fillna(0.0)
    dates = frame["gameDateTimeEst"]
    # Season start-year; Sep-Dec belongs to that year (covers the 2020 bubble
    # in Aug 2020 -> 2019 and the Dec 2020 start -> 2020).
    frame["season"] = dates.dt.year - (dates.dt.month < 9).astype(int)
    frame = frame[frame["season"].isin(seasons)]
    frame = frame.sort_values("gameDateTimeEst")
    grouped = frame.groupby(["personId", "season"])
    out = grouped.agg(
        minutes=("minutes", "sum"),
        plus_minus=("plus_minus", "sum"),
        games=("minutes", lambda values: int((values > 0).sum())),
        last_team=("teamId", "last"),
        last_game=("gameDateTimeEst", "last"),
    ).reset_index()
    out["personId"] = out["personId"].astype(int)
    out["last_team"] = out["last_team"].astype(int)
    return add_player_values(out)


def add_player_values(player_seasons, shrink=SHRINK_MINUTES):
    """(Re)compute each player-season's shrunk plus-minus per 48 minutes."""
    player_seasons = player_seasons.copy()
    player_seasons["value"] = (
        player_seasons["plus_minus"] / (player_seasons["minutes"] + float(shrink)) * 48.0
    )
    return player_seasons


def replacement_value(player_seasons, season):
    """Minutes-weighted plus-minus per 48 of fringe players in ``season``."""
    rows = player_seasons[
        (player_seasons["season"] == int(season))
        & (player_seasons["minutes"] > 0)
        & (player_seasons["minutes"] < REPLACEMENT_MAX_MINUTES)
    ]
    if rows.empty or rows["minutes"].sum() <= 0:
        return 0.0
    return float(rows["plus_minus"].sum() / rows["minutes"].sum() * 48.0)


def opening_rosters(season, cutoff, player_seasons, transactions, team_ids):
    """Players on each team at ``cutoff`` with their previous-season minutes/value.

    Start: every player who played in ``season - 1`` belongs to the team of
    his last game that season. Then every transaction after that season's
    last regular-season game and strictly before ``cutoff`` is applied in
    order (``add`` assigns the player; ``remove`` unassigns him if he is on
    that team). Players added with no previous-season minutes are kept with
    zero minutes (they draw on replacement level).
    """
    cutoff = pd.Timestamp(cutoff)
    previous = player_seasons[player_seasons["season"] == int(season) - 1]
    assignment = dict(zip(previous["personId"], previous["last_team"]))
    if transactions is not None and not transactions.empty:
        start = previous["last_game"].max() if not previous.empty else cutoff
        stamps = transactions["event_timestamp"]
        if getattr(stamps.dt, "tz", None) is not None:
            stamps = stamps.dt.tz_localize(None)
        window = transactions[(stamps > start) & (stamps < cutoff)]
        for person, team, change in zip(window["person_id"], window["team_id"],
                                        window["change_type"]):
            person, team = int(person), int(team)
            if change == "add":
                assignment[person] = team
            elif assignment.get(person) == team:
                assignment[person] = None
    minutes = dict(zip(previous["personId"], previous["minutes"]))
    values = dict(zip(previous["personId"], previous["value"]))
    rosters = {int(team): [] for team in team_ids}
    for person, team in assignment.items():
        if team is not None and int(team) in rosters:
            rosters[int(team)].append({
                "person_id": int(person),
                "minutes": float(minutes.get(person, 0.0)),
                "value": float(values.get(person, 0.0)),
            })
    return rosters


def roster_values(season, cutoff, player_seasons, transactions, team_ids):
    """Minutes-weighted roster value per team (plus-minus per 48 minutes).

    Weight = each player's previous-season minutes. The team's minute pool
    is the previous season's games per team x 240; minutes the roster does
    not cover are filled at replacement level, and a roster with more
    minutes than the pool is scaled down proportionally.
    """
    previous = player_seasons[player_seasons["season"] == int(season) - 1]
    replacement = replacement_value(player_seasons, int(season) - 1)
    # Team games last season ~ total minutes / (240 * teams); robust to 72-
    # and 66-game seasons.
    teams_last = previous["last_team"].nunique() or 30
    pool = previous["minutes"].sum() / max(teams_last, 1)
    if pool <= 0:
        pool = 82 * MINUTES_PER_TEAM_GAME
    rosters = opening_rosters(season, cutoff, player_seasons, transactions, team_ids)
    out = {}
    for team, players in rosters.items():
        covered = sum(player["minutes"] for player in players)
        weighted = sum(player["minutes"] * player["value"] for player in players)
        filler = max(pool - covered, 0.0)
        out[team] = float((weighted + filler * replacement) / max(covered + filler, 1e-9))
    return out


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

def _logit(probability):
    probability = np.clip(np.asarray(probability, dtype=float), 1e-6, 1 - 1e-6)
    return np.log(probability / (1 - probability))


def _season_of(features):
    """The feature table's season label (start year), derived from dates if absent."""
    if "season" in features.columns:
        return features["season"]
    dates = pd.to_datetime(features["gameDateTimeEst"])
    return dates.dt.year - (dates.dt.month < 10).astype(int)


def margin_to_date(features, season, cutoff, team_ids):
    """Point margin per game over the season's games strictly before ``cutoff``."""
    cutoff = pd.Timestamp(cutoff)
    rows = features[(_season_of(features) == int(season))
                    & (features["gameDateTimeEst"] < cutoff)
                    & features["teamId"].isin(team_ids)]
    margins = (rows["teamScore"] - rows["opponentScore"]).groupby(rows["teamId"]).mean()
    return {int(team): float(margins.get(team, 0.0)) for team in team_ids}


def last_season_margin(features, season, team_ids):
    """Point margin per game over the previous regular season (same franchise)."""
    rows = features[(_season_of(features) == int(season) - 1) & features["teamId"].isin(team_ids)]
    margins = (rows["teamScore"] - rows["opponentScore"]).groupby(rows["teamId"]).mean()
    return {int(team): float(margins.get(team, 0.0)) for team in team_ids}


def team_signals(inputs, season, cutoff, team_ids, player_seasons, transactions,
                 snapshots=None):
    """``{signal: {team_id: value}}`` for ``SIGNALS``, each centered on 0."""
    team_ids = [int(team) for team in team_ids]
    table = strength_table(inputs, as_of=cutoff, team_ids=team_ids, snapshots=snapshots)
    model = {row["teamId"]: float(_logit(row["win_probability_vs_average"])) for row in table}
    signals = {
        "model": model,
        "margin": margin_to_date(inputs.features, season, cutoff, team_ids),
        "roster": roster_values(season, cutoff, player_seasons, transactions, team_ids),
        "last_margin": last_season_margin(inputs.features, season, team_ids),
    }
    for name, values in signals.items():
        mean = float(np.mean(list(values.values()))) if values else 0.0
        signals[name] = {team: value - mean for team, value in values.items()}
    return signals


def signal_differences(pairs, signals, names=SIGNALS):
    """Home-minus-away matrix (n_games x len(names))."""
    home = pairs["homeTeamId"].astype(int).to_numpy()
    away = pairs["awayTeamId"].astype(int).to_numpy()
    return np.column_stack([
        np.array([signals[name][team] for team in home])
        - np.array([signals[name][team] for team in away])
        for name in names
    ]) if len(home) else np.zeros((0, len(names)))


# ---------------------------------------------------------------------------
# Fit and apply
# ---------------------------------------------------------------------------

def fit_offset_logistic(X, offset, y, ridge=RIDGE, iterations=50):
    """Logistic regression with a fixed offset and no intercept (Newton)."""
    X = np.asarray(X, dtype=float)
    offset = np.asarray(offset, dtype=float)
    y = np.asarray(y, dtype=float)
    weights = np.zeros(X.shape[1])
    for _ in range(iterations):
        eta = offset + X @ weights
        p = 1.0 / (1.0 + np.exp(-eta))
        gradient = X.T @ (p - y) + ridge * len(y) * weights
        hessian = (X * (p * (1 - p))[:, None]).T @ X + ridge * len(y) * np.eye(X.shape[1])
        step = np.linalg.solve(hessian, gradient)
        weights -= step
        if np.max(np.abs(step)) < 1e-9:
            break
    return weights


def log_loss(p, y):
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    y = np.asarray(y, dtype=float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def apply_layer(probabilities, differences, weights):
    """Shift frozen-model probabilities by the weighted signal differences."""
    eta = _logit(probabilities) + np.asarray(differences, dtype=float) @ np.asarray(weights, dtype=float)
    return 1.0 / (1.0 + np.exp(-eta))


def interpolate_weights(layer, fraction_completed):
    """Weights for any share of the season played (linear between checkpoints)."""
    points = sorted(float(key) for key in layer["weights_by_checkpoint"])
    table = np.array([layer["weights_by_checkpoint"][f"{point:.2f}"] for point in points])
    return np.array([
        np.interp(float(fraction_completed), points, table[:, column])
        for column in range(table.shape[1])
    ])


def load_layer(path=LAYER_PATH):
    path = Path(path)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def checkpoint_design(inputs, season, fraction, player_seasons, transactions, schedule=None):
    """Remaining games at a checkpoint: frozen probability, signal diffs, outcome."""
    schedule = season_schedule(inputs, season) if schedule is None else schedule
    cutoff = _checkpoint_cutoff(schedule, fraction)
    remaining = schedule[schedule["gameDateTimeEst"] >= cutoff].reset_index(drop=True)
    teams = sorted(set(schedule["homeTeamId"]) | set(schedule["awayTeamId"]))
    signals = team_signals(inputs, season, cutoff, teams, player_seasons, transactions)
    scored = frozen_matchup_probabilities(remaining[["homeTeamId", "awayTeamId"]], cutoff, inputs)
    return {
        "season": int(season),
        "checkpoint": float(fraction),
        "cutoff": cutoff,
        "probability": scored["home_win_probability"].to_numpy(dtype=float),
        "pairs": remaining[["homeTeamId", "awayTeamId"]],
        "target": remaining["target"].to_numpy(dtype=float),
        "signals": signals,
    }


def fit_layer(designs, names=SIGNALS):
    """Per-checkpoint weights fitted on the given designs (calibration seasons)."""
    names = list(names)
    by_checkpoint, diagnostics = {}, {}
    for fraction in sorted({design["checkpoint"] for design in designs}):
        group = [design for design in designs if design["checkpoint"] == fraction]
        X = np.vstack([signal_differences(design["pairs"], design["signals"], names)
                       for design in group])
        offset = _logit(np.concatenate([design["probability"] for design in group]))
        y = np.concatenate([design["target"] for design in group])
        weights = fit_offset_logistic(X, offset, y)
        by_checkpoint[f"{fraction:.2f}"] = [float(value) for value in weights]
        diagnostics[f"{fraction:.2f}"] = {
            "games": int(len(y)),
            "frozen_log_loss": log_loss(1 / (1 + np.exp(-offset)), y),
            "layered_log_loss": log_loss(apply_layer(1 / (1 + np.exp(-offset)), X, weights), y),
        }
    if "1.00" not in by_checkpoint and by_checkpoint:
        last = max(by_checkpoint, key=float)
        by_checkpoint["1.00"] = by_checkpoint[last]
    return {"signals": names, "weights_by_checkpoint": by_checkpoint,
            "fit_diagnostics": diagnostics}


def score_designs(designs, layer):
    """Frozen vs layered remaining-game log loss per checkpoint."""
    out = {}
    for fraction in sorted({design["checkpoint"] for design in designs}):
        group = [design for design in designs if design["checkpoint"] == fraction]
        weights = interpolate_weights(layer, fraction)
        frozen, layered, targets = [], [], []
        for design in group:
            frozen.append(design["probability"])
            differences = signal_differences(design["pairs"], design["signals"], layer["signals"])
            layered.append(apply_layer(design["probability"], differences, weights))
            targets.append(design["target"])
        y = np.concatenate(targets)
        out[f"{fraction:.2f}"] = {
            "games": int(len(y)),
            "frozen_log_loss": log_loss(np.concatenate(frozen), y),
            "layered_log_loss": log_loss(np.concatenate(layered), y),
        }
    return out


def build_designs(inputs, seasons, checkpoints, db_path=TEAM_DB_PATH, transactions=None,
                  player_seasons=None):
    """Checkpoint designs for every season x checkpoint (see ``checkpoint_design``)."""
    if transactions is None:
        transactions = _load_transactions()
    if player_seasons is None:
        player_seasons = load_player_seasons(
            [season - 1 for season in seasons] + list(seasons), db_path)
    designs = []
    for season in seasons:
        schedule = season_schedule(inputs, season)
        for fraction in checkpoints:
            designs.append(checkpoint_design(inputs, season, fraction, player_seasons,
                                             transactions, schedule=schedule))
    return designs, player_seasons, transactions


def _load_transactions():
    try:
        from src.roster_state import load_transactions
    except ImportError:  # pragma: no cover
        from roster_state import load_transactions
    return load_transactions()


def refresh_roster_signal(designs, player_seasons, transactions, shrink):
    """Recompute the ``roster`` signal of every design with another shrinkage."""
    valued = add_player_values(player_seasons, shrink)
    for design in designs:
        teams = list(design["signals"]["model"])
        values = roster_values(design["season"], design["cutoff"], valued, transactions, teams)
        mean = float(np.mean(list(values.values())))
        design["signals"]["roster"] = {team: value - mean for team, value in values.items()}
    return designs


def cross_validate(designs, names):
    """Leave-one-season-out log loss per checkpoint (games-weighted)."""
    totals = {}
    for season in sorted({design["season"] for design in designs}):
        layer = fit_layer([d for d in designs if d["season"] != season], names)
        scores = score_designs([d for d in designs if d["season"] == season], layer)
        for key, row in scores.items():
            entry = totals.setdefault(key, {"frozen": 0.0, "layered": 0.0, "games": 0})
            entry["frozen"] += row["frozen_log_loss"] * row["games"]
            entry["layered"] += row["layered_log_loss"] * row["games"]
            entry["games"] += row["games"]
    return {
        key: {"games": row["games"], "frozen_log_loss": row["frozen"] / row["games"],
              "layered_log_loss": row["layered"] / row["games"]}
        for key, row in totals.items()
    }


def _mean_layered(scores):
    return float(np.mean([row["layered_log_loss"] for row in scores.values()]))


def fit_production_layer(inputs, calibration_seasons=CALIBRATION_SEASONS,
                         holdout_seasons=BACKTEST_SEASONS, checkpoints=BACKTEST_CHECKPOINTS):
    """Select shrinkage and signal set by CV on calibration seasons; fit; score holdout.

    Every choice uses the calibration seasons only; the holdout seasons are
    scored once with the final layer (reported, never used to choose).
    """
    seasons = list(calibration_seasons) + list(holdout_seasons)
    designs, player_seasons, transactions = build_designs(inputs, seasons, checkpoints)
    calibration = [d for d in designs if d["season"] in calibration_seasons]
    holdout = [d for d in designs if d["season"] in holdout_seasons]

    shrink_cv = {}
    for shrink in SHRINK_GRID:
        refresh_roster_signal(calibration, player_seasons, transactions, shrink)
        cv = cross_validate(calibration, CANDIDATE_SIGNAL_SETS["model+margin+roster"])
        shrink_cv[f"{shrink:g}"] = cv["0.00"]["layered_log_loss"]
    shrink = float(min(shrink_cv, key=shrink_cv.get))
    refresh_roster_signal(designs, player_seasons, transactions, shrink)

    set_cv = {label: cross_validate(calibration, names)
              for label, names in CANDIDATE_SIGNAL_SETS.items()}
    best = min(_mean_layered(scores) for scores in set_cv.values())
    selected = min(
        (label for label in set_cv if _mean_layered(set_cv[label]) <= best + PARSIMONY_TOLERANCE),
        key=lambda label: (len(CANDIDATE_SIGNAL_SETS[label]), _mean_layered(set_cv[label])),
    )
    layer = fit_layer(calibration, CANDIDATE_SIGNAL_SETS[selected])
    holdout_by_set = {
        label: score_designs(holdout, fit_layer(calibration, names))
        for label, names in CANDIDATE_SIGNAL_SETS.items()
    }
    layer.update({
        "shrink_minutes": shrink,
        "selected_signal_set": selected,
        "calibration_seasons": [int(season) for season in calibration_seasons],
        "holdout_seasons": [int(season) for season in holdout_seasons],
        "selection": {
            "criterion": "leave-one-season-out log loss of remaining games, calibration "
                         "seasons only (shrinkage: preseason checkpoint; signal set: fewest "
                         "signals within PARSIMONY_TOLERANCE of the best mean over checkpoints)",
            "parsimony_tolerance": PARSIMONY_TOLERANCE,
            "shrink_cv_preseason_log_loss": shrink_cv,
            "signal_set_cv": set_cv,
        },
        "holdout_remaining_game_log_loss": holdout_by_set[selected],
        "holdout_by_signal_set": holdout_by_set,
        "note": "Every choice above uses the calibration seasons only. Caveat: during "
                "development, exploratory runs printed holdout log loss for several signal "
                "sets before the parsimony rule was added, so the holdout is not perfectly "
                "untouched; holdout_by_signal_set is reported for transparency.",
    })
    return layer


def calibrate_layer_strength_sd(inputs, layer, seasons=CALIBRATION_SEASONS):
    """Strength-shock SD per checkpoint with the layer applied (CRPS, calibration seasons).

    A sharper mean projection needs less added strength uncertainty, so the
    frozen model's SD table is not reused.
    """
    try:
        from src.forward_projection import calibrate_strength_sd
    except ImportError:  # pragma: no cover
        from forward_projection import calibrate_strength_sd
    report = calibrate_strength_sd(inputs, seasons, strength_layer=layer)
    table = {key: value for key, value in report["selected_strength_sd"].items()}
    table.setdefault("1.00", table[max(table, key=float)])
    return {
        "strength_sd_by_season_fraction": table,
        "strength_sd_calibration": {
            "seasons": report["calibration_seasons"],
            "criterion": report["criterion"],
            "grid": report["grid"],
            "by_checkpoint": report["by_checkpoint"],
        },
    }


# ---------------------------------------------------------------------------
# Runtime: signals and layered probabilities for a projection
# ---------------------------------------------------------------------------

_RUNTIME_CACHE = {}


def runtime_signals(inputs, season, cutoff, team_ids, layer, db_path=TEAM_DB_PATH,
                    snapshots=None, transactions=None):
    """Signals at ``cutoff`` for a live projection (player data cached per season)."""
    key = (int(season), str(db_path))
    if key not in _RUNTIME_CACHE:
        _RUNTIME_CACHE[key] = load_player_seasons([int(season) - 1, int(season)], db_path)
    if transactions is None:
        if "transactions" not in _RUNTIME_CACHE:
            _RUNTIME_CACHE["transactions"] = _load_transactions()
        transactions = _RUNTIME_CACHE["transactions"]
    player_seasons = add_player_values(_RUNTIME_CACHE[key],
                                       layer.get("shrink_minutes", SHRINK_MINUTES))
    return team_signals(inputs, season, cutoff, team_ids, player_seasons, transactions,
                        snapshots=snapshots)


def layered_probabilities(pairs, probabilities, signals, layer, fraction_completed):
    """Apply ``layer`` to frozen-model probabilities for ``pairs``."""
    weights = interpolate_weights(layer, fraction_completed)
    differences = signal_differences(pairs, signals, layer["signals"])
    return apply_layer(probabilities, differences, weights)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fit", action="store_true",
                        help="Select and fit the layer on the calibration seasons, score the "
                             "held-out seasons once, and write models/strength_layer.json.")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if not args.fit:
        raise SystemExit("Nothing to do; pass --fit.")
    inputs = load_model_inputs()
    layer = fit_production_layer(inputs)
    layer.update(calibrate_layer_strength_sd(inputs, layer))
    LAYER_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAYER_PATH.write_text(json.dumps(layer, indent=2) + "\n", encoding="utf-8")
    print(f"shrink {layer['shrink_minutes']:g} | signals {layer['selected_signal_set']}")
    for key, row in layer["holdout_remaining_game_log_loss"].items():
        weights = np.round(layer["weights_by_checkpoint"][key], 4).tolist()
        print(f"  checkpoint {key}: weights {weights} | holdout log loss "
              f"{row['frozen_log_loss']:.4f} -> {row['layered_log_loss']:.4f}")
    print(f"Saved {LAYER_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
