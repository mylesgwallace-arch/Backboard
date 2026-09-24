"""Forward-looking season projection with team strength frozen at a cutoff date.

``simulate_season.project_season`` replays a season game by game with each
game's own pregame probability. Those probabilities are leakage-safe for the
game they describe, but they are built from the results of every earlier game
in the same season, so a "projection" made that way already knows how the
season went up to each game. That is fine for replay validation but it is not
what "project the rest of the season from today" means.

This module answers that question honestly:

* Games played before ``as_of`` are fixed at their actual results (the
  current win-loss record).
* Every remaining game gets the production ``elo_boosted_ensemble``
  probability computed exactly the way ``src.main.predict_matchup`` computes it
  with ``game_date=as_of``: each team's latest pregame feature row on or
  before the cutoff, and Elo ratings replayed from games strictly before it.
  No information from on/after the cutoff is used for any remaining game.
* The remaining schedule is sampled ``n_simulations`` times and added to the
  actual record, then summarized with the same standings/seed/playoff-field
  helpers the full-season simulator uses.

``as_of`` equal to the season's first game date gives a genuine preseason
projection (strength frozen at the end of the prior season). ``backtest``
measures how accurate these forward projections are against actual final
standings at several checkpoints of completed seasons.
"""

import argparse
import json
import pickle
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from src.main import FEATURES_PATH, METRICS_PATH, MODEL_PATH, load_elo_config
    from src.train_baseline_model import build_game_dataset, elo_win_probability
    from src.simulate_season import (
        DIRECT_PLAYOFF_SEEDS,
        add_season_labels,
        attach_team_names,
        build_league_summary,
        build_seedings_table,
        conference_of,
        load_team_names,
        summarize_team_wins,
    )
except ImportError:  # pragma: no cover - direct-script support
    from main import FEATURES_PATH, METRICS_PATH, MODEL_PATH, load_elo_config
    from train_baseline_model import build_game_dataset, elo_win_probability
    from simulate_season import (
        DIRECT_PLAYOFF_SEEDS,
        add_season_labels,
        attach_team_names,
        build_league_summary,
        build_seedings_table,
        conference_of,
        load_team_names,
        summarize_team_wins,
    )


ROOT = Path(__file__).resolve().parents[1]
BACKTEST_METRICS_PATH = ROOT / "models" / "forward_projection_backtest.json"
DEFAULT_SIMULATIONS = 1000
BACKTEST_SEASONS = [2022, 2023, 2024, 2025]
BACKTEST_CHECKPOINTS = [0.0, 0.25, 0.5, 0.75]
PRODUCTION_MODEL = "elo_boosted_ensemble"

# Team-strength uncertainty (log-odds SD of a per-team, per-simulation shock),
# chosen by mean CRPS of final wins on seasons 2015-2021 only
# (``calibrate_strength_sd``; grid 0.0-1.0). The evaluation seasons
# (``BACKTEST_SEASONS``) were never used to choose these values. Keyed by the
# share of the season's games already played; interpolated in between.
CALIBRATION_SEASONS = [2015, 2016, 2017, 2018, 2019, 2020, 2021]
STRENGTH_SD_BY_SEASON_FRACTION = {0.0: 0.6, 0.25: 0.4, 0.5: 0.4, 0.75: 0.4, 1.0: 0.4}


def default_strength_sd(fraction_completed):
    """Calibrated strength uncertainty for a cutoff ``fraction_completed`` in."""
    points = sorted(STRENGTH_SD_BY_SEASON_FRACTION)
    values = [STRENGTH_SD_BY_SEASON_FRACTION[point] for point in points]
    return float(np.interp(float(fraction_completed), points, values))


@dataclass
class ModelInputs:
    """Everything needed to score hypothetical games with the production model."""

    features: pd.DataFrame
    games: pd.DataFrame
    model: object
    predictors: list
    elo_config: dict


def load_model_inputs(features_path=FEATURES_PATH, model_path=MODEL_PATH,
                      metrics_path=METRICS_PATH):
    """Load the feature table, the paired game dataset and the frozen model."""
    features = pd.read_csv(features_path)
    features["gameDateTimeEst"] = pd.to_datetime(features["gameDateTimeEst"])
    games, _ = build_game_dataset(features)
    games["gameDateTimeEst"] = pd.to_datetime(games["gameDateTimeEst"])
    games = add_season_labels(games)
    with Path(model_path).open("rb") as handle:
        bundle = pickle.load(handle)
    return ModelInputs(
        features=features,
        games=games,
        model=bundle["model"],
        predictors=list(bundle["predictors"]),
        elo_config=load_elo_config(metrics_path),
    )


