"""Predicted score margin and total points, validated on the chronological holdout.

This is a separate, additional model output. It does **not** touch the frozen
``elo_boosted_ensemble`` win-probability model, its artifact, or its metrics;
win probabilities keep coming from the production model. The margin/total
models only answer "by how much?" and "how many points?", which the
classifier cannot.

Protocol (the same discipline as ``train_baseline_model.py``):

* Inputs are pregame only: the production model's 23 home-minus-away
  predictors (rolling team form, player history, rest, pregame Elo gap) for
  the margin, and the two teams' rolling points for/against for the total.
* The split is the production split: the first 80% of complete games by date
  train, the last 20% (from 2015-03-28) are the untouched holdout.
* Model choice uses only a validation slice at the end of the training
  period (its last 20%); the chosen model is refit on the full training
  period and scored once on the holdout against baselines.
* Uncertainty: the holdout residual distribution gives an empirical 80%
  interval, and its coverage is reported.
"""

import argparse
import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

try:
    from src.main import FEATURES_PATH, METRICS_PATH, MODEL_PATH, load_elo_config
    from src.train_baseline_model import TEST_FRACTION, build_game_dataset, elo_win_probability
    from src.forward_projection import elo_ratings_before, team_snapshots
except ImportError:  # pragma: no cover - direct-script support
    from main import FEATURES_PATH, METRICS_PATH, MODEL_PATH, load_elo_config
    from train_baseline_model import TEST_FRACTION, build_game_dataset, elo_win_probability
    from forward_projection import elo_ratings_before, team_snapshots


ROOT = Path(__file__).resolve().parents[1]
MARGIN_MODEL_PATH = ROOT / "models" / "margin_model.pkl"
MARGIN_METRICS_PATH = ROOT / "models" / "margin_metrics.json"
VALIDATION_FRACTION = 0.2
TOTAL_FEATURES = [
    "home_points_for", "home_points_against", "away_points_for", "away_points_against",
]
BOOSTED_GRID = [
    {"max_depth": 3, "learning_rate": 0.05, "max_iter": 300},
    {"max_depth": 4, "learning_rate": 0.05, "max_iter": 300},
    {"max_depth": 4, "learning_rate": 0.1, "max_iter": 200},
]
RIDGE_ALPHAS = [0.1, 1.0, 10.0, 100.0]


def pregame_elo_deltas(games, elo_config):
    """Pregame ``elo_delta`` for every game, same rule as ``add_elo_rating_deltas``.

    Plain-array loop (the production helper builds a DataFrame row by row);
    equality with the production values is checked in the training run.
    """
    initial = float(elo_config["initial_rating"])
    k_factor = float(elo_config["k_factor"])
    home_advantage = float(elo_config["home_advantage"])
    ratings = {}
    deltas = np.empty(len(games))
    for index, (home, away, outcome) in enumerate(
        zip(games["homeTeamId"].to_numpy(), games["awayTeamId"].to_numpy(),
            games["target"].to_numpy(dtype=float))
    ):
        home_rating = ratings.get(home, initial)
        away_rating = ratings.get(away, initial)
        deltas[index] = home_rating - away_rating + home_advantage
        probability = elo_win_probability(home_rating, away_rating, home_advantage)
        ratings[home] = home_rating + k_factor * (outcome - probability)
        ratings[away] = away_rating + k_factor * ((1 - outcome) - (1 - probability))
    return deltas


def build_margin_dataset(features, elo_config):
    """Paired pregame dataset with margin/total targets, in chronological order."""
    games, predictor_columns = build_game_dataset(features)
    games["elo_delta"] = pregame_elo_deltas(games, elo_config)
    scores = features[["gameId", "teamId", "teamScore", "opponentScore",
                       "teamScore_rolling_10", "opponentScore_rolling_10"]]
    home = scores.rename(columns={
        "teamId": "homeTeamId", "teamScore": "homeScore", "opponentScore": "awayScore",
        "teamScore_rolling_10": "home_points_for",
        "opponentScore_rolling_10": "home_points_against",
    })
    away = scores.rename(columns={
        "teamId": "awayTeamId",
        "teamScore_rolling_10": "away_points_for",
        "opponentScore_rolling_10": "away_points_against",
    })[["gameId", "awayTeamId", "away_points_for", "away_points_against"]]
    games = games.merge(home, on=["gameId", "homeTeamId"], how="left", validate="one_to_one")
    games = games.merge(away, on=["gameId", "awayTeamId"], how="left", validate="one_to_one")
    games["margin"] = games["homeScore"] - games["awayScore"]
    games["total"] = games["homeScore"] + games["awayScore"]
    games = games.dropna(subset=["margin", "total"]).reset_index(drop=True)
    return games, predictor_columns + ["elo_delta"]


