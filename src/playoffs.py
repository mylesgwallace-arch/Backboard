"""Play-in, playoff-bracket and championship probabilities.

Built entirely on the frozen, validated per-game model: every playoff or
play-in game gets the ``elo_boosted_ensemble`` home-win probability computed
from information available at the end of the regular season (the same frozen
strength ``forward_projection.frozen_matchup_probabilities`` uses). Nothing
is re-trained. What this module adds is structure:

* **Series math.** The probability that the higher seed wins a best-of-5/7
  series is computed exactly (dynamic programming over the home-court
  pattern, e.g. 2-2-1-1-1), from the two per-game probabilities for "higher
  seed at home" and "higher seed on the road".
* **Play-in (2020-21 onward).** 7 v 8 at the 7 seed (winner = 7 seed),
  9 v 10 at the 9 seed, then the 7/8 loser hosts the 9/10 winner for the
  8 seed. Each play-in outcome is enumerated exactly.
* **Bracket.** 1v8, 4v5, 2v7, 3v6; winners meet (1/8 v 4/5, 2/7 v 3/6); the
  better seed has home court through the conference finals; the NBA Finals
  home court goes to the better regular-season record. Given seeds, every
  team's probability of reaching each round is computed exactly.
* **Season odds.** ``season_playoff_odds`` couples this with the forward
  projection: in each simulated season the standings set the seeds (ties
  broken at random) and the bracket is sampled series by series, using the
  same per-team strength shock as the regular-season simulation.

Validation (``validate_playoffs``) replays real postseasons: per-game and
per-series log loss/Brier against simple baselines, and the pre-play-in
title probability given to each actual champion.
"""

import argparse
import itertools
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from src.forward_projection import (
        _logit,
        default_strength_sd,
        frozen_matchup_probabilities,
        load_model_inputs,
        project_from_date,
        season_schedule,
    )
    from src.simulate_season import conference_of, load_team_names
    from src.main import TEAM_DB_PATH
except ImportError:  # pragma: no cover - direct-script support
    from forward_projection import (
        _logit,
        default_strength_sd,
        frozen_matchup_probabilities,
        load_model_inputs,
        project_from_date,
        season_schedule,
    )
    from simulate_season import conference_of, load_team_names
    from main import TEAM_DB_PATH


ROOT = Path(__file__).resolve().parents[1]
PLAYOFF_METRICS_PATH = ROOT / "models" / "playoff_validation.json"
PRODUCTION_MODEL = "elo_boosted_ensemble"
ROUNDS = ["first_round", "conf_semifinals", "conf_finals", "finals"]
ROUND_LABELS = {
    "made_playoffs": "reached the first round",
    "won_first_round": "reached the conference semifinals",
    "won_conf_semifinals": "reached the conference finals",
    "won_conf_finals": "reached the NBA Finals",
    "champion": "won the title",
}
# Home pattern from the higher seed's point of view (H = higher seed at home).
PATTERN_2_2_1_1_1 = "HHAAHAH"
PATTERN_2_3_2 = "HHAAAHH"
PATTERN_2_2_1 = "HHAAH"
PLAY_IN_FIRST_SEASON = 2020  # the 2020-21 season introduced the current play-in
DEFAULT_VALIDATION_SEASONS = list(range(2014, 2026))
BUBBLE_SEASON = 2019  # 2019-20 playoffs were played at a neutral site
BRACKET_PAIRS = [(1, 8), (4, 5), (2, 7), (3, 6)]


# ---------------------------------------------------------------------------
# Series math
# ---------------------------------------------------------------------------

def series_pattern(season, round_name, best_of):
    """Home-court pattern for a series (higher seed's perspective)."""
    if best_of == 5:
        return PATTERN_2_2_1
    if round_name == "finals" and 1984 <= int(season) <= 2012:
        return PATTERN_2_3_2
    return PATTERN_2_2_1_1_1


def series_win_probability(p_home, p_away, pattern=PATTERN_2_2_1_1_1):
    """Exact P(higher seed wins the series).

    ``p_home``: P(higher seed wins a game it hosts); ``p_away``: P(higher
    seed wins a game on the road). Games are treated as independent given
    those two probabilities.
    """
    needed = len(pattern) // 2 + 1
    # states[(wins_high, wins_low)] = probability of reaching that score.
    states = {(0, 0): 1.0}
    finished_high = 0.0
    for venue in pattern:
        p = p_home if venue == "H" else p_away
        next_states = {}
        for (high, low), probability in states.items():
            for won, factor in ((True, p), (False, 1.0 - p)):
                score = (high + 1, low) if won else (high, low + 1)
                if score[0] == needed:
                    finished_high += probability * factor
                elif score[1] == needed:
                    continue
                else:
                    next_states[score] = next_states.get(score, 0.0) + probability * factor
        states = next_states
    return float(finished_high)