def elo_ratings_before(games, cutoff, elo_config):
    """Replay Elo over games strictly before ``cutoff`` (None = all games).

    Numerically identical to ``src.main.compute_elo_ratings_as_of`` (same
    update rule and order) but iterates plain arrays, so it is fast enough to
    call once per projection.
    """
    initial = float(elo_config["initial_rating"])
    k_factor = float(elo_config["k_factor"])
    home_advantage = float(elo_config["home_advantage"])
    if cutoff is not None:
        games = games[games["gameDateTimeEst"] < pd.Timestamp(cutoff)]
    ratings = {}
    homes = games["homeTeamId"].to_numpy()
    aways = games["awayTeamId"].to_numpy()
    outcomes = games["target"].to_numpy(dtype=float)
    for home, away, outcome in zip(homes, aways, outcomes):
        home_rating = ratings.get(home, initial)
        away_rating = ratings.get(away, initial)
        probability = elo_win_probability(home_rating, away_rating, home_advantage)
        ratings[home] = home_rating + k_factor * (outcome - probability)
        ratings[away] = away_rating + k_factor * ((1 - outcome) - (1 - probability))
    return {int(team): float(rating) for team, rating in ratings.items()}


def team_snapshots(features, cutoff):
    """Each team's latest pregame feature row on or before ``cutoff``.

    Mirrors ``src.main.lookup_last_team_row`` (inclusive ``<=`` cutoff),
    for every team at once.
    """
    rows = features
    if cutoff is not None:
        rows = rows[rows["gameDateTimeEst"] <= pd.Timestamp(cutoff)]
    rows = rows.sort_values(["gameDateTimeEst", "gameId"])
    latest = rows.groupby("teamId", sort=False).tail(1)
    return latest.set_index("teamId")


def frozen_matchup_probabilities(pairs, as_of, inputs, ratings=None, snapshots=None):
    """Production-model home-win probability for each (home, away) pair.

    ``pairs`` needs ``homeTeamId`` and ``awayTeamId`` columns. Strength is
    frozen at ``as_of`` exactly as ``predict_matchup(game_date=as_of)`` does:
    boosted-hybrid on home-minus-away snapshot deltas plus ``elo_delta``,
    averaged with the Elo probability. Returns a DataFrame with
    ``elo_probability``, ``boosted_probability`` and ``home_win_probability``.
    """
    pairs = pairs.reset_index(drop=True)
    if pairs.empty:
        return pairs.assign(
            elo_probability=[], boosted_probability=[], home_win_probability=[]
        )
    if ratings is None:
        ratings = elo_ratings_before(inputs.games, as_of, inputs.elo_config)
    if snapshots is None:
        snapshots = team_snapshots(inputs.features, as_of)
    home_ids = pairs["homeTeamId"].astype(int)
    away_ids = pairs["awayTeamId"].astype(int)
    missing = sorted(
        {int(team) for team in pd.concat([home_ids, away_ids])}
        - (set(ratings) & set(int(team) for team in snapshots.index))
    )
    if missing:
        raise ValueError(
            f"No pregame history on or before {as_of} for teamId(s) {missing}; "
            "cannot score their games."
        )

    home_advantage = float(inputs.elo_config["home_advantage"])
    home_ratings = home_ids.map(ratings).to_numpy(dtype=float)
    away_ratings = away_ids.map(ratings).to_numpy(dtype=float)
    elo_probability = elo_win_probability(home_ratings, away_ratings, home_advantage)

    non_elo = [column for column in inputs.predictors if column != "elo_delta"]
    home_rows = snapshots.loc[home_ids.to_numpy(), non_elo].to_numpy(dtype=float)
    away_rows = snapshots.loc[away_ids.to_numpy(), non_elo].to_numpy(dtype=float)
    frame = pd.DataFrame(home_rows - away_rows, columns=non_elo)
    if "elo_delta" in inputs.predictors:
        frame["elo_delta"] = home_ratings - away_ratings + home_advantage
    frame = frame[inputs.predictors]
    boosted_probability = inputs.model.predict_proba(frame)[:, 1]
    return pairs.assign(
        elo_probability=elo_probability,
        boosted_probability=boosted_probability,
        home_win_probability=(elo_probability + boosted_probability) / 2.0,
    )


