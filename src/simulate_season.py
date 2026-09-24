"""Monte Carlo season simulator built on the validated production model.

The simulator reuses the frozen production prediction engine
(``elo_boosted_ensemble``) to produce a leakage-safe pregame home-win
probability for every game in a season's schedule, then samples each game's
outcome many times to project each team's win-total distribution and playoff
odds.

Design rules:

* No player-impact or roster-change signal is injected; only the validated
  per-game probability model is used.
* Every probability is formed strictly from information available before that
  game (chronological Elo replay + pregame rolling features), so a season is
  simulated forward in time without leakage.
* Results are validation-driven: the same simulator can replay completed
  seasons (2023-2025) and compare projected win totals and playoff fields
  against actual outcomes.
"""

import argparse
import json
import pickle
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from src.train_baseline_model import (
        add_elo_rating_deltas,
        build_game_dataset,
    )
except ImportError:  # pragma: no cover - direct-script support
    from train_baseline_model import (
        add_elo_rating_deltas,
        build_game_dataset,
    )


ROOT = Path(__file__).resolve().parents[1]
FEATURES_PATH = ROOT / "data" / "processed" / "game_features.csv"
MODEL_PATH = ROOT / "models" / "baseline_logistic.pkl"
METRICS_PATH = ROOT / "models" / "baseline_metrics.json"
SIMULATION_METRICS_PATH = ROOT / "models" / "season_simulation_metrics.json"
TEAM_DB_PATH = ROOT / "data" / "database" / "nba.db"

VALIDATION_SEASONS = [2023, 2024, 2025]
DEFAULT_SEASON = 2025
DEFAULT_SIMULATIONS = 1000
DIRECT_PLAYOFF_SEEDS = 6

# Current-era (post-2004) Eastern Conference membership for the 30 active NBA
# franchises. This is stable factual team metadata, not a model output, and it
# is used only to turn per-simulation win totals into playoff-qualification
# probabilities for the modern NBA structure (top six per conference).
EASTERN_CONFERENCE_TEAM_IDS = {
    1610612737,  # Atlanta Hawks
    1610612738,  # Boston Celtics
    1610612739,  # Cleveland Cavaliers
    1610612741,  # Chicago Bulls
    1610612748,  # Miami Heat
    1610612749,  # Milwaukee Bucks
    1610612751,  # Brooklyn Nets
    1610612752,  # New York Knicks
    1610612753,  # Orlando Magic
    1610612754,  # Indiana Pacers
    1610612755,  # Philadelphia 76ers
    1610612761,  # Toronto Raptors
    1610612764,  # Washington Wizards
    1610612765,  # Detroit Pistons
    1610612766,  # Charlotte Hornets
}
WESTERN_CONFERENCE_TEAM_IDS = {
    1610612740,  # New Orleans Pelicans
    1610612742,  # Dallas Mavericks
    1610612743,  # Denver Nuggets
    1610612744,  # Golden State Warriors
    1610612745,  # Houston Rockets
    1610612746,  # Los Angeles Clippers
    1610612747,  # Los Angeles Lakers
    1610612750,  # Minnesota Timberwolves
    1610612756,  # Phoenix Suns
    1610612757,  # Portland Trail Blazers
    1610612758,  # Sacramento Kings
    1610612759,  # San Antonio Spurs
    1610612760,  # Oklahoma City Thunder
    1610612762,  # Utah Jazz
    1610612763,  # Memphis Grizzlies
}


def add_season_labels(games):
    """Label each game with the NBA season (start-year) it belongs to.

    Matches the feature-pipeline rule: a game is labeled by the calendar year
    of the season it starts in (games in October-December belong to that year;
    games in January-April belong to the prior year).
    """
    dates = pd.to_datetime(games["gameDateTimeEst"])
    games = games.copy()
    games["season"] = dates.dt.year - (dates.dt.month < 10).astype(int)
    return games


