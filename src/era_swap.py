"""Cross-era hypothetical roster swaps ("the 1992-93 Bulls with 2015-16 Curry").

This is a WHAT-IF simulation, not a factual or causal claim. Every step is an
explicit, testable assumption, and the uncertainty that each step adds is
measured wherever the data allows it.

Pipeline
--------
1. **Player-season rates** (``build_player_seasons``). Regular-season box
   scores are summed per player, team and season, and turned into per-100-
   possession rates using that team's pace. Pace is estimated from team box
   scores as ``FGA - OREB + TOV + 0.44 FTA``, averaged over the team and its
   opponents. Team box scores are complete only from 1985-86, so both the
   host team-season and the incoming player's season must be 1985-86 or
   later.
2. **Era translation** (``translate_rates``). Each rate or percentage is
   placed within its own season's league distribution: the minutes-weighted
   mean and SD over players with at least ``QUALIFY_MINUTES`` minutes. It is
   then re-expressed in the target season's distribution (``zscore``: same
   number of SDs from the mean). A ``ratio`` alternative (same multiple of
   the league mean; percentages shifted additively) is reported as a
   sensitivity check. Translating a season into itself returns it unchanged,
   and a round trip A -> B -> A is exact; both are tested.
3. **Box-score team-strength model** (``fit_team_model``). A player's value
   is a vector of league-relative per-100 features: scoring above
   league-average efficiency, shot volume, assists, turnovers, rebounds,
   steals, blocks, fouls and three-point volume. A team's composite is the
   minutes-weighted mean of its players' vectors. A ridge regression maps
   the composite to team net rating per 100 possessions. It is fit on
   1985-2009 and scored on 2010-2025 (chronological).
4. **Does player value transfer to a new team?** (``validate_transfer``).
   Each team's net rating in season s is predicted from its players'
   season s-1 vectors, translated into season s, weighted by their actual
   season-s minutes. Minutes are therefore an oracle input; that is stated.
   The prediction is compared with the team's own previous net rating,
   overall and for the highest-roster-turnover teams. This is the evidence
   for (or against) the swap assumption. It is measured within adjacent
   seasons only, because cross-era transfer has no ground truth.
5. **The swap** (``simulate_swap``). The removed player's minutes share goes
   to the incoming player's translated vector. The change in team net rating
   becomes a per-game margin change at the host team's pace, and then a
   shift in each game's win probability: ``p' = Phi(Phi^-1(p) + dm / sigma)``,
   where ``sigma`` is set so that an average team gains the empirical
   wins-per-net-rating-point slope measured across all team-seasons. The
   host's real season (the production model's per-game probabilities) is
   re-simulated with that shift, with the same random seed as the unchanged
   season, then the era-appropriate playoff format is played. Everything is
   reported as a delta versus the unchanged team, with intervals that
   combine simulation noise and the model's measured transfer error.
"""

import argparse
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from src.main import TEAM_DB_PATH
except ImportError:  # pragma: no cover - direct-script support
    from main import TEAM_DB_PATH


ROOT = Path(__file__).resolve().parents[1]
PLAYER_SEASONS_PATH = ROOT / "data" / "processed" / "player_team_seasons.csv"
TEAM_SEASONS_PATH = ROOT / "data" / "processed" / "team_seasons.csv"
ERA_MODEL_PATH = ROOT / "models" / "era_swap_model.json"
FIRST_SEASON = 1985          # first season with complete team box scores
QUALIFY_MINUTES = 500        # league distributions use these players
TRAIN_LAST_SEASON = 2009     # team model: fit <= 2009, evaluate >= 2010
RATE_STATS = ["pts", "tsa", "ast", "tov", "orb", "drb", "stl", "blk", "pf", "tpa"]
PCT_STATS = ["ts", "tp_pct", "ft_pct"]
FEATURES = [
    "scoring_value", "usage", "ast", "tov", "orb", "drb", "stl", "blk", "pf", "tpa",
]


# ---------------------------------------------------------------------------
# 1. Player-season rates
# ---------------------------------------------------------------------------

SEASON_SQL = (
    "(CAST(substr({col},1,4) AS INTEGER) - (CAST(substr({col},6,2) AS INTEGER) < 9))"
)


def build_team_seasons(db_path=TEAM_DB_PATH):
    """Team-season totals, pace (possessions per 48 min) and net rating per 100."""
    season = SEASON_SQL.format(col="t.gameDateTimeEst")
    with sqlite3.connect(db_path) as connection:
        frame = pd.read_sql_query(
            f"""
            SELECT t.teamId AS teamId, {season} AS season, COUNT(*) AS games,
                   SUM(t.teamScore) AS pts_for, SUM(t.opponentScore) AS pts_against,
                   SUM(t.win) AS wins,
                   SUM(t.fieldGoalsAttempted) AS fga, SUM(t.freeThrowsAttempted) AS fta,
                   SUM(t.reboundsOffensive) AS orb, SUM(t.turnovers) AS tov,
                   SUM(o.fieldGoalsAttempted) AS opp_fga, SUM(o.freeThrowsAttempted) AS opp_fta,
                   SUM(o.reboundsOffensive) AS opp_orb, SUM(o.turnovers) AS opp_tov,
                   SUM(CASE WHEN CAST(t.numMinutes AS REAL) > 0 THEN CAST(t.numMinutes AS REAL)
                            ELSE 240 END) AS team_minutes
            FROM team_statistics t
            JOIN team_statistics o ON o.gameId = t.gameId AND o.teamId = t.opponentTeamId
            LEFT JOIN games g ON g.gameId = t.gameId
            WHERE COALESCE(t.gameType, g.gameType) = 'Regular Season'
              AND t.gameDateTimeEst >= '{FIRST_SEASON}-09-01'
            GROUP BY t.teamId, season
            """,
            connection,
        )
    own = frame["fga"] - frame["orb"] + frame["tov"] + 0.44 * frame["fta"]
    opp = frame["opp_fga"] - frame["opp_orb"] + frame["opp_tov"] + 0.44 * frame["opp_fta"]
    frame["possessions"] = 0.5 * (own + opp)
    # Team minutes are summed over the five players (240 per regulation game).
    frame["pace"] = frame["possessions"] / (frame["team_minutes"] / 5.0) * 48.0
    frame["off_rating"] = 100.0 * frame["pts_for"] / frame["possessions"]
    frame["def_rating"] = 100.0 * frame["pts_against"] / frame["possessions"]
    frame["net_rating"] = frame["off_rating"] - frame["def_rating"]
    frame["win_pct"] = frame["wins"] / frame["games"]
    return frame