def season_schedule(inputs, season):
    """All completed regular-season games of ``season`` in the model dataset."""
    schedule = inputs.games[inputs.games["season"] == int(season)]
    if schedule.empty:
        raise ValueError(f"No games found for season {season}.")
    return schedule.sort_values(["gameDateTimeEst", "gameId"]).reset_index(drop=True)


def _record_to_date(completed, teams):
    wins = {team: 0 for team in teams}
    losses = {team: 0 for team in teams}
    for home, away, target in zip(
        completed["homeTeamId"], completed["awayTeamId"], completed["target"]
    ):
        if int(target) == 1:
            wins[home] += 1
            losses[away] += 1
        else:
            wins[away] += 1
            losses[home] += 1
    return wins, losses


def _logit(probability):
    probability = np.clip(probability, 1e-6, 1 - 1e-6)
    return np.log(probability / (1 - probability))


def simulate_remaining_wins(base_wins, home_index, away_index, probabilities,
                            n_simulations, random_state, strength_sd=0.0,
                            shocks=None):
    """Sample the remaining schedule ``n_simulations`` times.

    ``strength_sd`` (log-odds scale) adds one persistent strength shock per
    team per simulation, shared by all of that team's remaining games. With
    ``0.0`` only game-to-game outcome noise is simulated, which treats the
    frozen strength estimate as exact; a positive value also represents
    uncertainty about how good each team really is. A caller that needs the
    same shocks later (the playoff simulation) can pass a precomputed
    ``shocks`` array of shape ``(n_simulations, n_teams)`` instead.
    """
    wins = np.tile(np.asarray(base_wins, dtype=int), (int(n_simulations), 1))
    if len(probabilities) == 0:
        return wins
    rng = np.random.default_rng(random_state)
    base_logit = _logit(np.asarray(probabilities, dtype=float))
    n_teams = wins.shape[1]
    for simulation in range(int(n_simulations)):
        if shocks is not None:
            team_shocks = shocks[simulation]
        elif strength_sd > 0:
            team_shocks = rng.normal(0.0, strength_sd, size=n_teams)
        else:
            team_shocks = None
        if team_shocks is not None:
            game_probability = 1.0 / (
                1.0 + np.exp(-(base_logit + team_shocks[home_index] - team_shocks[away_index]))
            )
        else:
            game_probability = probabilities
        home_wins = rng.random(len(probabilities)) < game_probability
        np.add.at(wins[simulation], home_index, home_wins)
        np.add.at(wins[simulation], away_index, ~home_wins)
    return wins