def matchup_matrix(team_ids, cutoff, inputs):
    """``{(home, away): P(home wins)}`` for every ordered pair, frozen at cutoff."""
    team_ids = sorted(int(team) for team in team_ids)
    pairs = pd.DataFrame(
        [(home, away) for home in team_ids for away in team_ids if home != away],
        columns=["homeTeamId", "awayTeamId"],
    )
    scored = frozen_matchup_probabilities(pairs, cutoff, inputs)
    return {
        (int(home), int(away)): float(probability)
        for home, away, probability in zip(
            scored["homeTeamId"], scored["awayTeamId"], scored["home_win_probability"]
        )
    }


def _game_probability(matrix, home, away, shocks=None):
    probability = matrix[(home, away)]
    if shocks is None:
        return probability
    logit = _logit(probability) + shocks.get(home, 0.0) - shocks.get(away, 0.0)
    return float(1.0 / (1.0 + np.exp(-logit)))


def higher_seed_series_probability(matrix, higher, lower, pattern, shocks=None):
    return series_win_probability(
        _game_probability(matrix, higher, lower, shocks),
        1.0 - _game_probability(matrix, lower, higher, shocks),
        pattern,
    )


# ---------------------------------------------------------------------------
# Exact bracket probabilities for fixed seeds
# ---------------------------------------------------------------------------

def play_in_outcomes(seeds, matrix, shocks=None):
    """Enumerate the play-in: list of ``(probability, seed7_team, seed8_team)``.

    ``seeds`` maps seed number -> teamId for seeds 7-10.
    """
    s7, s8, s9, s10 = seeds[7], seeds[8], seeds[9], seeds[10]
    p_a = _game_probability(matrix, s7, s8, shocks)    # 7 hosts 8
    p_b = _game_probability(matrix, s9, s10, shocks)   # 9 hosts 10
    outcomes = []
    for a_winner, a_loser, p_a_outcome in ((s7, s8, p_a), (s8, s7, 1 - p_a)):
        for b_winner, p_b_outcome in ((s9, p_b), (s10, 1 - p_b)):
            p_c = _game_probability(matrix, a_loser, b_winner, shocks)
            for c_winner, p_c_outcome in ((a_loser, p_c), (b_winner, 1 - p_c)):
                outcomes.append((p_a_outcome * p_b_outcome * p_c_outcome, a_winner, c_winner))
    return outcomes


def _merge(distributions):
    merged = {}
    for weight, distribution in distributions:
        for team, probability in distribution.items():
            merged[team] = merged.get(team, 0.0) + weight * probability
    return merged


def _series_distribution(left, right, seed_of, matrix, pattern, shocks=None):
    """Winner distribution of a series between two independent slot distributions."""
    result = {}
    for team_a, p_a in left.items():
        for team_b, p_b in right.items():
            if seed_of[team_a] <= seed_of[team_b]:
                higher, lower = team_a, team_b
            else:
                higher, lower = team_b, team_a
            p_higher = higher_seed_series_probability(matrix, higher, lower, pattern, shocks)
            weight = p_a * p_b
            result[higher] = result.get(higher, 0.0) + weight * p_higher
            result[lower] = result.get(lower, 0.0) + weight * (1 - p_higher)
    return result


def conference_bracket(seeds, matrix, season, shocks=None, best_of_first_round=7):
    """Exact round-reach probabilities for one conference with fixed seeds 1-8.

    Returns ``(reach, champion_distribution)`` where ``reach[team]`` has
    ``won_first_round``, ``won_conf_semifinals`` and ``won_conf_finals``.
    """
    seed_of = {team: seed for seed, team in seeds.items()}
    first_pattern = series_pattern(season, "first_round", best_of_first_round)
    later_pattern = series_pattern(season, "conf_semifinals", 7)
    round_one = {
        pair: _series_distribution({seeds[pair[0]]: 1.0}, {seeds[pair[1]]: 1.0},
                                   seed_of, matrix, first_pattern, shocks)
        for pair in BRACKET_PAIRS
    }
    semi_top = _series_distribution(round_one[(1, 8)], round_one[(4, 5)],
                                    seed_of, matrix, later_pattern, shocks)
    semi_bottom = _series_distribution(round_one[(2, 7)], round_one[(3, 6)],
                                       seed_of, matrix, later_pattern, shocks)
    final = _series_distribution(semi_top, semi_bottom, seed_of, matrix,
                                 later_pattern, shocks)
    reach = {}
    for team in seeds.values():
        reach[team] = {
            "won_first_round": sum(dist.get(team, 0.0) for dist in round_one.values()),
            "won_conf_semifinals": semi_top.get(team, 0.0) + semi_bottom.get(team, 0.0),
            "won_conf_finals": final.get(team, 0.0),
        }
    return reach, final


