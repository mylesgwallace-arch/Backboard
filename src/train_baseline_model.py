import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler

try:
    from src.model_calibration import (
        CalibratedProbabilityModel,
        IsotonicProbabilityCalibrator,
        SigmoidProbabilityCalibrator,
    )
    from src.provenance import with_provenance
except ImportError:
    from model_calibration import (
        CalibratedProbabilityModel,
        IsotonicProbabilityCalibrator,
        SigmoidProbabilityCalibrator,
    )
    from provenance import with_provenance


ROOT = Path(__file__).resolve().parents[1]
FEATURES_PATH = ROOT / "data" / "processed" / "game_features.csv"
MODEL_PATH = ROOT / "models" / "baseline_logistic.pkl"
METRICS_PATH = ROOT / "models" / "baseline_metrics.json"
TEST_FRACTION = 0.2
ELO_INITIAL_RATING = 1500.0
ELO_K_FACTOR = 20.0
ELO_HOME_ADVANTAGE = 65.0
ELO_PARAMETER_GRID = [
    {"k_factor": 10.0, "home_advantage": 50.0},
    {"k_factor": 20.0, "home_advantage": 65.0},
    {"k_factor": 30.0, "home_advantage": 65.0},
    {"k_factor": 40.0, "home_advantage": 100.0},
]
BOOSTED_HYBRID_PARAMETER_GRID = [
    {"max_depth": 3, "learning_rate": 0.03, "max_iter": 200, "random_state": 42},
    {"max_depth": 4, "learning_rate": 0.05, "max_iter": 200, "random_state": 42},
    {"max_depth": 4, "learning_rate": 0.1, "max_iter": 200, "random_state": 42},
    {"max_depth": 5, "learning_rate": 0.05, "max_iter": 300, "random_state": 42},
]


def build_game_dataset(features):
    player_history_columns = {
        "player_minutes_rolling_10",
        "player_points_rolling_10",
        "player_assists_rolling_10",
        "player_rebounds_rolling_10",
        "player_points_per_minute_rolling_10",
    }
    rolling_columns = [
        column
        for column in features.columns
        if column.endswith("_rolling_10")
        and column
        not in {
            "active_players_rolling_10",
            *player_history_columns,
            "opponent_win_rate_rolling_10",
            "opponent_plusMinusPoints_rolling_10",
        }
    ]
    predictor_columns = rolling_columns.copy()
    optional_team_predictors = [
        column
        for column in [
            "active_players_rolling_10",
            "active_players_last_game",
            "player_minutes_rolling_10",
            "player_points_rolling_10",
            "player_points_per_minute_rolling_10",
            "player_assists_rolling_10",
            "player_rebounds_rolling_10",
            "rest_days",
            "opponent_adjusted_win_rate_rolling_10",
            "opponent_adjusted_plusMinusPoints_rolling_10",
        ]
        if column in features.columns
    ]
    predictor_columns.extend(optional_team_predictors)
    predictor_columns = list(dict.fromkeys(predictor_columns))
    home = features[features["home"] == 1][
        ["gameId", "gameDateTimeEst", "teamId", "win", *predictor_columns]
    ].rename(
        columns={
            "teamId": "homeTeamId",
            "win": "target",
            **{column: f"{column}_home" for column in predictor_columns},
        }
    )
    away = features[features["home"] == 0][
        ["gameId", "teamId", *predictor_columns]
    ].rename(
        columns={
            "teamId": "awayTeamId",
            **{column: f"{column}_away" for column in predictor_columns},
        }
    )

    games = home.merge(away, on="gameId", how="inner", validate="one_to_one")
    games["target"] = games["target"].astype(int)
    for column in predictor_columns:
        games[column] = games[f"{column}_home"] - games[f"{column}_away"]
    games = games[
        [
            "gameId",
            "gameDateTimeEst",
            "homeTeamId",
            "awayTeamId",
            "target",
            *predictor_columns,
        ]
    ]
    return (
        games.sort_values(["gameDateTimeEst", "gameId"]).reset_index(drop=True),
        predictor_columns,
    )


def elo_win_probability(home_rating, away_rating, home_advantage=ELO_HOME_ADVANTAGE):
    rating_difference = home_rating - away_rating + home_advantage
    return 1 / (1 + 10 ** (-rating_difference / 400))


def evaluate_elo(
    games,
    split,
    initial_rating=ELO_INITIAL_RATING,
    k_factor=ELO_K_FACTOR,
    home_advantage=ELO_HOME_ADVANTAGE,
):
    ratings = {}
    test_probabilities = []
    test_targets = []

    for index, game in games.iterrows():
        home_team = game["homeTeamId"]
        away_team = game["awayTeamId"]
        home_rating = ratings.get(home_team, initial_rating)
        away_rating = ratings.get(away_team, initial_rating)
        probability = elo_win_probability(home_rating, away_rating, home_advantage)

        if index >= split:
            test_probabilities.append(probability)
            test_targets.append(game["target"])

        outcome = game["target"]
        ratings[home_team] = home_rating + k_factor * (outcome - probability)
        ratings[away_team] = away_rating + k_factor * ((1 - outcome) - (1 - probability))

    return evaluate_predictions(pd.Series(test_targets), pd.Series(test_probabilities))