def project_from_date(season, as_of, inputs, n_simulations=DEFAULT_SIMULATIONS,
                      random_state=42, schedule=None, team_names=None,
                      strength_sd=None, return_samples=False, shocks=None):
    """Project final standings from the actual record at ``as_of``.

    ``schedule`` defaults to the season's games in the model dataset; a caller
    projecting a season that has not finished can pass its full schedule
    (``homeTeamId``, ``awayTeamId``, ``gameDateTimeEst`` and ``target``, with
    ``target`` NaN for unplayed games). ``strength_sd`` is passed to
    ``simulate_remaining_wins``; ``None`` uses the calibrated
    ``default_strength_sd`` for the share of games already played, ``0.0``
    simulates game-outcome noise only. With ``return_samples`` the result is
    ``(projection, wins_matrix, team_ids)``.
    """
    if schedule is None:
        schedule = season_schedule(inputs, season)
    schedule = schedule.copy()
    schedule["gameDateTimeEst"] = pd.to_datetime(schedule["gameDateTimeEst"])
    cutoff = pd.Timestamp(as_of)
    teams = sorted(
        int(team) for team in set(schedule["homeTeamId"]) | set(schedule["awayTeamId"])
    )
    played = schedule["gameDateTimeEst"] < cutoff
    if "target" in schedule and schedule.loc[played, "target"].isna().any():
        raise ValueError(
            "Some games before the cutoff have no recorded result; ingest the "
            "results or move the cutoff earlier."
        )
    completed = schedule[played]
    remaining = schedule[~played].reset_index(drop=True)
    fraction_completed = len(completed) / max(len(schedule), 1)
    if strength_sd is None:
        strength_sd = default_strength_sd(fraction_completed)

    current_wins, current_losses = _record_to_date(completed, teams)
    team_index = {team: index for index, team in enumerate(teams)}
    base = np.array([current_wins[team] for team in teams], dtype=int)

    if not remaining.empty:
        scored = frozen_matchup_probabilities(
            remaining[["homeTeamId", "awayTeamId"]], cutoff, inputs
        )
        probabilities = scored["home_win_probability"].to_numpy(dtype=float)
        home_index = remaining["homeTeamId"].map(team_index).to_numpy()
        away_index = remaining["awayTeamId"].map(team_index).to_numpy()
        mean_remaining_probability = float(probabilities.mean())
    else:
        probabilities = np.array([])
        home_index = away_index = np.array([], dtype=int)
        mean_remaining_probability = None
    wins = simulate_remaining_wins(
        base, home_index, away_index, probabilities, n_simulations,
        random_state, strength_sd=strength_sd, shocks=shocks,
    )

    summary = summarize_team_wins(wins, teams, schedule, season)
    games_scheduled = {team: 0 for team in teams}
    for team in pd.concat([schedule["homeTeamId"], schedule["awayTeamId"]]):
        games_scheduled[int(team)] += 1
    summary["current_wins"] = summary["teamId"].map(current_wins)
    summary["current_losses"] = summary["teamId"].map(current_losses)
    summary["games_remaining"] = summary["teamId"].map(
        lambda team: games_scheduled[team] - current_wins[team] - current_losses[team]
    )
    projection = {
        "season": int(season),
        "as_of": str(cutoff.date()),
        "mode": "forward_from_cutoff",
        "model": PRODUCTION_MODEL,
        "n_simulations": int(n_simulations),
        "random_state": int(random_state),
        "teams": int(len(teams)),
        "games_completed": int(len(completed)),
        "games_remaining": int(len(remaining)),
        "fraction_completed": float(fraction_completed),
        "mean_remaining_home_win_probability": mean_remaining_probability,
        "strength_sd": float(strength_sd),
        "projected_standings": summary.to_dict(orient="records"),
        "projected_seedings": build_seedings_table(summary),
        "league_summary": build_league_summary(summary),
    }
    projection = attach_team_names(projection, team_names)
    if return_samples:
        return projection, wins, teams
    return projection


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------

def _actual_final_wins(schedule):
    teams = sorted(set(schedule["homeTeamId"]) | set(schedule["awayTeamId"]))
    wins, _ = _record_to_date(schedule, teams)
    return {int(team): int(value) for team, value in wins.items()}


def _top_six(values_by_team):
    field = set()
    for conference in ("East", "West"):
        members = [team for team in values_by_team if conference_of(team) == conference]
        ranked = sorted(members, key=lambda team: (-values_by_team[team], team))
        field.update(ranked[:DIRECT_PLAYOFF_SEEDS])
    return field


def crps_from_samples(samples, observed):
    """Continuous ranked probability score of a sample ensemble (lower is better).

    ``samples`` is (n_samples, n_teams); ``observed`` is (n_teams,). Uses
    CRPS = E|X - y| - 0.5 E|X - X'| with the sorted-sample identity for the
    second term. It rewards a sharp distribution centred on the outcome and
    penalizes both bias and over/under-dispersion, so it is the criterion
    used to choose ``strength_sd``.
    """
    samples = np.sort(np.asarray(samples, dtype=float), axis=0)
    observed = np.asarray(observed, dtype=float)
    n = samples.shape[0]
    first = np.abs(samples - observed).mean(axis=0)
    weights = (2 * np.arange(1, n + 1) - n - 1) / (n * n)
    second = (weights[:, None] * samples).sum(axis=0)
    return first - second


def _checkpoint_cutoff(schedule, fraction):
    index = min(int(round(fraction * len(schedule))), len(schedule) - 1)
    return schedule.loc[index, "gameDateTimeEst"].normalize()


