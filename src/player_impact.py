"""Leakage-safe, assumption-labeled player impact estimates."""

import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

if __package__:
    from .provenance import with_provenance
    from .roster_change_data import validate_roster_change_events
else:
    from provenance import with_provenance
    from roster_change_data import validate_roster_change_events


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "database" / "nba.db"
METRICS_PATH = ROOT / "models" / "player_impact_metrics.json"
WINDOW = 10
BASELINE_NET_RATING = 0.0
HOLDOUT_BOOTSTRAP_REPLICATES = 1000
HOLDOUT_BOOTSTRAP_SEED = 42


def load_player_history(connection, person_id, before=None):
    """Load regular-season player appearances available before ``before``."""
    query = """
        SELECT player_statistics_extended.personId,
               player_statistics_extended.gameId,
               player_statistics_extended.gameDateTimeEst,
               player_statistics_extended.playerteamId AS teamId,
               CAST(player_statistics_extended.numMinutes AS REAL) AS minutes,
               player_statistics_extended.netRating
        FROM player_statistics_extended
        JOIN games USING (gameId)
        WHERE personId = ?
          AND COALESCE(player_statistics_extended.gameType, games.gameType) =
                'Regular Season'
          AND player_statistics_extended.playerteamId IS NOT NULL
          AND CAST(player_statistics_extended.numMinutes AS REAL) > 0
          AND player_statistics_extended.netRating IS NOT NULL
    """
    parameters = [person_id]
    if before is not None:
        query += " AND games.gameDateTimeEst < ?"
        parameters.append(pd.Timestamp(before).strftime("%Y-%m-%d %H:%M:%S"))
    history = pd.read_sql_query(query, connection, params=parameters)
    if not history.empty:
        history["gameDateTimeEst"] = pd.to_datetime(history["gameDateTimeEst"])
        history = history.sort_values(["gameDateTimeEst", "gameId"])
    return history


def summarize_player_impact(person_id, before=None, window=WINDOW):
    """Return a single-player descriptive impact estimate from prior regular-season data."""
    with sqlite3.connect(DB_PATH) as connection:
        history = load_player_history(connection, person_id, before=before)
    if history.empty:
        raise ValueError(f"No prior regular-season appearances found for personId={person_id}.")
    estimate = estimate_player_impact(history, window=window)
    team_counts = history["teamId"].value_counts(dropna=False)
    recent_team_id = int(team_counts.index[0]) if not team_counts.empty else None
    return {
        "person_id": int(person_id),
        "recent_team_id": recent_team_id,
        "before": None if before is None else str(pd.Timestamp(before).date()),
        "window": int(window),
        "prior_games": estimate["prior_games"],
        "player_net_rating": float(estimate["player_net_rating"]),
        "expected_minutes": float(estimate["expected_minutes"]),
        "estimated_net_rating_change": float(estimate["estimated_net_rating_change"]),
        "baseline_net_rating": float(estimate["baseline_net_rating"]),
        "direction": estimate["direction"],
        "note": (
            "This is a descriptive player-impact association estimate. It is not a "
            "causal forecast of roster or trade impact."
        ),
    }


def estimate_player_impact(
    history, window=WINDOW, baseline_net_rating=BASELINE_NET_RATING,
    direction="addition",
):
    """Estimate team net-rating change from a player's expected minutes.

    This is a descriptive proxy, not a causal estimate: player net rating is
    treated as transferable team impact, replacement minutes are assigned a
    fixed baseline net rating, and expected minutes are the player's prior
    average. The baseline is a reference value, not a replacement-level
    estimate.
    """
    if direction not in {"addition", "removal"}:
        raise ValueError("direction must be 'addition' or 'removal'")
    if window < 1:
        raise ValueError("window must be positive")
    required = {"minutes", "netRating"}
    missing = required.difference(history.columns)
    if missing:
        raise ValueError(f"history is missing columns: {sorted(missing)}")
    recent = history.tail(window).dropna(subset=["minutes", "netRating"])
    if recent.empty:
        raise ValueError("at least one valid prior player appearance is required")
    weights = recent["minutes"].clip(lower=0)
    if weights.sum() <= 0:
        raise ValueError("prior player minutes must have a positive total")
    player_net_rating = float(np.average(recent["netRating"], weights=weights))
    expected_minutes = float(weights.mean())
    delta = (player_net_rating - baseline_net_rating) * expected_minutes / 48.0
    if direction == "removal":
        delta = -delta
    return {
        "prior_games": int(len(recent)),
        "player_net_rating": player_net_rating,
        "expected_minutes": expected_minutes,
        "baseline_net_rating": float(baseline_net_rating),
        "estimated_net_rating_change": float(delta),
        "direction": direction,
        "assumptions": [
            "Prior player net rating transfers to the new team context.",
            "Expected minutes equal the prior-window average.",
            "Baseline minutes have the configured reference net rating of "
            f"{baseline_net_rating}. This is not a replacement-level estimate.",
            "This estimate is descriptive and is not a causal trade prediction.",
        ],
    }