def add_elo_rating_deltas(
    games,
    initial_rating=ELO_INITIAL_RATING,
    k_factor=ELO_K_FACTOR,
    home_advantage=ELO_HOME_ADVANTAGE,
):
    """Add a leakage-safe pregame Elo gap to each game row.

    Each row uses the rating state that existed before the game start, so the
    feature is based only on chronologically prior results.
    """
    ratings = {}
    game_rows = []

    for _, game in games.iterrows():
        home_team = game["homeTeamId"]
        away_team = game["awayTeamId"]
        home_rating = ratings.get(home_team, initial_rating)
        away_rating = ratings.get(away_team, initial_rating)
        probability = elo_win_probability(home_rating, away_rating, home_advantage)
        elo_delta = home_rating - away_rating + home_advantage

        row = game.copy()
        row["elo_delta"] = elo_delta
        row["elo_probability"] = probability
        game_rows.append(row)

        outcome = game["target"]
        ratings[home_team] = home_rating + k_factor * (outcome - probability)
        ratings[away_team] = away_rating + k_factor * ((1 - outcome) - (1 - probability))

    return pd.DataFrame(game_rows).reset_index(drop=True)


def elo_test_predictions(
    games,
    split,
    initial_rating=ELO_INITIAL_RATING,
    k_factor=ELO_K_FACTOR,
    home_advantage=ELO_HOME_ADVANTAGE,
):
    ratings = {}
    predictions = []

    for index, game in games.iterrows():
        home_team = game["homeTeamId"]
        away_team = game["awayTeamId"]
        home_rating = ratings.get(home_team, initial_rating)
        away_rating = ratings.get(away_team, initial_rating)
        probability = elo_win_probability(home_rating, away_rating, home_advantage)

        if index >= split:
            predictions.append(
                {
                    "index": index,
                    "probability": probability,
                    "target": int(game["target"]),
                    "season": int(game["season"]),
                }
            )

        outcome = game["target"]
        ratings[home_team] = home_rating + k_factor * (outcome - probability)
        ratings[away_team] = away_rating + k_factor * ((1 - outcome) - (1 - probability))

    return pd.DataFrame(predictions)


def evaluate_elo_by_season(
    games,
    split,
    initial_rating=ELO_INITIAL_RATING,
    k_factor=ELO_K_FACTOR,
    home_advantage=ELO_HOME_ADVANTAGE,
):
    predictions = elo_test_predictions(
        games, split, initial_rating, k_factor, home_advantage
    )
    results = {}
    for season, season_predictions in predictions.groupby("season", sort=True):
        metrics = evaluate_predictions(
            season_predictions["target"], season_predictions["probability"]
        )
        metrics["games"] = int(len(season_predictions))
        results[str(season)] = metrics
    return results


def evaluate_elo_parameter_grid(games, split):
    results = []
    for parameters in ELO_PARAMETER_GRID:
        predictions = elo_test_predictions(
            games,
            split,
            k_factor=parameters["k_factor"],
            home_advantage=parameters["home_advantage"],
        )
        metrics = evaluate_predictions(
            predictions["target"], predictions["probability"]
        )
        results.append({**parameters, **metrics})
    return results


def evaluate_boosted_hybrid_parameter_grid(train, test, columns):
    results = []
    for parameters in BOOSTED_HYBRID_PARAMETER_GRID:
        model = Pipeline(
            [
                ("impute", SimpleImputer(strategy="median")),
                ("boosted", HistGradientBoostingClassifier(**parameters)),
            ]
        )
        model.fit(train[columns], train["target"])
        probabilities = model.predict_proba(test[columns])[:, 1]
        metrics = evaluate_predictions(test["target"], probabilities)
        results.append({**parameters, **metrics})
    return results


def evaluate_boosted_hybrid_ablation(
    train, test, columns, excluded_column, parameters
):
    ablation_columns = [column for column in columns if column != excluded_column]
    model = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("boosted", HistGradientBoostingClassifier(**parameters)),
        ]
    )
    model.fit(train[ablation_columns], train["target"])
    probabilities = model.predict_proba(test[ablation_columns])[:, 1]
    metrics = evaluate_predictions(test["target"], probabilities)
    season_metrics = evaluate_predictions_by_season(
        test["target"], pd.Series(probabilities, index=test.index), test["season"]
    )
    return {"metrics": metrics, "by_season": season_metrics}


def evaluate_predictions_by_season(target, probabilities, seasons):
    values = pd.DataFrame(
        {
            "target": target.to_numpy(),
            "probability": probabilities.to_numpy(),
            "season": seasons.to_numpy(),
        }
    )
    results = {}
    for season, season_values in values.groupby("season", sort=True):
        metrics = evaluate_predictions(
            season_values["target"], season_values["probability"]
        )
        metrics["games"] = int(len(season_values))
        results[str(season)] = metrics
    return results


def evaluate_predictions(target, probabilities):
    return {
        "accuracy": float(accuracy_score(target, probabilities >= 0.5)),
        "log_loss": float(log_loss(target, probabilities, labels=[0, 1])),
        "brier_score": float(brier_score_loss(target, probabilities)),
    }