def backtest(inputs, seasons=None, checkpoints=None,
             n_simulations=DEFAULT_SIMULATIONS, random_state=42, strength_sd=None):
    """Score forward projections against actual final standings.

    For each season and checkpoint fraction ``f`` the cutoff is the date of
    the first game after ``f`` of the season's games were played. Reports
    win-total MAE for the model projection and two naive baselines
    (``pace``: current win% carried forward, 0.500 before any game;
    ``coin_flip``: every remaining game 50/50), plus direct-playoff-field
    overlap, the Brier score of the direct-playoff probabilities, the
    coverage of the 5th-95th percentile win range, and mean CRPS.
    """
    seasons = seasons or BACKTEST_SEASONS
    checkpoints = checkpoints or BACKTEST_CHECKPOINTS
    results = []
    for season in seasons:
        schedule = season_schedule(inputs, season)
        final_wins = _actual_final_wins(schedule)
        actual_field = _top_six(final_wins)
        for fraction in checkpoints:
            as_of = _checkpoint_cutoff(schedule, fraction)
            projection, samples, teams = project_from_date(
                season, as_of, inputs, n_simulations=n_simulations,
                random_state=random_state, schedule=schedule,
                strength_sd=strength_sd, return_samples=True,
            )
            crps = crps_from_samples(samples, [final_wins[team] for team in teams])
            rows = pd.DataFrame(projection["projected_standings"])
            rows["actual_wins"] = rows["teamId"].map(final_wins)
            played = rows["current_wins"] + rows["current_losses"]
            pace_rate = np.where(played > 0, rows["current_wins"] / played.clip(lower=1), 0.5)
            rows["pace_wins"] = rows["current_wins"] + rows["games_remaining"] * pace_rate
            rows["coin_flip_wins"] = rows["current_wins"] + 0.5 * rows["games_remaining"]
            projected_field = _top_six(dict(zip(rows["teamId"], rows["mean_wins"])))
            in_field = rows["teamId"].isin(actual_field).astype(float)
            results.append(
                {
                    "season": int(season),
                    "checkpoint": float(fraction),
                    "as_of": projection["as_of"],
                    "games_completed": projection["games_completed"],
                    "games_remaining": projection["games_remaining"],
                    "model_mae_wins": float((rows["mean_wins"] - rows["actual_wins"]).abs().mean()),
                    "pace_mae_wins": float((rows["pace_wins"] - rows["actual_wins"]).abs().mean()),
                    "coin_flip_mae_wins": float(
                        (rows["coin_flip_wins"] - rows["actual_wins"]).abs().mean()
                    ),
                    "model_rmse_wins": float(
                        np.sqrt(((rows["mean_wins"] - rows["actual_wins"]) ** 2).mean())
                    ),
                    "playoff_field_overlap_count": int(len(projected_field & actual_field)),
                    "direct_playoff_brier": float(
                        ((rows["direct_playoff_probability"] - in_field) ** 2).mean()
                    ),
                    "actual_within_p5_p95_share": float(
                        (
                            (rows["actual_wins"] >= rows["p5_wins"])
                            & (rows["actual_wins"] <= rows["p95_wins"])
                        ).mean()
                    ),
                    "mean_crps_wins": float(np.mean(crps)),
                }
            )
    by_checkpoint = {}
    frame = pd.DataFrame(results)
    for fraction, group in frame.groupby("checkpoint"):
        by_checkpoint[f"{fraction:.2f}"] = {
            "seasons": [int(season) for season in group["season"]],
            "model_mae_wins": float(group["model_mae_wins"].mean()),
            "pace_mae_wins": float(group["pace_mae_wins"].mean()),
            "coin_flip_mae_wins": float(group["coin_flip_mae_wins"].mean()),
            "playoff_field_overlap_mean": float(group["playoff_field_overlap_count"].mean()),
            "direct_playoff_brier": float(group["direct_playoff_brier"].mean()),
            "actual_within_p5_p95_share": float(group["actual_within_p5_p95_share"].mean()),
            "mean_crps_wins": float(group["mean_crps_wins"].mean()),
        }
    return {
        "model": PRODUCTION_MODEL,
        "n_simulations": int(n_simulations),
        "random_state": int(random_state),
        "strength_sd": (
            "calibrated default (see STRENGTH_SD_BY_SEASON_FRACTION)"
            if strength_sd is None else float(strength_sd)
        ),
        "seasons": [int(season) for season in seasons],
        "checkpoints": [float(fraction) for fraction in checkpoints],
        "results": results,
        "summary_by_checkpoint": by_checkpoint,
    }


