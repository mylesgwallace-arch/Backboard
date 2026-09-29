"""Why the production model favors a team: feature-group ablation for one matchup.

The frozen ``elo_boosted_ensemble`` averages an Elo probability with a boosted
model over home-minus-away feature differences plus ``elo_delta``. For each
readable group of inputs this module asks: *if the two teams were even on
these inputs, how would the home-win probability change?* It sets that group's
differences to zero (for the Elo group, the rating gap to zero in both halves
of the ensemble while keeping home court) and re-scores. The shift is the
group's contribution to the prediction.

Contributions do not add up exactly to the total (the boosted model has
interactions); the remainder is reported. This explains the model; it says
nothing about what causes wins.
"""

import numpy as np
import pandas as pd

try:
    from src.forward_projection import elo_ratings_before, team_snapshots
    from src.train_baseline_model import elo_win_probability
except ImportError:  # pragma: no cover - direct-script support
    from forward_projection import elo_ratings_before, team_snapshots
    from train_baseline_model import elo_win_probability

GROUPS = {
    "Elo rating gap": ["elo_delta"],
    "Recent point margin (last 10 games)": [
        "plusMinusPoints_rolling_10", "teamScore_rolling_10", "opponentScore_rolling_10",
        "opponent_adjusted_plusMinusPoints_rolling_10",
    ],
    "Recent win rate (last 10 games)": [
        "win_rate_rolling_10", "opponent_adjusted_win_rate_rolling_10",
    ],
    "Shooting (last 10 games)": [
        "fieldGoalsPercentage_rolling_10", "threePointersPercentage_rolling_10",
        "freeThrowsPercentage_rolling_10",
    ],
    "Other box-score stats (last 10 games)": [
        "assists_rolling_10", "steals_rolling_10", "blocks_rolling_10", "reboundsTotal_rolling_10",
        "turnovers_rolling_10",
    ],
    "Players available and their production": [
        "active_players_rolling_10", "active_players_last_game", "player_minutes_rolling_10",
        "player_points_rolling_10", "player_points_per_minute_rolling_10",
        "player_assists_rolling_10", "player_rebounds_rolling_10",
    ],
    "Rest days": ["rest_days"],
}


def _score(inputs, frame, home_rating, away_rating, home_advantage):
    elo = float(elo_win_probability(home_rating, away_rating, home_advantage))
    boosted = float(inputs.model.predict_proba(frame[inputs.predictors])[:, 1][0])
    return (elo + boosted) / 2.0, elo, boosted


def explain_matchup(inputs, home_team_id, away_team_id, as_of=None):
    """Contribution of each input group to the home-win probability.

    ``as_of`` follows ``predict_matchup``'s ``game_date``: feature rows on or
    before it, Elo from games strictly before it (``None`` = latest data).
    """
    home_team_id, away_team_id = int(home_team_id), int(away_team_id)
    ratings = elo_ratings_before(inputs.games, as_of, inputs.elo_config)
    snapshots = team_snapshots(inputs.features, as_of)
    for team in (home_team_id, away_team_id):
        if team not in ratings or team not in snapshots.index:
            raise ValueError(f"No pregame history for teamId {team}.")
    home_advantage = float(inputs.elo_config["home_advantage"])
    home_rating, away_rating = ratings[home_team_id], ratings[away_team_id]
    non_elo = [column for column in inputs.predictors if column != "elo_delta"]
    home_row = snapshots.loc[home_team_id, non_elo].to_numpy(dtype=float)
    away_row = snapshots.loc[away_team_id, non_elo].to_numpy(dtype=float)
    frame = pd.DataFrame([home_row - away_row], columns=non_elo)
    frame["elo_delta"] = home_rating - away_rating + home_advantage

    probability, elo, boosted = _score(inputs, frame, home_rating, away_rating, home_advantage)
    contributions = []
    for label, columns in GROUPS.items():
        present = [column for column in columns if column in inputs.predictors]
        if not present:
            continue
        even = frame.copy()
        even_home, even_away = home_rating, away_rating
        for column in present:
            if column == "elo_delta":
                even[column] = home_advantage      # equal ratings, home court kept
                even_home = even_away = (home_rating + away_rating) / 2.0
            else:
                even[column] = 0.0
        if all(frame[column].isna().all() for column in present if column != "elo_delta") \
                and "elo_delta" not in present:
            continue  # the model imputes these; nothing to explain
        without, _, _ = _score(inputs, even, even_home, even_away, home_advantage)
        contributions.append({
            "group": label,
            "features": present,
            "contribution": probability - without,
            "probability_if_even": without,
            "favors": "home" if probability - without > 0 else "away",
        })
    contributions.sort(key=lambda row: -abs(row["contribution"]))

    neutral = frame.copy()
    neutral[non_elo] = 0.0
    neutral["elo_delta"] = home_advantage
    middle = (home_rating + away_rating) / 2.0
    home_court_only, _, _ = _score(inputs, neutral, middle, middle, home_advantage)
    explained = sum(row["contribution"] for row in contributions)
    return {
        "home_team_id": home_team_id,
        "away_team_id": away_team_id,
        "as_of": None if as_of is None else str(pd.Timestamp(as_of).date()),
        "home_win_probability": probability,
        "elo_probability": elo,
        "boosted_probability": boosted,
        "home_court_only_probability": home_court_only,
        "contributions": contributions,
        "interaction_remainder": (probability - home_court_only) - explained,
        "elo_ratings": {"home": round(home_rating, 1), "away": round(away_rating, 1)},
        "snapshot_dates": {
            "home": str(pd.Timestamp(snapshots.loc[home_team_id, "gameDateTimeEst"]).date()),
            "away": str(pd.Timestamp(snapshots.loc[away_team_id, "gameDateTimeEst"]).date()),
        },
        "method": "Group ablation: each group's home-minus-away differences set to zero "
                  "(teams even on it), everything else unchanged; contribution = actual "
                  "probability minus that. Contributions interact, so they do not sum exactly.",
    }


def top_raw_differences(inputs, home_team_id, away_team_id, as_of=None, limit=6):
    """Largest standardized home-minus-away input differences (for display)."""
    snapshots = team_snapshots(inputs.features, as_of)
    non_elo = [column for column in inputs.predictors if column != "elo_delta"]
    spread = inputs.features[non_elo].std(numeric_only=True).replace(0, np.nan)
    diff = (snapshots.loc[int(home_team_id), non_elo].astype(float)
            - snapshots.loc[int(away_team_id), non_elo].astype(float))
    z = (diff / spread).dropna()
    order = z.abs().sort_values(ascending=False).index[:limit]
    return [{"feature": feature, "home_minus_away": float(diff[feature]),
             "standardized": float(z[feature])} for feature in order]