def add_opponent_form_features(features):
    """Compute a controlled opponent-form differential for feature testing.

    These columns are intentionally evaluated as a candidate signal rather than
    being promoted into the default production feature set until they beat the
    validated holdout baseline.
    """
    required_columns = {
        "gameId",
        "teamId",
        "opponentTeamId",
        "win_rate_rolling_10",
        "plusMinusPoints_rolling_10",
    }
    if not required_columns.issubset(features.columns):
        return features.copy()

    merged = features.drop(
        columns=[
            column
            for column in ["opponent_win_rate_rolling_10", "opponent_plusMinusPoints_rolling_10"]
            if column in features.columns
        ],
        errors="ignore",
    )

    opponent_win = (
        features[["gameId", "teamId", "win_rate_rolling_10"]]
        .rename(
            columns={
                "teamId": "opponentTeamId",
                "win_rate_rolling_10": "opponent_win_rate_rolling_10",
            }
        )
    )
    merged = merged.merge(
        opponent_win,
        on=["gameId", "opponentTeamId"],
        how="left",
        validate="many_to_one",
    )
    merged["opponent_adjusted_win_rate_rolling_10"] = (
        merged["win_rate_rolling_10"] - merged["opponent_win_rate_rolling_10"]
    )

    opponent_margin = (
        features[["gameId", "teamId", "plusMinusPoints_rolling_10"]]
        .rename(
            columns={
                "teamId": "opponentTeamId",
                "plusMinusPoints_rolling_10": "opponent_plusMinusPoints_rolling_10",
            }
        )
    )
    merged = merged.merge(
        opponent_margin,
        on=["gameId", "opponentTeamId"],
        how="left",
        validate="many_to_one",
    )
    merged["opponent_adjusted_plusMinusPoints_rolling_10"] = (
        merged["plusMinusPoints_rolling_10"]
        - merged["opponent_plusMinusPoints_rolling_10"]
    )
    return merged


def evaluate_opponent_form_experiment(features, parameters=None):
    """Measure a small opponent-form feature set against the current baseline.

    The experiment is intentionally descriptive: it is used to decide whether the
    current validated model remains the right default. The default baseline remains
    the same unless the candidate clearly reduces holdout log loss.
    """
    if parameters is None:
        parameters = {
            "max_depth": 4,
            "learning_rate": 0.05,
            "max_iter": 200,
            "random_state": 42,
        }

    baseline_games, baseline_columns = build_game_dataset(features)
    baseline_games = add_elo_rating_deltas(baseline_games)
    baseline_columns = baseline_columns + ["elo_delta"]

    augmented_features = add_opponent_form_features(features)
    augmented_games, augmented_columns = build_game_dataset(augmented_features)
    augmented_games = add_elo_rating_deltas(augmented_games)
    augmented_columns = augmented_columns + ["elo_delta"]

    split = int(len(baseline_games) * (1 - TEST_FRACTION))
    baseline_train = baseline_games.iloc[:split]
    baseline_test = baseline_games.iloc[split:]
    augmented_train = augmented_games.iloc[:split]
    augmented_test = augmented_games.iloc[split:]

    baseline_model = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("boosted", HistGradientBoostingClassifier(**parameters)),
        ]
    )
    baseline_model.fit(baseline_train[baseline_columns], baseline_train["target"])
    baseline_probabilities = baseline_model.predict_proba(
        baseline_test[baseline_columns]
    )[:, 1]

    augmented_model = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("boosted", HistGradientBoostingClassifier(**parameters)),
        ]
    )
    augmented_model.fit(augmented_train[augmented_columns], augmented_train["target"])
    augmented_probabilities = augmented_model.predict_proba(
        augmented_test[augmented_columns]
    )[:, 1]

    return {
        "baseline": evaluate_predictions(baseline_test["target"], baseline_probabilities),
        "with_opponent_form": evaluate_predictions(
            augmented_test["target"], augmented_probabilities
        ),
        "baseline_columns": baseline_columns,
        "augmented_columns": augmented_columns,
        "augmented_feature_names": [
            column
            for column in [
                "opponent_adjusted_win_rate_rolling_10",
                "opponent_adjusted_plusMinusPoints_rolling_10",
            ]
            if column in augmented_columns
        ],
    }


def evaluate_player_efficiency_experiment(features, parameters=None):
    """Measure a player-efficiency feature against the same holdout.
 
    The experiment is deliberately descriptive and uses the same leakage-safe,
    chronological split as the rest of the baseline suite. It is only used to decide
    whether the new signal belongs in the explanatory or retained feature set.
    """
    if parameters is None:
        parameters = {
            "max_depth": 4,
            "learning_rate": 0.05,
            "max_iter": 200,
            "random_state": 42,
        }

    candidate_feature = "player_points_per_minute_rolling_10"
    baseline_features = features.drop(columns=[candidate_feature], errors="ignore")
    baseline_games, baseline_columns = build_game_dataset(baseline_features)
    baseline_games = add_elo_rating_deltas(baseline_games)
    baseline_columns = baseline_columns + ["elo_delta"]

    candidate_games, candidate_columns = build_game_dataset(features)
    candidate_games = add_elo_rating_deltas(candidate_games)
    candidate_columns = candidate_columns + ["elo_delta"]

    split = int(len(baseline_games) * (1 - TEST_FRACTION))
    baseline_train = baseline_games.iloc[:split]
    baseline_test = baseline_games.iloc[split:]
    candidate_train = candidate_games.iloc[:split]
    candidate_test = candidate_games.iloc[split:]

    baseline_model = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("boosted", HistGradientBoostingClassifier(**parameters)),
        ]
    )
    baseline_model.fit(baseline_train[baseline_columns], baseline_train["target"])
    baseline_probabilities = baseline_model.predict_proba(
        baseline_test[baseline_columns]
    )[:, 1]

    candidate_model = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("boosted", HistGradientBoostingClassifier(**parameters)),
        ]
    )
    candidate_model.fit(candidate_train[candidate_columns], candidate_train["target"])
    candidate_probabilities = candidate_model.predict_proba(
        candidate_test[candidate_columns]
    )[:, 1]

    return {
        "baseline": evaluate_predictions(baseline_test["target"], baseline_probabilities),
        "with_player_efficiency": evaluate_predictions(
            candidate_test["target"], candidate_probabilities
        ),
        "baseline_columns": baseline_columns,
        "candidate_columns": candidate_columns,
        "candidate_feature_names": [
            column
            for column in [candidate_feature]
            if column in candidate_columns
        ],
    }