def _finals_distribution(east, west, record, matrix, season, shocks=None):
    """Title distribution; Finals home court goes to the better record."""
    pattern_for = lambda: series_pattern(season, "finals", 7)  # noqa: E731
    title = {}
    for team_e, p_e in east.items():
        for team_w, p_w in west.items():
            if (record.get(team_e, 0), -team_e) >= (record.get(team_w, 0), -team_w):
                higher, lower = team_e, team_w
            else:
                higher, lower = team_w, team_e
            p_higher = higher_seed_series_probability(matrix, higher, lower,
                                                      pattern_for(), shocks)
            weight = p_e * p_w
            title[higher] = title.get(higher, 0.0) + weight * p_higher
            title[lower] = title.get(lower, 0.0) + weight * (1 - p_higher)
    return title


def exact_playoff_odds(fields, record, matrix, season, shocks=None):
    """Exact per-team round probabilities for fixed conference seeds.

    ``fields``: ``{"East": {seed: teamId}, "West": {...}}`` with seeds 1-8,
    or 1-10 when a play-in is played (seasons >= 2020). ``record``: regular-
    season wins per team (Finals home court).
    """
    per_team = {}
    champions = {}
    best_of_first = 5 if 1983 <= int(season) <= 2001 else 7
    for conference, seeds in fields.items():
        if len(seeds) >= 10:
            scenarios = []
            for probability, seed7, seed8 in play_in_outcomes(seeds, matrix, shocks):
                bracket = {seed: seeds[seed] for seed in range(1, 7)}
                bracket[7], bracket[8] = seed7, seed8
                scenarios.append((probability, bracket))
        else:
            scenarios = [(1.0, {seed: seeds[seed] for seed in range(1, 9)})]
        conference_champion = {}
        for team in seeds.values():
            per_team[team] = {
                "conference": conference,
                "seed": next(seed for seed, value in seeds.items() if value == team),
                "made_playoffs": 0.0,
                "won_first_round": 0.0,
                "won_conf_semifinals": 0.0,
                "won_conf_finals": 0.0,
            }
        for probability, bracket in scenarios:
            reach, final = conference_bracket(bracket, matrix, season, shocks,
                                              best_of_first_round=best_of_first)
            for team in bracket.values():
                per_team[team]["made_playoffs"] += probability
                for key, value in reach[team].items():
                    per_team[team][key] += probability * value
            for team, value in final.items():
                conference_champion[team] = conference_champion.get(team, 0.0) + probability * value
        champions[conference] = conference_champion
    title = _finals_distribution(champions["East"], champions["West"], record,
                                 matrix, season, shocks)
    for team, row in per_team.items():
        row["champion"] = title.get(team, 0.0)
    return per_team


# ---------------------------------------------------------------------------
# Actual postseasons from the database
# ---------------------------------------------------------------------------

def _season_window(season):
    return f"{int(season) + 1}-04-01", f"{int(season) + 1}-07-31"


def load_postseason_games(season, db_path=TEAM_DB_PATH):
    """Actual play-in and playoff games of ``season`` (start year), in order."""
    start, end = _season_window(season)
    with sqlite3.connect(db_path) as connection:
        frame = pd.read_sql_query(
            """
            SELECT gameId, gameDateTimeEst, gameType, gameLabel, hometeamId, awayteamId,
                   homeScore, awayScore, winner
            FROM games
            WHERE gameType IN ('Playoffs', 'Play-in Tournament')
              AND gameDateTimeEst BETWEEN ? AND ?
            ORDER BY gameDateTimeEst, gameId
            """,
            connection,
            params=(start, end + " 23:59:59"),
        )
        seeds = pd.read_sql_query(
            """
            SELECT ts.gameId, ts.teamId, ts.seed
            FROM team_statistics ts
            JOIN games g ON g.gameId = ts.gameId
            WHERE g.gameType IN ('Playoffs', 'Play-in Tournament')
              AND g.gameDateTimeEst BETWEEN ? AND ?
            """,
            connection,
            params=(start, end + " 23:59:59"),
        )
    frame["gameDateTimeEst"] = pd.to_datetime(frame["gameDateTimeEst"])
    return frame, seeds


def actual_series(season, db_path=TEAM_DB_PATH, games=None):
    """Group actual playoff games into series with round, home court and winner.

    The round is the series' position in each team's postseason (1st series =
    first round, ...); home court is the home team of game 1.
    """
    if games is None:
        games, _ = load_postseason_games(season, db_path)
    playoff = games[games["gameType"] == "Playoffs"].copy()
    playoff["pair"] = [
        tuple(sorted((int(home), int(away))))
        for home, away in zip(playoff["hometeamId"], playoff["awayteamId"])
    ]
    series = []
    for pair, group in playoff.groupby("pair", sort=False):
        group = group.sort_values("gameDateTimeEst")
        wins = group["winner"].astype(int).value_counts().to_dict()
        first = group.iloc[0]
        higher = int(first["hometeamId"])
        lower = int(first["awayteamId"])
        winner = max(pair, key=lambda team: wins.get(team, 0))
        series.append(
            {
                "teams": list(pair),
                "higher_seed_team": higher,
                "lower_seed_team": lower,
                "start": group["gameDateTimeEst"].min(),
                "games": int(len(group)),
                "best_of": 5 if max(wins.values()) == 3 else 7,
                "winner": int(winner),
                "higher_seed_won": int(winner) == higher,
                "game_rows": group,
            }
        )
    series.sort(key=lambda item: item["start"])
    count = {}
    for item in series:
        index = max(count.get(team, 0) for team in item["teams"])
        item["round"] = ROUNDS[min(index, 3)]
        for team in item["teams"]:
            count[team] = index + 1
    return series