def _calibrate_signal(training_values):
    """Fit a linear calibration using only targets before the holdout."""
    if len(training_values) < 2 or training_values["player_signal"].nunique() < 2:
        return 1.0, 0.0, "identity"
    signal = training_values["player_signal"].to_numpy(dtype=float)
    target = training_values["leave_one_out_target"].to_numpy(dtype=float)
    slope = float(np.cov(signal, target, ddof=0)[0, 1] / np.var(signal))
    intercept = float(target.mean() - slope * signal.mean())
    return slope, intercept, "linear"


def _fit_linear_predictions(training, holdout, feature_columns, target_column):
    """Fit a small pregame control model using training games only."""
    if len(training) < len(feature_columns) + 1:
        return (
            np.full(len(holdout), training[target_column].mean(), dtype=float),
            "training_mean",
        )
    design = np.column_stack(
        [np.ones(len(training)), training[feature_columns].to_numpy(dtype=float)]
    )
    coefficients, _, _, _ = np.linalg.lstsq(
        design, training[target_column].to_numpy(dtype=float), rcond=None
    )
    holdout_design = np.column_stack(
        [np.ones(len(holdout)), holdout[feature_columns].to_numpy(dtype=float)]
    )
    return holdout_design @ coefficients, "linear"


def _add_team_participation_controls(players):
    """Add prior-game team participation controls without using current rows."""
    team_games = (
        players.groupby(["teamId", "gameId", "gameDateTimeEst"], as_index=False)
        .agg(
            prior_active_players=("personId", "nunique"),
            prior_rotation_minutes=("minutes", "sum"),
        )
        .sort_values(["teamId", "gameDateTimeEst", "gameId"])
    )
    team_groups = team_games.groupby("teamId", sort=False)
    team_games["prior_active_players"] = team_groups[
        "prior_active_players"
    ].shift(1)
    team_games["prior_rotation_minutes"] = team_groups[
        "prior_rotation_minutes"
    ].shift(1)
    return players.merge(
        team_games[
            [
                "teamId",
                "gameId",
                "prior_active_players",
                "prior_rotation_minutes",
            ]
        ],
        on=["teamId", "gameId"],
        how="left",
        validate="many_to_one",
    )


def _mae(target, prediction):
    return float(np.mean(np.abs(target - prediction)))


def _bootstrap_holdout_intervals(
    holdout, prediction_column, replicates=HOLDOUT_BOOTSTRAP_REPLICATES,
    seed=HOLDOUT_BOOTSTRAP_SEED, target_column="leave_one_out_target",
    baseline_prediction=0.0,
):
    """Estimate clustered 95% MAE intervals by resampling complete games."""
    errors = holdout.assign(
        model_error=(
            holdout[target_column]
            - holdout[prediction_column]
        ).abs(),
        baseline_error=(holdout[target_column] - baseline_prediction).abs(),
    )
    by_game = errors.groupby("gameId")[["model_error", "baseline_error"]].agg(
        ["sum", "count"]
    )
    rng = np.random.default_rng(seed)
    game_indexes = rng.integers(0, len(by_game), size=(replicates, len(by_game)))
    model_sums = by_game["model_error"]["sum"].to_numpy()[game_indexes].sum(axis=1)
    baseline_sums = by_game["baseline_error"]["sum"].to_numpy()[game_indexes].sum(
        axis=1
    )
    counts = by_game["model_error"]["count"].to_numpy()[game_indexes].sum(axis=1)
    model_mae = model_sums / counts
    baseline_mae = baseline_sums / counts
    difference = model_mae - baseline_mae

    def interval(values):
        lower, upper = np.percentile(values, [2.5, 97.5])
        return [float(lower), float(upper)]

    return {
        "replicates": replicates,
        "seed": seed,
        "cluster": "gameId",
        "confidence_level": 0.95,
        "calibrated_mae": interval(model_mae),
        "zero_change_baseline_mae": interval(baseline_mae),
        "calibrated_minus_baseline_mae": interval(difference),
    }