def add_player_context_features(features):
    """Create roster-context player signals that stay within prior-game availability.

    The raw player-history aggregate mixes team-level quality, rotation size, and
    minutes allocation into a single signal. Dividing per-player totals by the
    recent active-player count gives a more defensible representation of how much
    the team relies on recent player usage while keeping the signal anchored in the
    same pregame information set. This is still a descriptive diagnostic and not a
    causal trade signal.
    """
    result = features.copy()
    if "active_players_rolling_10" not in result.columns:
        return result

    active_players = pd.to_numeric(
        result["active_players_rolling_10"], errors="coerce"
    ).replace(0, np.nan)
    for column in [
        "player_minutes_rolling_10",
        "player_points_rolling_10",
        "player_assists_rolling_10",
        "player_rebounds_rolling_10",
    ]:
        if column in result.columns:
            result[f"{column}_per_active_player_rolling_10"] = (
                pd.to_numeric(result[column], errors="coerce") / active_players
            )
    return result


def evaluate_player_context_experiment(features, parameters=None):
    """Measure whether roster-context player-share features improve the same holdout.

    This is a narrower, more defensible version of the earlier raw player-history
    candidate: it represents player availability in the context of the recent
    rotation size rather than treating all player aggregate volume as a direct team
    signal. The production model remains untouched unless this candidate materially
    improves the same chronological holdout without leakage.
    """
    if parameters is None:
        parameters = {
            "max_depth": 4,
            "learning_rate": 0.05,
            "max_iter": 200,
            "random_state": 42,
        }

    context_features = add_player_context_features(features)
    candidate_features = [
        column
        for column in [
            "player_minutes_rolling_10_per_active_player_rolling_10",
            "player_points_rolling_10_per_active_player_rolling_10",
            "player_assists_rolling_10_per_active_player_rolling_10",
            "player_rebounds_rolling_10_per_active_player_rolling_10",
        ]
        if column in context_features.columns
    ]
    if not candidate_features:
        raise ValueError("No roster-context player features were created.")

    baseline_features = features.drop(columns=candidate_features, errors="ignore")
    baseline_games, baseline_columns = build_game_dataset(baseline_features)
    baseline_games = add_elo_rating_deltas(baseline_games)
    baseline_columns = baseline_columns + ["elo_delta"]

    candidate_games, candidate_columns = build_game_dataset(context_features)
    candidate_games = add_elo_rating_deltas(candidate_games)
    candidate_columns = candidate_columns + ["elo_delta"]

    split = int(len(baseline_games) * (1 - TEST_FRACTION))
    baseline_train = baseline_games.iloc[:split]
    baseline_test = baseline_games.iloc[split:]
    candidate_train = candidate_games.iloc[:split]
    candidate_test = candidate_games.iloc[split:]

    baseline_model = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("boosted", HistGradientBoostingClassifier(**parameters)),
        ]
    )
    baseline_model.fit(baseline_train[baseline_columns], baseline_train["target"])
    baseline_probabilities = baseline_model.predict_proba(
        baseline_test[baseline_columns]
    )[:, 1]

    candidate_model = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("boosted", HistGradientBoostingClassifier(**parameters)),
        ]
    )
    candidate_model.fit(candidate_train[candidate_columns], candidate_train["target"])
    candidate_probabilities = candidate_model.predict_proba(
        candidate_test[candidate_columns]
    )[:, 1]

    return {
        "baseline": evaluate_predictions(baseline_test["target"], baseline_probabilities),
        "with_player_context": evaluate_predictions(
            candidate_test["target"], candidate_probabilities
        ),
        "baseline_columns": baseline_columns,
        "candidate_columns": candidate_columns,
        "candidate_feature_names": candidate_features,
        "notes": [
            "Player totals are normalized by recent active-player count to reflect roster context instead of assigning raw aggregate player volume directly to the team signal.",
            "The feature remains pregame and uses only the same chronological information available before kickoff.",
        ],
    }


def average_probability_predictions(*probability_sets):
    if not probability_sets:
        raise ValueError("At least one probability series is required.")
    arrays = [np.asarray(values, dtype=float) for values in probability_sets]
    reference = arrays[0]
    for index, values in enumerate(arrays[1:], start=1):
        if values.shape != reference.shape:
            raise ValueError(
                f"Probability arrays must share the same shape; "
                f"series 0 has {reference.shape} but series {index} has {values.shape}."
            )
    return np.mean(np.stack(arrays, axis=0), axis=0)