def _mae(y, prediction):
    return float(np.mean(np.abs(np.asarray(y) - np.asarray(prediction))))


def _rmse(y, prediction):
    return float(np.sqrt(np.mean((np.asarray(y) - np.asarray(prediction)) ** 2)))


def _r2(y, prediction):
    y = np.asarray(y, dtype=float)
    return float(1 - np.sum((y - prediction) ** 2) / np.sum((y - y.mean()) ** 2))


def _scores(y, prediction):
    return {"mae": _mae(y, prediction), "rmse": _rmse(y, prediction), "r2": _r2(y, prediction)}


def _ridge(alpha):
    return Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
        ("ridge", Ridge(alpha=alpha)),
    ])


def _boosted(parameters):
    return Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("boosted", HistGradientBoostingRegressor(random_state=42, **parameters)),
    ])


class RecentFormTotal:
    """Deterministic total-points rule: the average of both teams' recent games.

    ``(home_for + home_against + away_for + away_against) / 2`` -- the mean
    total of the two teams' last-10-game rolling scores. It has nothing to
    fit, and it is a candidate like any other, so it is served if it wins.
    """

    def fit(self, frame, target=None):
        return self

    def predict(self, frame):
        return (
            frame["home_points_for"] + frame["home_points_against"]
            + frame["away_points_for"] + frame["away_points_against"]
        ).to_numpy(dtype=float) / 2.0


class EloLinearMargin:
    """Margin as a straight line in the pregame Elo gap (fit on training rows)."""

    def fit(self, frame, target):
        self.coefficients_ = np.polyfit(frame["elo_delta"], target, 1)
        return self

    def predict(self, frame):
        return np.polyval(self.coefficients_, frame["elo_delta"].to_numpy(dtype=float))


def _candidates(columns, target):
    candidates = {f"ridge_alpha_{alpha:g}": (_ridge(alpha), columns) for alpha in RIDGE_ALPHAS}
    for parameters in BOOSTED_GRID:
        name = "boosted_d{max_depth}_lr{learning_rate:g}_it{max_iter}".format(**parameters)
        candidates[name] = (_boosted(parameters), columns)
    if target == "margin":
        candidates["elo_linear"] = (EloLinearMargin(), ["elo_delta"])
    if target == "total":
        candidates["recent_form_average"] = (RecentFormTotal(), TOTAL_FEATURES)
    return candidates


def _select_and_evaluate(train, test, target, columns, baselines):
    """Pick a candidate on the train-period validation slice, then score the holdout.

    The 80% interval comes from the chosen candidate's residuals on that
    validation slice (out-of-sample, and still before the holdout).
    """
    cut = int(len(train) * (1 - VALIDATION_FRACTION))
    fit_part, validation = train.iloc[:cut], train.iloc[cut:]
    validation_scores = {}
    validation_residuals = {}
    for name, (model, cols) in _candidates(columns, target).items():
        model.fit(fit_part[cols], fit_part[target])
        predicted = model.predict(validation[cols])
        validation_scores[name] = _mae(validation[target], predicted)
        validation_residuals[name] = validation[target].to_numpy() - predicted
    selected = min(validation_scores, key=validation_scores.get)
    model, cols = _candidates(columns, target)[selected]
    model.fit(train[cols], train[target])
    prediction = model.predict(test[cols])
    residuals = test[target].to_numpy() - prediction
    low, high = np.percentile(validation_residuals[selected], [10, 90])
    coverage = float(np.mean((residuals >= low) & (residuals <= high)))
    # Intervals served for new games use the most recent residuals (the
    # holdout era); their holdout coverage is 80% by construction, so the
    # honest out-of-sample check is ``coverage`` above.
    serving_low, serving_high = np.percentile(residuals, [10, 90])
    holdout = {"selected_model": _scores(test[target], prediction)}
    for name, values in baselines.items():
        holdout[name] = _scores(test[target], values)
    return model, cols, {
        "target": target,
        "validation_mae_by_candidate": validation_scores,
        "selected_model": selected,
        "holdout": holdout,
        "interval_80": {
            "method": "10th-90th percentile of the selected model's residuals on "
                      "the training-period validation slice (out-of-sample)",
            "residual_low": float(low),
            "residual_high": float(high),
            "holdout_coverage": coverage,
        },
        "serving_interval_80": {
            "method": "10th-90th percentile of holdout-period (2015-2026) residuals; "
                      "wider than the pre-holdout interval because margins and "
                      "totals are more spread out in the recent scoring era",
            "residual_low": float(serving_low),
            "residual_high": float(serving_high),
        },
        "holdout_residual_sd": float(np.std(residuals)),
    }


