"""Current team composition from box scores + timestamped transactions.

Purpose (Phase 3): make the model's *roster-derived* inputs describe the
players a team actually has on a date, instead of whoever played for it in its
last ten games. This is bookkeeping about who is on which team -- it does not
estimate how good a move is (that is the separate, association-only
player-impact question).

How the production features are built (``build_features.py``): for each team
game, ``player_*_rolling_10`` is the sum, over every player with a box-score
row in the team's previous ten games, of that player's mean per-game value in
those games; ``active_players_rolling_10`` counts the players with minutes in
those games and ``active_players_last_game`` those with minutes in the
previous game. The model reads the home-minus-away difference of each.

A roster-adjusted snapshot starts from the team's real pregame feature row
(exactly what ``predict_matchup`` would use) and changes only those columns:

* a player who has left (traded, waived, or signed by another team after the
  snapshot game) is subtracted: his mean within the snapshot window, or 1
  from each count he was part of;
* a player who has joined is added: his mean per-game value over his last ten
  regular-season box-score rows for any team before the cutoff, and 1 to the
  ten-game active count (not to the last-game count, which he did not play).

With no transactions the adjusted snapshot equals the production snapshot
exactly. Team-level rolling stats and the Elo rating are team properties and
are never changed.

Known gaps (documented, not modeled): free agents who have not signed
elsewhere and retired players stay attributed to their last team (the feed
has no "contract expired" event); rookies and players with no NBA box-score
history add nothing to the player sums; injuries are not tracked.
"""

import argparse
import datetime
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from src.forward_projection import team_snapshots
    from src.main import TEAM_DB_PATH
    from src.roster_change_data import (
        DEFAULT_PLAYER_MOVEMENT_EVENTS_PATH,
        load_roster_change_events,
    )
except ImportError:  # pragma: no cover - direct-script support
    from forward_projection import team_snapshots
    from main import TEAM_DB_PATH
    from roster_change_data import (
        DEFAULT_PLAYER_MOVEMENT_EVENTS_PATH,
        load_roster_change_events,
    )


ROOT = Path(__file__).resolve().parents[1]
MANUAL_TRANSACTIONS_PATH = ROOT / "data" / "manual" / "roster_transactions.csv"
WINDOW = 10
SUM_FEATURES = {
    "player_minutes_rolling_10": "minutes",
    "player_points_rolling_10": "points",
    "player_assists_rolling_10": "assists",
    "player_rebounds_rolling_10": "rebounds",
}
ROSTER_FEATURES = list(SUM_FEATURES) + [
    "active_players_rolling_10",
    "active_players_last_game",
]


def load_transactions(paths=None):
    """Load and merge validated roster-change event files (add/remove).

    Defaults to the normalized NBA player-movement feed plus the optional
    manual file ``data/manual/roster_transactions.csv`` (same contract:
    ``event_id, event_timestamp, team_id, person_id, change_type, source,
    source_url``). Missing files are skipped.
    """
    if paths is None:
        paths = [DEFAULT_PLAYER_MOVEMENT_EVENTS_PATH, MANUAL_TRANSACTIONS_PATH]
    frames = []
    for path in paths:
        path = Path(path)
        if path.exists():
            frame = pd.read_csv(path)
            if frame.empty:
                continue
            frames.append(load_roster_change_events(path))
    if not frames:
        return pd.DataFrame(columns=["event_id", "event_timestamp", "team_id",
                                     "person_id", "change_type", "source", "source_url"])
    events = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["event_id"])
    if "confidence_level" in events.columns:
        events = events[events["confidence_level"].fillna("high") == "high"]
    return events.sort_values(["event_timestamp", "event_id"]).reset_index(drop=True)