def _evaluate_independent_pregame_target(
    values, test_fraction, include_usage_strata=False
):
    """Compare player history with team/opponent pregame controls."""
    target_column = "current_team_net_rating"
    control_columns = [
        "prior_team_net_rating",
        "prior_active_players",
        "prior_rotation_minutes",
    ]
    if values["prior_opponent_net_rating"].notna().mean() >= 0.9:
        control_columns.append("prior_opponent_net_rating")
    candidate_columns = [*control_columns, "player_signal"]
    independent_values = values.dropna(
        subset=[target_column, *candidate_columns]
    ).copy()
    ordered_games = (
        independent_values[["gameId", "gameDateTimeEst"]]
        .drop_duplicates()
        .sort_values(["gameDateTimeEst", "gameId"])
    )
    split_games = max(
        1,
        min(
            len(ordered_games) - 1,
            int(len(ordered_games) * (1 - test_fraction)),
        ),
    )
    holdout_game_ids = set(ordered_games.iloc[split_games:]["gameId"])
    holdout = independent_values[
        independent_values["gameId"].isin(holdout_game_ids)
    ].copy()
    training = independent_values[
        ~independent_values["gameId"].isin(holdout_game_ids)
    ]
    baseline_prediction, baseline_calibration = _fit_linear_predictions(
        training, holdout, control_columns, target_column
    )
    candidate_prediction, candidate_calibration = _fit_linear_predictions(
        training, holdout, candidate_columns, target_column
    )
    holdout["control_prediction"] = baseline_prediction
    holdout["candidate_prediction"] = candidate_prediction
    baseline_mae = _mae(holdout[target_column], holdout["control_prediction"])
    candidate_mae = _mae(holdout[target_column], holdout["candidate_prediction"])
    holdout["season"] = holdout["gameDateTimeEst"].dt.year - (
        holdout["gameDateTimeEst"].dt.month < 10
    )
    by_season = {}
    for season, season_values in holdout.groupby("season", sort=True):
        season_candidate_mae = _mae(
            season_values[target_column], season_values["candidate_prediction"]
        )
        season_baseline_mae = _mae(
            season_values[target_column], season_values["control_prediction"]
        )
        by_season[str(season)] = {
            "evaluated_player_games": int(len(season_values)),
            "candidate_mae": season_candidate_mae,
            "pregame_control_mae": season_baseline_mae,
            "improves_pregame_control": season_candidate_mae < season_baseline_mae,
        }
    result = {
        "target": "current team net rating",
        "target_is_derived_by_removing_current_player_scoring": False,
        "control_predictors": control_columns,
        "candidate_predictors": candidate_columns,
        "training_games": int(split_games),
        "holdout_games": int(len(ordered_games) - split_games),
        "holdout_player_games": int(len(holdout)),
        "holdout_start": holdout["gameDateTimeEst"].min().isoformat(),
        "control_calibration": baseline_calibration,
        "candidate_calibration": candidate_calibration,
        "pregame_control_mae": baseline_mae,
        "candidate_mae": candidate_mae,
        "improves_pregame_control": candidate_mae < baseline_mae,
        "bootstrap_intervals": _bootstrap_holdout_intervals(
            holdout,
            "candidate_prediction",
            target_column=target_column,
            baseline_prediction=holdout["control_prediction"],
        ),
        "by_season": by_season,
        "note": (
            "The target is the observed current team net rating. The control "
            "model uses prior team/opponent form and prior-game team "
            "participation; the candidate adds prior player production. This "
            "is an incremental association test, not a causal player-impact "
            "estimate."
        ),
    }
    if include_usage_strata:
        training_minutes = training["prior_minutes"].dropna()
        thresholds = training_minutes.quantile([1 / 3, 2 / 3]).to_numpy()
        holdout["usage_stratum"] = np.digitize(
            holdout["prior_minutes"].to_numpy(dtype=float), thresholds
        )
        usage_strata = {}
        for stratum, stratum_values in holdout.groupby(
            "usage_stratum", sort=True
        ):
            label = ("low", "middle", "high")[min(int(stratum), 2)]
            usage_strata[label] = {
                "evaluated_player_games": int(len(stratum_values)),
                "prior_minutes_min": float(stratum_values["prior_minutes"].min()),
                "prior_minutes_max": float(stratum_values["prior_minutes"].max()),
                "candidate_mae": _mae(
                    stratum_values[target_column],
                    stratum_values["candidate_prediction"],
                ),
                "pregame_control_mae": _mae(
                    stratum_values[target_column],
                    stratum_values["control_prediction"],
                ),
            }
            usage_strata[label]["improves_pregame_control"] = (
                usage_strata[label]["candidate_mae"]
                < usage_strata[label]["pregame_control_mae"]
            )
        result["usage_strata"] = usage_strata
        result["usage_strata_definition"] = (
            "Low, middle, and high strata use prior average minutes tertiles "
            "computed from the training games for each chronological window."
        )
    return result


def _evaluate_independent_pregame_robustness(values, test_fractions):
    """Evaluate the independent target across time windows and usage strata."""
    return {
        str(test_fraction): _evaluate_independent_pregame_target(
            values, test_fraction, include_usage_strata=True
        )
        for test_fraction in test_fractions
    }