def build_player_seasons(db_path=TEAM_DB_PATH, team_seasons=None):
    """Player-team-season box totals with per-100-possession rates."""
    season = SEASON_SQL.format(col="ps.gameDateTimeEst")
    with sqlite3.connect(db_path) as connection:
        frame = pd.read_sql_query(
            f"""
            SELECT ps.personId AS personId, ps.playerteamId AS teamId, {season} AS season,
                   MAX(ps.firstName) AS firstName, MAX(ps.lastName) AS lastName,
                   COUNT(*) AS games, SUM(CAST(ps.numMinutes AS REAL)) AS minutes,
                   SUM(COALESCE(ps.points, 0)) AS pts,
                   SUM(COALESCE(ps.fieldGoalsMade, 0)) AS fgm,
                   SUM(COALESCE(ps.fieldGoalsAttempted, 0)) AS fga,
                   SUM(COALESCE(ps.threePointersMade, 0)) AS tpm,
                   SUM(COALESCE(ps.threePointersAttempted, 0)) AS tpa,
                   SUM(COALESCE(ps.freeThrowsMade, 0)) AS ftm,
                   SUM(COALESCE(ps.freeThrowsAttempted, 0)) AS fta,
                   SUM(COALESCE(ps.reboundsOffensive, 0)) AS orb,
                   SUM(COALESCE(ps.reboundsDefensive, 0)) AS drb,
                   SUM(COALESCE(ps.assists, 0)) AS ast,
                   SUM(COALESCE(ps.steals, 0)) AS stl,
                   SUM(COALESCE(ps.blocks, 0)) AS blk,
                   SUM(COALESCE(ps.turnovers, 0)) AS tov,
                   SUM(COALESCE(ps.foulsPersonal, 0)) AS pf
            FROM player_statistics ps
            LEFT JOIN games g ON g.gameId = ps.gameId
            WHERE COALESCE(ps.gameType, g.gameType) = 'Regular Season'
              AND CAST(ps.numMinutes AS REAL) > 0
              AND ps.playerteamId IS NOT NULL
              AND ps.gameDateTimeEst >= '{FIRST_SEASON}-09-01'
            GROUP BY ps.personId, ps.playerteamId, season
            """,
            connection,
        )
    if team_seasons is None:
        team_seasons = build_team_seasons(db_path)
    frame = frame.merge(
        team_seasons[["teamId", "season", "possessions", "team_minutes", "pace"]],
        on=["teamId", "season"], how="inner",
    )
    return add_rates(frame)


def add_rates(frame):
    """Per-100 rates and percentages from box totals plus team possessions/minutes."""
    frame = frame.copy()
    # Possessions the player was on the floor for.
    frame["player_possessions"] = frame["minutes"] * frame["possessions"] / (frame["team_minutes"] / 5.0)
    frame["tsa"] = frame["fga"] + 0.44 * frame["fta"]
    for stat in RATE_STATS:
        frame[f"{stat}_100"] = 100.0 * frame[stat] / frame["player_possessions"]
    frame["ts"] = np.where(frame["tsa"] > 0, frame["pts"] / (2.0 * frame["tsa"]), np.nan)
    frame["tp_pct"] = np.where(frame["tpa"] > 0, frame["tpm"] / frame["tpa"], np.nan)
    frame["ft_pct"] = np.where(frame["fta"] > 0, frame["ftm"] / frame["fta"], np.nan)
    frame["minutes_share"] = frame["minutes"] / frame["team_minutes"]
    return frame


NBA_TEAM_IDS = range(1610612737, 1610612767)


def _clean(team_seasons, player_seasons):
    """Keep the 30 NBA franchises and team-seasons with usable possession counts."""
    team_seasons = team_seasons[team_seasons["teamId"].isin(NBA_TEAM_IDS)
                                & np.isfinite(team_seasons["net_rating"])
                                & (team_seasons["possessions"] > 0)].reset_index(drop=True)
    keys = set(zip(team_seasons["teamId"], team_seasons["season"]))
    player_seasons = player_seasons[[
        (team, season) in keys for team, season in zip(player_seasons["teamId"], player_seasons["season"])
    ]].reset_index(drop=True)
    return team_seasons, player_seasons


def load_or_build(db_path=TEAM_DB_PATH, rebuild=False):
    """Cached team-season and player-season tables (derived, reproducible).

    The first build scans every box score since 1985 and can take tens of
    minutes; later calls read the cached CSVs in ``data/processed``.
    """
    if not rebuild and PLAYER_SEASONS_PATH.exists() and TEAM_SEASONS_PATH.exists():
        return _clean(pd.read_csv(TEAM_SEASONS_PATH), pd.read_csv(PLAYER_SEASONS_PATH))
    team_seasons = build_team_seasons(db_path)
    player_seasons = build_player_seasons(db_path, team_seasons)
    PLAYER_SEASONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    team_seasons.to_csv(TEAM_SEASONS_PATH, index=False)
    player_seasons.to_csv(PLAYER_SEASONS_PATH, index=False)
    return _clean(team_seasons, player_seasons)


def combine_player_season(player_seasons, person_id, season):
    """One player's whole season (all teams) as a single rate row."""
    rows = player_seasons[(player_seasons["personId"] == int(person_id))
                          & (player_seasons["season"] == int(season))]
    if rows.empty:
        return None
    totals = rows[["minutes", "player_possessions", "pts", "fgm", "fga", "tpm", "tpa", "ftm",
                   "fta", "orb", "drb", "ast", "stl", "blk", "tov", "pf", "games"]].sum()
    out = totals.to_dict()
    out["tsa"] = out["fga"] + 0.44 * out["fta"]
    for stat in RATE_STATS:
        out[f"{stat}_100"] = 100.0 * out[stat] / out["player_possessions"]
    out["ts"] = out["pts"] / (2.0 * out["tsa"]) if out["tsa"] > 0 else np.nan
    out["tp_pct"] = out["tpm"] / out["tpa"] if out["tpa"] > 0 else np.nan
    out["ft_pct"] = out["ftm"] / out["fta"] if out["fta"] > 0 else np.nan
    out.update({"personId": int(person_id), "season": int(season),
                "firstName": rows["firstName"].iloc[0], "lastName": rows["lastName"].iloc[0],
                "teams": [int(team) for team in rows["teamId"]]})
    return out


def combined_index(player_seasons):
    """``{(personId, season): whole-season rate row}`` for every player-season."""
    totals = player_seasons.groupby(["personId", "season"])[
        ["minutes", "player_possessions", "pts", "fgm", "fga", "tpm", "tpa", "ftm", "fta",
         "orb", "drb", "ast", "stl", "blk", "tov", "pf", "games"]
    ].sum()
    totals["tsa"] = totals["fga"] + 0.44 * totals["fta"]
    for stat in RATE_STATS:
        totals[f"{stat}_100"] = 100.0 * totals[stat] / totals["player_possessions"]
    totals["ts"] = np.where(totals["tsa"] > 0, totals["pts"] / (2.0 * totals["tsa"]), np.nan)
    totals["tp_pct"] = np.where(totals["tpa"] > 0, totals["tpm"] / totals["tpa"], np.nan)
    totals["ft_pct"] = np.where(totals["fta"] > 0, totals["ftm"] / totals["fta"], np.nan)
    return {key: row for key, row in zip(totals.index, totals.to_dict("records"))}