def actual_postseason_field(season, db_path=TEAM_DB_PATH, games=None, seeds=None):
    """Actual conference seeds (1-10 with a play-in, else 1-8) or ``None``.

    Seeds come from ``team_statistics.seed``. With a play-in, seeds 7-10 are
    read from the play-in games (7 hosts 8, 9 hosts 10). Returns ``None``
    when the database has no seeds for that postseason.
    """
    if games is None or seeds is None:
        games, seeds = load_postseason_games(season, db_path)
    if games.empty:
        return None
    if seeds["seed"].isna().all():
        return _field_from_bracket_structure(games)
    playoff_ids = set(games.loc[games["gameType"] == "Playoffs", "gameId"])
    playin_ids = set(games.loc[games["gameType"] == "Play-in Tournament", "gameId"])
    fields = {"East": {}, "West": {}}
    playoff_seeds = (
        seeds[seeds["gameId"].isin(playoff_ids)].dropna(subset=["seed"])
        .groupby("teamId")["seed"].first()
    )
    for team, seed in playoff_seeds.items():
        conference = conference_of(int(team))
        if conference in fields:
            fields[conference][int(seed)] = int(team)
    if playin_ids:
        playin_seeds = (
            seeds[seeds["gameId"].isin(playin_ids)].dropna(subset=["seed"])
            .groupby("teamId")["seed"].first()
        )
        for conference in fields:
            fields[conference] = {
                seed: team for seed, team in fields[conference].items() if seed <= 6
            }
        for team, seed in playin_seeds.items():
            conference = conference_of(int(team))
            if conference in fields:
                fields[conference][int(seed)] = int(team)
    expected = 10 if playin_ids else 8
    if any(sorted(field) != list(range(1, expected + 1)) for field in fields.values()):
        return _field_from_bracket_structure(games)
    return fields


def _field_from_bracket_structure(games):
    """Recover seeds from the bracket itself when the seed column is missing.

    The play-in fixes 7-10 (7 hosts 8, 9 hosts 10; the 7/8 winner is the 7
    seed, the last game's winner the 8 seed). In the first round the 8 seed
    plays the 1 seed and the 7 seed the 2 seed; of the other two series, the
    one whose winner met the 1 v 8 winner in round two is 4 v 5 and the other
    is 3 v 6, with the game-1 host as the higher seed. Returns ``None`` if the
    postseason does not have that structure (e.g. the 2019-20 bubble).
    """
    series = actual_series(None, games=games)
    play_in = games[games["gameType"] == "Play-in Tournament"].sort_values("gameDateTimeEst")
    fields = {}
    for conference in ("East", "West"):
        conf_games = play_in[play_in["hometeamId"].map(conference_of) == conference]
        conf_series = [item for item in series
                       if conference_of(item["higher_seed_team"]) == conference]
        first_round = [item for item in conf_series if item["round"] == "first_round"]
        second_round = [item for item in conf_series if item["round"] == "conf_semifinals"]
        if len(first_round) != 4 or len(second_round) != 2:
            return None
        seeds = {}
        if not conf_games.empty:
            if len(conf_games) != 3:
                return None
            last = conf_games.iloc[-1]
            early = conf_games.iloc[:2]
            seed8_team = int(last["winner"])
            loser_a = int(last["hometeamId"])  # the 7/8 loser hosts the last game
            game_a = early[(early["hometeamId"] == loser_a) | (early["awayteamId"] == loser_a)]
            game_b = early.drop(game_a.index)
            if len(game_a) != 1 or len(game_b) != 1:
                return None
            game_a, game_b = game_a.iloc[0], game_b.iloc[0]
            seeds[7], seeds[8] = int(game_a["hometeamId"]), int(game_a["awayteamId"])
            seeds[9], seeds[10] = int(game_b["hometeamId"]), int(game_b["awayteamId"])
            playoff_seven = int(game_a["winner"])
            playoff_eight = seed8_team
        else:
            return None
        by_lower = {item["lower_seed_team"]: item for item in first_round}
        if playoff_eight not in by_lower or playoff_seven not in by_lower:
            return None
        one_eight = by_lower.pop(playoff_eight)
        two_seven = by_lower.pop(playoff_seven)
        seeds[1] = one_eight["higher_seed_team"]
        seeds[2] = two_seven["higher_seed_team"]
        others = list(by_lower.values())
        top_semi = next(
            (item for item in second_round if one_eight["winner"] in item["teams"]), None
        )
        if top_semi is None:
            return None
        four_five = next(
            (item for item in others if item["winner"] in top_semi["teams"]), None
        )
        if four_five is None:
            return None
        three_six = next(item for item in others if item is not four_five)
        seeds[4], seeds[5] = four_five["higher_seed_team"], four_five["lower_seed_team"]
        seeds[3], seeds[6] = three_six["higher_seed_team"], three_six["lower_seed_team"]
        fields[conference] = seeds
    return fields