def _evaluate_later_team_game_target(values, validation_start_season=2024):
    """Evaluate the player signal once per team-game on later seasons."""
    values = values.copy()
    values["season"] = values["gameDateTimeEst"].dt.year - (
        values["gameDateTimeEst"].dt.month < 10
    )
    team_games = (
        values.groupby(["gameId", "teamId"], as_index=False)
        .agg(
            gameDateTimeEst=("gameDateTimeEst", "first"),
            current_team_net_rating=("current_team_net_rating", "first"),
            prior_team_net_rating=("prior_team_net_rating", "first"),
            prior_opponent_net_rating=("prior_opponent_net_rating", "first"),
            prior_active_players=("prior_active_players", "first"),
            prior_rotation_minutes=("prior_rotation_minutes", "first"),
            prior_team_possessions=("prior_team_possessions", "first"),
            player_signal=(
                "player_signal",
                lambda series: float(series.mean()),
            ),
            season=("season", "first"),
            home=("home", "first"),
        )
        .sort_values(["teamId", "gameDateTimeEst", "gameId"])
    )
    team_groups = team_games.groupby("teamId", sort=False)
    team_games["rest_days"] = team_groups["gameDateTimeEst"].diff().dt.total_seconds() / (
        24 * 60 * 60
    )
    team_games = team_games.dropna(
        subset=[
            "current_team_net_rating",
            "prior_team_net_rating",
            "prior_opponent_net_rating",
            "prior_active_players",
            "prior_rotation_minutes",
            "prior_team_possessions",
            "player_signal",
            "rest_days",
        ]
    )
    training = team_games[team_games["season"] < validation_start_season]
    holdout = team_games[team_games["season"] >= validation_start_season].copy()
    control_columns = [
        "prior_team_net_rating",
        "prior_opponent_net_rating",
        "prior_active_players",
        "prior_rotation_minutes",
        "prior_team_possessions",
        "rest_days",
        "home",
    ]
    candidate_columns = [*control_columns, "player_signal"]
    if training.empty or holdout.empty:
        return {
            "status": "insufficient_later_season_data",
            "validation_start_season": validation_start_season,
            "training_team_games": int(len(training)),
            "holdout_team_games": int(len(holdout)),
            "control_predictors": control_columns,
            "candidate_predictors": candidate_columns,
            "control_has_player_signal": False,
            "candidate_adds_player_signal": True,
        }
    control_prediction, control_calibration = _fit_linear_predictions(
        training, holdout, control_columns, "current_team_net_rating"
    )
    candidate_prediction, candidate_calibration = _fit_linear_predictions(
        training, holdout, candidate_columns, "current_team_net_rating"
    )
    holdout["control_prediction"] = control_prediction
    holdout["candidate_prediction"] = candidate_prediction
    result = {
        "status": "evaluated",
        "validation_start_season": validation_start_season,
        "training_team_games": int(len(training)),
        "holdout_team_games": int(len(holdout)),
        "holdout_games": int(holdout["gameId"].nunique()),
        "holdout_start": holdout["gameDateTimeEst"].min().isoformat(),
        "control_predictors": control_columns,
        "candidate_predictors": candidate_columns,
        "control_has_player_signal": False,
        "candidate_adds_player_signal": True,
        "control_calibration": control_calibration,
        "candidate_calibration": candidate_calibration,
        "pregame_control_mae": _mae(
            holdout["current_team_net_rating"], holdout["control_prediction"]
        ),
        "candidate_mae": _mae(
            holdout["current_team_net_rating"], holdout["candidate_prediction"]
        ),
        "bootstrap_intervals": _bootstrap_holdout_intervals(
            holdout,
            "candidate_prediction",
            target_column="current_team_net_rating",
            baseline_prediction=holdout["control_prediction"],
        ),
        "note": (
            "Each team-game contributes once. The later seasons are held out "
            "untouched, and the candidate adds the mean prior player signal to "
            "pregame team, opponent, availability, possession, rest, and home "
            "controls. This remains an association diagnostic."
        ),
    }
    return result


def _evaluate_later_team_game_splits(values, validation_start_seasons):
    """Evaluate the later-window control and candidate across season cutoffs."""
    return {
        str(validation_start_season): _evaluate_later_team_game_target(
            values, validation_start_season
        )
        for validation_start_season in validation_start_seasons
    }