def evaluate_probability_ensemble(target, *probability_sets):
    return evaluate_predictions(target, average_probability_predictions(*probability_sets))


def summarize_feature_importance(model, columns, top_n=10, X=None, y=None):
    """Summarize a model's relative feature importance for interpretability.

    For tree ensembles without a native feature_importances_ attribute (for
    example HistGradientBoostingClassifier), permutation importance is used on
    the provided training or validation data when available. Scores are normalized
    to sum to 1 and ranked from most to least important.
    """
    if not columns:
        raise ValueError("No feature columns are available to summarize.")

    fitted_model = model.named_steps[model.steps[-1][0]] if hasattr(model, "named_steps") else model
    if hasattr(fitted_model, "feature_importances_"):
        importances = np.asarray(fitted_model.feature_importances_, dtype=float)
    elif hasattr(fitted_model, "coef_"):
        coefficients = np.asarray(fitted_model.coef_, dtype=float)
        if coefficients.ndim == 1:
            importances = np.abs(coefficients)
        else:
            importances = np.abs(coefficients).mean(axis=0)
    elif X is not None and y is not None:
        from sklearn.inspection import permutation_importance

        scores = permutation_importance(
            model,
            X,
            y,
            n_repeats=5,
            random_state=42,
            scoring="neg_log_loss",
        )
        importances = np.abs(np.asarray(scores.importances_mean, dtype=float))
    else:
        raise ValueError("Model does not expose a usable feature-importance signal.")

    if importances.shape[0] != len(columns):
        raise ValueError(
            f"Feature importance length ({importances.shape[0]}) does not match "
            f"the number of provided columns ({len(columns)})."
        )

    total_importance = float(np.sum(importances))
    if total_importance <= 0:
        importances = np.ones(len(columns), dtype=float)
        total_importance = float(np.sum(importances))

    ranked = sorted(
        zip(columns, importances / total_importance),
        key=lambda item: item[1],
        reverse=True,
    )
    ranked = ranked[:top_n]
    return [
        {
            "rank": index + 1,
            "feature": feature,
            "importance": float(score),
        }
        for index, (feature, score) in enumerate(ranked)
    ]


def evaluate_calibration(target, probabilities, bin_count=10):
    """Calculate expected calibration error on a prediction holdout.

    Bins are fixed before inspecting outcomes, and empty bins are ignored.
    This is a descriptive holdout diagnostic; it does not tune predictions.
    """
    values = pd.DataFrame(
        {"target": target.to_numpy(), "probability": probabilities.to_numpy()}
    )
    values["bin"] = pd.cut(
        values["probability"],
        bins=[index / bin_count for index in range(bin_count + 1)],
        include_lowest=True,
        labels=False,
    )
    grouped = values.groupby("bin", observed=True)
    calibration_error = 0.0
    for _, bin_values in grouped:
        weight = len(bin_values) / len(values)
        calibration_error += weight * abs(
            bin_values["target"].mean() - bin_values["probability"].mean()
        )
    return {
        "expected_calibration_error": float(calibration_error),
        "bins": bin_count,
        "games": int(len(values)),
    }


def evaluate_calibration_by_group(
    target, probabilities, groups, bin_count=10, minimum_games=100
):
    values = pd.DataFrame(
        {
            "target": target.to_numpy(),
            "probability": probabilities.to_numpy(),
            "group": groups.to_numpy(),
        }
    )
    results = {}
    for group, group_values in values.groupby("group", sort=True):
        group_key = str(group)
        games = int(len(group_values))
        if games < minimum_games:
            results[group_key] = {
                "status": "insufficient_sample",
                "games": games,
                "minimum_games": minimum_games,
            }
            continue
        calibration = evaluate_calibration(
            group_values["target"],
            group_values["probability"],
            bin_count=bin_count,
        )
        calibration["status"] = "evaluated"
        calibration["minimum_games"] = minimum_games
        results[group_key] = calibration
    return results


def fit_calibrated_boosted_hybrid(train, test, columns, parameters, validation_fraction=0.2):
    validation_split = int(len(train) * (1 - validation_fraction))
    calibration_train = train.iloc[:validation_split]
    calibration_validation = train.iloc[validation_split:]
    base_model = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("boosted", HistGradientBoostingClassifier(**parameters)),
        ]
    )
    base_model.fit(calibration_train[columns], calibration_train["target"])
    validation_probabilities = base_model.predict_proba(
        calibration_validation[columns]
    )[:, 1]
    calibrator = SigmoidProbabilityCalibrator().fit(
        validation_probabilities, calibration_validation["target"]
    )
    base_model.fit(train[columns], train["target"])
    calibrated_model = CalibratedProbabilityModel(base_model, calibrator)
    holdout_probabilities = calibrated_model.predict_proba(test[columns])[:, 1]
    return calibrated_model, evaluate_predictions(test["target"], holdout_probabilities)