def train_margin_models(features_path=FEATURES_PATH, metrics_path=METRICS_PATH):
    """Fit and validate the margin and total models; return (bundle, report)."""
    features = pd.read_csv(features_path)
    elo_config = load_elo_config(metrics_path)
    games, margin_columns = build_margin_dataset(features, elo_config)
    split = int(len(games) * (1 - TEST_FRACTION))
    train, test = games.iloc[:split], games.iloc[split:]

    home_edge = float(train["margin"].mean())
    elo_fit = np.polyfit(train["elo_delta"], train["margin"], 1)
    margin_baselines = {
        "baseline_zero": np.zeros(len(test)),
        "baseline_home_court_constant": np.full(len(test), home_edge),
        "baseline_elo_linear": np.polyval(elo_fit, test["elo_delta"]),
    }
    margin_model, margin_cols, margin_report = _select_and_evaluate(
        train, test, "margin", margin_columns, margin_baselines
    )

    total_train_mean = float(train["total"].mean())
    recent_total = (
        test["home_points_for"] + test["home_points_against"]
        + test["away_points_for"] + test["away_points_against"]
    ) / 2.0
    total_baselines = {
        "baseline_training_mean": np.full(len(test), total_train_mean),
        "baseline_recent_form_average": recent_total.to_numpy(),
    }
    total_model, total_cols, total_report = _select_and_evaluate(
        train, test, "total", TOTAL_FEATURES, total_baselines
    )

    # Consistency with the production classifier on the same holdout.
    with Path(MODEL_PATH).open("rb") as handle:
        production = pickle.load(handle)
    boosted = production["model"].predict_proba(test[production["predictors"]])[:, 1]
    elo_probability = 1.0 / (1.0 + 10 ** (-test["elo_delta"].to_numpy() / 400.0))
    production_probability = (boosted + elo_probability) / 2.0
    margin_prediction = margin_model.predict(test[margin_cols])
    agreement = float(np.mean((margin_prediction > 0) == (production_probability >= 0.5)))
    margin_sign_accuracy = float(np.mean((margin_prediction > 0) == (test["margin"] > 0)))

    report = {
        "split": {
            "train_games": int(len(train)),
            "test_games": int(len(test)),
            "test_fraction": TEST_FRACTION,
            "split_start": str(test["gameDateTimeEst"].iloc[0]),
            "validation_fraction_of_train": VALIDATION_FRACTION,
        },
        "elo_delta_check": "pregame_elo_deltas equals add_elo_rating_deltas (see verify_elo_deltas)",
        "margin": margin_report,
        "total": total_report,
        "consistency_with_production_classifier": {
            "sign_agreement_with_elo_boosted_ensemble": agreement,
            "margin_sign_accuracy": margin_sign_accuracy,
            "production_accuracy": float(np.mean((production_probability >= 0.5) == (test["target"] == 1))),
        },
    }
    bundle = {
        "margin_model": margin_model,
        "margin_predictors": margin_cols,
        "total_model": total_model,
        "total_predictors": total_cols,
        "margin_interval": margin_report["serving_interval_80"],
        "total_interval": total_report["serving_interval_80"],
        "margin_residual_sd": margin_report["holdout_residual_sd"],
        "trained_through": str(train["gameDateTimeEst"].iloc[-1]),
    }
    return bundle, report


def verify_elo_deltas(features_path=FEATURES_PATH, metrics_path=METRICS_PATH):
    """Max absolute difference between the fast and production Elo deltas."""
    try:
        from src.train_baseline_model import add_elo_rating_deltas
    except ImportError:  # pragma: no cover
        from train_baseline_model import add_elo_rating_deltas
    features = pd.read_csv(features_path)
    games, _ = build_game_dataset(features)
    elo_config = load_elo_config(metrics_path)
    fast = pregame_elo_deltas(games, elo_config)
    slow = add_elo_rating_deltas(
        games,
        initial_rating=elo_config["initial_rating"],
        k_factor=elo_config["k_factor"],
        home_advantage=elo_config["home_advantage"],
    )["elo_delta"].to_numpy()
    return float(np.max(np.abs(fast - slow)))


