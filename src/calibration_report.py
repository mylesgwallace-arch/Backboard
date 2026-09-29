"""Reliability (calibration) table for the production model on its holdout.

When the model says 70%, does the home team win about 70% of the time? This
bins every holdout game (the chronological 20% the model never trained on, from
``split_start`` in ``models/baseline_metrics.json``) by predicted home-win
probability and compares the mean prediction with the observed win rate, with a
binomial 90% interval per bin. The web app draws it as a reliability chart.

    python src/calibration_report.py   # writes models/calibration_report.json
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from src.main import METRICS_PATH
    from src.provenance import with_provenance
    from src.simulate_season import load_pregame_probabilities
except ImportError:  # pragma: no cover - direct-script support
    from main import METRICS_PATH
    from provenance import with_provenance
    from simulate_season import load_pregame_probabilities

ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "models" / "calibration_report.json"
BIN_EDGES = [0.0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
Z90 = 1.6449


def reliability_table(probabilities, outcomes, edges=BIN_EDGES):
    """Per-bin count, mean predicted, observed rate and a 90% Wilson interval."""
    probabilities = np.asarray(probabilities, dtype=float)
    outcomes = np.asarray(outcomes, dtype=float)
    rows = []
    for low, high in zip(edges[:-1], edges[1:]):
        mask = (probabilities >= low) & ((probabilities < high) | (high == edges[-1]))
        n = int(mask.sum())
        if n == 0:
            continue
        observed = float(outcomes[mask].mean())
        denominator = 1 + Z90 ** 2 / n
        centre = (observed + Z90 ** 2 / (2 * n)) / denominator
        half = Z90 * np.sqrt(observed * (1 - observed) / n + Z90 ** 2 / (4 * n * n)) / denominator
        rows.append({
            "bin": [low, high], "games": n,
            "mean_predicted": float(probabilities[mask].mean()),
            "observed_rate": observed,
            "observed_90pct_interval": [float(centre - half), float(centre + half)],
        })
    weights = np.array([row["games"] for row in rows], dtype=float)
    gaps = np.array([abs(row["mean_predicted"] - row["observed_rate"]) for row in rows])
    return {"bins": rows,
            "expected_calibration_error": float((weights * gaps).sum() / weights.sum()),
            "games": int(weights.sum())}


def build_report():
    metrics = json.loads(Path(METRICS_PATH).read_text(encoding="utf-8"))
    split_start = pd.Timestamp(metrics["split_start"])
    games = load_pregame_probabilities()
    holdout = games[pd.to_datetime(games["gameDateTimeEst"]) >= split_start]
    report = {
        "model": "elo_boosted_ensemble",
        "holdout_start": str(split_start),
        "note": "ECE here uses the fixed bins above, so it can differ slightly from the "
                "10 equal-width bins in baseline_metrics.json.",
        "overall": reliability_table(holdout["home_win_probability"], holdout["target"]),
        "by_season": {
            str(int(season)): {
                key: value for key, value in
                reliability_table(group["home_win_probability"], group["target"]).items()
                if key != "bins"
            }
            for season, group in holdout.groupby("season") if len(group) >= 100
        },
    }
    return report


def main():
    report = build_report()
    REPORT_PATH.write_text(json.dumps(with_provenance(report), indent=2) + "\n", encoding="utf-8")
    for row in report["overall"]["bins"]:
        print(f"{row['bin'][0]:.1f}-{row['bin'][1]:.1f}: n={row['games']:5d} "
              f"predicted {row['mean_predicted']:.3f} observed {row['observed_rate']:.3f}")
    print(f"ECE {report['overall']['expected_calibration_error']:.4f} "
          f"on {report['overall']['games']} games")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