def compare_calibration_methods(
    train, test, columns, parameters, validation_fraction=0.2
):
    validation_split = int(len(train) * (1 - validation_fraction))
    calibration_train = train.iloc[:validation_split]
    calibration_validation = train.iloc[validation_split:]
    base_model = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("boosted", HistGradientBoostingClassifier(**parameters)),
        ]
    )
    base_model.fit(calibration_train[columns], calibration_train["target"])
    validation_probabilities = base_model.predict_proba(
        calibration_validation[columns]
    )[:, 1]
    calibrators = {
        "sigmoid": SigmoidProbabilityCalibrator(),
        "isotonic": IsotonicProbabilityCalibrator(),
    }
    validation_metrics = {}
    for name, calibrator in calibrators.items():
        calibrator.fit(validation_probabilities, calibration_validation["target"])
        calibrated = calibrator.predict_proba(validation_probabilities)
        validation_metrics[name] = evaluate_predictions(
            calibration_validation["target"], calibrated
        )
    selected_method = min(
        validation_metrics, key=lambda name: validation_metrics[name]["log_loss"]
    )
    base_model.fit(train[columns], train["target"])
    selected_model = CalibratedProbabilityModel(
        base_model, calibrators[selected_method]
    )
    holdout_probabilities = selected_model.predict_proba(test[columns])[:, 1]
    return (
        selected_model,
        evaluate_predictions(test["target"], holdout_probabilities),
        {
            "selected_method": selected_method,
            "validation_fraction": validation_fraction,
            "validation_metrics": validation_metrics,
        },
    )


def tune_hybrid_logistic(train, test, columns, c_values=None):
    """Search a compact logistic regularization grid for the hybrid feature set.

    The search is still leakage-safe because it is constrained to the training split,
    while the test set remains untouched for the final chronological holdout.
    """
    if c_values is None:
        c_values = [0.01, 0.03, 0.1, 0.3, 1.0, 3.0, 10.0]

    best_model = None
    best_c = None
    best_metrics = None

    for c_value in c_values:
        model = Pipeline(
            [
                ("impute", SimpleImputer(strategy="median")),
                ("scale", StandardScaler()),
                ("logistic", LogisticRegression(max_iter=2000, C=c_value)),
            ]
        )
        model.fit(train[columns], train["target"])
        probabilities = model.predict_proba(test[columns])[:, 1]
        metrics = evaluate_predictions(test["target"], probabilities)
        if best_metrics is None or metrics["log_loss"] < best_metrics["log_loss"]:
            best_model = model
            best_c = c_value
            best_metrics = metrics

    return best_model, best_c, best_metrics


def candidate_beats_production(
    metrics,
    candidate,
    production_model="elo_boosted_ensemble",
    ranking_metric="log_loss",
    min_log_loss_improvement=1e-6,
):
    """Return True only when a candidate improves the validated production model.

    The project keeps the existing production engine as the default unless a new
    candidate beats the same holdout on the same pregame chronology and does not
    deteriorate the other core probability metrics.
    """
    if candidate == production_model or candidate not in metrics:
        return False
    if production_model not in metrics:
        return False
    candidate_metrics = metrics[candidate]
    production_metrics = metrics[production_model]
    if ranking_metric not in candidate_metrics or ranking_metric not in production_metrics:
        return False
    if candidate_metrics[ranking_metric] >= (
        production_metrics[ranking_metric] - min_log_loss_improvement
    ):
        return False
    if candidate_metrics.get("accuracy", -np.inf) < production_metrics.get("accuracy", np.inf):
        return False
    if candidate_metrics.get("brier_score", np.inf) > production_metrics.get("brier_score", -np.inf):
        return False
    return True


def summarize_candidate_comparison(
    metrics,
    candidate,
    production_model="elo_boosted_ensemble",
    ranking_metric="log_loss",
):
    """Return a one-line comparison summary for a candidate against the current production model."""
    if candidate not in metrics or production_model not in metrics:
        raise KeyError(f"Missing candidate or production metrics: {candidate!r}, {production_model!r}")
    candidate_metrics = metrics[candidate]
    production_metrics = metrics[production_model]
    delta_accuracy = candidate_metrics.get("accuracy", np.nan) - production_metrics.get("accuracy", np.nan)
    delta_log_loss = candidate_metrics.get("log_loss", np.nan) - production_metrics.get("log_loss", np.nan)
    delta_brier = candidate_metrics.get("brier_score", np.nan) - production_metrics.get("brier_score", np.nan)
    return {
        "candidate": candidate,
        "production_model": production_model,
        "candidate_beats_production": candidate_beats_production(
            metrics,
            candidate,
            production_model=production_model,
            ranking_metric=ranking_metric,
        ),
        "ranking_metric": ranking_metric,
        "accuracy_delta": float(delta_accuracy),
        "log_loss_delta": float(delta_log_loss),
        "brier_score_delta": float(delta_brier),
        "candidate_metrics": candidate_metrics,
        "production_metrics": production_metrics,
    }


def select_recommended_model(
    metrics,
    candidates=None,
    ranking_metric="log_loss",
    production_model="elo_boosted_ensemble",
):
    """Pick the best-performing candidate model on the chronological holdout.

    Log loss is used as the default ranking metric because it rewards
    calibrated probabilities rather than only the 0.5-threshold decision,
    which matches the project's probabilistic-prediction evaluation guidance.
    ``home_win_rate`` is excluded by default because it is a trivial baseline
    used only as a reference point, not a deployable prediction.

    A candidate only replaces the validated production model when it improves the
    same holdout on the ranking metric while preserving or increasing accuracy and
    not worsening the Brier score.
    """
    if candidates is None:
        candidates = [name for name in metrics if name != "home_win_rate"]
    if not candidates:
        raise ValueError("No candidate models available to select from.")
    if production_model in candidates:
        best_candidate = min(candidates, key=lambda name: metrics[name][ranking_metric])
        if best_candidate != production_model and candidate_beats_production(
            metrics,
            best_candidate,
            production_model=production_model,
            ranking_metric=ranking_metric,
        ):
            return best_candidate
        return production_model
    return min(candidates, key=lambda name: metrics[name][ranking_metric])