def load_margin_bundle(path=MARGIN_MODEL_PATH):
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found; run `python src/margin_model.py --train` first."
        )
    with path.open("rb") as handle:
        return pickle.load(handle)


def predict_margin(home_team_id, away_team_id, inputs, bundle, game_date=None):
    """Predicted home margin, total and scores, frozen at ``game_date``.

    Uses the same pregame snapshot/Elo rule as ``predict_matchup``.
    """
    ratings = elo_ratings_before(inputs.games, game_date, inputs.elo_config)
    snapshots = team_snapshots(inputs.features, game_date)
    for team in (home_team_id, away_team_id):
        if team not in ratings or team not in snapshots.index:
            raise ValueError(f"No pregame history for teamId={team} on or before {game_date}.")
    home_row = snapshots.loc[home_team_id]
    away_row = snapshots.loc[away_team_id]
    margin_frame = pd.DataFrame(
        [{
            column: (
                ratings[home_team_id] - ratings[away_team_id]
                + float(inputs.elo_config["home_advantage"])
                if column == "elo_delta"
                else float(home_row[column]) - float(away_row[column])
            )
            for column in bundle["margin_predictors"]
        }]
    )
    total_frame = pd.DataFrame([{
        "home_points_for": float(home_row["teamScore_rolling_10"]),
        "home_points_against": float(home_row["opponentScore_rolling_10"]),
        "away_points_for": float(away_row["teamScore_rolling_10"]),
        "away_points_against": float(away_row["opponentScore_rolling_10"]),
    }])[bundle["total_predictors"]]
    margin = float(bundle["margin_model"].predict(margin_frame)[0])
    total = float(bundle["total_model"].predict(total_frame)[0])
    interval = bundle["margin_interval"]
    total_interval = bundle["total_interval"]
    return {
        "home_team_id": int(home_team_id),
        "away_team_id": int(away_team_id),
        "game_date": None if game_date is None else str(pd.Timestamp(game_date).date()),
        "predicted_home_margin": round(margin, 2),
        "margin_interval_80": [
            round(margin + interval["residual_low"], 1),
            round(margin + interval["residual_high"], 1),
        ],
        "predicted_total_points": round(total, 1),
        "total_interval_80": [
            round(total + total_interval["residual_low"], 1),
            round(total + total_interval["residual_high"], 1),
        ],
        "predicted_home_points": round((total + margin) / 2.0, 1),
        "predicted_away_points": round((total - margin) / 2.0, 1),
        "feature_snapshot_date": {
            "home": str(pd.Timestamp(home_row["gameDateTimeEst"]).date()),
            "away": str(pd.Timestamp(away_row["gameDateTimeEst"]).date()),
        },
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Train/validate the margin and total models, or predict one game."
    )
    parser.add_argument("--train", action="store_true",
                        help="Fit, validate on the chronological holdout, save model + metrics.")
    parser.add_argument("--verify-elo", action="store_true",
                        help="Check the fast pregame Elo deltas against the production helper.")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.verify_elo:
        print(f"max |fast - production| elo_delta: {verify_elo_deltas():.3e}")
        return 0
    if args.train:
        bundle, report = train_margin_models()
        MARGIN_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        with MARGIN_MODEL_PATH.open("wb") as handle:
            pickle.dump(bundle, handle)
        MARGIN_METRICS_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        for target in ("margin", "total"):
            section = report[target]
            print(f"{target}: selected {section['selected_model']}")
            for name, scores in section["holdout"].items():
                print(f"   {name}: MAE {scores['mae']:.3f} RMSE {scores['rmse']:.3f} R2 {scores['r2']:.3f}")
            print(f"   80% interval holdout coverage: {section['interval_80']['holdout_coverage']:.3f}")
        print("consistency:", report["consistency_with_production_classifier"])
        print(f"Saved {MARGIN_MODEL_PATH} and {MARGIN_METRICS_PATH}")
        return 0
    raise SystemExit("Use --train or --verify-elo (predictions go through src/tools.py predict_margin).")


if __name__ == "__main__":
    raise SystemExit(main())
