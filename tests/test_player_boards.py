"""Leaders, character-select cards and similar players over a fake league reference."""

import pandas as pd
import pytest

from src import player_boards as boards

BOS, NYK = 1610612738, 1610612752


def player(pid, name, games, minutes, pts, ast, ts, box, wins, tpa=0, tpm=0, fta=0, ftm=0, season=2024, age=25):
    return {
        "personId": pid, "name": name, "season": season, "games": games, "minutes": minutes,
        "pts": pts, "orb": 50, "drb": 150, "ast": ast, "stl": 40, "blk": 20, "tov": 50,
        "tpm": tpm, "tpa": tpa, "ftm": ftm, "fta": fta, "fga": pts / 2, "tsa": pts / 2 + 0.44 * fta,
        "ts": ts, "tp_pct": tpm / tpa if tpa else float("nan"), "ft_pct": ftm / fta if fta else float("nan"),
        "mpg": minutes / games, "pts_100": pts / minutes * 70, "ast_100": ast / minutes * 70,
        "trb_100": 200 / minutes * 70, "stl_100": 40 / minutes * 70, "blk_100": 20 / minutes * 70,
        "box_impact": box, "wins_added": wins, "age": age, "season_games": 82,
    }


@pytest.fixture
def league(monkeypatch):
    rows = [
        player(1, "Star Scorer", 80, 2800, 2400, 400, 0.62, 6.0, 12.0, tpa=500, tpm=200, fta=500, ftm=450),
        player(2, "Role Guard", 75, 1800, 900, 500, 0.55, 1.0, 2.0, tpa=300, tpm=110, fta=100, ftm=80),
        player(3, "Short Season", 20, 600, 700, 50, 0.70, 3.0, 2.5, tpa=40, tpm=20, fta=30, ftm=25),
        player(4, "Big Man", 70, 2000, 1000, 100, 0.64, 2.5, 4.0, tpa=0, tpm=0, fta=200, ftm=120),
        player(5, "Deep Bench", 30, 200, 60, 10, 0.40, -3.0, -0.5),
    ]
    rows += [player(100 + i, f"Filler {i}", 60, 1000 + i * 10, 500 + i * 5, 100 + i, 0.5 + i / 1000,
                    i / 30 - 0.5, 0.5) for i in range(30)]
    frame = pd.DataFrame(rows)
    monkeypatch.setattr(boards.profiles, "league_reference", lambda: {"frame": frame})
    monkeypatch.setattr(boards, "current_team_by_player",
                        lambda season, db_path=None: {1: BOS, 2: NYK, 3: BOS, 4: NYK, 5: BOS})
    monkeypatch.setattr(boards, "_team_names", lambda db_path=None: {
        BOS: {"name": "Boston Celtics", "abbreviation": "BOS"},
        NYK: {"name": "New York Knicks", "abbreviation": "NYK"}})
    monkeypatch.setattr(boards, "_positions_cached", lambda db_path: {1: "G", 4: "C"})
    return frame


def test_points_leaders_need_seventy_percent_of_games(league):
    result = boards.stat_leaders(2024, "pts", limit=3)
    assert result["min_games"] == 58  # ceil(0.7 * 82)
    assert [row["name"] for row in result["leaders"]][:2] == ["Star Scorer", "Big Man"]  # 30.0 and 14.3 per game
    assert result["leaders"][0]["value"] == 30.0 and result["leaders"][0]["team"] == "BOS"
    assert all(row["name"] != "Short Season" for row in result["leaders"])


def test_percentage_leaders_need_attempts(league):
    result = boards.stat_leaders(2024, "tp_pct", limit=5)
    names = [row["name"] for row in result["leaders"]]
    assert "Big Man" not in names  # no threes
    assert names[0] == "Star Scorer"  # .400 on 500 attempts
    custom = boards.stat_leaders(2024, "ts", min_games=10)
    assert custom["leaders"][0]["name"] == "Short Season"


def test_unknown_stat_and_season_are_errors(league):
    with pytest.raises(ValueError):
        boards.stat_leaders(2024, "dunks")
    with pytest.raises(ValueError):
        boards.stat_leaders(1990, "pts")


def test_cards_rate_players_and_filter_by_team(league):
    result = boards.player_cards(2024)
    names = [card["name"] for card in result["cards"]]
    assert names[0] == "Star Scorer"  # most wins added first
    assert "Deep Bench" not in names  # under the minutes needed for a card
    star = result["cards"][0]
    assert star["ratings"]["scoring"] >= 95 and star["ratings"]["impact"] >= 95
    assert star["ppg"] == 30.0 and star["position"] == "G" and star["team"] == "BOS"
    assert set(star["ratings"]) == {"scoring", "shooting", "playmaking", "rebounding", "defense", "impact"}
    celtics = boards.player_cards(2024, team_id=BOS)
    assert {card["team_id"] for card in celtics["cards"]} == {BOS}


def test_similar_players_passes_the_age_window(league, monkeypatch):
    seen = {}

    def fake_comps(frame, row, n_shown, age_window):
        seen["age_window"] = age_window
        return frame.head(0), frame.head(0)

    monkeypatch.setattr(boards.profiles, "_comps", fake_comps)
    monkeypatch.setattr(boards.profiles, "_row_for",
                        lambda frame, pid, season: frame[frame["personId"] == pid].iloc[0])
    boards.similar_players(1, 2024)
    assert seen["age_window"] == 1
    result = boards.similar_players(1, 2024, any_age=True)
    assert seen["age_window"] is None and result["any_age"] is True
    with pytest.raises(ValueError):
        boards.similar_players(999)