# ---------------------------------------------------------------------------
# 2. League context and era translation
# ---------------------------------------------------------------------------

def _weighted_mean_sd(values, weights):
    mask = np.isfinite(values) & np.isfinite(weights) & (weights > 0)
    values, weights = values[mask], weights[mask]
    if len(values) == 0:
        return np.nan, np.nan
    mean = float(np.average(values, weights=weights))
    sd = float(np.sqrt(np.average((values - mean) ** 2, weights=weights)))
    return mean, sd


def league_context(player_seasons):
    """Per-season league averages (all players) and qualified-player distributions."""
    rows = []
    for season, group in player_seasons.groupby("season"):
        totals = group[["pts", "tsa", "ast", "tov", "orb", "drb", "stl", "blk", "pf", "tpa", "tpm",
                        "ftm", "fta", "player_possessions"]].sum()
        entry = {"season": int(season)}
        for stat in RATE_STATS:
            entry[f"lg_{stat}_100"] = 100.0 * totals[stat] / totals["player_possessions"]
        entry["lg_ts"] = totals["pts"] / (2.0 * totals["tsa"])
        entry["lg_tp_pct"] = totals["tpm"] / totals["tpa"] if totals["tpa"] > 0 else np.nan
        entry["lg_ft_pct"] = totals["ftm"] / totals["fta"]
        qualified = group[group["minutes"] >= QUALIFY_MINUTES]
        weights = qualified["minutes"].to_numpy(dtype=float)
        for stat in [f"{s}_100" for s in RATE_STATS] + PCT_STATS:
            mean, sd = _weighted_mean_sd(qualified[stat].to_numpy(dtype=float), weights)
            entry[f"mu_{stat}"] = mean
            entry[f"sd_{stat}"] = sd
        entry["qualified_players"] = int(len(qualified))
        rows.append(entry)
    return pd.DataFrame(rows).set_index("season")


def translate_rates(row, origin, target, context, method="zscore"):
    """Re-express a player's rates from season ``origin`` in season ``target``."""
    origin_ctx, target_ctx = context.loc[int(origin)], context.loc[int(target)]
    out = {}
    for stat in [f"{s}_100" for s in RATE_STATS] + PCT_STATS:
        value = row.get(stat)
        if value is None or not np.isfinite(value):
            out[stat] = value
            continue
        mu_o, sd_o = origin_ctx[f"mu_{stat}"], origin_ctx[f"sd_{stat}"]
        mu_t, sd_t = target_ctx[f"mu_{stat}"], target_ctx[f"sd_{stat}"]
        if method == "zscore":
            out[stat] = mu_t + (value - mu_o) * (sd_t / sd_o) if sd_o > 0 else mu_t
        elif method == "ratio":
            if stat in PCT_STATS:
                out[stat] = value + (mu_t - mu_o)
            else:
                out[stat] = value * (mu_t / mu_o) if mu_o > 0 else value
        else:
            raise ValueError("method must be 'zscore' or 'ratio'")
        if stat not in PCT_STATS:
            out[stat] = max(out[stat], 0.0)
    # Points follow from translated efficiency and volume, so the line stays consistent.
    if np.isfinite(out.get("ts", np.nan)) and np.isfinite(out.get("tsa_100", np.nan)):
        out["pts_100"] = 2.0 * out["ts"] * out["tsa_100"]
    return out


# ---------------------------------------------------------------------------
# 3. Features and the team-strength model
# ---------------------------------------------------------------------------

def player_features(rates, season, context):
    """League-relative per-100 feature vector for one player-season."""
    lg = context.loc[int(season)]
    ts = rates.get("ts")
    tsa = rates.get("tsa_100", 0.0)
    scoring = 0.0 if ts is None or not np.isfinite(ts) else 2.0 * (ts - lg["lg_ts"]) * tsa
    return np.array([
        scoring,
        tsa - lg["lg_tsa_100"],
        rates["ast_100"] - lg["lg_ast_100"],
        rates["tov_100"] - lg["lg_tov_100"],
        rates["orb_100"] - lg["lg_orb_100"],
        rates["drb_100"] - lg["lg_drb_100"],
        rates["stl_100"] - lg["lg_stl_100"],
        rates["blk_100"] - lg["lg_blk_100"],
        rates["pf_100"] - lg["lg_pf_100"],
        rates["tpa_100"] - lg["lg_tpa_100"],
    ], dtype=float)


def team_composites(player_seasons, context):
    """Minutes-weighted mean feature vector per team-season (same-season rates)."""
    rows = []
    for (team, season), group in player_seasons.groupby(["teamId", "season"]):
        weights = group["minutes"].to_numpy(dtype=float)
        weights = weights / weights.sum()
        vectors = np.vstack([player_features(row, season, context) for row in group.to_dict("records")])
        rows.append({"teamId": int(team), "season": int(season),
                     **dict(zip(FEATURES, weights @ vectors))})
    return pd.DataFrame(rows)


def _ridge_fit(X, y, alpha):
    n_features = X.shape[1]
    design = np.column_stack([np.ones(len(X)), X])
    penalty = alpha * np.eye(n_features + 1)
    penalty[0, 0] = 0.0
    return np.linalg.solve(design.T @ design + penalty, design.T @ y)


def _ridge_predict(coefficients, X):
    return coefficients[0] + np.asarray(X) @ coefficients[1:]


def fit_team_model(team_seasons, composites, alpha=1.0):
    """Ridge: team net rating ~ team composite. Fit <= 2009, evaluate >= 2010."""
    data = composites.merge(team_seasons[["teamId", "season", "net_rating", "win_pct", "pace"]],
                            on=["teamId", "season"])
    train = data[data["season"] <= TRAIN_LAST_SEASON]
    test = data[data["season"] > TRAIN_LAST_SEASON]
    coefficients = _ridge_fit(train[FEATURES].to_numpy(), train["net_rating"].to_numpy(), alpha)
    prediction = _ridge_predict(coefficients, test[FEATURES].to_numpy())
    residual = test["net_rating"].to_numpy() - prediction
    report = {
        "train_seasons": [int(train["season"].min()), int(train["season"].max())],
        "test_seasons": [int(test["season"].min()), int(test["season"].max())],
        "train_team_seasons": int(len(train)),
        "test_team_seasons": int(len(test)),
        "alpha": alpha,
        "coefficients": dict(zip(["intercept"] + FEATURES, map(float, coefficients))),
        "test_mae": float(np.mean(np.abs(residual))),
        "test_rmse": float(np.sqrt(np.mean(residual ** 2))),
        "test_r2": float(1 - np.sum(residual ** 2) / np.sum((test["net_rating"] - test["net_rating"].mean()) ** 2)),
        "baseline_zero_mae": float(np.mean(np.abs(test["net_rating"]))),
        "note": "Same-season box scores: this measures how well box-score production accounts for "
                "net rating (largely offense), not prediction.",
    }
    return coefficients, report