def postseason_cutoff(games):
    """The day the postseason (play-in or playoffs) started."""
    return games["gameDateTimeEst"].min().normalize()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _log_loss(outcomes, probabilities):
    probabilities = np.clip(np.asarray(probabilities, dtype=float), 1e-6, 1 - 1e-6)
    outcomes = np.asarray(outcomes, dtype=float)
    return float(-np.mean(outcomes * np.log(probabilities) + (1 - outcomes) * np.log(1 - probabilities)))


def _brier(outcomes, probabilities):
    return float(np.mean((np.asarray(probabilities, dtype=float) - np.asarray(outcomes, dtype=float)) ** 2))


def validate_playoffs(inputs, seasons=None, db_path=TEAM_DB_PATH,
                      baseline_seasons=(2003, 2013)):
    """Replay actual postseasons with probabilities frozen at the regular-season end.

    Returns per-game and per-series metrics against baselines, plus the title
    probability the model gave each actual champion before the postseason.
    Baselines: home team at the historical playoff home-win rate, and the
    higher seed at the historical higher-seed series win rate, both measured
    on ``baseline_seasons`` (strictly before the evaluation seasons).
    """
    seasons = seasons or DEFAULT_VALIDATION_SEASONS

    # Baseline rates from earlier seasons only.
    base_games, base_series = [], []
    for season in range(baseline_seasons[0], baseline_seasons[1] + 1):
        games, _ = load_postseason_games(season, db_path)
        playoff = games[games["gameType"] == "Playoffs"]
        base_games.extend((playoff["winner"] == playoff["hometeamId"]).astype(int).tolist())
        base_series.extend(int(item["higher_seed_won"]) for item in actual_series(season, games=games))
    home_rate = float(np.mean(base_games))
    higher_rate = float(np.mean(base_series))

    game_rows, series_rows, bracket_rows = [], [], []
    for season in seasons:
        games, seeds = load_postseason_games(season, db_path)
        if games.empty:
            continue
        cutoff = postseason_cutoff(games)
        teams = sorted(set(games["hometeamId"]) | set(games["awayteamId"]))
        matrix = matchup_matrix(teams, cutoff, inputs)
        neutral = int(season) == BUBBLE_SEASON
        for row in games.itertuples():
            home, away = int(row.hometeamId), int(row.awayteamId)
            game_rows.append(
                {
                    "season": int(season),
                    "game_type": row.gameType,
                    "neutral_site": neutral,
                    "home_won": int(int(row.winner) == home),
                    "model_probability": matrix[(home, away)],
                    "baseline_probability": home_rate,
                }
            )
        for item in actual_series(season, games=games):
            pattern = series_pattern(season, item["round"], item["best_of"])
            probability = higher_seed_series_probability(
                matrix, item["higher_seed_team"], item["lower_seed_team"], pattern
            )
            series_rows.append(
                {
                    "season": int(season),
                    "round": item["round"],
                    "higher_seed_team": item["higher_seed_team"],
                    "lower_seed_team": item["lower_seed_team"],
                    "neutral_site": neutral,
                    "higher_seed_won": int(item["higher_seed_won"]),
                    "model_probability": probability,
                    "baseline_probability": higher_rate,
                }
            )
        field = actual_postseason_field(season, games=games, seeds=seeds)
        if field is not None:
            schedule = season_schedule(inputs, season)
            record = {}
            for home, away, target in zip(schedule["homeTeamId"], schedule["awayTeamId"], schedule["target"]):
                winner = home if int(target) == 1 else away
                record[int(winner)] = record.get(int(winner), 0) + 1
            odds = exact_playoff_odds(field, record, matrix, season)
            finals = [item for item in actual_series(season, games=games) if item["round"] == "finals"]
            champion = finals[0]["winner"] if finals else None
            ranked = sorted(odds, key=lambda team: -odds[team]["champion"])
            bracket_rows.append(
                {
                    "season": int(season),
                    "cutoff": str(cutoff.date()),
                    "actual_champion": champion,
                    "model_title_probability_for_champion": (
                        odds[champion]["champion"] if champion in odds else None
                    ),
                    "champion_rank_by_model": (
                        ranked.index(champion) + 1 if champion in odds else None
                    ),
                    "model_favorite": ranked[0],
                    "model_favorite_title_probability": odds[ranked[0]]["champion"],
                    "teams_in_field": len(odds),
                    "uniform_title_probability": 1.0 / 16,
                }
            )

    def summarize(rows, outcome_key, include_neutral):
        frame = pd.DataFrame(rows)
        if not include_neutral:
            frame = frame[~frame["neutral_site"]]
        if frame.empty:
            return {}
        return {
            "count": int(len(frame)),
            "model_log_loss": _log_loss(frame[outcome_key], frame["model_probability"]),
            "baseline_log_loss": _log_loss(frame[outcome_key], frame["baseline_probability"]),
            "model_brier": _brier(frame[outcome_key], frame["model_probability"]),
            "baseline_brier": _brier(frame[outcome_key], frame["baseline_probability"]),
            "model_accuracy": float(((frame["model_probability"] >= 0.5) == (frame[outcome_key] == 1)).mean()),
            "baseline_accuracy": float(((frame["baseline_probability"] >= 0.5) == (frame[outcome_key] == 1)).mean()),
            "outcome_rate": float(frame[outcome_key].mean()),
        }

    series_frame = pd.DataFrame(series_rows)
    by_round = {}
    for round_name, group in series_frame[~series_frame["neutral_site"]].groupby("round"):
        by_round[round_name] = summarize(group.to_dict("records"), "higher_seed_won", True)
    scored_brackets = [
        row for row in bracket_rows
        if row["model_title_probability_for_champion"] is not None
        and row["season"] != BUBBLE_SEASON
    ]
    champion_probabilities = np.array(
        [row["model_title_probability_for_champion"] for row in scored_brackets]
    )
    bracket_summary = {
        "seasons": [row["season"] for row in scored_brackets],
        "mean_title_probability_given_to_actual_champion": (
            float(champion_probabilities.mean()) if len(scored_brackets) else None
        ),
        "mean_log_title_probability": (
            float(np.log(np.clip(champion_probabilities, 1e-6, 1)).mean())
            if len(scored_brackets) else None
        ),
        "uniform_log_title_probability": float(np.log(1 / 16)),
        "champion_was_model_favorite_share": (
            float(np.mean([row["champion_rank_by_model"] == 1 for row in scored_brackets]))
            if len(scored_brackets) else None
        ),
    }
    return {
        "model": PRODUCTION_MODEL,
        "strength_frozen_at": "day before the first postseason game of each season",
        "seasons": [int(season) for season in seasons],
        "excluded_from_headline_metrics": {
            str(BUBBLE_SEASON): "2019-20 playoffs were played at a neutral site (bubble)"
        },
        "baseline_rates": {
            "seasons": list(baseline_seasons),
            "playoff_home_win_rate": home_rate,
            "higher_seed_series_win_rate": higher_rate,
        },
        "games": summarize(game_rows, "home_won", include_neutral=False),
        "games_including_bubble": summarize(game_rows, "home_won", include_neutral=True),
        "series": summarize(series_rows, "higher_seed_won", include_neutral=False),
        "series_by_round": by_round,
        "bracket_summary": bracket_summary,
        "brackets": bracket_rows,
        "series_detail": [
            {key: value for key, value in row.items()} for row in series_rows
        ],
    }