def _select_external_roster_change_appearances(values, roster_events):
    """Link validated addition events to the first later player appearance."""
    events = validate_roster_change_events(roster_events)
    if "confidence_level" in events.columns:
        events = events[events["confidence_level"] == "high"].copy()
    additions = events[events["change_type"] == "add"]
    values = values.copy()
    values["_appearance_timestamp"] = pd.to_datetime(
        values["gameDateTimeEst"], utc=True
    )
    linked = []
    for event in additions.itertuples(index=False):
        candidates = values[
            (values["personId"] == event.person_id)
            & (values["teamId"] == event.team_id)
            & (values["_appearance_timestamp"] > event.event_timestamp)
        ].sort_values(["_appearance_timestamp", "gameId"])
        if not candidates.empty:
            first = candidates.iloc[[0]].copy()
            first["external_event_id"] = event.event_id
            linked.append(first)
    if not linked:
        return values.iloc[0:0].drop(columns="_appearance_timestamp")
    return pd.concat(linked, ignore_index=True).drop(
        columns="_appearance_timestamp"
    )


def _evaluate_roster_change_target(values, test_fraction, roster_events=None):
    """Evaluate the player signal on first appearances after a team change."""
    values = values.copy()
    values["gameDateTimeEst"] = pd.to_datetime(values["gameDateTimeEst"])
    if roster_events is None:
        transitions = values[
            values["prior_team_id"].notna()
            & (values["prior_team_id"] != values["teamId"])
        ].copy()
        event_source = "historical_team_id_transitions"
        ignored_removals = 0
    else:
        validated_events = validate_roster_change_events(roster_events)
        if "confidence_level" in validated_events.columns:
            validated_events = validated_events[
                validated_events["confidence_level"] == "high"
            ].copy()
        transitions = _select_external_roster_change_appearances(
            values, validated_events
        )
        event_source = "external_timestamped_additions"
        ignored_removals = int((validated_events["change_type"] == "remove").sum())
    if transitions.empty:
        return {
            "status": "insufficient_roster_change_data",
            "transition_player_games": 0,
            "event_source": event_source,
            "ignored_removal_events": ignored_removals,
        }
    transition_events = (
        transitions.groupby(["gameId", "teamId"], as_index=False)
        .agg(
            gameDateTimeEst=("gameDateTimeEst", "first"),
            current_team_net_rating=("current_team_net_rating", "first"),
            prior_team_net_rating=("prior_team_net_rating", "first"),
            prior_opponent_net_rating=("prior_opponent_net_rating", "first"),
            prior_active_players=("prior_active_players", "first"),
            prior_rotation_minutes=("prior_rotation_minutes", "first"),
            prior_team_possessions=("prior_team_possessions", "first"),
            player_signal=("player_signal", "mean"),
            home=("home", "first"),
            transition_player_games=("personId", "count"),
        )
        .sort_values(["teamId", "gameDateTimeEst", "gameId"])
    )
    team_schedule = (
        values[["gameId", "teamId", "gameDateTimeEst"]]
        .drop_duplicates()
        .sort_values(["teamId", "gameDateTimeEst", "gameId"])
    )
    team_groups = team_schedule.groupby("teamId", sort=False)
    team_schedule["rest_days"] = (
        team_groups["gameDateTimeEst"].diff().dt.total_seconds()
        / (24 * 60 * 60)
    )
    transition_events = transition_events.merge(
        team_schedule[["gameId", "teamId", "rest_days"]],
        on=["gameId", "teamId"],
        how="left",
        validate="one_to_one",
    )
    control_columns = [
        "prior_team_net_rating",
        "prior_opponent_net_rating",
        "prior_active_players",
        "prior_rotation_minutes",
        "prior_team_possessions",
        "rest_days",
        "home",
    ]
    candidate_columns = [*control_columns, "player_signal"]
    transition_events = transition_events.dropna(
        subset=["current_team_net_rating", *candidate_columns]
    )
    ordered_games = (
        transition_events[["gameId", "gameDateTimeEst"]]
        .drop_duplicates()
        .sort_values(["gameDateTimeEst", "gameId"])
    )
    if len(ordered_games) < 2:
        return {
            "status": "insufficient_roster_change_data",
            "transition_player_games": int(len(transitions)),
            "evaluated_transition_events": int(len(transition_events)),
            "event_source": event_source,
            "ignored_removal_events": ignored_removals,
        }
    split_games = max(
        1,
        min(
            len(ordered_games) - 1,
            int(len(ordered_games) * (1 - test_fraction)),
        ),
    )
    holdout_game_ids = set(ordered_games.iloc[split_games:]["gameId"])
    training = transition_events[
        ~transition_events["gameId"].isin(holdout_game_ids)
    ]
    holdout = transition_events[
        transition_events["gameId"].isin(holdout_game_ids)
    ].copy()
    control_prediction, control_calibration = _fit_linear_predictions(
        training, holdout, control_columns, "current_team_net_rating"
    )
    candidate_prediction, candidate_calibration = _fit_linear_predictions(
        training, holdout, candidate_columns, "current_team_net_rating"
    )
    holdout["control_prediction"] = control_prediction
    holdout["candidate_prediction"] = candidate_prediction
    control_mae = _mae(
        holdout["current_team_net_rating"], holdout["control_prediction"]
    )
    candidate_mae = _mae(
        holdout["current_team_net_rating"], holdout["candidate_prediction"]
    )
    return {
        "status": "evaluated",
        "target": "current team net rating on first appearance after team change",
        "target_is_derived_by_removing_current_player_scoring": False,
        "control_predictors": control_columns,
        "candidate_predictors": candidate_columns,
        "transition_player_games": int(len(transitions)),
        "evaluated_transition_events": int(len(transition_events)),
        "event_source": event_source,
        "ignored_removal_events": ignored_removals,
        "training_events": int(len(training)),
        "holdout_events": int(len(holdout)),
        "holdout_games": int(holdout["gameId"].nunique()),
        "holdout_start": holdout["gameDateTimeEst"].min().isoformat(),
        "control_calibration": control_calibration,
        "candidate_calibration": candidate_calibration,
        "pregame_control_mae": control_mae,
        "candidate_mae": candidate_mae,
        "improves_pregame_control": candidate_mae < control_mae,
        "bootstrap_intervals": _bootstrap_holdout_intervals(
            holdout,
            "candidate_prediction",
            target_column="current_team_net_rating",
            baseline_prediction=holdout["control_prediction"],
        ),
        "note": (
            "Events are first player appearances after an observed team change. "
            "The candidate adds prior player production to pregame team controls. "
            "This is an independent association diagnostic, not causal evidence "
            "for a hypothetical roster change."
        ),
    }