def bootstrap_realization_factor(frame, replicates=500, seed=7):
    """10th/90th percentiles of the realization factor over resampled train seasons."""
    train = frame[frame["season"] <= TRAIN_LAST_SEASON]
    rng = np.random.default_rng(seed)
    seasons = train["season"].unique()
    factors = []
    for _ in range(replicates):
        sample = pd.concat([train[train["season"] == s] for s in rng.choice(seasons, len(seasons))])
        design = np.column_stack([np.ones(len(sample)), sample["delta_pred"], sample["previous"]])
        beta, *_ = np.linalg.lstsq(design, sample["change"], rcond=None)
        factors.append(beta[1])
    low, high = np.percentile(factors, [10, 90])
    return float(low), float(high)


def wins_per_net_rating(team_seasons):
    """Empirical slope of wins-per-82 on net rating across team-seasons (sanity check)."""
    wins82 = team_seasons["win_pct"] * 82.0
    slope, intercept = np.polyfit(team_seasons["net_rating"], wins82, 1)
    return float(slope), float(intercept)


# ---------------------------------------------------------------------------
# 4. Transfer validation (season s-1 player values -> season s team rating)
# ---------------------------------------------------------------------------

def prior_vector(prior, season, context, method, fallback):
    """A player's previous-season vector translated into ``season`` (or ``fallback``)."""
    if prior is None or not prior.get("player_possessions"):
        return fallback
    translated = translate_rates(prior, season - 1, season, context, method)
    vector = player_features(translated, season, context)
    return vector if np.all(np.isfinite(vector)) else fallback


def rookie_mean_vector(player_seasons, context, last_season=TRAIN_LAST_SEASON):
    """Minutes-weighted mean first-season vector (training seasons only)."""
    first_season = player_seasons.groupby("personId")["season"].transform("min")
    rookies = player_seasons[(player_seasons["season"] == first_season)
                             & (player_seasons["season"] <= last_season)]
    vectors = np.vstack([player_features(row, row["season"], context) for row in rookies.to_dict("records")])
    mask = np.all(np.isfinite(vectors), axis=1)
    return np.average(vectors[mask], axis=0, weights=rookies["minutes"].to_numpy()[mask])


def calibrate_roster_change(team_seasons, player_seasons, context, coefficients, method="zscore"):
    """How much of a box-score-valued roster change shows up in real net rating?

    For each team and season s: value every player at their season s-1
    level (translated into s) and compute the team composite twice -- with
    the season-s minutes (new roster) and with the season s-1 minutes (old
    roster). The model-implied change ``delta_pred`` isolates who plays, not
    how well any player played. Actual change = net(s) - net(s-1). A linear
    model ``change = a * delta_pred + b * net(s-1) + c`` (reversion to the
    mean is the ``b`` term) is fit on seasons <= 2009 and scored on 2010+
    against the reversion-only model. ``a`` is the realization factor the
    swap applies; the residual SD is the swap's added uncertainty.
    """
    combined = combined_index(player_seasons)
    rookie = rookie_mean_vector(player_seasons, context)
    rating = team_seasons.set_index(["teamId", "season"])["net_rating"].to_dict()
    groups = {key: group for key, group in player_seasons.groupby(["teamId", "season"])}
    rows = []
    for (team, season), group in groups.items():
        previous_group = groups.get((team, season - 1))
        if previous_group is None or (team, season - 1) not in rating:
            continue
        people = set(group["personId"]) | set(previous_group["personId"])
        vectors = {
            int(person): prior_vector(combined.get((int(person), int(season) - 1)), season, context,
                                      method, rookie)
            for person in people
        }

        def composite(frame):
            weights = frame["minutes"].to_numpy(dtype=float)
            weights = weights / weights.sum()
            return weights @ np.vstack([vectors[int(p)] for p in frame["personId"]])

        delta = composite(group) - composite(previous_group)
        rows.append({
            "teamId": int(team), "season": int(season),
            "delta_pred": float(np.asarray(coefficients[1:]) @ delta),
            "previous": rating[(team, season - 1)],
            "change": rating[(team, season)] - rating[(team, season - 1)],
        })
    frame = pd.DataFrame(rows)
    train, test = frame[frame["season"] <= TRAIN_LAST_SEASON], frame[frame["season"] > TRAIN_LAST_SEASON]

    def fit(columns):
        design = np.column_stack([np.ones(len(train))] + [train[c] for c in columns])
        beta, *_ = np.linalg.lstsq(design, train["change"], rcond=None)
        test_design = np.column_stack([np.ones(len(test))] + [test[c] for c in columns])
        residual = test["change"].to_numpy() - test_design @ beta
        return beta, residual

    full_beta, full_residual = fit(["delta_pred", "previous"])
    reversion_beta, reversion_residual = fit(["previous"])
    only_beta, only_residual = fit(["delta_pred"])
    return {
        "method": method,
        "train_seasons": [int(train["season"].min()), int(train["season"].max())],
        "test_seasons": [int(test["season"].min()), int(test["season"].max())],
        "test_team_seasons": int(len(test)),
        "realization_factor": float(full_beta[1]),
        "reversion_coefficient": float(full_beta[2]),
        "realization_factor_without_reversion": float(only_beta[1]),
        "test_mae_with_roster_change": float(np.mean(np.abs(full_residual))),
        "test_mae_reversion_only": float(np.mean(np.abs(reversion_residual))),
        "test_mae_no_change": float(np.mean(np.abs(test["change"]))),
        "test_residual_sd_with_roster_change": float(np.std(full_residual)),
        "test_corr_delta_pred_vs_change": float(np.corrcoef(test["delta_pred"], test["change"])[0, 1]),
        "delta_pred_sd": float(test["delta_pred"].std()),
    }, frame