def calibrate_strength_sd(inputs, seasons, checkpoints=None, grid=None,
                          n_simulations=DEFAULT_SIMULATIONS, random_state=42):
    """Pick ``strength_sd`` per checkpoint by mean CRPS on ``seasons``.

    Call it with seasons that are strictly earlier than the ones used to
    report accuracy, so the choice never sees the evaluation seasons.
    """
    checkpoints = checkpoints or BACKTEST_CHECKPOINTS
    grid = grid or [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6]
    table = {}
    for strength_sd in grid:
        report = backtest(inputs, seasons=seasons, checkpoints=checkpoints,
                          n_simulations=n_simulations, random_state=random_state,
                          strength_sd=strength_sd)
        for key, row in report["summary_by_checkpoint"].items():
            table.setdefault(key, {})[f"{strength_sd:.2f}"] = {
                "mean_crps_wins": row["mean_crps_wins"],
                "actual_within_p5_p95_share": row["actual_within_p5_p95_share"],
                "direct_playoff_brier": row["direct_playoff_brier"],
                "model_mae_wins": row["model_mae_wins"],
            }
    selected = {
        key: float(min(rows, key=lambda sd: rows[sd]["mean_crps_wins"]))
        for key, rows in table.items()
    }
    return {
        "calibration_seasons": [int(season) for season in seasons],
        "grid": [float(value) for value in grid],
        "criterion": "mean CRPS of simulated final wins (lower is better)",
        "by_checkpoint": table,
        "selected_strength_sd": selected,
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Project the rest of a season from the actual record on a cutoff "
            "date, with production-model team strength frozen at that date."
        )
    )
    parser.add_argument("--season", type=int, help="Season start year, e.g. 2024.")
    parser.add_argument("--as-of", type=str, help="Cutoff date YYYY-MM-DD.")
    parser.add_argument("--simulations", type=int, default=DEFAULT_SIMULATIONS)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--backtest", action="store_true",
                        help="Score forward projections for completed seasons and "
                             "write models/forward_projection_backtest.json.")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    inputs = load_model_inputs()
    if args.backtest:
        reports = {
            "calibrated_strength_uncertainty": backtest(
                inputs, n_simulations=args.simulations,
                random_state=args.random_state, strength_sd=None,
            ),
            "game_noise_only": backtest(
                inputs, n_simulations=args.simulations,
                random_state=args.random_state, strength_sd=0.0,
            ),
            "calibration_seasons": CALIBRATION_SEASONS,
            "strength_sd_by_season_fraction": {
                str(key): value for key, value in STRENGTH_SD_BY_SEASON_FRACTION.items()
            },
        }
        BACKTEST_METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
        BACKTEST_METRICS_PATH.write_text(json.dumps(reports, indent=2) + "\n",
                                         encoding="utf-8")
        for label in ("calibrated_strength_uncertainty", "game_noise_only"):
            print(label)
            for key, row in reports[label]["summary_by_checkpoint"].items():
                print(
                    f"  checkpoint {key}: model MAE {row['model_mae_wins']:.2f} | "
                    f"pace {row['pace_mae_wins']:.2f} | coin flip {row['coin_flip_mae_wins']:.2f} "
                    f"| playoff field {row['playoff_field_overlap_mean']:.1f}/12 "
                    f"| Brier {row['direct_playoff_brier']:.3f} "
                    f"| p5-p95 coverage {row['actual_within_p5_p95_share']:.0%} "
                    f"| CRPS {row['mean_crps_wins']:.2f}"
                )
        print(f"Saved backtest to {BACKTEST_METRICS_PATH}")
        return 0
    if args.season is None or args.as_of is None:
        raise SystemExit("Provide --season and --as-of, or --backtest.")
    projection = project_from_date(
        args.season, args.as_of, inputs, n_simulations=args.simulations,
        random_state=args.random_state, team_names=load_team_names(args.season),
    )
    print(json.dumps(projection, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
