"""NBA draft lottery odds and draws.

The lottery (rules in force since 2019): the 14 teams that miss the playoffs
get 140, 140, 140, 125, 105, 90, 75, 60, 45, 30, 20, 15, 10 and 5 of 1,000
combinations, worst record first. Four picks are drawn; picks 5-14 follow in
reverse order of record. A team can fall at most four places.

``slot_pick_matrix`` is exact: it enumerates every ordered draw of four teams.
``lottery_odds`` combines it with where each team is likely to finish:

* a finished regular season uses the final standings and, when the
  postseason is in the database, the real playoff field;
* a season in progress uses the production model's forward simulation
  (``forward_projection.project_from_date``) with play-in games sampled from
  the frozen matchup matrix, exactly as the playoff-odds tool does.

Simplifications: ties in record are broken at random (the NBA splits the
tied teams' combinations and draws lots); pick swaps and protections in
trades are not modelled, so the odds are the original owners'.
"""

from itertools import permutations

import numpy as np
import pandas as pd

try:
    from src.forward_projection import project_from_date, season_schedule
    from src.playoffs import (
        PLAY_IN_FIRST_SEASON, _sample_game, _seed_conference, load_postseason_games,
        matchup_matrix,
    )
    from src.simulate_season import conference_of
except ImportError:  # pragma: no cover - direct-script support
    from forward_projection import project_from_date, season_schedule
    from playoffs import (
        PLAY_IN_FIRST_SEASON, _sample_game, _seed_conference, load_postseason_games,
        matchup_matrix,
    )
    from simulate_season import conference_of


COMBINATIONS = [140, 140, 140, 125, 105, 90, 75, 60, 45, 30, 20, 15, 10, 5]
LOTTERY_TEAMS = len(COMBINATIONS)
PICKS_DRAWN = 4


def slot_pick_matrix(combinations=COMBINATIONS, drawn=PICKS_DRAWN):
    """``m[slot][pick]``: chance the team in lottery slot ``slot`` (0 = worst) gets ``pick`` (0 = first)."""
    n = len(combinations)
    total = float(sum(combinations))
    matrix = np.zeros((n, n))
    for order in permutations(range(n), drawn):
        probability, left = 1.0, total
        for slot in order:
            probability *= combinations[slot] / left
            left -= combinations[slot]
        for pick, slot in enumerate(order):
            matrix[slot, pick] += probability
        rest = [slot for slot in range(n) if slot not in order]
        for offset, slot in enumerate(rest):
            matrix[slot, drawn + offset] += probability
    return matrix


_MATRIX = None


def _matrix():
    global _MATRIX
    if _MATRIX is None:
        _MATRIX = slot_pick_matrix()
    return _MATRIX


def _lottery_order(wins_by_team, non_playoff, rng):
    """Non-playoff teams worst first, random tie-breaks."""
    tiebreak = {team: rng.random() for team in non_playoff}
    return sorted(non_playoff, key=lambda team: (wins_by_team[team], tiebreak[team]))


def _simulated_field(season, wins_row, teams, matrix, rng, shocks=None):
    """Teams that make the playoffs in one simulated season (play-in sampled)."""
    field = set()
    play_in = int(season) >= PLAY_IN_FIRST_SEASON
    for conference in ("East", "West"):
        members = [index for index, team in enumerate(teams) if conference_of(team) == conference]
        ids = [teams[index] for index in members]
        seeds = _seed_conference(wins_row[members], ids, rng, 10 if play_in else 8)
        field |= {seeds[seed] for seed in range(1, 7)}
        if play_in:
            winner_a = _sample_game(rng, matrix, seeds[7], seeds[8], shocks)
            loser_a = seeds[8] if winner_a == seeds[7] else seeds[7]
            winner_b = _sample_game(rng, matrix, seeds[9], seeds[10], shocks)
            field |= {winner_a, _sample_game(rng, matrix, loser_a, winner_b, shocks)}
        else:
            field |= {seeds[7], seeds[8]}
    return field


def lottery_odds(season, as_of, inputs, n_simulations=1000, random_state=7, team_names=None):
    """Every team's chance at each pick, from the standings as they stand or will."""
    rng = np.random.default_rng(random_state)
    schedule = season_schedule(inputs, season)
    cutoff = pd.Timestamp(as_of)
    projection, wins, teams = project_from_date(
        season, cutoff, inputs, n_simulations=n_simulations, random_state=random_state,
        schedule=schedule, return_samples=True, team_names=team_names,
    )
    finished = projection["games_remaining"] == 0
    actual_field = None
    if finished:
        games, _ = load_postseason_games(season)
        playoffs = games[games["gameType"] == "Playoffs"] if not games.empty else games
        field = set(playoffs["hometeamId"]) | set(playoffs["awayteamId"]) if not playoffs.empty else set()
        if len(field) == 16:
            actual_field = {int(team) for team in field}
    matrix = None if actual_field is not None else matchup_matrix(teams, cutoff, inputs)
    slot_odds = _matrix()
    slot_counts = {team: np.zeros(LOTTERY_TEAMS) for team in teams}
    lottery_count = {team: 0 for team in teams}
    runs = 1 if finished and actual_field is not None else int(n_simulations)
    for simulation in range(runs):
        row = wins[simulation]
        wins_by_team = dict(zip(teams, row))
        field = actual_field if actual_field is not None else _simulated_field(season, row, teams, matrix, rng)
        non_playoff = [team for team in teams if team not in field]
        if len(non_playoff) != LOTTERY_TEAMS:
            continue  # a season with a different number of teams
        for slot, team in enumerate(_lottery_order(wins_by_team, non_playoff, rng)):
            slot_counts[team][slot] += 1
            lottery_count[team] += 1
    standings = {int(r["teamId"]): r for r in projection["projected_standings"]}
    rows = []
    for team in teams:
        slots = slot_counts[team] / runs
        picks = slots @ slot_odds
        p_lottery = lottery_count[team] / runs
        if p_lottery == 0:
            continue
        expected_pick = float((picks * np.arange(1, LOTTERY_TEAMS + 1)).sum() / p_lottery)
        info = standings.get(team, {})
        rows.append({
            "teamId": int(team),
            "teamName": info.get("teamName") or info.get("team_name") or str(team),
            "current_wins": int(info.get("current_wins", 0)),
            "current_losses": int(info.get("current_losses", 0)),
            "projected_wins": round(float(info.get("mean_wins", np.mean(wins[:, teams.index(team)]))), 1),
            "p_lottery": round(p_lottery, 4),
            "slot_distribution": [round(float(v), 4) for v in slots],
            "pick_distribution": [round(float(v), 4) for v in picks],
            "p_first_pick": round(float(picks[0]), 4),
            "p_top_four": round(float(picks[:PICKS_DRAWN].sum()), 4),
            "expected_pick": round(expected_pick, 2),
        })
    rows.sort(key=lambda r: (r["expected_pick"], -r["p_lottery"]))
    return {
        "season": int(season),
        "draft_year": int(season) + 1,
        "as_of": str(cutoff.date()),
        "mode": "final" if finished else "projected",
        "field_source": "actual postseason" if actual_field is not None else "simulated play-in",
        "n_simulations": runs,
        "combinations": COMBINATIONS,
        "slot_pick_odds": [[round(float(v), 4) for v in slot] for slot in slot_odds],
        "teams": rows,
        "rules": "2019 rules: 14 teams, 1,000 combinations, 4 picks drawn, falls of up to 4 places. "
                 "Ties broken at random; traded picks and protections not modelled.",
    }