def load_pregame_probabilities(
    features_path=FEATURES_PATH,
    model_path=MODEL_PATH,
    metrics_path=METRICS_PATH,
):
    """Return per-game leakage-safe ensemble home-win probabilities.

    The boosted-hybrid probability comes from the frozen production model and
    the Elo probability comes from a single chronological replay that uses only
    pregame ratings, so ``home_win_probability`` is the same
    ``elo_boosted_ensemble`` output the prediction CLI serves, evaluated for
    every game in the dataset at once.
    """
    features = pd.read_csv(features_path)
    games, _ = build_game_dataset(features)
    games = add_elo_rating_deltas(games)

    with model_path.open("rb") as handle:
        bundle = pickle.load(handle)
    model = bundle["model"]
    predictors = bundle["predictors"]

    boosted_probability = model.predict_proba(games[predictors])[:, 1]
    games["boosted_probability"] = boosted_probability
    games["home_win_probability"] = (
        boosted_probability + games["elo_probability"].to_numpy()
    ) / 2.0
    games = add_season_labels(games)

    probability_columns = [
        "gameId",
        "gameDateTimeEst",
        "season",
        "homeTeamId",
        "awayTeamId",
        "target",
        "elo_probability",
        "boosted_probability",
        "home_win_probability",
    ]
    return games[probability_columns].reset_index(drop=True)


def simulate_season(
    probabilities,
    season,
    n_simulations=DEFAULT_SIMULATIONS,
    random_state=42,
):
    """Run a Monte Carlo simulation of one season's schedule.

    Returns ``(wins, teams)`` where ``wins`` is a boolean/int matrix of shape
    ``(n_simulations, len(teams))`` counting each team's simulated wins, and
    ``teams`` is the ordered teamId list matching the matrix columns.
    """
    season_games = probabilities[probabilities["season"] == season].copy()
    if season_games.empty:
        raise ValueError(f"No games found for season {season}.")

    teams = sorted(
        set(season_games["homeTeamId"]) | set(season_games["awayTeamId"])
    )
    team_index = {team_id: index for index, team_id in enumerate(teams)}
    home_indices = season_games["homeTeamId"].map(team_index).to_numpy()
    away_indices = season_games["awayTeamId"].map(team_index).to_numpy()
    probs = season_games["home_win_probability"].to_numpy(dtype=float)

    rng = np.random.default_rng(random_state)
    wins = np.zeros((n_simulations, len(teams)), dtype=int)
    for simulation in range(n_simulations):
        home_wins = rng.random(len(probs)) < probs
        np.add.at(wins[simulation], home_indices, home_wins)
        np.add.at(wins[simulation], away_indices, ~home_wins)
    return wins, teams