def _team_game_sequence(connection, team_ids, before):
    """Each team's regular-season games strictly before ``before``, newest first."""
    placeholders = ", ".join("?" for _ in team_ids)
    frame = pd.read_sql_query(
        f"""
        SELECT ts.teamId, ts.gameId, ts.gameDateTimeEst
        FROM team_statistics ts
        JOIN games g ON g.gameId = ts.gameId
        WHERE ts.teamId IN ({placeholders})
          AND COALESCE(ts.gameType, g.gameType) = 'Regular Season'
          AND ts.gameDateTimeEst < ?
        """,
        connection,
        params=[*map(int, team_ids), pd.Timestamp(before).strftime("%Y-%m-%d %H:%M:%S")],
    )
    frame["gameDateTimeEst"] = pd.to_datetime(frame["gameDateTimeEst"])
    return frame.drop_duplicates(["teamId", "gameId"]).sort_values(
        ["teamId", "gameDateTimeEst", "gameId"], ascending=[True, False, False]
    )


def _player_rows(connection, game_ids=None, person_ids=None):
    """Per player-game-team box-score sums, as ``build_features.load_player_history``."""
    clauses, params = [], []
    if game_ids is not None:
        clauses.append(f"ps.gameId IN ({', '.join('?' for _ in game_ids)})")
        params.extend(int(game) for game in game_ids)
    if person_ids is not None:
        clauses.append(f"ps.personId IN ({', '.join('?' for _ in person_ids)})")
        params.extend(int(person) for person in person_ids)
    where = " AND ".join(clauses) if clauses else "1 = 1"
    frame = pd.read_sql_query(
        f"""
        SELECT ps.gameId, ps.playerteamId AS teamId, ps.personId,
               MIN(ps.gameDateTimeEst) AS gameDateTimeEst,
               MAX(ps.firstName) AS firstName, MAX(ps.lastName) AS lastName,
               SUM(COALESCE(CAST(ps.numMinutes AS REAL), 0)) AS minutes,
               SUM(COALESCE(ps.points, 0)) AS points,
               SUM(COALESCE(ps.assists, 0)) AS assists,
               SUM(COALESCE(ps.reboundsTotal, 0)) AS rebounds
        FROM player_statistics ps
        JOIN games g ON g.gameId = ps.gameId
        WHERE {where}
          AND COALESCE(ps.gameType, g.gameType) = 'Regular Season'
          AND ps.playerteamId IS NOT NULL
        GROUP BY ps.gameId, ps.playerteamId, ps.personId
        """,
        connection,
        params=params,
    )
    frame["gameDateTimeEst"] = pd.to_datetime(frame["gameDateTimeEst"])
    return frame


