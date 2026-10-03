"""Sandbox scenarios on a synthetic 30-team league (no database needed)."""

import numpy as np
import pandas as pd
import pytest

from src import era_swap as es
from src import sandbox as sb

TEAMS = list(range(1610612737, 1610612767))
HOST = 2025


def _season(season, seed, scale=1.0):
    """Ten players per team with spread-out per-36 lines."""
    rng = np.random.default_rng(seed)
    rows = []
    for t_index, team in enumerate(TEAMS):
        for p_index in range(10):
            person = 10_000 + t_index * 10 + p_index
            minutes = 2400 - 180 * p_index
            per36 = minutes / 36
            fga = rng.uniform(8, 20) * per36 * scale
            fta = rng.uniform(1, 6) * per36
            tpa = rng.uniform(0, 6) * per36
            rows.append({
                "personId": person, "teamId": team, "season": season, "firstName": "P", "lastName": str(person),
                "games": 80, "minutes": minutes,
                "fga": fga, "fgm": fga * rng.uniform(0.40, 0.55), "tpa": tpa, "tpm": tpa * rng.uniform(0.3, 0.42),
                "fta": fta, "ftm": fta * rng.uniform(0.7, 0.9), "orb": rng.uniform(0.5, 3) * per36,
                "drb": rng.uniform(2, 9) * per36, "ast": rng.uniform(1, 8) * per36,
                "stl": rng.uniform(0.5, 2) * per36, "blk": rng.uniform(0.1, 2) * per36,
                "tov": rng.uniform(1, 3) * per36, "pf": rng.uniform(1.5, 3.5) * per36,
            })
    frame = pd.DataFrame(rows)
    frame["pts"] = 2 * frame["fgm"] + frame["tpm"] + frame["ftm"]
    frame["team_minutes"] = frame.groupby("teamId")["minutes"].transform("sum")
    frame["possessions"] = frame["team_minutes"] / 5.0 / 48.0 * 98.0
    frame["pace"] = 98.0
    return es.add_rates(frame)


@pytest.fixture
def engine(monkeypatch):
    players = pd.concat([_season(2024, 1, 0.95), _season(HOST, 2)], ignore_index=True)
    context = es.league_context(players)
    team_seasons = pd.DataFrame([{"teamId": t, "season": s, "pace": 98.0, "games": 82}
                                 for t in TEAMS for s in (2024, HOST)])
    replacement = {}
    for season, group in players.groupby("season"):
        vectors = np.vstack([es.player_features(r, season, context) for r in group.to_dict("records")])
        replacement[int(season)] = vectors.mean(axis=0) - 1.0
    fake = {
        "team_seasons": team_seasons, "player_seasons": players, "context": context,
        "beta": np.array([4.3, 3.3, 0.6, -2.2, 1.4, 8.3, 8.8, 1.4, 1.1, 0.1]),
        "realization": 0.2, "realization_interval": [0.15, 0.25], "wins_slope": 2.5, "sigma": 12.6,
        "team_model_r2": 0.9,
        "calibration": {"test_mae_with_roster_change": 2.9, "test_mae_reversion_only": 3.1},
        "combined": es.combined_index(players),
        "name_of": {int(p): f"Player {p}" for p in players["personId"].unique()},
        "replacement": replacement, "season_games": {2024: 82, HOST: 82},
        "first_season": 2024, "latest_season": HOST,
    }
    monkeypatch.setattr(sb, "engine", lambda: fake)
    monkeypatch.setattr(sb, "load_team_names", lambda season=None: {
        t: {"teamName": f"Team {t}", "teamAbbreviation": f"T{t % 100}"} for t in TEAMS})
    last = {int(p): int(t) for p, t in zip(players.loc[players.season == HOST, "personId"],
                                            players.loc[players.season == HOST, "teamId"])}
    monkeypatch.setattr(sb, "_last_teams", lambda host: last)
    sb._baseline_cached.cache_clear()
    yield fake
    sb._baseline_cached.cache_clear()


def _impact(engine, person):
    row = engine["player_seasons"]
    row = row[(row.personId == person) & (row.season == HOST)].iloc[0].to_dict()
    return float(engine["beta"] @ es.player_features(row, HOST, engine["context"]))


def _roster(team):
    return [10_000 + TEAMS.index(team) * 10 + i for i in range(10)]


def test_baseline_rosters_fill_exactly_one_team(engine):
    host, rosters = sb.baseline_rosters(HOST, "replay")
    assert host == HOST
    for team in TEAMS:
        assert sum(e["share"] for e in rosters[team]) == pytest.approx(1.0)


def test_rebalance_respects_caps_and_fills_gaps_with_replacement_players(engine):
    _, rosters = sb.baseline_rosters(HOST, "replay")
    roster = rosters[TEAMS[0]][:3]          # a gutted roster: three players left
    for entry in roster:
        entry["notes"] = []
    sb._rebalance(roster, HOST)
    assert sum(e["share"] for e in roster) == pytest.approx(1.0)
    real = [e for e in roster if e["status"] != "replacement"]
    assert all(e["share"] <= sb.MAX_SHARE + 1e-9 for e in real)
    assert roster[-1]["status"] == "replacement" and roster[-1]["share"] > 0.4