def load_team_names(season=None, db_path=TEAM_DB_PATH):
    """Return ``{teamId: {"teamName", "teamAbbreviation"}}`` from ``team_histories``.

    With a ``season`` (start year), each franchise gets the name it carried
    that season (e.g. 1610612760 is the Seattle SuperSonics in 2005 and the
    Oklahoma City Thunder in 2025). Without one, the current names are used.
    Returns an empty mapping when the database is unavailable, so callers
    degrade to ids only instead of failing.
    """
    try:
        with sqlite3.connect(db_path) as connection:
            if season is None:
                rows = connection.execute(
                    """
                    SELECT teamId, teamCity, teamName, teamAbbrev, seasonFounded
                    FROM team_histories
                    WHERE seasonActiveTill >= 2100
                    """
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT teamId, teamCity, teamName, teamAbbrev, seasonFounded
                    FROM team_histories
                    WHERE seasonFounded <= ? AND seasonActiveTill >= ?
                    """,
                    (int(season), int(season)),
                ).fetchall()
    except sqlite3.Error:
        return {}
    names = {}
    # If a franchise has overlapping rows, keep the most recently founded one.
    for team_id, city, name, abbrev, founded in sorted(rows, key=lambda row: row[4]):
        names[int(team_id)] = {
            "teamName": f"{city} {name}".strip(),
            "teamAbbreviation": (abbrev or "").strip(),
        }
    return names


def attach_team_names(projection, team_names):
    """Add ``teamName`` (and abbreviation) next to every ``teamId`` in a projection.

    Modifies and returns ``projection``. Teams missing from ``team_names``
    keep their numeric id only.
    """
    if not team_names:
        return projection

    def _label(row):
        info = team_names.get(int(row["teamId"]))
        if info:
            row["teamName"] = info["teamName"]
            row["teamAbbreviation"] = info["teamAbbreviation"]
        return row

    for row in projection.get("projected_standings", []):
        _label(row)
    for row in projection.get("projected_seedings", []):
        _label(row)
    league = projection.get("league_summary") or {}
    for key in ("best_team", "worst_team"):
        if isinstance(league.get(key), dict):
            _label(league[key])
    return projection


def conference_of(team_id):
    if team_id in EASTERN_CONFERENCE_TEAM_IDS:
        return "East"
    if team_id in WESTERN_CONFERENCE_TEAM_IDS:
        return "West"
    return "Unknown"


def summarize_team_wins(wins, teams, probabilities=None, season=None):
    """Build a per-team win-distribution summary for one simulated season.

    When conference ranks are available (the normal ``project_season`` path),
    each team also gets its projected conference seed (mean/median across
    simulations) and the probability of finishing at each exact seed slot
    ``p_seed_1`` .. ``p_seed_6`` plus ``out_of_playoffs_probability``.
    """
    ranks = (
        _conference_rank_matrix(wins, teams)
        if probabilities is not None and season is not None
        else None
    )
    rows = []
    for index, team_id in enumerate(teams):
        team_wins = wins[:, index]
        conference = conference_of(team_id)
        playoff_probability = None
        seed_probabilities = None
        mean_conference_seed = None
        median_conference_seed = None
        if ranks is not None:
            team_ranks = ranks[:, index]
            playoff_probability = float((team_ranks <= DIRECT_PLAYOFF_SEEDS).mean())
            seed_probabilities = {
                f"p_seed_{seed}": float((team_ranks == seed).mean())
                for seed in range(1, DIRECT_PLAYOFF_SEEDS + 1)
            }
            seed_probabilities["out_of_playoffs_probability"] = float(
                (team_ranks > DIRECT_PLAYOFF_SEEDS).mean()
            )
            mean_conference_seed = float(team_ranks.mean())
            median_conference_seed = float(np.median(team_ranks))
        row = {
            "teamId": int(team_id),
            "conference": conference,
            "mean_wins": float(team_wins.mean()),
            "median_wins": float(np.median(team_wins)),
            "p5_wins": float(np.percentile(team_wins, 5)),
            "p95_wins": float(np.percentile(team_wins, 95)),
            "direct_playoff_probability": playoff_probability,
            "mean_conference_seed": mean_conference_seed,
            "median_conference_seed": median_conference_seed,
        }
        if seed_probabilities is not None:
            row.update(seed_probabilities)
        rows.append(row)
    summary = pd.DataFrame(rows).sort_values(
        ["conference", "mean_wins"], ascending=[True, False]
    )
    return summary.reset_index(drop=True)


def _conference_rank_matrix(wins, teams):
    """Return per-simulation conference ranks for every team.

    Teams are ranked by simulated wins within their conference (descending);
    ties are broken by teamId so the ranking is deterministic and reproducible.
    """
    team_conference = np.array(
        [conference_of(team_id) for team_id in teams], dtype=object
    )
    ranks = np.zeros_like(wins, dtype=int)
    for conference in ("East", "West"):
        conference_indices = np.where(team_conference == conference)[0]
        conference_teams = [teams[index] for index in conference_indices]
        # Stable descending sort by wins, then ascending by teamId.
        order = np.argsort(
            -wins[:, conference_indices], axis=1, kind="stable"
        )
        conference_sorted_teams = np.array(conference_teams)[order]
        # The rank of team ``t`` in simulation ``s`` is its position in the
        # sorted list of that simulation's conference winners.
        position = np.argsort(conference_sorted_teams, axis=1, kind="stable")
        rank_matrix = position + 1
        ranks[:, conference_indices] = rank_matrix
    return ranks


def build_seedings_table(summary, n_seeds=DIRECT_PLAYOFF_SEEDS):
    """Return the most likely team for each conference seed slot.

    For each conference and each direct-playoff seed slot 1..n_seeds, find the
    team with the highest probability of finishing at exactly that seed. The
    table is the "projected playoff field": the expected occupants of the
    twelve direct-playoff seeds.
    """
    rows = []
    for conference in ("East", "West"):
        conference_teams = summary[summary["conference"] == conference]
        # A conference with fewer than n_seeds teams cannot fill every slot.
        n_seed_slots = min(n_seeds, len(conference_teams))
        for seed in range(1, n_seed_slots + 1):
            column = f"p_seed_{seed}"
            if column not in conference_teams.columns:
                continue
            best_index = conference_teams[column].idxmax()
            best = conference_teams.loc[best_index]
            rows.append(
                {
                    "conference": conference,
                    "seed": seed,
                    "teamId": int(best["teamId"]),
                    "probability": float(best[column]),
                }
            )
    return rows


def build_league_summary(summary):
    """Return league-wide aggregate strength statistics from a season summary."""
    mean_wins = summary["mean_wins"]
    conference_means = (
        summary.groupby("conference")["mean_wins"].mean().to_dict()
    )
    return {
        "n_teams": int(len(summary)),
        "league_mean_wins": float(mean_wins.mean()),
        "league_median_wins": float(mean_wins.median()),
        "best_team": {
            "teamId": int(summary.loc[mean_wins.idxmax(), "teamId"]),
            "mean_wins": float(mean_wins.max()),
        },
        "worst_team": {
            "teamId": int(summary.loc[mean_wins.idxmin(), "teamId"]),
            "mean_wins": float(mean_wins.min()),
        },
        "conference_mean_wins": {
            str(conference): float(value)
            for conference, value in conference_means.items()
        },
    }


def project_season(
    probabilities,
    season,
    n_simulations=DEFAULT_SIMULATIONS,
    random_state=42,
    team_names=None,
):
    """Return a full season projection: win distributions + playoff odds.

    ``team_names`` (from ``load_team_names``) is optional; when given, every
    ``teamId`` in the output also carries its ``teamName``.
    """
    wins, teams = simulate_season(
        probabilities, season, n_simulations=n_simulations, random_state=random_state
    )
    summary = summarize_team_wins(wins, teams, probabilities, season)
    projection = {
        "season": int(season),
        "n_simulations": int(n_simulations),
        "random_state": int(random_state),
        "teams": int(len(teams)),
        "projected_standings": summary.to_dict(orient="records"),
        "projected_seedings": build_seedings_table(summary),
        "league_summary": build_league_summary(summary),
    }
    return attach_team_names(projection, team_names)


def actual_season_standings(probabilities, season):
    """Compute each team's actual wins for a completed season."""
    season_games = probabilities[probabilities["season"] == season].copy()
    if season_games.empty:
        raise ValueError(f"No games found for season {season}.")

    all_teams = sorted(
        set(season_games["homeTeamId"]) | set(season_games["awayTeamId"])
    )
    home_wins = (
        season_games[season_games["target"] == 1]
        .groupby("homeTeamId")
        .size()
        .rename("wins")
    )
    away_wins = (
        season_games[season_games["target"] == 0]
        .groupby("awayTeamId")
        .size()
        .rename("wins")
    )
    wins = home_wins.add(away_wins, fill_value=0).astype(int)
    wins = wins.reindex(all_teams, fill_value=0)
    standings = wins.rename_axis("teamId").reset_index()
    standings["conference"] = standings["teamId"].map(conference_of)
    return standings.sort_values(
        ["conference", "wins"], ascending=[True, False]
    ).reset_index(drop=True)


def _actual_playoff_teams(standings, season):
    """Top-six-per-conference teams by actual wins (deterministic tie-break)."""
    playoff_teams = []
    for conference in ("East", "West"):
        conference_teams = standings[
            standings["conference"] == conference
        ].copy()
        conference_teams = conference_teams.sort_values(
            ["wins", "teamId"], ascending=[False, True]
        )
        playoff_teams.extend(
            conference_teams.head(DIRECT_PLAYOFF_SEEDS)["teamId"].tolist()
        )
    return set(playoff_teams)


def _projected_playoff_teams(projection, n_seeds=DIRECT_PLAYOFF_SEEDS):
    """Top-six-per-conference teams by mean projected wins (deterministic tie-break)."""
    standings = pd.DataFrame(projection["projected_standings"])
    playoff_teams = []
    for conference in ("East", "West"):
        conference_teams = standings[
            standings["conference"] == conference
        ].copy()
        conference_teams = conference_teams.sort_values(
            ["mean_wins", "teamId"], ascending=[False, True]
        )
        playoff_teams.extend(
            conference_teams.head(n_seeds)["teamId"].tolist()
        )
    return set(playoff_teams)


def validate_season(
    probabilities,
    season,
    n_simulations=DEFAULT_SIMULATIONS,
    random_state=42,
):
    """Compare one simulated season against its actual outcome."""
    projection = project_season(
        probabilities, season, n_simulations=n_simulations, random_state=random_state
    )
    actual = actual_season_standings(probabilities, season)

    projected = pd.DataFrame(projection["projected_standings"])
    merged = projected.merge(
        actual[["teamId", "wins"]], on="teamId", how="inner"
    )
    win_error = (merged["mean_wins"] - merged["wins"]).abs()
    mean_absolute_error = float(win_error.mean())
    correlation = float(
        np.corrcoef(merged["mean_wins"], merged["wins"])[0, 1]
        if len(merged) > 2
        else np.nan
    )

    actual_playoff = _actual_playoff_teams(actual, season)
    projected_playoff = _projected_playoff_teams(projection)
    overlap = len(actual_playoff & projected_playoff)
    playoff_field_overlap = overlap / max(len(actual_playoff), 1)

    return {
        "season": int(season),
        "n_simulations": int(n_simulations),
        "teams": int(len(merged)),
        "mean_absolute_error_wins": mean_absolute_error,
        "projected_vs_actual_correlation": correlation,
        "actual_playoff_teams": sorted(actual_playoff),
        "projected_playoff_teams": sorted(projected_playoff),
        "playoff_field_overlap": playoff_field_overlap,
        "playoff_field_overlap_count": int(overlap),
    }


def validate_seasons(
    probabilities,
    seasons=None,
    n_simulations=DEFAULT_SIMULATIONS,
    random_state=42,
):
    if seasons is None:
        seasons = VALIDATION_SEASONS
    results = {}
    for season in seasons:
        results[str(season)] = validate_season(
            probabilities,
            season,
            n_simulations=n_simulations,
            random_state=random_state,
        )
    return results


def write_metrics(metrics, path=SIMULATION_METRICS_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Run a leakage-safe Monte Carlo season simulation using the "
            "validated production prediction model."
        )
    )
    parser.add_argument(
        "--season",
        type=int,
        default=DEFAULT_SEASON,
        help="NBA season (start year) to simulate. Default: most recent complete season.",
    )
    parser.add_argument(
        "--simulations",
        type=int,
        default=DEFAULT_SIMULATIONS,
        help="Number of Monte Carlo simulations to run.",
    )
    parser.add_argument(
        "--random-state",
        type=int,
        default=42,
        help="Random seed for reproducible simulations.",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help=(
            "Replay completed seasons (2023, 2024, 2025) and compare projected "
            "win totals and playoff fields against actual outcomes."
        ),
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    probabilities = load_pregame_probabilities()
    result = {"model": "elo_boosted_ensemble", "leakage_safe": True}
    if args.validate:
        validation = validate_seasons(
            probabilities,
            n_simulations=args.simulations,
            random_state=args.random_state,
        )
        result["validation"] = validation
        for season, metrics in validation.items():
            print(
                f"Season {season}: MAE(wins)={metrics['mean_absolute_error_wins']:.2f}, "
                f"correlation={metrics['projected_vs_actual_correlation']:.3f}, "
                f"playoff field overlap={metrics['playoff_field_overlap']:.2f} "
                f"({metrics['playoff_field_overlap_count']}/12)"
            )
    projection = project_season(
        probabilities,
        args.season,
        n_simulations=args.simulations,
        random_state=args.random_state,
        team_names=load_team_names(args.season),
    )
    result["projection"] = projection
    write_metrics(result)
    print(f"Projected season {projection['season']} standings "
          f"({projection['n_simulations']} simulations):")
    for row in projection["projected_standings"]:
        playoff = (
            f"  playoff={row['direct_playoff_probability']:.1%}"
            if row["direct_playoff_probability"] is not None
            else ""
        )
        print(
            f"  {row['conference']:4s} {row.get('teamName', row['teamId'])} "
            f"mean={row['mean_wins']:.1f} "
            f"[{row['p5_wins']:.0f}-{row['p95_wins']:.0f}]{playoff}"
        )
    print("Projected direct-playoff field (most likely team per seed):")
    for slot in projection["projected_seedings"]:
        print(
            f"  {slot['conference']:4s} seed {slot['seed']}: "
            f"{slot.get('teamName', slot['teamId'])} ({slot['probability']:.1%})"
        )
    print(f"Saved simulation metrics to {SIMULATION_METRICS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())