# ---------------------------------------------------------------------------
# Season-level championship odds (standings simulated, bracket sampled)
# ---------------------------------------------------------------------------

def _seed_conference(wins_row, team_ids, rng, n_seeds):
    """Order a conference by wins with random tie-breaks; return seed -> team."""
    tiebreak = rng.random(len(team_ids))
    order = sorted(range(len(team_ids)), key=lambda i: (-wins_row[i], tiebreak[i]))
    return {seed + 1: team_ids[order[seed]] for seed in range(min(n_seeds, len(order)))}


def _sample_series(rng, matrix, team_a, team_b, seed_of, pattern, shocks):
    if seed_of[team_a] <= seed_of[team_b]:
        higher, lower = team_a, team_b
    else:
        higher, lower = team_b, team_a
    p_higher = higher_seed_series_probability(matrix, higher, lower, pattern, shocks)
    return higher if rng.random() < p_higher else lower


def _sample_game(rng, matrix, home, away, shocks):
    return home if rng.random() < _game_probability(matrix, home, away, shocks) else away


def season_playoff_odds(season, as_of, inputs, n_simulations=1000, random_state=42,
                        strength_sd=None, team_names=None):
    """Championship and round odds from the actual record on ``as_of``.

    Regular-season remainder: ``forward_projection.project_from_date``
    (same probabilities, same per-team strength shock). Postseason: seeds from
    each simulated standings table (random tie-breaks), play-in when
    ``season >= 2020``, and series sampled with the frozen matchup matrix
    shifted by the same simulation's strength shocks.
    """
    schedule = season_schedule(inputs, season)
    cutoff = pd.Timestamp(as_of)
    teams = sorted(
        int(team) for team in set(schedule["homeTeamId"]) | set(schedule["awayTeamId"])
    )
    fraction_completed = float((schedule["gameDateTimeEst"] < cutoff).mean())
    if strength_sd is None:
        # A complete regular season freezes strength where the playoff
        # replay validation measured it (no extra shock helped there).
        sd = default_strength_sd(fraction_completed) if fraction_completed < 1 else 0.0
    else:
        sd = float(strength_sd)
    rng = np.random.default_rng(random_state + 1)
    # One shock per team per simulation, shared by the regular-season
    # remainder and the playoffs so a team that is secretly better is better
    # in both.
    shock_matrix = (
        rng.normal(0.0, sd, size=(int(n_simulations), len(teams))) if sd > 0 else None
    )
    projection, wins, teams = project_from_date(
        season, cutoff, inputs, n_simulations=n_simulations, random_state=random_state,
        schedule=schedule, strength_sd=sd, return_samples=True, shocks=shock_matrix,
    )
    matrix = matchup_matrix(teams, cutoff, inputs)
    has_play_in = int(season) >= PLAY_IN_FIRST_SEASON
    n_seeds = 10 if has_play_in else 8
    best_of_first = 5 if 1983 <= int(season) <= 2001 else 7
    first_pattern = series_pattern(season, "first_round", best_of_first)
    later_pattern = PATTERN_2_2_1_1_1
    conference_members = {
        conference: [index for index, team in enumerate(teams) if conference_of(team) == conference]
        for conference in ("East", "West")
    }
    counts = {team: {key: 0 for key in ROUND_LABELS} for team in teams}
    counts_play_in = {team: 0 for team in teams}
    for simulation in range(int(n_simulations)):
        shocks = (
            {team: float(value) for team, value in zip(teams, shock_matrix[simulation])}
            if shock_matrix is not None else None
        )
        champions = {}
        for conference, indices in conference_members.items():
            member_ids = [teams[index] for index in indices]
            seeds = _seed_conference(wins[simulation, indices], member_ids, rng, n_seeds)
            if has_play_in:
                for seed in (7, 8, 9, 10):
                    counts_play_in[seeds[seed]] += 1
                winner_a = _sample_game(rng, matrix, seeds[7], seeds[8], shocks)
                loser_a = seeds[8] if winner_a == seeds[7] else seeds[7]
                winner_b = _sample_game(rng, matrix, seeds[9], seeds[10], shocks)
                winner_c = _sample_game(rng, matrix, loser_a, winner_b, shocks)
                bracket = {seed: seeds[seed] for seed in range(1, 7)}
                bracket[7], bracket[8] = winner_a, winner_c
            else:
                bracket = seeds
            seed_of = {team: seed for seed, team in bracket.items()}
            for team in bracket.values():
                counts[team]["made_playoffs"] += 1
            round_one = {}
            for high, low in BRACKET_PAIRS:
                winner = _sample_series(rng, matrix, bracket[high], bracket[low], seed_of,
                                        first_pattern, shocks)
                round_one[(high, low)] = winner
                counts[winner]["won_first_round"] += 1
            semi_top = _sample_series(rng, matrix, round_one[(1, 8)], round_one[(4, 5)],
                                      seed_of, later_pattern, shocks)
            semi_bottom = _sample_series(rng, matrix, round_one[(2, 7)], round_one[(3, 6)],
                                         seed_of, later_pattern, shocks)
            for winner in (semi_top, semi_bottom):
                counts[winner]["won_conf_semifinals"] += 1
            champion = _sample_series(rng, matrix, semi_top, semi_bottom, seed_of,
                                      later_pattern, shocks)
            counts[champion]["won_conf_finals"] += 1
            champions[conference] = champion
        east, west = champions["East"], champions["West"]
        index_of = {team: index for index, team in enumerate(teams)}
        record_east = wins[simulation, index_of[east]]
        record_west = wins[simulation, index_of[west]]
        if (record_east, rng.random()) >= (record_west, rng.random()):
            higher, lower = east, west
        else:
            higher, lower = west, east
        p_higher = higher_seed_series_probability(
            matrix, higher, lower, series_pattern(season, "finals", 7), shocks
        )
        title = higher if rng.random() < p_higher else lower
        counts[title]["champion"] += 1

    rows = []
    for team in teams:
        row = {"teamId": int(team), "conference": conference_of(team)}
        for key in ROUND_LABELS:
            row[f"p_{key}"] = counts[team][key] / n_simulations
        if has_play_in:
            row["p_play_in"] = counts_play_in[team] / n_simulations
        standing = next(
            (item for item in projection["projected_standings"] if item["teamId"] == team), {}
        )
        row["mean_wins"] = standing.get("mean_wins")
        rows.append(row)
    rows.sort(key=lambda row: -row["p_champion"])
    result = {
        "season": int(season),
        "as_of": projection["as_of"],
        "model": PRODUCTION_MODEL,
        "mode": (
            "regular season complete; bracket sampled from actual standings"
            if projection["games_remaining"] == 0
            else "regular-season remainder and bracket simulated"
        ),
        "n_simulations": int(n_simulations),
        "strength_sd": float(sd),
        "play_in": has_play_in,
        "games_completed": projection["games_completed"],
        "games_remaining": projection["games_remaining"],
        "tiebreak_rule": "random (real NBA tiebreakers are not modeled)",
        "teams": rows,
    }
    if team_names:
        for row in result["teams"]:
            info = team_names.get(row["teamId"])
            if info:
                row["teamName"] = info["teamName"]
    return result