def test_trading_a_better_player_helps_the_receiver_and_hurts_the_sender(engine):
    a, b = TEAMS[0], TEAMS[1]
    best_a = max(_roster(a), key=lambda p: _impact(engine, p))
    worst_b = min(_roster(b), key=lambda p: _impact(engine, p))
    moves = [{"type": "trade", "assets": [
        {"person_id": best_a, "from_team_id": a, "to_team_id": b},
        {"person_id": worst_b, "from_team_id": b, "to_team_id": a},
    ]}]
    result = sb.preview(HOST, "replay", moves)
    effects = {t["team_id"]: t for t in result["teams"]}
    assert set(effects) == {a, b}
    assert effects[b]["delta_net_rating"] > 0 > effects[a]["delta_net_rating"]
    # Calibrated = realization factor x full box-score change.
    assert effects[b]["delta_net_rating"] == pytest.approx(0.2 * effects[b]["delta_net_rating_full"], abs=1e-3)
    statuses = {r["person_id"]: r["status"] for r in effects[b]["roster"]}
    assert statuses[best_a] == "acquired" and statuses[worst_b] == "departed"


def test_moving_a_player_off_the_wrong_team_is_a_clear_error(engine):
    with pytest.raises(sb.ScenarioError, match="Move 1 \\(trade\\).*not on"):
        sb.preview(HOST, "replay", [{"type": "trade", "assets": [
            {"person_id": _roster(TEAMS[0])[0], "from_team_id": TEAMS[1], "to_team_id": TEAMS[2]}]}])
    with pytest.raises(sb.ScenarioError, match="type"):
        sb.preview(HOST, "replay", [{"type": "teleport"}])
    star = _roster(TEAMS[0])[0]
    with pytest.raises(sb.ScenarioError, match="twice"):
        sb.preview(HOST, "replay", [{"type": "trade", "assets": [
            {"person_id": star, "from_team_id": TEAMS[0], "to_team_id": TEAMS[1]},
            {"person_id": star, "from_team_id": TEAMS[0], "to_team_id": TEAMS[2]}]}])


def test_signing_from_another_season_translates_and_leaves_the_old_team(engine):
    person = _roster(TEAMS[3])[0]
    result = sb.preview(HOST, "replay", [{"type": "sign", "person_id": person, "to_team_id": TEAMS[5],
                                          "from_season": 2024}])
    effects = {t["team_id"]: t for t in result["teams"]}
    assert set(effects) == {TEAMS[3], TEAMS[5]}
    signed = next(r for r in effects[TEAMS[5]]["roster"] if r["person_id"] == person)
    assert signed["status"] == "signed" and signed["source_season"] == 2024
    assert any("era-translated" in note for note in signed["notes"])


def test_injury_and_minutes_moves_change_minutes_shares(engine):
    team = TEAMS[2]
    star = _roster(team)[0]
    hurt = sb.preview(HOST, "replay", [{"type": "injury", "person_id": star, "team_id": team, "games_missed": 40}])
    row = next(r for r in hurt["teams"][0]["roster"] if r["person_id"] == star)
    assert row["games_after"] == 40 and row["mpg_after"] == pytest.approx(row["mpg_before"], abs=0.1)
    capped = sb.preview(HOST, "replay", [{"type": "minutes", "person_id": star, "team_id": team, "mpg": 10,
                                          "games": 82}])
    row = next(r for r in capped["teams"][0]["roster"] if r["person_id"] == star)
    assert row["mpg_after"] == pytest.approx(10.0, abs=0.1) and row["games_after"] == 82
    with pytest.raises(sb.ScenarioError):
        sb.preview(HOST, "replay", [{"type": "minutes", "person_id": star, "team_id": team, "mpg": 60}])


@pytest.fixture
def setup(monkeypatch, engine):
    pairs = [(h, a) for h in TEAMS for a in TEAMS if h != a]
    games = pd.DataFrame(pairs, columns=["homeTeamId", "awayTeamId"])
    games["home_win_probability"] = 0.58
    fake = {"games": games, "teams": TEAMS, "matrix": {pair: 0.58 for pair in pairs}, "strength_sd": 0.0,
            "play_in": True, "postseason": True, "postseason_note": None, "schedule_note": "synthetic"}
    monkeypatch.setattr(sb, "season_setup", lambda *args, **kwargs: fake)
    return fake


def test_no_moves_means_no_difference(engine, setup):
    result = sb.simulate_scenario(HOST, "replay", [], n_simulations=200)
    assert all(t["delta"]["mean_wins"] == 0 and t["delta"]["p_champion"] == 0 for t in result["teams"])


def test_paired_season_and_playoffs_with_a_trade(engine, setup):
    a, b = TEAMS[0], TEAMS[20]
    best_a = max(_roster(a), key=lambda p: _impact(engine, p))
    result = sb.simulate_scenario(HOST, "replay", [{"type": "trade", "assets": [
        {"person_id": best_a, "from_team_id": a, "to_team_id": b}]}], n_simulations=300, transfer="full")
    teams = {t["team_id"]: t for t in result["teams"]}
    assert teams[b]["delta"]["mean_wins"] > 0 > teams[a]["delta"]["mean_wins"]
    assert teams[a]["changed"] and teams[b]["changed"] and not teams[TEAMS[5]]["changed"]
    for side in ("baseline", "scenario"):
        assert sum(t[side]["mean_wins"] for t in result["teams"]) == pytest.approx(len(setup["games"]), abs=0.2)
        assert sum(t[side]["p_champion"] for t in result["teams"]) == pytest.approx(1.0, abs=0.005)
        assert sum(t[side]["p_made_playoffs"] for t in result["teams"]) == pytest.approx(16.0, abs=0.01)
    story = result["stories"]["scenario"][0]
    assert len(story["conferences"]["East"]["rounds"]) == 7
    assert max(story["finals"]["score"]) == 4
    assert result["trade_verdicts"][0]["sides"][0]["team_id"] == b