def build_roster_state(as_of, features, db_path=TEAM_DB_PATH, transactions=None,
                       team_ids=None):
    """Who is on each team at ``as_of``, relative to its pregame feature window.

    Returns ``{team_id: {...}}`` with the snapshot game, the window players,
    ``departed`` and ``arrived`` player lists (with the per-game values that
    enter the feature sums) and the transactions applied.
    """
    as_of = pd.Timestamp(as_of)
    snapshots = team_snapshots(features, as_of)
    if team_ids is not None:
        snapshots = snapshots.loc[[team for team in team_ids if team in snapshots.index]]
    if transactions is None:
        transactions = load_transactions()
    state = {}
    with sqlite3.connect(db_path) as connection:
        # The window for a snapshot row is the team's 10 games before the
        # snapshot game (build_features uses the previous 10 team games).
        sequences = _team_game_sequence(
            connection, list(snapshots.index),
            pd.Timestamp(snapshots["gameDateTimeEst"].max()).to_pydatetime()
            + datetime.timedelta(seconds=1),
        )
        windows, previous_game = {}, {}
        for team_id, row in snapshots.iterrows():
            team_games = sequences[
                (sequences["teamId"] == team_id)
                & (sequences["gameDateTimeEst"] < row["gameDateTimeEst"])
            ]
            window = team_games.head(WINDOW)
            windows[team_id] = list(window["gameId"])
            previous_game[team_id] = window["gameId"].iloc[0] if not window.empty else None
        all_games = sorted({game for games in windows.values() for game in games})
        rows = _player_rows(connection, game_ids=all_games) if all_games else pd.DataFrame()

        # Starting assignment: a player belongs to the team of his most recent
        # window appearance.
        assignment, names = {}, {}
        if not rows.empty:
            for team_id, games in windows.items():
                team_rows = rows[(rows["teamId"] == team_id) & rows["gameId"].isin(games)]
                for record in team_rows.itertuples(index=False):
                    names[int(record.personId)] = f"{record.firstName} {record.lastName}".strip()
                    current = assignment.get(int(record.personId))
                    if current is None or record.gameDateTimeEst > current[1]:
                        assignment[int(record.personId)] = (int(team_id), record.gameDateTimeEst)
        assignment = {person: team for person, (team, _) in assignment.items()}

        applied = []
        if not transactions.empty:
            first_snapshot = snapshots["gameDateTimeEst"].min()
            window_events = transactions[
                (transactions["event_timestamp"].dt.tz_localize(None) > first_snapshot)
                & (transactions["event_timestamp"].dt.tz_localize(None) < as_of)
            ]
            for event in window_events.itertuples(index=False):
                person, team = int(event.person_id), int(event.team_id)
                snapshot_date = (
                    snapshots.loc[team, "gameDateTimeEst"] if team in snapshots.index else first_snapshot
                )
                if pd.Timestamp(event.event_timestamp).tz_localize(None) <= snapshot_date:
                    continue
                if event.change_type == "add":
                    assignment[person] = team
                elif assignment.get(person) == team:
                    assignment[person] = None
                applied.append(event)

        window_players = {}
        for team_id, games in windows.items():
            if rows.empty:
                window_players[team_id] = set()
            else:
                window_players[team_id] = set(
                    int(person) for person in rows.loc[
                        (rows["teamId"] == team_id) & rows["gameId"].isin(games), "personId"
                    ]
                )
        # Arrivals: assigned to a team without appearing in its window.
        candidates = sorted(
            person for person, team in assignment.items()
            if team is not None and team in windows and person not in window_players[team]
        )
        history = pd.DataFrame()
        if candidates:
            history = _player_rows(connection, person_ids=candidates)
            history = history[history["gameDateTimeEst"] < as_of]
            name_rows = connection.execute(
                f"SELECT personId, firstName, lastName FROM players WHERE personId IN "
                f"({', '.join('?' for _ in candidates)})",
                candidates,
            ).fetchall()
            for person, first, last in name_rows:
                names.setdefault(int(person), f"{first} {last}".strip())

    for team_id, games in windows.items():
        team_rows = rows[(rows["teamId"] == team_id) & rows["gameId"].isin(games)] if not rows.empty else rows
        departed, arrived = [], []
        for person in sorted(window_players[team_id]):
            if assignment.get(person) != team_id:
                player_rows = team_rows[team_rows["personId"] == person]
                departed.append(_player_entry(person, names, player_rows, previous_game[team_id],
                                              destination=assignment.get(person)))
        for person in candidates:
            if assignment.get(person) == team_id:
                player_history = (
                    history[history["personId"] == person]
                    .sort_values("gameDateTimeEst").tail(WINDOW)
                    if not history.empty else pd.DataFrame()
                )
                arrived.append(_player_entry(person, names, player_history, None))
        state[int(team_id)] = {
            "snapshot_game_date": str(snapshots.loc[team_id, "gameDateTimeEst"].date()),
            "window_games": len(games),
            "window_players": len(window_players[team_id]),
            "departed": departed,
            "arrived": arrived,
        }
    return state, [
        {
            "event_id": event.event_id,
            "date": str(pd.Timestamp(event.event_timestamp).date()),
            "team_id": int(event.team_id),
            "person_id": int(event.person_id),
            "change_type": event.change_type,
            "source": event.source,
        }
        for event in applied
    ]


def _player_entry(person, names, rows, previous_game, destination=None):
    """Per-game means of the summed features, plus activity flags."""
    entry = {"person_id": int(person), "name": names.get(int(person), str(person))}
    if rows is None or len(rows) == 0:
        entry.update({feature: 0.0 for feature in SUM_FEATURES})
        entry.update({"games": 0, "had_minutes": False, "played_previous_game": False,
                      "no_nba_history": True})
    else:
        entry.update({
            feature: float(rows[column].mean()) for feature, column in SUM_FEATURES.items()
        })
        entry["games"] = int(len(rows))
        entry["had_minutes"] = bool((rows["minutes"] > 0).any())
        entry["played_previous_game"] = bool(
            previous_game is not None
            and ((rows["gameId"] == previous_game) & (rows["minutes"] > 0)).any()
        )
        entry["no_nba_history"] = False
    if destination is not None:
        entry["now_with_team_id"] = int(destination)
    return entry