def actual_bracket_odds(season, inputs, db_path=TEAM_DB_PATH, team_names=None):
    """Exact odds for the real seeded field, frozen at the day the postseason began."""
    games, seeds = load_postseason_games(season, db_path)
    if games.empty:
        raise ValueError(f"No postseason games found for season {season}.")
    field = actual_postseason_field(season, games=games, seeds=seeds)
    if field is None:
        raise ValueError(
            f"Actual seeds for the {season} postseason are not in the database."
        )
    cutoff = postseason_cutoff(games)
    teams = sorted(team for seeds_ in field.values() for team in seeds_.values())
    matrix = matchup_matrix(teams, cutoff, inputs)
    schedule = season_schedule(inputs, season)
    record = {}
    for home, away, target in zip(schedule["homeTeamId"], schedule["awayTeamId"], schedule["target"]):
        winner = home if int(target) == 1 else away
        record[int(winner)] = record.get(int(winner), 0) + 1
    odds = exact_playoff_odds(field, record, matrix, season)
    series = actual_series(season, games=games)
    outcomes = {}
    for item in series:
        loser = [team for team in item["teams"] if team != item["winner"]][0]
        outcomes[loser] = f"lost in the {item['round'].replace('_', ' ')}"
    finals = [item for item in series if item["round"] == "finals"]
    if finals:
        outcomes[finals[0]["winner"]] = "won the title"
    playoff_teams = {team for item in series for team in item["teams"]}
    for seeds_ in field.values():
        for team in seeds_.values():
            if team not in playoff_teams:
                outcomes[team] = "lost in the play-in"
    rows = []
    for team, row in odds.items():
        entry = {"teamId": int(team), **{
            key: (float(value) if isinstance(value, float) else value) for key, value in row.items()
        }, "regular_season_wins": record.get(team, 0), "actual_result": outcomes.get(team)}
        if team_names and team in team_names:
            entry["teamName"] = team_names[team]["teamName"]
        rows.append(entry)
    rows.sort(key=lambda row: -row["champion"])
    return {
        "season": int(season),
        "model": PRODUCTION_MODEL,
        "mode": "actual seeds, exact bracket probabilities",
        "strength_frozen_at": str(cutoff.date()),
        "play_in": any(len(seeds_) >= 10 for seeds_ in field.values()),
        "field": {conf: {str(seed): team for seed, team in seeds_.items()} for conf, seeds_ in field.items()},
        "teams": rows,
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Playoff bracket and championship probabilities on the frozen production model."
    )
    parser.add_argument("--season", type=int, help="Season start year, e.g. 2024.")
    parser.add_argument("--as-of", type=str, help="Cutoff date for season odds (YYYY-MM-DD).")
    parser.add_argument("--actual-bracket", action="store_true",
                        help="Exact odds for the real seeded field of --season.")
    parser.add_argument("--simulations", type=int, default=1000)
    parser.add_argument("--validate", action="store_true",
                        help="Replay 2014-2025 postseasons and write models/playoff_validation.json.")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    inputs = load_model_inputs()
    if args.validate:
        report = validate_playoffs(inputs)
        PLAYOFF_METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
        PLAYOFF_METRICS_PATH.write_text(json.dumps(report, indent=2, default=str) + "\n",
                                        encoding="utf-8")
        for key in ("games", "series"):
            row = report[key]
            print(
                f"{key}: n={row['count']} log loss model {row['model_log_loss']:.4f} vs "
                f"baseline {row['baseline_log_loss']:.4f} | Brier {row['model_brier']:.4f} vs "
                f"{row['baseline_brier']:.4f} | accuracy {row['model_accuracy']:.3f} vs "
                f"{row['baseline_accuracy']:.3f}"
            )
        for round_name, row in report["series_by_round"].items():
            print(
                f"  {round_name}: n={row['count']} log loss {row['model_log_loss']:.4f} vs "
                f"{row['baseline_log_loss']:.4f} | accuracy {row['model_accuracy']:.3f} vs "
                f"{row['baseline_accuracy']:.3f}"
            )
        for row in report["brackets"]:
            print(
                f"season {row['season']}: champion {row['actual_champion']} model title "
                f"probability {row['model_title_probability_for_champion']:.3f} "
                f"(rank {row['champion_rank_by_model']}); favorite {row['model_favorite']} "
                f"{row['model_favorite_title_probability']:.3f}"
            )
        print("bracket summary:", json.dumps(report["bracket_summary"]))
        print(f"Saved validation to {PLAYOFF_METRICS_PATH}")
        return 0
    if args.season is None:
        raise SystemExit("Provide --season (with --as-of or --actual-bracket), or --validate.")
    names = load_team_names(args.season)
    if args.actual_bracket:
        result = actual_bracket_odds(args.season, inputs, team_names=names)
    else:
        if not args.as_of:
            raise SystemExit("Provide --as-of for season odds, or --actual-bracket.")
        result = season_playoff_odds(args.season, args.as_of, inputs,
                                     n_simulations=args.simulations, team_names=names)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