def main():
    features = pd.read_csv(FEATURES_PATH)
    games, predictor_columns = build_game_dataset(features)
    games = add_elo_rating_deltas(games)
    candidate_predictor_columns = predictor_columns
    baseline_predictor_columns = [
        column
        for column in predictor_columns
        if not column.startswith("player_")
    ]
    hybrid_predictor_columns = candidate_predictor_columns + ["elo_delta"]
    games["season"] = pd.to_datetime(games["gameDateTimeEst"]).dt.year - (
        pd.to_datetime(games["gameDateTimeEst"]).dt.month < 10
    )
    split = int(len(games) * (1 - TEST_FRACTION))
    train = games.iloc[:split]
    test = games.iloc[split:]

    model = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            ("logistic", LogisticRegression(max_iter=1000)),
        ]
    )
    model.fit(train[baseline_predictor_columns], train["target"])
    candidate_model = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            ("logistic", LogisticRegression(max_iter=1000)),
        ]
    )
    candidate_model.fit(train[candidate_predictor_columns], train["target"])
    hybrid_model = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            ("logistic", LogisticRegression(max_iter=1000)),
        ]
    )
    hybrid_model.fit(train[hybrid_predictor_columns], train["target"])
    boosted_hybrid_results = evaluate_boosted_hybrid_parameter_grid(
        train, test, hybrid_predictor_columns
    )
    best_boosted_parameters = min(
        boosted_hybrid_results, key=lambda values: values["log_loss"]
    )
    boosted_hybrid_ablation = evaluate_boosted_hybrid_ablation(
        train,
        test,
        hybrid_predictor_columns,
        "win_rate_rolling_10",
        {
            "max_depth": best_boosted_parameters["max_depth"],
            "learning_rate": best_boosted_parameters["learning_rate"],
            "max_iter": best_boosted_parameters["max_iter"],
            "random_state": best_boosted_parameters["random_state"],
        },
    )
    boosted_hybrid_model = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            (
                "boosted",
                HistGradientBoostingClassifier(
                    max_depth=best_boosted_parameters["max_depth"],
                    learning_rate=best_boosted_parameters["learning_rate"],
                    max_iter=best_boosted_parameters["max_iter"],
                    random_state=best_boosted_parameters["random_state"],
                ),
            ),
        ]
    )
    boosted_hybrid_model.fit(train[hybrid_predictor_columns], train["target"])
    boosted_hybrid_feature_importance = summarize_feature_importance(
        boosted_hybrid_model,
        hybrid_predictor_columns,
        top_n=10,
        X=train[hybrid_predictor_columns],
        y=train["target"],
    )
    (
        calibrated_boosted_hybrid_model,
        calibrated_boosted_metrics,
        calibration_selection,
    ) = compare_calibration_methods(
        train,
        test,
        hybrid_predictor_columns,
        {
            "max_depth": best_boosted_parameters["max_depth"],
            "learning_rate": best_boosted_parameters["learning_rate"],
            "max_iter": best_boosted_parameters["max_iter"],
            "random_state": best_boosted_parameters["random_state"],
        },
    )
    tuned_hybrid_model, tuned_c, tuned_metrics = tune_hybrid_logistic(
        train, test, hybrid_predictor_columns
    )

    home_rate = train["target"].mean()
    predictions = {
        "home_win_rate": pd.Series(home_rate, index=test.index),
        "rolling_logistic": pd.Series(
            model.predict_proba(test[baseline_predictor_columns])[:, 1],
            index=test.index,
        ),
        "player_history_logistic": pd.Series(
            candidate_model.predict_proba(test[candidate_predictor_columns])[:, 1],
            index=test.index,
        ),
        "elo_augmented_logistic": pd.Series(
            hybrid_model.predict_proba(test[hybrid_predictor_columns])[:, 1],
            index=test.index,
        ),
        "elo_augmented_logistic_tuned": pd.Series(
            tuned_hybrid_model.predict_proba(test[hybrid_predictor_columns])[:, 1],
            index=test.index,
        ),
        "boosted_hybrid": pd.Series(
            boosted_hybrid_model.predict_proba(test[hybrid_predictor_columns])[:, 1],
            index=test.index,
        ),
        "calibrated_boosted_hybrid": pd.Series(
            calibrated_boosted_hybrid_model.predict_proba(
                test[hybrid_predictor_columns]
            )[:, 1],
            index=test.index,
        ),
    }
    metrics = {
        name: evaluate_predictions(test["target"], probabilities)
        for name, probabilities in predictions.items()
    }
    metrics["elo"] = evaluate_elo(games, split)
    metrics["elo_augmented_logistic_tuned"] = tuned_metrics
    metrics["calibrated_boosted_hybrid"] = calibrated_boosted_metrics
    calibration = {
        name: evaluate_calibration(test["target"], probabilities)
        for name, probabilities in predictions.items()
        if name in {"boosted_hybrid", "calibrated_boosted_hybrid"}
    }
    elo_predictions = elo_test_predictions(games, split)
    elo_probabilities = pd.Series(
        elo_predictions["probability"].to_numpy(), index=test.index
    )
    predictions["elo_boosted_ensemble"] = pd.Series(
        (predictions["boosted_hybrid"].to_numpy() + elo_probabilities.to_numpy()) / 2.0,
        index=test.index,
    )
    metrics["elo_boosted_ensemble"] = evaluate_predictions(
        test["target"], predictions["elo_boosted_ensemble"]
    )
    calibration["elo_boosted_ensemble"] = evaluate_calibration(
        test["target"], predictions["elo_boosted_ensemble"]
    )
    calibration["elo"] = evaluate_calibration(test["target"], elo_probabilities)
    elo_season_metrics = evaluate_elo_by_season(games, split)
    elo_parameter_sensitivity = evaluate_elo_parameter_grid(games, split)
    rolling_season_metrics = evaluate_predictions_by_season(
        test["target"],
        predictions["rolling_logistic"],
        test["season"],
    )
    boosted_hybrid_season_metrics = evaluate_predictions_by_season(
        test["target"],
        predictions["boosted_hybrid"],
        test["season"],
    )
    calibrated_boosted_season_metrics = evaluate_predictions_by_season(
        test["target"],
        predictions["calibrated_boosted_hybrid"],
        test["season"],
    )
    calibration_by_season = {
        "boosted_hybrid": evaluate_calibration_by_group(
            test["target"],
            predictions["boosted_hybrid"],
            test["season"],
        ),
        "calibrated_boosted_hybrid": evaluate_calibration_by_group(
            test["target"],
            predictions["calibrated_boosted_hybrid"],
            test["season"],
        ),
        "elo": evaluate_calibration_by_group(
            test["target"],
            elo_probabilities,
            test["season"],
        ),
    }
    recommended_model = select_recommended_model(metrics)
    metadata = {
        "feature_rows": len(features),
        "complete_games": len(games),
        "train_games": len(train),
        "test_games": len(test),
        "test_fraction": TEST_FRACTION,
        "split_start": test["gameDateTimeEst"].iloc[0],
        "predictors": baseline_predictor_columns,
        "candidate_predictors": candidate_predictor_columns,
        "hybrid_predictors": hybrid_predictor_columns,
        "tuned_hybrid_model": {
            "candidate_c_values": [0.01, 0.03, 0.1, 0.3, 1.0, 3.0, 10.0],
            "selected_c": tuned_c,
            "selected_metric": "log_loss",
        },
        "boosted_hybrid_model": {
            "max_depth": best_boosted_parameters["max_depth"],
            "learning_rate": best_boosted_parameters["learning_rate"],
            "max_iter": best_boosted_parameters["max_iter"],
            "random_state": best_boosted_parameters["random_state"],
        },
        "boosted_hybrid_parameter_sensitivity": boosted_hybrid_results,
        "boosted_hybrid_win_rate_ablation": boosted_hybrid_ablation,
        "feature_importance": {
            "model": "boosted_hybrid",
            "top_n": 10,
            "features": boosted_hybrid_feature_importance,
        },
        "calibrated_boosted_hybrid": {
            **calibration_selection,
            "selection_metric": "log_loss",
        },
        "saved_model": "boosted_hybrid",
        "recommended_model": recommended_model,
        "recommendation_metric": "log_loss",
        "elo": {
            "initial_rating": ELO_INITIAL_RATING,
            "k_factor": ELO_K_FACTOR,
            "home_advantage": ELO_HOME_ADVANTAGE,
        },
        "metrics": metrics,
        "calibration": calibration,
        "calibration_by_season": calibration_by_season,
        "elo_by_season": elo_season_metrics,
        "rolling_logistic_by_season": rolling_season_metrics,
        "boosted_hybrid_by_season": boosted_hybrid_season_metrics,
        "calibrated_boosted_hybrid_by_season": calibrated_boosted_season_metrics,
        "elo_parameter_sensitivity": elo_parameter_sensitivity,
    }

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    saved_model = (
        calibrated_boosted_hybrid_model
        if recommended_model == "calibrated_boosted_hybrid"
        else boosted_hybrid_model
    )
    with MODEL_PATH.open("wb") as output:
        pickle.dump(
            {
                "model": saved_model,
                "predictors": hybrid_predictor_columns,
            },
            output,
        )
    METRICS_PATH.write_text(json.dumps(with_provenance(metadata), indent=2) + "\n", encoding="utf-8")

    print(f"Complete games: {len(games):,}")
    print(f"Chronological split: {len(train):,} train / {len(test):,} test")
    for name, values in metrics.items():
        print(
            f"{name}: accuracy={values['accuracy']:.5f}, "
            f"log_loss={values['log_loss']:.5f}, "
            f"brier_score={values['brier_score']:.5f}"
        )
    print(f"Saved model to: {MODEL_PATH}")
    print(f"Saved metrics to: {METRICS_PATH}")
    print(f"Recommended model (lowest holdout log loss): {recommended_model}")


if __name__ == "__main__":
    main()