def adjusted_snapshots(as_of, features, db_path=TEAM_DB_PATH, transactions=None,
                       roster_state=None):
    """Production feature snapshots with the roster-derived columns adjusted.

    Returns ``(snapshots, state, applied_events)``; ``snapshots`` has the same
    shape as ``forward_projection.team_snapshots`` and can be passed straight
    to ``frozen_matchup_probabilities``. A NaN production value stays NaN.
    """
    as_of = pd.Timestamp(as_of)
    snapshots = team_snapshots(features, as_of).copy()
    if roster_state is None:
        state, applied = build_roster_state(as_of, features, db_path, transactions)
    else:
        state, applied = roster_state
    for team_id, team_state in state.items():
        if team_id not in snapshots.index:
            continue
        for feature in SUM_FEATURES:
            if feature not in snapshots.columns or pd.isna(snapshots.at[team_id, feature]):
                continue
            delta = (
                sum(player[feature] for player in team_state["arrived"])
                - sum(player[feature] for player in team_state["departed"])
            )
            snapshots.at[team_id, feature] = snapshots.at[team_id, feature] + delta
        if "active_players_rolling_10" in snapshots.columns and not pd.isna(
            snapshots.at[team_id, "active_players_rolling_10"]
        ):
            snapshots.at[team_id, "active_players_rolling_10"] += (
                sum(1 for player in team_state["arrived"] if not player["no_nba_history"])
                - sum(1 for player in team_state["departed"] if player["had_minutes"])
            )
        if "active_players_last_game" in snapshots.columns and not pd.isna(
            snapshots.at[team_id, "active_players_last_game"]
        ):
            snapshots.at[team_id, "active_players_last_game"] -= sum(
                1 for player in team_state["departed"] if player["played_previous_game"]
            )
    return snapshots, state, applied


def roster_change_summary(state, team_names=None):
    """Compact per-team view: minutes/points leaving and arriving."""
    rows = []
    for team_id, team_state in state.items():
        out_minutes = sum(player["player_minutes_rolling_10"] for player in team_state["departed"])
        in_minutes = sum(player["player_minutes_rolling_10"] for player in team_state["arrived"])
        rows.append({
            "teamId": int(team_id),
            "teamName": (team_names or {}).get(team_id, {}).get("teamName"),
            "players_departed": len(team_state["departed"]),
            "players_arrived": len(team_state["arrived"]),
            "minutes_per_game_departed": round(out_minutes, 1),
            "minutes_per_game_arrived": round(in_minutes, 1),
            "points_per_game_departed": round(
                sum(p["player_points_rolling_10"] for p in team_state["departed"]), 1),
            "points_per_game_arrived": round(
                sum(p["player_points_rolling_10"] for p in team_state["arrived"]), 1),
        })
    return sorted(rows, key=lambda row: -(row["minutes_per_game_arrived"] - row["minutes_per_game_departed"]))


BACKTEST_PATH = ROOT / "models" / "roster_adjustment_backtest.json"
BACKTEST_SEASONS = list(range(2015, 2026))