def validate_transfer(team_seasons, player_seasons, context, coefficients, method="zscore",
                      first_test_season=TRAIN_LAST_SEASON + 1):
    """Predict each team's season-s net rating from its players' season s-1 values.

    Weights are the players' actual season-s minutes (an oracle input, which
    the swap also uses: the incoming player takes the outgoing player's
    minutes). Players without a previous NBA season get the average feature
    vector of first-season players in the training seasons.
    """
    combined = combined_index(player_seasons)
    rookie_vector = rookie_mean_vector(player_seasons, context)

    previous_rating = team_seasons.set_index(["teamId", "season"])["net_rating"].to_dict()
    rows = []
    for (team, season), group in player_seasons[player_seasons["season"] >= FIRST_SEASON + 1].groupby(
            ["teamId", "season"]):
        weights = group["minutes"].to_numpy(dtype=float)
        weights = weights / weights.sum()
        vectors, new_minutes = [], 0.0
        prior_team = player_seasons[(player_seasons["teamId"] == team) & (player_seasons["season"] == season - 1)]
        continuing = set(prior_team["personId"])
        for row, weight in zip(group.to_dict("records"), weights):
            prior = combined.get((int(row["personId"]), int(season) - 1))
            vectors.append(prior_vector(prior, season, context, method, rookie_vector))
            if row["personId"] not in continuing:
                new_minutes += weight
        composite = weights @ np.vstack(vectors)
        rows.append({
            "teamId": int(team), "season": int(season),
            "predicted": float(_ridge_predict(coefficients, composite[None, :])[0]),
            "previous": previous_rating.get((int(team), int(season) - 1), np.nan),
            "actual": previous_rating.get((int(team), int(season)), np.nan),
            "new_minutes_share": float(new_minutes),
        })
    frame = pd.DataFrame(rows).dropna(subset=["previous", "actual"])
    train = frame[frame["season"] <= TRAIN_LAST_SEASON]
    test = frame[frame["season"] >= first_test_season]
    # A blend of the player-based prediction with persistence, weights fit on train.
    design = np.column_stack([train["predicted"], train["previous"]])
    blend, *_ = np.linalg.lstsq(np.column_stack([np.ones(len(train)), design]), train["actual"], rcond=None)
    persistence_slope = float(np.polyfit(train["previous"], train["actual"], 1)[0])

    def scores(subset):
        actual = subset["actual"].to_numpy()
        blended = blend[0] + blend[1] * subset["predicted"] + blend[2] * subset["previous"]
        return {
            "team_seasons": int(len(subset)),
            "player_based_mae": float(np.mean(np.abs(actual - subset["predicted"]))),
            "previous_rating_mae": float(np.mean(np.abs(actual - subset["previous"]))),
            "regressed_previous_mae": float(np.mean(np.abs(actual - persistence_slope * subset["previous"]))),
            "blend_mae": float(np.mean(np.abs(actual - blended))),
            "zero_mae": float(np.mean(np.abs(actual))),
            "player_based_residual_sd": float(np.std(actual - subset["predicted"])),
            "corr_player_based": float(np.corrcoef(actual, subset["predicted"])[0, 1]),
        }

    turnover_cut = float(test["new_minutes_share"].quantile(2 / 3))
    return {
        "method": method,
        "oracle_minutes": True,
        "test_seasons": [int(test["season"].min()), int(test["season"].max())],
        "overall": scores(test),
        "high_turnover_third": {"new_minutes_share_at_least": turnover_cut,
                                **scores(test[test["new_minutes_share"] >= turnover_cut])},
        "blend_weights": {"intercept": float(blend[0]), "player_based": float(blend[1]),
                          "previous": float(blend[2])},
        "regressed_previous_slope": persistence_slope,
        "rookie_vector": dict(zip(FEATURES, map(float, rookie_vector))),
    }, frame


# ---------------------------------------------------------------------------
# 5. Fitted model bundle (fit once, cached; reports persisted as JSON)
# ---------------------------------------------------------------------------

def build_era_model(rebuild_tables=False, alpha=1.0):
    """Fit everything the swap needs and return it with its validation reports."""
    team_seasons, player_seasons = load_or_build(rebuild=rebuild_tables)
    context = league_context(player_seasons)
    composites = team_composites(player_seasons, context)
    coefficients, team_model_report = fit_team_model(team_seasons, composites, alpha=alpha)
    transfer_report, _ = validate_transfer(team_seasons, player_seasons, context, coefficients)
    calibration, calibration_frame = calibrate_roster_change(
        team_seasons, player_seasons, context, coefficients)
    low, high = bootstrap_realization_factor(calibration_frame)
    calibration["realization_factor_80pct_interval"] = [low, high]
    slope, intercept = wins_per_net_rating(team_seasons)
    mean_pace = float(team_seasons["pace"].mean())
    # A probit shift of dm/sigma moves a 50/50 game by phi(0)*dm/sigma; choose
    # sigma so an average team gains the empirical wins-per-net-point over 82
    # games: 82 * phi(0) * (pace/100) / sigma = slope.
    probit_sigma = 82.0 * 0.3989422804 * (mean_pace / 100.0) / slope
    reports = {
        "first_season": FIRST_SEASON,
        "qualify_minutes": QUALIFY_MINUTES,
        "features": FEATURES,
        "team_model": team_model_report,
        "transfer_validation": transfer_report,
        "roster_change_calibration": calibration,
        "wins_per_net_rating_point": {"slope": slope, "intercept": intercept,
                                      "team_seasons": int(len(team_seasons))},
        "probit_sigma": {
            "value": probit_sigma,
            "mean_pace": mean_pace,
            "derivation": "sigma = 82 * phi(0) * (mean pace / 100) / empirical wins-per-net-point, so the "
                          "probability shift reproduces the observed net-rating-to-wins slope for an "
                          "average team",
        },
    }
    return {
        "team_seasons": team_seasons,
        "player_seasons": player_seasons,
        "context": context,
        "coefficients": np.asarray(coefficients),
        "combined": combined_index(player_seasons),
        "reports": reports,
    }