def validate_player_impact(
    player_games, team_games, window=WINDOW, test_fraction=0.2, roster_events=None
):
    """Validate a possession-normalized leave-one-player-out target.

    The target is the change in team scoring efficiency when the player's
    points and estimated possessions are removed from the current game.
    Pregame production is compared with a fixed-zero target baseline. This is
    still an association diagnostic, not causal evidence.
    """
    required_player = {
        "personId", "gameId", "teamId", "gameDateTimeEst", "minutes",
        "netRating", "points", "player_possessions",
    }
    required_team = {
        "gameId", "teamId", "gameDateTimeEst", "netRating", "team_points",
        "opponent_points", "team_possessions",
    }
    if not required_player.issubset(player_games.columns):
        raise ValueError("player_games does not contain the required columns")
    if not required_team.issubset(team_games.columns):
        raise ValueError("team_games does not contain the required columns")

    players = player_games.copy()
    teams = team_games.copy()
    players["gameDateTimeEst"] = pd.to_datetime(players["gameDateTimeEst"])
    teams["gameDateTimeEst"] = pd.to_datetime(teams["gameDateTimeEst"])
    players["teamId"] = players["teamId"].astype("int64")
    teams["teamId"] = teams["teamId"].astype("int64")
    players = players.sort_values(["personId", "gameDateTimeEst", "gameId"])
    teams = teams.sort_values(["teamId", "gameDateTimeEst", "gameId"])
    players = _add_team_participation_controls(players)
    player_groups = players.groupby("personId", sort=False)
    players["prior_team_id"] = player_groups["teamId"].transform(
        lambda values: values.shift(1)
    )
    players["prior_minutes"] = player_groups["minutes"].transform(
        lambda values: values.shift(1).rolling(window, min_periods=window).sum()
    )
    team_groups = teams.groupby("teamId", sort=False)
    teams["prior_team_net_rating"] = team_groups["netRating"].transform(
        lambda values: values.shift(1).rolling(window, min_periods=window).mean()
    )
    teams["prior_team_possessions"] = team_groups["team_possessions"].transform(
        lambda values: values.shift(1).rolling(window, min_periods=window).mean()
    )
    if "opponentTeamId" in teams:
        opponent_history = teams[["teamId", "gameDateTimeEst", "netRating"]].rename(
            columns={
                "teamId": "opponentTeamId",
                "gameDateTimeEst": "opponent_game_date",
                "netRating": "opponent_net_rating",
            }
        ).sort_values(["opponentTeamId", "opponent_game_date"])
        opponent_groups = opponent_history.groupby("opponentTeamId", sort=False)
        opponent_history["prior_opponent_net_rating"] = opponent_groups[
            "opponent_net_rating"
        ].transform(
            lambda values: values.shift(1).rolling(window, min_periods=window).mean()
        )
        teams = teams.merge(
            opponent_history[
                ["opponentTeamId", "opponent_game_date", "prior_opponent_net_rating"]
            ],
            left_on=["opponentTeamId", "gameDateTimeEst"],
            right_on=["opponentTeamId", "opponent_game_date"],
            how="left",
            validate="many_to_one",
        ).drop(columns=["opponent_game_date"])
    if "prior_opponent_net_rating" not in teams:
        teams["prior_opponent_net_rating"] = np.nan
    if "home" not in teams:
        teams["home"] = 0
    current_team = teams[
        [
            "gameId", "teamId", "netRating", "team_points",
            "opponent_points", "team_possessions", "prior_team_net_rating",
            "prior_team_possessions", "prior_opponent_net_rating", "home",
        ]
    ].rename(columns={"netRating": "current_team_net_rating"})
    values = players.merge(
        current_team, on=["gameId", "teamId"], how="inner", validate="many_to_one"
    )
    values = values.dropna(
        subset=["prior_minutes", "prior_team_possessions"]
    )
    player_groups = players.groupby("personId", sort=False)
    values["prior_points"] = player_groups["points"].transform(
        lambda series: series.shift(1).rolling(window, min_periods=window).sum()
    )
    values["prior_player_possessions"] = player_groups["player_possessions"].transform(
        lambda series: series.shift(1).rolling(window, min_periods=window).sum()
    )
    values["player_signal"] = (
        values["prior_points"] / values["prior_player_possessions"]
        * (values["prior_player_possessions"] / window)
        / values["prior_team_possessions"]
        * 100.0
    )
    values["leave_one_out_net_rating"] = (
        (
            values["team_points"] - values["points"] - values["opponent_points"]
        )
        / (values["team_possessions"] - values["player_possessions"])
        * 100.0
    )
    values["leave_one_out_target"] = (
        values["current_team_net_rating"] - values["leave_one_out_net_rating"]
    )
    values = values.replace([np.inf, -np.inf], np.nan).dropna(
        subset=["leave_one_out_target", "player_signal"]
    )
    if values.empty:
        raise ValueError("no player appearances have enough prior history to validate")
    target = values["leave_one_out_target"]
    prediction = values["player_signal"]
    model_mae = _mae(target, prediction)
    baseline_mae = _mae(target, 0.0)
    correlation = (
        prediction.corr(target)
        if prediction.nunique() > 1 and target.nunique() > 1
        else np.nan
    )
    if not 0 < test_fraction < 1:
        raise ValueError("test_fraction must be between 0 and 1")
    ordered_games = (
        values[["gameId", "gameDateTimeEst"]]
        .drop_duplicates()
        .sort_values(["gameDateTimeEst", "gameId"])
    )
    split_games = max(
        1,
        min(
            len(ordered_games) - 1,
            int(len(ordered_games) * (1 - test_fraction)),
        ),
    )
    holdout_game_ids = set(ordered_games.iloc[split_games:]["gameId"])
    holdout = values[values["gameId"].isin(holdout_game_ids)].copy()
    training = values[~values["gameId"].isin(holdout_game_ids)]
    slope, intercept, calibration = _calibrate_signal(training)
    holdout["calibrated_prediction"] = (
        slope * holdout["player_signal"] + intercept
    )
    holdout_model_mae = _mae(
        holdout["leave_one_out_target"], holdout["calibrated_prediction"]
    )
    holdout_baseline_mae = _mae(holdout["leave_one_out_target"], 0.0)
    holdout["season"] = holdout["gameDateTimeEst"].dt.year - (
        holdout["gameDateTimeEst"].dt.month < 10
    )
    holdout_seasons = {}
    for season, season_values in holdout.groupby("season", sort=True):
        season_model_mae = _mae(
            season_values["leave_one_out_target"],
            season_values["calibrated_prediction"],
        )
        season_baseline_mae = _mae(
            season_values["leave_one_out_target"], 0.0
        )
        holdout_seasons[str(season)] = {
            "evaluated_player_games": int(len(season_values)),
            "calibrated_mae": season_model_mae,
            "zero_change_baseline_mae": season_baseline_mae,
            "improves_zero_change_baseline": (
                season_model_mae < season_baseline_mae
            ),
        }
    return {
        "evaluated_player_games": int(len(values)),
        "window": window,
        "target": "current team net rating minus leave-one-player-out scoring net rating",
        "association_diagnostic_mae": model_mae,
        "zero_change_baseline_mae": baseline_mae,
        "improves_zero_change_baseline": model_mae < baseline_mae,
        "prediction_target_correlation": (
            None if pd.isna(correlation) else float(correlation)
        ),
        "chronological_holdout": {
            "test_fraction": test_fraction,
            "training_games": int(split_games),
            "holdout_games": int(len(ordered_games) - split_games),
            "holdout_start": holdout["gameDateTimeEst"].min().isoformat(),
            "training_player_games": int(len(training)),
            "holdout_player_games": int(len(holdout)),
            "calibration": calibration,
            "calibration_slope": slope,
            "calibration_intercept": intercept,
            "calibrated_mae": holdout_model_mae,
            "zero_change_baseline_mae": holdout_baseline_mae,
            "improves_zero_change_baseline": (
                holdout_model_mae < holdout_baseline_mae
            ),
            "improves_zero_change_baseline_all_seasons": bool(
                holdout_seasons
                and all(
                    result["improves_zero_change_baseline"]
                    for result in holdout_seasons.values()
                )
            ),
            "bootstrap_intervals": _bootstrap_holdout_intervals(
                holdout, "calibrated_prediction"
            ),
            "by_season": holdout_seasons,
        },
        "independent_pregame_target": _evaluate_independent_pregame_target(
            values, test_fraction
        ),
        "independent_pregame_robustness": _evaluate_independent_pregame_robustness(
            values, (0.1, 0.2, 0.3)
        ),
        "later_team_game_validation": _evaluate_later_team_game_target(values),
        "later_team_game_validation_by_season": _evaluate_later_team_game_splits(
            values, (2022, 2023, 2024, 2025)
        ),
        "roster_change_validation": _evaluate_roster_change_target(
            values, test_fraction, roster_events
        ),
        "note": (
            "The leave-one-out target removes current player points and "
            "possessions from team scoring efficiency. It is an association "
            "diagnostic, not a causal impact estimate. Calibration is fit "
            "only on games before the chronological holdout."
        ),
    }