def roster_adjustment_backtest(inputs, seasons=None, db_path=TEAM_DB_PATH,
                               transactions=None, n_simulations=1000):
    """Preseason projections with vs without roster-adjusted snapshots.

    For each season the cutoff is the opening day. Both variants use the
    same strength-uncertainty default and random seed, so the only difference
    is the roster-derived inputs. Scores: final-win MAE and CRPS, and the
    per-game log loss of every regular-season game with strength frozen on
    opening day (the direct test of whether the adjusted inputs predict games
    better).
    """
    try:
        from src.forward_projection import (
            crps_from_samples, frozen_matchup_probabilities, project_from_date, season_schedule,
        )
    except ImportError:  # pragma: no cover
        from forward_projection import (
            crps_from_samples, frozen_matchup_probabilities, project_from_date, season_schedule,
        )
    seasons = seasons or BACKTEST_SEASONS
    if transactions is None:
        transactions = load_transactions()
    rows = []
    for season in seasons:
        schedule = season_schedule(inputs, season)
        cutoff = schedule["gameDateTimeEst"].min().normalize()
        adjusted, state, applied = adjusted_snapshots(
            cutoff, inputs.features, db_path=db_path, transactions=transactions
        )
        final = {}
        for home, away, target in zip(schedule["homeTeamId"], schedule["awayTeamId"], schedule["target"]):
            final[int(home if target == 1 else away)] = final.get(int(home if target == 1 else away), 0) + 1
        result = {"season": int(season), "cutoff": str(cutoff.date()),
                  "transactions_applied": len(applied)}
        for label, snapshots in (("production", None), ("roster_adjusted", adjusted)):
            projection, samples, teams = project_from_date(
                season, cutoff, inputs, n_simulations=n_simulations, schedule=schedule,
                return_samples=True, snapshots=snapshots,
            )
            actual = np.array([final.get(team, 0) for team in teams])
            means = samples.mean(axis=0)
            games = frozen_matchup_probabilities(
                schedule[["homeTeamId", "awayTeamId"]], cutoff, inputs, snapshots=snapshots
            )
            p = np.clip(games["home_win_probability"].to_numpy(), 1e-6, 1 - 1e-6)
            y = schedule["target"].to_numpy()
            result[label] = {
                "mae_wins": float(np.mean(np.abs(means - actual))),
                "mean_crps_wins": float(np.mean(crps_from_samples(samples, actual))),
                "game_log_loss": float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))),
                "game_accuracy": float(np.mean((p >= 0.5) == (y == 1))),
            }
        rows.append(result)
    summary = {}
    for label in ("production", "roster_adjusted"):
        summary[label] = {
            key: float(np.mean([row[label][key] for row in rows]))
            for key in ("mae_wins", "mean_crps_wins", "game_log_loss", "game_accuracy")
        }
    improves = (
        summary["roster_adjusted"]["game_log_loss"] < summary["production"]["game_log_loss"]
        and summary["roster_adjusted"]["mae_wins"] <= summary["production"]["mae_wins"]
    )
    return {
        "seasons": [int(season) for season in seasons],
        "cutoff": "opening day of each season (preseason projection)",
        "transactions_source": "normalized NBA player-movement feed (+ manual file if present)",
        "summary": summary,
        "roster_adjustment_improves_preseason_projection": bool(improves),
        "by_season": rows,
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Show each team's roster changes since its last game (box scores + transactions)."
    )
    parser.add_argument("--as-of", help="Date YYYY-MM-DD.")
    parser.add_argument("--team-id", type=int, help="Show one team's arrivals/departures in detail.")
    parser.add_argument("--backtest", action="store_true",
                        help="Compare preseason projections with/without roster adjustment "
                             "(2015-2025) and write models/roster_adjustment_backtest.json.")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.backtest:
        try:
            from src.forward_projection import load_model_inputs
        except ImportError:  # pragma: no cover
            from forward_projection import load_model_inputs
        report = roster_adjustment_backtest(load_model_inputs())
        BACKTEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        BACKTEST_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        for row in report["by_season"]:
            print(
                f"{row['season']}: moves {row['transactions_applied']:4d} | MAE "
                f"{row['production']['mae_wins']:.2f} -> {row['roster_adjusted']['mae_wins']:.2f} | "
                f"game log loss {row['production']['game_log_loss']:.4f} -> "
                f"{row['roster_adjusted']['game_log_loss']:.4f}"
            )
        print("summary:", json.dumps(report["summary"], indent=1))
        print("improves:", report["roster_adjustment_improves_preseason_projection"])
        return 0
    if not args.as_of:
        raise SystemExit("Provide --as-of (or --backtest).")
    features = pd.read_csv(ROOT / "data" / "processed" / "game_features.csv")
    features["gameDateTimeEst"] = pd.to_datetime(features["gameDateTimeEst"])
    state, applied = build_roster_state(args.as_of, features)
    if args.team_id:
        print(json.dumps(state.get(args.team_id), indent=2))
    else:
        print(json.dumps(roster_change_summary(state), indent=2))
    print(f"transactions applied: {len(applied)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