def write_reports(model, path=ERA_MODEL_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(model["reports"], indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# 6. The swap
# ---------------------------------------------------------------------------

SHOWN_STATS = ["pts_100", "ts", "tsa_100", "tpa_100", "tp_pct", "ast_100", "tov_100",
               "orb_100", "drb_100", "stl_100", "blk_100", "ft_pct"]


def _stat_line(rates):
    return {stat: (None if rates.get(stat) is None or not np.isfinite(rates.get(stat))
                   else round(float(rates[stat]), 3 if stat in PCT_STATS else 1))
            for stat in SHOWN_STATS}


def _name(row):
    return f"{row.get('firstName', '')} {row.get('lastName', '')}".strip()


def swap_effect(model, team_id, season, out_person_id, in_person_id, in_season, method="zscore"):
    """Net-rating change from replacing one player with another player's (translated) season."""
    season, in_season = int(season), int(in_season)
    for label, value in (("host season", season), ("incoming player's season", in_season)):
        if value < FIRST_SEASON or value not in model["context"].index:
            raise ValueError(
                f"The {label} {value} is not supported: complete team box scores (needed for pace "
                f"and per-100 rates) start in {FIRST_SEASON}-{str(FIRST_SEASON + 1)[-2:]}."
            )
    players = model["player_seasons"]
    roster = players[(players["teamId"] == int(team_id)) & (players["season"] == season)]
    if roster.empty:
        raise ValueError(f"teamId {team_id} has no player box scores in the {season} season.")
    out_rows = roster[roster["personId"] == int(out_person_id)]
    if out_rows.empty:
        raise ValueError(f"personId {out_person_id} did not play for teamId {team_id} in {season}.")
    incoming = model["combined"].get((int(in_person_id), in_season))
    if incoming is None or not incoming.get("player_possessions"):
        raise ValueError(f"personId {in_person_id} has no regular-season minutes in {in_season}.")
    out_row = out_rows.iloc[0].to_dict()
    in_names = players[(players["personId"] == int(in_person_id)) & (players["season"] == in_season)].iloc[0]
    context, beta = model["context"], model["coefficients"][1:]
    weight = float(out_row["minutes"] / roster["minutes"].sum())
    out_vector = player_features(out_row, season, context)
    results = {}
    for name in ("zscore", "ratio"):
        translated = translate_rates(incoming, in_season, season, context, name)
        in_vector = player_features(translated, season, context)
        contributions = beta * weight * (in_vector - out_vector)
        results[name] = {"translated": translated, "contributions": contributions,
                         "delta_full": float(contributions.sum())}
    chosen = results[method]
    calibration = model["reports"]["roster_change_calibration"]
    factor = calibration["realization_factor"]
    low, high = calibration["realization_factor_80pct_interval"]
    team_row = model["team_seasons"][(model["team_seasons"]["teamId"] == int(team_id))
                                     & (model["team_seasons"]["season"] == season)].iloc[0]
    order = np.argsort(-np.abs(chosen["contributions"]))
    return {
        "team_id": int(team_id),
        "season": season,
        "out_player": {"person_id": int(out_person_id), "name": _name(out_row),
                       "minutes": float(out_row["minutes"]), "games": int(out_row["games"]),
                       "team_minutes_share": weight, "stat_line": _stat_line(out_row)},
        "in_player": {"person_id": int(in_person_id), "name": _name(in_names), "season": in_season,
                      "minutes": float(incoming["minutes"]), "games": int(incoming["games"]),
                      "original_stat_line": _stat_line(incoming),
                      "translated_stat_line": _stat_line(chosen["translated"]),
                      "translated_stat_line_ratio_method": _stat_line(results["ratio"]["translated"])},
        "host_team": {"net_rating": float(team_row["net_rating"]), "pace": float(team_row["pace"]),
                      "wins": int(team_row["wins"]), "games": int(team_row["games"])},
        "method": method,
        "delta_net_rating_full_transfer": chosen["delta_full"],
        "delta_net_rating_full_transfer_other_method": results["ratio" if method == "zscore" else "zscore"]["delta_full"],
        "realization_factor": factor,
        "realization_factor_80pct_interval": [low, high],
        "delta_net_rating": factor * chosen["delta_full"],
        "delta_net_rating_80pct": sorted([low * chosen["delta_full"], high * chosen["delta_full"]]),
        "main_factors": [
            {"feature": FEATURES[i], "delta_net_rating_full_transfer": float(chosen["contributions"][i])}
            for i in order[:4]
        ],
    }


def shift_probability(probability, delta_margin, sigma):
    """Move a home-win probability by a per-game margin change (probit scale)."""
    from scipy.stats import norm

    probability = np.clip(np.asarray(probability, dtype=float), 1e-6, 1 - 1e-6)
    return norm.cdf(norm.ppf(probability) + delta_margin / sigma)


def shifted_season_probabilities(probabilities, season, team_id, delta_margin, sigma):
    """Season game probabilities with ``team_id`` made ``delta_margin`` points better."""
    season_games = probabilities[probabilities["season"] == int(season)].copy()
    home = season_games["homeTeamId"] == int(team_id)
    away = season_games["awayTeamId"] == int(team_id)
    values = season_games["home_win_probability"].to_numpy(dtype=float)
    values = np.where(home, shift_probability(values, delta_margin, sigma), values)
    values = np.where(away, shift_probability(values, -delta_margin, sigma), values)
    season_games["home_win_probability"] = values
    return season_games


def _postseason_format(season, db_path=TEAM_DB_PATH):
    """Real postseason structure for ``season`` or ``None`` if it can't be reproduced."""
    try:
        from src.playoffs import load_postseason_games, postseason_cutoff, BUBBLE_SEASON
        from src.simulate_season import conference_of
    except ImportError:  # pragma: no cover
        from playoffs import load_postseason_games, postseason_cutoff, BUBBLE_SEASON
        from simulate_season import conference_of
    if int(season) == BUBBLE_SEASON:
        return None, "the 2019-20 postseason was played in a neutral-site bubble"
    games, _ = load_postseason_games(season, db_path)
    if games.empty:
        return None, "the database has no postseason for this season"
    playoff = games[games["gameType"] == "Playoffs"]
    teams = set(playoff["hometeamId"]) | set(playoff["awayteamId"])
    per_conference = {conf: sum(1 for team in teams if conference_of(team) == conf)
                      for conf in ("East", "West")}
    if per_conference != {"East": 8, "West": 8}:
        return None, ("the current conference alignment does not match that season's real playoff "
                      f"field ({per_conference})")
    play_in = bool((games["gameType"] == "Play-in Tournament").any())
    return {"cutoff": postseason_cutoff(games), "play_in": play_in}, None


def _simulate_postseason(rng, wins_row, teams, matrix, season, play_in):
    try:
        from src import playoffs as po
        from src.simulate_season import conference_of
    except ImportError:  # pragma: no cover
        import playoffs as po
        from simulate_season import conference_of
    n_seeds = 10 if play_in else 8
    best_of_first = 5 if 1983 <= int(season) <= 2001 else 7
    first_pattern = po.series_pattern(season, "first_round", best_of_first)
    later_pattern = po.series_pattern(season, "conf_semifinals", 7)
    reached = {}
    champions = {}
    seeds_by_conf = {}
    for conference in ("East", "West"):
        indices = [i for i, team in enumerate(teams) if conference_of(team) == conference]
        members = [teams[i] for i in indices]
        seeds = po._seed_conference(wins_row[indices], members, rng, n_seeds)
        seeds_by_conf[conference] = seeds
        if play_in:
            winner_a = po._sample_game(rng, matrix, seeds[7], seeds[8], None)
            loser_a = seeds[8] if winner_a == seeds[7] else seeds[7]
            winner_b = po._sample_game(rng, matrix, seeds[9], seeds[10], None)
            winner_c = po._sample_game(rng, matrix, loser_a, winner_b, None)
            bracket = {s: seeds[s] for s in range(1, 7)}
            bracket[7], bracket[8] = winner_a, winner_c
        else:
            bracket = seeds
        seed_of = {team: seed for seed, team in bracket.items()}
        for team in bracket.values():
            reached[team] = "made_playoffs"
        round_one = {}
        for high, low in po.BRACKET_PAIRS:
            round_one[(high, low)] = po._sample_series(rng, matrix, bracket[high], bracket[low], seed_of,
                                                       first_pattern, None)
            reached[round_one[(high, low)]] = "won_first_round"
        top = po._sample_series(rng, matrix, round_one[(1, 8)], round_one[(4, 5)], seed_of, later_pattern, None)
        bottom = po._sample_series(rng, matrix, round_one[(2, 7)], round_one[(3, 6)], seed_of, later_pattern, None)
        reached[top] = reached[bottom] = "won_conf_semifinals"
        champion = po._sample_series(rng, matrix, top, bottom, seed_of, later_pattern, None)
        reached[champion] = "won_conf_finals"
        champions[conference] = champion
    east, west = champions["East"], champions["West"]
    index = {team: i for i, team in enumerate(teams)}
    if (wins_row[index[east]], rng.random()) >= (wins_row[index[west]], rng.random()):
        higher, lower = east, west
    else:
        higher, lower = west, east
    p_higher = po.higher_seed_series_probability(matrix, higher, lower, po.series_pattern(season, "finals", 7))
    title = higher if rng.random() < p_higher else lower
    reached[title] = "champion"
    return reached, seeds_by_conf


STAGES = ["made_playoffs", "won_first_round", "won_conf_semifinals", "won_conf_finals", "champion"]


def simulate_team_season(probabilities, season, team_id, delta_margin, sigma, n_simulations=1000,
                         random_state=42, matrix=None, postseason=None):
    """Paired season (+ postseason) simulation for one team at a margin shift."""
    try:
        from src.simulate_season import simulate_season, conference_of
        from src.playoffs import _logit
    except ImportError:  # pragma: no cover
        from simulate_season import simulate_season, conference_of
        from playoffs import _logit
    games = shifted_season_probabilities(probabilities, season, team_id, delta_margin, sigma)
    wins, teams = simulate_season(games, season, n_simulations=n_simulations, random_state=random_state)
    index = teams.index(int(team_id))
    team_wins = wins[:, index]
    result = {
        "mean_wins": float(team_wins.mean()),
        "wins_10th_90th": [float(np.percentile(team_wins, 10)), float(np.percentile(team_wins, 90))],
        "games": int(((games["homeTeamId"] == int(team_id)) | (games["awayTeamId"] == int(team_id))).sum()),
    }
    conference = conference_of(int(team_id))
    members = [i for i, team in enumerate(teams) if conference_of(team) == conference]
    ranks = np.argsort(np.argsort(-wins[:, members] + 1e-6 * np.arange(len(members)), axis=1), axis=1) + 1
    result["conference_first_probability"] = float((ranks[:, members.index(index)] == 1).mean())
    if matrix is not None and postseason is not None:
        shifted = {}
        for (home, away), probability in matrix.items():
            if home == int(team_id):
                probability = float(shift_probability(probability, delta_margin, sigma))
            elif away == int(team_id):
                probability = float(shift_probability(probability, -delta_margin, sigma))
            shifted[(home, away)] = probability
        rng = np.random.default_rng(random_state + 1)
        counts = {stage: 0 for stage in STAGES}
        for simulation in range(int(n_simulations)):
            reached, _ = _simulate_postseason(rng, wins[simulation], teams, shifted, season,
                                              postseason["play_in"])
            stage = reached.get(int(team_id))
            if stage:
                for name in STAGES[:STAGES.index(stage) + 1]:
                    counts[name] += 1
        result.update({f"p_{stage}": counts[stage] / n_simulations for stage in STAGES})
    return result


def simulate_swap(model, probabilities, inputs, team_id, season, out_person_id, in_person_id,
                  in_season, method="zscore", n_simulations=1000, random_state=42, margin_sigma=None):
    """Full what-if: swap effect, then paired season/postseason simulations."""
    effect = swap_effect(model, team_id, season, out_person_id, in_person_id, in_season, method)
    pace = effect["host_team"]["pace"]
    sigma = margin_sigma or model["reports"]["probit_sigma"]["value"]
    postseason, postseason_note = _postseason_format(season)
    matrix = None
    if postseason is not None:
        try:
            from src.playoffs import matchup_matrix
        except ImportError:  # pragma: no cover
            from playoffs import matchup_matrix
        season_games = probabilities[probabilities["season"] == int(season)]
        teams = sorted(set(season_games["homeTeamId"]) | set(season_games["awayTeamId"]))
        matrix = matchup_matrix(teams, postseason["cutoff"], inputs)
    scenarios = {
        "actual_roster": 0.0,
        "swap_calibrated": effect["delta_net_rating"],
        "swap_calibrated_low": effect["delta_net_rating_80pct"][0],
        "swap_calibrated_high": effect["delta_net_rating_80pct"][1],
        "swap_full_transfer": effect["delta_net_rating_full_transfer"],
    }
    outcomes = {}
    for label, delta_net in scenarios.items():
        delta_margin = delta_net * pace / 100.0
        outcome = simulate_team_season(probabilities, season, team_id, delta_margin, sigma,
                                       n_simulations, random_state, matrix, postseason)
        outcome["delta_net_rating"] = float(delta_net)
        outcome["delta_margin_per_game"] = float(delta_margin)
        outcomes[label] = outcome
    base = outcomes["actual_roster"]
    for label, outcome in outcomes.items():
        outcome["delta_mean_wins"] = outcome["mean_wins"] - base["mean_wins"]
        for key in [k for k in outcome if k.startswith("p_") or k == "conference_first_probability"]:
            outcome[f"delta_{key}"] = outcome[key] - base[key]
    return {"effect": effect, "scenarios": outcomes, "margin_sigma": sigma,
            "postseason_simulated": postseason is not None, "postseason_note": postseason_note}


def transparency_report(result, team_name, reports):
    """PROJECT.md section 21 style: data, method, assumptions, meaning, confidence."""
    effect, scenarios = result["effect"], result["scenarios"]
    calibrated, base = scenarios["swap_calibrated"], scenarios["actual_roster"]
    low, high = scenarios["swap_calibrated_low"], scenarios["swap_calibrated_high"]
    full = scenarios["swap_full_transfer"]
    calibration = reports["roster_change_calibration"]
    transfer = reports["transfer_validation"]
    same_era = abs(effect["in_player"]["season"] - effect["season"]) <= 1
    return {
        "what_if": True,
        "headline": (
            f"What-if: {team_name} {effect['season']}-{str(effect['season'] + 1)[-2:]} with "
            f"{effect['in_player']['name']} ({effect['in_player']['season']}-"
            f"{str(effect['in_player']['season'] + 1)[-2:]}, translated) in place of "
            f"{effect['out_player']['name']}"
        ),
        "projected_change": {
            "net_rating_per_100": round(effect["delta_net_rating"], 2),
            "net_rating_80pct_range": [round(v, 2) for v in effect["delta_net_rating_80pct"]],
            "wins": round(calibrated["delta_mean_wins"], 1),
            "wins_80pct_range": [round(low["delta_mean_wins"], 1), round(high["delta_mean_wins"], 1)],
            "full_transfer_upper_scenario": {
                "net_rating_per_100": round(effect["delta_net_rating_full_transfer"], 2),
                "wins": round(full["delta_mean_wins"], 1),
            },
        },
        "baseline_team": {
            "actual_wins": effect["host_team"]["wins"],
            "actual_net_rating": round(effect["host_team"]["net_rating"], 2),
            "simulated_mean_wins": round(base["mean_wins"], 1),
        },
        "data_used": [
            f"Regular-season box scores in nba.db (player_statistics, team_statistics), "
            f"{FIRST_SEASON}-{str(FIRST_SEASON + 1)[-2:]} onward",
            "League distributions of per-100 rates for both seasons (players with 500+ minutes)",
            "The production model's per-game probabilities for the host season (elo_boosted_ensemble)",
        ],
        "method": [
            "Per-100-possession rates from box scores and team pace",
            f"Era translation: {effect['method']} (same number of league standard deviations from the "
            "league mean in the target season); the other method is reported as a sensitivity check",
            "Box-score team model: team net rating ~ minutes-weighted league-relative player features "
            f"(ridge; held-out R2 {reports['team_model']['test_r2']:.2f} on "
            f"{reports['team_model']['test_seasons'][0]}-{reports['team_model']['test_seasons'][1]})",
            f"Realization factor {calibration['realization_factor']:.2f} (80% range "
            f"{calibration['realization_factor_80pct_interval'][0]:.2f}-"
            f"{calibration['realization_factor_80pct_interval'][1]:.2f}): the share of a box-score-valued "
            "roster change that showed up in real team net rating, 1986-2009 fit",
            "Net-rating change -> per-game margin at the host team's pace -> probit shift of every game's "
            f"production-model win probability (sigma {result['margin_sigma']:.1f} points, set so an "
            f"average team gains the observed {reports['wins_per_net_rating_point']['slope']:.2f} wins per "
            "net-rating point); season and era-appropriate playoffs re-simulated with the same random "
            "draws as the unchanged team",
        ],
        "assumptions": [
            f"{effect['in_player']['name']} plays exactly the minutes {effect['out_player']['name']} played "
            f"({effect['out_player']['team_minutes_share']:.1%} of team minutes) and produces the translated "
            "per-100 line at those minutes.",
            "A player's value relative to their league carries across eras (rules, pace, spacing, and "
            "defensive schemes are absorbed only through league means and spreads).",
            "No fit, usage, chemistry or coaching interaction effects; everyone else is unchanged.",
            "Box scores capture offense far better than defense.",
        ],
        "what_it_means": (
            "A model-based estimate of how much better or worse the team would have been, under the "
            "assumptions above. It is not a historical fact and not a causal estimate."
        ),
        "confidence": {
            "level": "Low",
            "reasons": [
                ("Cross-era translation cannot be validated: there is no ground truth for how a player "
                 "would have played in another era.") if not same_era else
                ("Same-era swap: translation is minimal, but the transfer evidence below still applies."),
                (f"Within adjacent seasons, box-score-valued roster changes explain only part of real "
                 f"changes: held-out {calibration['test_seasons'][0]}-{calibration['test_seasons'][1]} "
                 f"error {calibration['test_mae_with_roster_change']:.2f} vs "
                 f"{calibration['test_mae_reversion_only']:.2f} points without them (correlation "
                 f"{calibration['test_corr_delta_pred_vs_change']:.2f}); residual SD "
                 f"{calibration['test_residual_sd_with_roster_change']:.1f} points."),
                (f"Last season's player values alone predict a team's next season worse than its own "
                 f"previous rating ({transfer['overall']['player_based_mae']:.2f} vs "
                 f"{transfer['overall']['previous_rating_mae']:.2f} MAE); they help only in combination."),
            ],
        },
        "main_factors": effect["main_factors"],
        "translated_stat_line": effect["in_player"]["translated_stat_line"],
        "postseason": (
            {"make_playoffs": [round(base.get("p_made_playoffs", 0), 3), round(calibrated.get("p_made_playoffs", 0), 3)],
             "title": [round(base.get("p_champion", 0), 3), round(calibrated.get("p_champion", 0), 3)],
             "note": "[actual roster, calibrated swap]; bracket seeded by simulated record (the era's "
                     "division-winner seeding rule is not modeled)"}
            if result["postseason_simulated"] else {"note": f"Playoffs not simulated: {result['postseason_note']}."}
        ),
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Cross-era what-if roster swaps (box-score model, calibrated on real roster changes)."
    )
    parser.add_argument("--reports", action="store_true",
                        help="Fit the model and write the validation reports to models/era_swap_model.json.")
    parser.add_argument("--rebuild-tables", action="store_true",
                        help="Rebuild the cached player/team season tables from nba.db (slow).")
    parser.add_argument("--team-id", type=int)
    parser.add_argument("--season", type=int, help="Host team's season start year (1992 = 1992-93).")
    parser.add_argument("--out-person-id", type=int)
    parser.add_argument("--in-person-id", type=int)
    parser.add_argument("--in-season", type=int, help="Incoming player's season start year.")
    parser.add_argument("--method", default="zscore", choices=["zscore", "ratio"])
    parser.add_argument("--simulations", type=int, default=1000)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    model = build_era_model(rebuild_tables=args.rebuild_tables)
    write_reports(model)
    if args.reports or args.team_id is None:
        print(json.dumps(model["reports"], indent=2))
        return 0
    try:
        from src.forward_projection import load_model_inputs
        from src.simulate_season import load_pregame_probabilities, load_team_names
    except ImportError:  # pragma: no cover
        from forward_projection import load_model_inputs
        from simulate_season import load_pregame_probabilities, load_team_names
    result = simulate_swap(model, load_pregame_probabilities(), load_model_inputs(), args.team_id,
                           args.season, args.out_person_id, args.in_person_id, args.in_season,
                           method=args.method, n_simulations=args.simulations)
    name = load_team_names(args.season).get(args.team_id, {}).get("teamName", str(args.team_id))
    print(json.dumps(transparency_report(result, name, model["reports"]), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