def load_validation_data(connection):
    player_games = pd.read_sql_query(
        """
        SELECT player_statistics_extended.personId, player_statistics_extended.gameId,
               player_statistics_extended.playerteamId AS teamId,
               player_statistics_extended.gameDateTimeEst,
               CAST(player_statistics_extended.numMinutes AS REAL) AS minutes,
               player_statistics_extended.netRating,
               CAST(player_statistics_extended.points AS REAL) AS points,
               CAST(player_statistics_extended.possessions AS REAL) AS player_possessions
        FROM player_statistics_extended
        JOIN games USING (gameId)
        WHERE COALESCE(player_statistics_extended.gameType, games.gameType) =
              'Regular Season'
          AND player_statistics_extended.playerteamId IS NOT NULL
          AND CAST(player_statistics_extended.numMinutes AS REAL) > 0
          AND player_statistics_extended.netRating IS NOT NULL
        """,
        connection,
    )
    team_games = pd.read_sql_query(
        """
        SELECT teamId, opponentTeamId, gameId, gameDateTimeEst, home,
               netRating AS teamNetRating, teamScore AS team_points,
               opponentScore AS opponent_points,
               CAST(possessions AS REAL) AS team_possessions
        FROM team_statistics_extended
        WHERE gameType = 'Regular Season' AND netRating IS NOT NULL
        """,
        connection,
    ).rename(columns={"teamNetRating": "netRating"})
    return player_games, team_games


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--person-id",
        type=int,
        help="Evaluate a player's recent regular-season impact using the repository's descriptive player-impact estimate.",
    )
    parser.add_argument(
        "--before",
        type=str,
        help="Optional cutoff date (YYYY-MM-DD) for the player-impact estimate; only prior games are used.",
    )
    parser.add_argument(
        "--window",
        type=int,
        default=WINDOW,
        help="Number of recent games to use when estimating average previous-player impact.",
    )
    parser.add_argument(
        "--roster-events",
        type=Path,
        help="CSV of independently sourced timestamped roster changes",
    )
    parser.add_argument(
        "--validate-roster-events",
        type=Path,
        help="Validate an external roster-change CSV against the repository contract and print a summary JSON without running the impact benchmark.",
    )
    arguments = parser.parse_args(argv)
    if arguments.validate_roster_events is not None:
        if __package__:
            from .roster_change_data import load_roster_change_events, summarize_roster_change_events
        else:
            from roster_change_data import load_roster_change_events, summarize_roster_change_events

        events = load_roster_change_events(arguments.validate_roster_events)
        print(json.dumps(summarize_roster_change_events(events), indent=2))
        return 0

    if arguments.person_id is not None:
        metrics = summarize_player_impact(
            arguments.person_id,
            before=arguments.before,
            window=arguments.window,
        )
        print(json.dumps(metrics, indent=2))
        return 0

    with sqlite3.connect(DB_PATH) as connection:
        player_games, team_games = load_validation_data(connection)
    roster_events = None
    if arguments.roster_events is not None:
        if __package__:
            from .roster_change_data import load_roster_change_events
        else:
            from roster_change_data import load_roster_change_events

        roster_events = load_roster_change_events(arguments.roster_events)
    metrics = validate_player_impact(
        player_games, team_games, roster_events=roster_events
    )
    METRICS_PATH.write_text(json.dumps(with_provenance(metrics), indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metrics, indent=2))
    print(f"Saved to: {METRICS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
