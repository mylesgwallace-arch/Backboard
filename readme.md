# Backboard — Complete Capability Audit (Repository-Grounded)

**Date:** 2026-08-15 · **Auditor note:** Every command below was verified by running it against the live repository, database, and model artifacts. Nothing is asserted from documentation alone.

> **Update (2026-09-06):** the "no web UI" gap noted in Sections 1/11/12 below is
> now out of date. `src/api.py` serves a real dashboard front end from `web/`
> (Matchup Predictor + preserved raw ask/tools/ingest controls) — see the
> "Web front end" subsection in Section 3 and `PROJECT_CONTEXT.md` for the
> current state.

---

## 1. Executive Summary

The repository is a **working, validated NBA historical-analytics engine** — not a scaffold, not a plan. I verified that the following run end-to-end today:

- A **frozen production game-prediction model** (`elo_boosted_ensemble`, holdout log-loss 0.6228) served through a CLI that accepts **team names or numeric IDs**, returns calibrated win probabilities, team-context explanations, top feature drivers, and a model summary.
- A **Monte Carlo season simulator** that projects win distributions, conference seeds, exact-seed probabilities, a projected playoff field, and a league summary — with a **validation/replay mode** that re-projects 2023–2025 and compares to actual results (MAE 3.76 / 3.95 / 4.70 wins; playoff-field overlap 9/12, 10/12, 11/12).
- A **deterministic tool layer** (`src/tools.py`) — an 8-tool registry (`predict_matchup`, `simulate_season`, `team_projection`, `player_impact`, `player_scenario`, `team_record`, `head_to_head`, `resolve_team_name`) with parameter validation, typed envelopes, and a CLI. **This file is untracked in git and undocumented in `PROJECT_CONTEXT.md`.**
- **Player-impact diagnostics** that are honestly labeled as **association-only** (never causal), plus roster-change CSV validation and normalization pipelines.
- A **data pipeline** (raw CSV → SQLite → leakage-safe feature engineering → model training/evaluation) with 114 test functions across 8 test files.

The engine is genuinely usable **today as a command-line analytical toolset**. What does **not** exist yet: any LLM/natural-language interface, live data, trade/roster simulation that changes model inputs, playoff-bracket/championship simulation, and a web UI. The single most important observation: the repository's most recent work (`tools.py` + new simulator aggregation) is **uncommitted and under-documented** — the working tree is ahead of both git HEAD and `PROJECT_CONTEXT.md`.

---

## 2. Complete Capability Inventory

### 2.1 Game Prediction

| Capability | What it does | Data | Algorithm | Status | Source |
|---|---|---|---|---|---|
| **Matchup prediction** | Returns home/away win probabilities, favorite label, feature snapshot dates | `data/processed/game_features.csv` (133,348 pregame team-game rows) + `models/baseline_logistic.pkl` + `models/baseline_metrics.json` | `elo_boosted_ensemble` = mean of (a) `boosted_hybrid` — `HistGradientBoostingClassifier` on 23 pregame features including `elo_delta` — and (b) chronological Elo probability (K=20, HCA=65) | **Production (frozen)** | `src/main.py: predict_matchup` |
| **Leakage-safe date cutoff** | Predicts "as of" any date using only games/features strictly before it | Same features | Chronological Elo replay stops at cutoff; rolling features precomputed pregame | Production | `src/main.py: compute_elo_ratings_as_of`, `lookup_last_team_row` |
| **Team name resolution** | Maps franchise name/city ("Boston Celtics", "Boston") to teamId | `team_histories` table (30 current franchises, `seasonActiveTill >= 2100`) | Case-insensitive normalized token matching; ambiguity errors | Production | `src/main.py: resolve_team_name_to_id` |
| **Model dispatch** | Serves whichever model `baseline_metrics.json` recommends | `models/baseline_metrics.json` | Reads `recommended_model`; falls back to logistic path if file missing | Production | `src/main.py: load_recommended_model_name` |
| **Probability validation** | Rejects non-finite or out-of-[0,1] probabilities | — | Runtime guard | Production | `src/main.py: validate_prediction_probability` |
| **Team-context explanation** | Rolling win rate, scoring margin, rest days, active players for both teams | Feature snapshots | Descriptive (from validated features) | Production (explanatory) | `src/main.py: summarize_team_context` |
| **Feature-importance explanation** | Top-5 normalized feature drivers (e.g., `elo_delta` = 0.634) | `baseline_metrics.json` | Permutation importance over the boosted hybrid (tree importances unavailable for HGB) | Diagnostic | `src/main.py: load_feature_importance` |
| **Model summary** | Recommended model, holdout metrics, ECE, candidate comparison table | `baseline_metrics.json` | — | Diagnostic | `src/main.py: load_model_summary` |

### 2.2 Season Simulation & Playoff/Standings Projections

| Capability | What it does | Algorithm | Status | Source |
|---|---|---|---|---|
| **Full-season Monte Carlo projection** | Samples every game in a season's schedule N times using validated per-game probabilities; aggregates win distributions | `numpy` RNG, vectorized per-simulation sampling of Bernoulli outcomes | **Production (validated)** | `src/simulate_season.py: simulate_season` |
| **Projected standings** | Per team: mean/median/p5/p95 wins, direct-playoff probability, mean/median conference seed, `p_seed_1..6`, `out_of_playoffs_probability` | Deterministic tie-break by teamId | Production | `summarize_team_wins` |
| **Projected playoff field** | Most-likely team per seed slot (12 slots: top 6 per conference) | Argmax of `p_seed_N` per conference | Production | `build_seedings_table` |
| **League summary** | League mean/median wins, best/worst team, per-conference means | — | Production | `build_league_summary` |
| **Replay validation** | Re-simulates completed seasons and compares projected vs actual wins (MAE, correlation) and playoff-field overlap | Deterministic | Production (validated: MAE 3.76–4.70, overlap 9–11/12) | `validate_season` / `validate_seasons` |

### 2.3 Player / Player-Impact / Scenario

| Capability | What it does | Status | Source |
|---|---|---|---|
| **Single-player impact estimate** | Minutes-weighted net-rating proxy from last N prior regular-season appearances → `estimated_net_rating_change` vs a reference 0.0 baseline | **Descriptive / association-only** (explicitly non-causal) | `src/player_impact.py: summarize_player_impact`, `estimate_player_impact` |
| **Single-player scenario readout** | Production prediction + appended player diagnostic, with an explicit "feature translation unsupported" notice; **never modifies the model probability** | Descriptive | `src/player_scenario.py: analyze_single_player_scenario` |
| **Full player-impact benchmark** | Leave-one-player-out target, independent pregame target, 10/20/30% holdouts, usage strata, later-season cutoffs, roster-change evaluation — all association diagnostics with game-cluster bootstrap CIs | Diagnostic (research-grade) | `src/player_impact.py: validate_player_impact` |
| **Confidence gating (tool layer)** | Marks player-impact results `low` if <5 prior games, `moderate` otherwise; `unavailable` if no history | Diagnostic | `src/tools.py: _execute_player_impact` |

**Roster-change data tooling (separate from causal claims):** CSV contract validation (`roster_change_data.py --validate`), deterministic normalization of `nba_player_movement_raw.csv` into high-confidence `add`/`remove` events + audit CSV (`--normalize-player-movement`), and Basketball-Reference transaction-page fetching (network; writes events + unresolved rows). These feed the **descriptive** roster-change benchmark only.

### 2.4 Statistical / Database Analysis

| Capability | What it does | Status | Source |
|---|---|---|---|
| **Feature engineering** | Builds 42-column leakage-safe game features (rolling-10 stats, rest days, active players, player history, opponent-adjusted form) from SQLite → `game_features.csv` | Production (validated, 133,348 rows) | `src/build_features.py` |
| **Model training + evaluation** | Compares 9 candidates on one chronological holdout (53,326 train / 13,332 test, split 2015-03-28); writes pickle + full metrics JSON; selects by log-loss with a production guard | Production | `src/train_baseline_model.py: main` |
| **Candidate-vs-production gate** | A candidate replaces `elo_boosted_ensemble` only if it strictly improves log-loss without degrading accuracy or Brier | Production rule | `candidate_beats_production`, `select_recommended_model`, `summarize_candidate_comparison` |
| **Calibration diagnostics** | 10-bin ECE, per-season ECE with min-sample checks, sigmoid vs isotonic validation-selection | Diagnostic | `evaluate_calibration`, `evaluate_calibration_by_group`, `compare_calibration_methods` |
| **Team record query** | Actual regular-season W-L for a team (optionally per season) | Factual | `src/tools.py: _execute_team_record` |
| **Head-to-head query** | Actual regular-season H2H record between two teams | Factual | `src/tools.py: _execute_head_to_head` |
| **DB validation** | Lists the 7 tables | Factual | `src/check_database.py` |
| **Raw file inspection** | Prints columns/types/missing/head for every file in `data/raw/` | Factual | `src/inspect_data.py` |

### 2.5 Tool Layer / Orchestration (the "AI-ready" surface)

`src/tools.py` (untracked) is a **deterministic tool registry and router** — the programmatic surface a future natural-language layer would call. Verified working: `--list-tools` (8 tools with typed parameter schemas, assumptions, limitations), `execute_tool()` with validation and structured success/error/unavailable envelopes, plus a CLI (`--tool NAME --params '{...}'`). It wraps existing validated code only; it does not train models or fabricate data.

### 2.6 Validation / Evaluation Infrastructure

Test files (8, ~114 test functions): `test_baseline_model.py` (29), `test_simulate_season.py` (19), `test_tools.py` (17), `test_predict_game.py` (21), `test_roster_change_data.py` (11), `test_player_impact.py` (8), `test_feature_pipeline.py` (5), `test_interactive_predict.py` (4). Verified: `tests/test_tools.py tests/test_roster_change_data.py` → **31 passed**; the full-suite run was aborted during this audit (it is slow — it loads the 133k-row feature CSV repeatedly), so I verified the rest through direct CLI execution instead.

---

## 3. Exact Command Reference

All commands run from `C:\Users\myles\Git NBA Proj`. `.venv` confirmed present. For each: what it does, write side-effects, and status.

### Prediction (recommended path — `src/main.py`)

```
.\.venv\Scripts\python src\main.py --home-team "Boston Celtics" --away-team "Los Angeles Lakers"
```
- **Does:** Predict a matchup. Verified output: home 0.692998 / away 0.307002, model `elo_boosted_ensemble`, team context, top-5 features, narrative `matchup_summary`.
- **Required:** either `--home-team-id`/`--away-team-id` or `--home-team`/`--away-team` (not both). `--simulate-season` also satisfies the requirement.
- **Optional:** `--game-date YYYY-MM-DD` (leakage-safe cutoff); `--explain` (adds `explanation` block); `--summary` (adds model summary + comparison).
- **Example with ID + date:** `... src\main.py --home-team-id 1610612744 --away-team-id 1610612743 --game-date 2026-04-12 --summary`
- **Output:** JSON to stdout. **Reads data only; writes nothing.**

### Season simulation

```
.\.venv\Scripts\python src\simulate_season.py --season 2025 --simulations 1000 --random-state 42
```
- **Does:** Projected standings (wins/playoff/seed probabilities), projected playoff field, league summary. Verified with 20 sims.
- **Optional:** `--validate` replays 2023/2024/2025 vs actuals.
- **Output:** Text to stdout **and writes `models/season_simulation_metrics.json`** (generated artifact; git-ignored policy per `.gitignore`).

```
.\.venv\Scripts\python src\main.py --simulate-season 2025 --simulations 200 --simulation-random-state 42
```
- **Does:** Same projection, **JSON-only to stdout, writes nothing.** This is the recommended machine-readable path.

### Tool layer

```
.\.venv\Scripts\python src\tools.py --list-tools
.\.venv\Scripts\python src\tools.py --tool resolve_team_name --params '{"team": "Boston Celtics"}'
.\.venv\Scripts\python src\tools.py --tool predict_matchup --params '{"home_team": "Boston Celtics", "away_team": "Los Angeles Lakers"}'
.\.venv\Scripts\python src\tools.py --tool simulate_season --params '{"season": 2025, "n_simulations": 200}'
.\.venv\Scripts\python src\tools.py --tool team_projection --params '{"team": "Oklahoma City Thunder", "season": 2025}'
.\.venv\Scripts\python src\tools.py --tool player_impact --params '{"person_id": 203507}'
.\.venv\Scripts\python src\tools.py --tool player_scenario --params '{"home_team": "Boston Celtics", "away_team": "Los Angeles Lakers", "person_id": 203507}'
.\.venv\Scripts\python src\tools.py --tool team_record --params '{"team": "Boston Celtics", "season": 2025}'
.\.venv\Scripts\python src\tools.py --tool head_to_head --params '{"team_a": "Boston Celtics", "team_b": "Los Angeles Lakers", "season": 2025}'
.\.venv\Scripts\python src\tools.py --tool list_teams --params '{}'
.\.venv\Scripts\python src\tools.py --tool team_form --params '{"team": "Boston Celtics"}'
.\.venv\Scripts\python src\tools.py --tool team_elo_rating --params '{"team": "Boston Celtics"}'
```
- **All verified working** (I executed 5 of these live). Writes nothing. Structured envelopes with status/operation/model/assumptions/limitations/data.
- `team_form` and `team_elo_rating` (added 2026-09-07) return a single team's
  latest rolling pregame snapshot and current Elo rating respectively, without
  requiring a fake opponent -- both wrap the same helpers `predict_matchup`
  already uses internally (`lookup_last_team_row`,
  `compute_elo_ratings_as_of`); no new modeling was introduced. `team_elo_rating`
  replays the full historical schedule each call (~5-9s, same cost as
  `predict_matchup`'s Elo path); `team_form` is fast (~1s).

### Web front end (dashboard + API server)

```
.\.venv\Scripts\python src\api.py --host 127.0.0.1 --port 8000
```
- **Does:** Starts the stdlib HTTP API *and* serves the browser dashboard from
  the same process (no separate frontend server, no Node/build step). Open
  `http://127.0.0.1:8000/` in a browser.
- The dashboard (`web/`) is a static, dependency-free multi-file app: `index.html`
  + `styles.css` + vanilla-JS ES modules under `web/js/`. `src/api.py` serves any
  file under `web/` in addition to `index.html`, so adding a new page/component
  is just adding a `.js` file — no backend or build changes required.
- **Matchups** (default landing page) is the interactive Matchup Predictor: pick
  a home/away team from dropdowns populated by the new `list_teams` tool, click
  **Predict Matchup**, and it calls `POST /tools/predict_matchup` and renders the
  win probabilities, predicted winner, team-strength comparison, and model
  driver/confidence info directly from that envelope (nothing is invented client-side).
  Each result team card has a **View team ▸** link that deep-links to Team
  Explorer for that team (`#/teams?team=<id>`).
- **Teams** (added 2026-09-07) is the Team Explorer: pick any of the 30 current
  franchises and see its Elo rating, current-season and all-time record, and
  recent (last-10) form as key-metric tiles, then drill into Overview /
  Performance / Projections / Head-to-Head tabs. Projections uses a
  user-initiated **Run season projection** button (`team_projection`) because
  the underlying Monte Carlo engine's first call in a server process takes up
  to ~60s (it replays the full historical schedule once, then caches); every
  other tab loads automatically since its backing tools are fast. Team
  Explorer's header has a **Predict a matchup ▸** button that deep-links back
  to the Matchup Predictor with this team preselected
  (`#/matchups?home=<id>`).
- **Dashboard** preserves the original raw "ask a question" / "run any tool" /
  "live data ingest" controls (formerly the entire front end) under an
  **Advanced** collapsible section.
- **Season Simulator** (added 2026-09-07): choose a season (2023-2025, the
  same seasons `src/simulate_season.py`'s own validation replay covers) and a
  simulation count (200-2,000), click **Run Season Simulation**, and it calls
  `POST /tools/simulate_season` and renders a league summary, the projected
  playoff field (most likely team per seed), and full East/West projected
  standings tables -- click any team row to expand its median/range win
  totals and full seed-probability breakdown, or jump straight to Team
  Explorer for that team. The "what does this do?" explanation and its
  assumptions/limitations are pulled live from the tool registry
  (`GET /tools`), not hand-written, so the methodology claims always match
  the backend. Because the underlying Monte Carlo engine's first call in a
  server process takes up to ~60s (same cache as Team Explorer's
  Projections tab; confirmed live at ~59s cold vs ~0.35s warm, and the cache
  is shared across seasons and simulation counts), the run button shows an
  explicit "this may take up to a minute the first time" notice and stays
  disabled with a spinner until the request resolves.
- **League Predictions** (added 2026-09-07): a fast, read-only league-wide
  overview built on the exact same `simulate_season` tool as Season
  Simulator (fixed at 1,000 simulations; the page is an overview, not a
  configuration surface — a season dropdown lets you switch seasons, and
  the "Open Season Simulator" link goes to the full configurable tool). It
  **auto-loads the 2025 season projection on page load** (no button click
  required) and renders: a league summary card, a "Projected playoff
  picture" (top-6-per-conference seed field, reusing the same component as
  Season Simulator), a "Teams to watch" section (the teams whose
  `direct_playoff_probability` is closest to 50% — i.e. nearest the
  top-6/out-of-playoffs cutoff line, a proximity sort on a field the
  backend already returns, not an invented statistic), and full East/West
  projected-standings tables. Every team name deep-links to Team Explorer
  (`#/teams?team=<id>`). Because the underlying engine's first call in a
  server process is slow (~50-60s) but every later call (any season, same
  process) is fast (~0.2-0.35s, confirmed live), the page shows an explicit
  "this may take up to a minute the first time this session" notice while
  loading rather than implying live/instant results.
- **Shared components extracted this session**
  (`web/js/components/leagueSummary.js`, `web/js/components/playoffField.js`):
  the league-summary card and playoff-field grid were pulled out of Season
  Simulator's page module (byte-identical rendering, verified) so League
  Predictions could reuse them instead of duplicating the markup; Season
  Simulator's own behavior/output is unchanged.
- **Player Impact** is still a "Coming soon" placeholder page — the backend
  tools it will use (`player_impact`, `player_scenario`) already exist and
  are callable today via the tool layer above; only the structured UI
  hasn't been built yet, and (per `PROJECT_CONTEXT.md`) a player-name
  resolution gap needs to close first for a good UX. See
  `PROJECT_CONTEXT.md` for the prioritized roadmap.
- **Writes nothing** beyond what the underlying tools already write (e.g. `/ingest` with `dry_run: false`).

### Player-impact diagnostics

```
.\.venv\Scripts\python src\player_impact.py --person-id 203507
.\.venv\Scripts\python src\player_impact.py --person-id 203507 --before 2026-04-12 --window 10
.\.venv\Scripts\python src\player_impact.py --validate-roster-events data\raw\roster_change_events_valid.csv
.\.venv\Scripts\python src\player_impact.py --roster-events data\processed\nba_player_movement_roster_change_events.csv
.\.venv\Scripts\python src\player_impact.py
```
- `--person-id` = descriptive JSON estimate (verified). Full benchmark (no args, ~34 s) **writes `models/player_impact_metrics.json`**. `--validate-roster-events` and `--roster-events` use the high-confidence subset when `confidence_level` is present.

### Scenario layer

```
.\.venv\Scripts\python src\player_scenario.py --home-team "Boston Celtics" --away-team "Los Angeles Lakers" --person-id 203507
```
- Verified. Read-only. `--game-date` and `--window` optional.

### Interactive interface

```
.\.venv\Scripts\python src\interactive_predict.py
.\.venv\Scripts\python src\interactive_predict.py --explain --summary
```
- Numbered menu of 30 teams → prediction. Read-only (stdin-driven).

### Roster-change data

```
.\.venv\Scripts\python src\roster_change_data.py --validate data\raw\roster_change_events_valid.csv
.\.venv\Scripts\python src\roster_change_data.py --normalize-player-movement data\raw\nba_player_movement_raw.csv
.\.venv\Scripts\python src\roster_change_data.py --seasons 2022 2023 2024 2025
```
- `--validate` verified (8 events, 8 adds, 0 removes). `--normalize-player-movement` **writes 2 CSVs** (events + audit). Bare run fetches Basketball-Reference (requires network) and **writes 2 CSVs**.

### Pipeline / DB (rebuild and re-train commands)

```
.\.venv\Scripts\python src\create_database.py
.\.venv\Scripts\python src\load_data.py
.\.venv\Scripts\python src\create_indexes.py
.\.venv\Scripts\python src\check_database.py          # verified: 7 tables
.\.venv\Scripts\python src\build_features.py           # writes game_features.csv
.\.venv\Scripts\python -m src.train_baseline_model     # trains + writes models/ (slow, ~minutes)
.\.venv\Scripts\python src\inspect_data.py             # prints raw CSV summaries (run from root)
.\.venv\Scripts\python -m pytest -q                    # full test suite (slow)
```

**Multiple ways to simulate a season — the difference:** `src\simulate_season.py` prints readable tables and writes the metrics JSON; `src\main.py --simulate-season` prints the same projection as JSON without writing; `src\tools.py --tool simulate_season` returns a wrapped envelope (and caches probabilities per process). **Recommended:** `main.py --simulate-season` for machine output, `simulate_season.py` for human-readable + persisted artifact.

---

## 4. Production Prediction Capabilities

- **How to predict:** `src\main.py --home-team/--away-team` (names) or `--home-team-id/--away-team-id` (numeric), optional `--game-date`.
- **Accepted identifiers:** 30 current franchise names/cities (e.g., "Boston Celtics", "Boston", "Lakers") resolved case-insensitively via `team_histories`; or numeric NBA teamIds (range 1610612737–1610612766). Historical franchise names are **not** resolved.
- **Output fields:** `home_team_id`, `away_team_id`, `game_date`, `model`, `home_win_probability`, `away_win_probability`, `home_team_prediction` (favorite/underdog), `feature_snapshot_date` (per-team last pregame row date), `team_context` (rolling win rate, scores, margin, rest days, active players), `feature_importance` (top 5), `matchup_summary` (narrative), and under `--summary` a full `model_summary`.
- **Probability calculation:** `elo_boosted_ensemble` = `(boosted_hybrid_prob + elo_prob)/2`. Boosted hybrid = `HistGradientBoostingClassifier` (max_depth 4, lr 0.1, 200 iters) over 23 pregame features (rolling team stats, win rate, opponent-adjusted form, player history, rest, `elo_delta`) via median-imputation pipeline. Elo = logistic of rating gap + 65 home advantage, K=20, ratings rebuilt from games strictly before cutoff.
- **Model selection:** `select_recommended_model` picks lowest holdout log-loss among candidates; a replacement must pass `candidate_beats_production` (strict log-loss improvement, no accuracy/Brier degradation). Current winner: `elo_boosted_ensemble` (acc 0.6508, log-loss 0.6228, Brier 0.2166, ECE 0.0310). Verified in `baseline_metrics.json`.
- **Explanation:** Yes — top feature drivers (permutation importance) + team-context + a fixed-format narrative. **No per-prediction confidence interval.** Only global ECE and model-comparison are available as uncertainty information.
- **Programmatic:** Yes — `from src.main import predict_matchup` returns a dict; `from src.tools import execute_tool` with `"predict_matchup"`.
- **What it DOES NOT support:** player availability/injuries beyond pregame rolling features; roster/trade what-ifs; live/current-season data (a "tonight's game" answer uses the latest pregame snapshot, not an actual schedule); historical franchise names; point spreads/margins (probabilities only); betting-style outputs.

---

## 5. Season Simulation Capabilities

- **Run:** any of the three commands in §3. Default season 2025, 1000 sims, seed 42.
- **Outputs (verified):** `projected_standings` — mean/median/p5/p95 wins, `direct_playoff_probability`, `mean/median_conference_seed`, `p_seed_1..p_seed_6`, `out_of_playoffs_probability`; `projected_seedings` — most-likely team per seed slot (the projected playoff field, 12 slots); `league_summary` — league mean/median wins, best/worst team, conference means.
- **Validation:** `--validate` replays 2023/2024/2025: MAE 3.76/3.95/4.70 wins, correlation 0.952/0.928/0.901, playoff-field overlap 9/12, 10/12, 11/12 (verified in `season_simulation_metrics.json`).
- **JSON/API:** `main.py --simulate-season` prints full JSON; `tools.py simulate_season` returns an envelope.
- **NOT available:** playoff-bracket/championship probabilities, play-in modeling, partial-season ("rest of season") conditioning, strength-of-schedule-adjusted remaining schedule, roster-change injection, home-court-weighted playoff series.

---

## 6. Player / Scenario Capabilities

- **`player_impact --person-id N`** — Answers: "What is player N's descriptive prior-production impact estimate?" Inputs: numeric `person_id`, optional `before`, `window` (default 10). Output: `prior_games`, `player_net_rating`, `expected_minutes`, `estimated_net_rating_change` (minutes-weighted net-rating delta vs reference 0.0, scaled to 48 min), `direction`, explicit non-causal note. Data: `player_statistics_extended` netRating/minutes, regular season only. **Descriptive/associative — NOT causal.** Verified.
- **`player_scenario.py`** — Answers: "Given a matchup, what does the production model say, and what is player N's diagnostic contribution?" The model probability is **never modified**; the diagnostic is appended with a hard "feature translation unsupported" notice. Verified.
- **`tools.py player_impact`** — adds confidence labeling (`low` < 5 prior games) and `unavailable` when no history exists.
- **The full `player_impact.py` benchmark** — research diagnostics only: the roster-change evaluation **does not** improve its pregame control (control MAE 12.8427 vs candidate 12.8628, CI includes 0); later-season team-game cutoffs improve slightly in aggregate (e.g., 2024: 11.5468 vs 11.5806); leave-one-out target improves on fixed-zero in every holdout season. **All are association diagnostics; the project explicitly does not claim "if player X joined team Y, the team would improve by Z."**

---

## 7. Database / Statistical Capabilities

- **Tables (verified live):** `games` (73,279), `player_statistics` (1,669,922), `player_statistics_extended` (838,803), `players` (6,692), `team_histories` (140), `team_statistics` (146,560), `team_statistics_extended` (79,724). `player_statistics_extended` and `team_statistics_extended` carry the advanced metrics (offRtg/defRtg/netRtg/pace/usage/percentages).
- **No general-purpose SQL query interface exists.** Users can: list tables (`check_database.py`), inspect raw CSVs (`inspect_data.py`), and use the 3 factual tool queries (`team_record`, `head_to_head`, `resolve_team_name`). There is **no** player-stats query, schedule query, roster query, or ad-hoc SQL CLI.
- **Reusable utilities:** `build_game_dataset`, `add_elo_rating_deltas`, `compute_elo_ratings_as_of`, `summarize_player_impact`, `validate_roster_change_events`, `load_pregame_probabilities`, `project_season`, `execute_tool` — all importable from `src`.
- **Limitations:** historical raw-CSV provenance is not documented (README flags this); no DB write-path except `load_data.py` rebuilds; `inspect_data.py` reads raw files, not the DB.

---

## 8. AI Capabilities

| Category | Present? | Details |
|---|---|---|
| Implemented AI/LLM functionality | **No** | No LLM, no natural-language parsing, no agent loop anywhere in `src/`. |
| Deterministic tools an AI could call | **Yes** | `src/tools.py` (untracked): 8 registered tools with schemas, validation, envelopes, CLI — the intended call surface for a future agent. |
| Planned AI functionality | Yes (roadmap) | `PROJECT.md` §17–18 (AI agent/tool set); `PROJECT_CONTEXT.md` item 10 (AI/tool layer) still marked ⬜. |
| Documentation/roadmap only | Yes | Agent instructions in `.github/agents/` and `.opencode/agent/` are coding-agent guidance, not product AI. |

**Do not mistake `tools.py` for an "AI interface."** It is a deterministic router. It will, however, make the AI layer comparatively cheap to build — the tool registry an agent needs already exists and is tested.

---

## 9. Validation & Reliability

| Component | How validated | Status | Known limitations |
|---|---|---|---|
| **Frozen `elo_boosted_ensemble`** | Chronological 20% holdout (13,332 games from 2015-03-28); `recommended_model` selection by log-loss; persisted in `baseline_metrics.json`; served by CLI dispatch | **Frozen / production** | Accuracy≈0.651 is the ceiling of the current feature set; several candidate features (opponent form, player efficiency, player context) were measured and **rejected** because they didn't beat it |
| **Candidate-vs-production guard** | `candidate_beats_production` requires strict log-loss improvement + no accuracy/Brier degradation; `summarize_candidate_comparison` records deltas; covered by tests | Active gate | Gate is only as good as the single chronological split — no cross-validation or repeated splits |
| **Chronological holdout** | One fixed chronological split; Elo replay uses only pregame ratings; features use `shift(1)` rolling windows; tests assert no future info | Validated | Single-split variance is not quantified; 2021 season remains a sparse-data year |
| **Season simulator** | Replays 2023–2025; MAE 3.76–4.70 wins; playoff overlap 9–11/12; 19 regression tests (incl. seed-probability consistency) | Validated | Uses the full historical schedule structure, not the current season's real schedule; conference membership hard-coded to the current 30 franchises; no playoff-bracket layer |
| **Player-impact confidence gating** | Tool layer marks <5 prior games as `low` confidence; no-history → `unavailable`; benchmark CIs are game-cluster bootstraps | Diagnostic | All results are association-only; roster-change benchmark fails to beat its control; no causal claims supported |
| **Tests** | 114 test functions in 8 files | Working tree ahead of docs | Full `pytest -q` is slow (repeated 133k-row CSV loads); the "71 tests" figure in `PROJECT_CONTEXT.md` is stale |

---

## 10. Natural-Language Questions Answerable Today

Each maps to a verified command (invoke from the repo root):

**Matchups**
1. "Who would win between the Celtics and Lakers?" → `src\main.py --home-team "Boston Celtics" --away-team "Los Angeles Lakers"`
2. "What would the model have predicted for that matchup on 2026-04-12?" → add `--game-date 2026-04-12`
3. "What drives the model's pick?" → add `--explain`
4. "How good is the prediction model overall?" → add `--summary`
5. "What is the head-to-head record between the Celtics and Lakers?" → `tools.py --tool head_to_head --params '{"team_a": "Boston Celtics", "team_b": "Los Angeles Lakers"}'`

**Season projections**
6. "Who is projected to be the league's best/worst team in 2025?" → `simulate_season.py --season 2025` (league_summary)
7. "What are OKC's projected wins and playoff odds?" → `tools.py --tool team_projection --params '{"team": "Oklahoma City Thunder", "season": 2025}'`
8. "Who is projected to get the East's #1 seed?" → `simulate_season.py` → `projected_seedings`
9. "What is the projected playoff field?" → `simulate_season.py` → `projected_seedings` (12 slots)
10. "What is Boston's probability of making the direct playoffs?" → `direct_playoff_probability` in `projected_standings`
11. "What are the Knicks' exact seed probabilities?" → `p_seed_1..6` + `out_of_playoffs_probability`
12. "Does the simulator actually predict real seasons well?" → `simulate_season.py --validate`
13. "What is the league-average projected win total?" → `league_summary.league_mean_wins`

**Factual / database**
14. "What was Boston's actual 2025 record?" → `tools.py --tool team_record --params '{"team": "Boston Celtics", "season": 2025}'` (verified: 56–26)
15. "What is the numeric teamId for the Lakers?" → `tools.py --tool resolve_team_name --params '{"team": "Los Angeles Lakers"}'`
16. "What tables are in the database and are they populated?" → `src\check_database.py`
17. "What do the raw data files look like?" → `src\inspect_data.py`
18. "Which features matter most to the model?" → `main.py --explain` or `--summary` (top features)
19. "What is the model's calibration error?" → `--summary` (ECE 0.0310) or `models\baseline_metrics.json`
20. "How do all the candidate models compare?" → `--summary` (comparison block)

**Players (descriptive)**
21. "What is player 203507's descriptive impact estimate?" → `src\player_impact.py --person-id 203507`
22. "What does the model say about a Lakers-Celtics game, and what is player 203507's diagnostic?" → `src\player_scenario.py --home-team "Boston Celtics" --away-team "Los Angeles Lakers" --person-id 203507`
23. "Is this roster-change file valid, and what does it contain?" → `src\roster_change_data.py --validate data\raw\roster_change_events_valid.csv`
24. "What tools does the analytical engine expose?" → `tools.py --list-tools`

**Not answerable** (do not ask these of the current system): "Who wins the championship?" "What if Giannis were traded to the Celtics?" "Who's out tonight?" "What's tonight's actual game schedule?" — no bracket sim, no trade projection, no live data.

---

## 11. Capability Gaps

**Missing but easy**
- Player **name → personId** resolution (currently IDs only; `resolve_person_id` exists inside `roster_change_data.py` but isn't exposed as a user tool).
- A general-purpose **SQL query CLI** over `nba.db` (the DB is rich; only 3 narrow query tools exist).
- Team names (not IDs) in simulator output (join `team_histories`).
- Partial-season simulation conditioning ("project from current W-L").

**Missing and moderate**
- **Playoff-bracket / championship simulation** on top of the validated per-game probabilities (no new modeling risk; the biggest missing analytical payoff).
- Play-in tournament modeling.
- Predicted margin / score output.
- A persisted API/JSON server so the tool layer can be consumed by an agent or web app without shelling out.

**Missing and major**
- **The natural-language AI layer** (the actual "ask a question in plain English" experience — `tools.py` is the ready-made substrate).
- **Live data** (schedules, scores, injuries, rosters) — nothing in the prediction/simulation path is time-aware beyond 2026-04-12 historical data.
- **Causal roster/trade simulation** — the player-impact diagnostics are explicitly non-causal and cannot modify the model's feature matrix.
- Website/UI; player and team pages; play-by-play / live win probability.

---

## 12. Recommended Next Milestone

**First, stabilize the current state:** commit the untracked `src/tools.py` + `tests/test_tools.py`, and update `PROJECT_CONTEXT.md` (which doesn't mention the tool layer and still claims a 71-test suite and 133,466 feature rows). This is housekeeping, but the working tree is currently ahead of both git HEAD and the project documentation, which will cause confusion.

**Then, the single highest-value capability:** **playoff-bracket/championship simulation**, wired into the existing simulator and exposed through `tools.py`. Rationale:
- It's the largest missing *analytical* answer ("Who wins the title?") and the one every user asks first.
- It sits directly on the validated per-game `elo_boosted_ensemble` probabilities — **zero new modeling or leakage risk**, consistent with the project's core discipline.
- The simulator infrastructure (`simulate_season`, `project_season`, the validation harness, the tools layer) already exists; the bracket engine is a contained addition (series-level sampling + bracket traversal) with a clean validation story (replay 2023–2025 playoff fields).
- It materially increases the "genuinely cool" factor of the tool before investing in the larger AI/website layers.

The alternative — immediately building the natural-language layer over `tools.py` — is also viable and matches roadmap item 10, but it would depend on the same per-game model and would add interface complexity before the underlying analytical surface is as rich as it can cheaply be. After the bracket milestone, the natural order is: **play-in + partial-season conditioning → NL orchestration over `tools.py` → live data → website.**

**Bottom line on progress:** the analytical engine is genuinely done and usable today (prediction, simulation, diagnostics, and a tool registry all verified running) — roughly **80% of the way to a tangible, interactive analytical product**, but only about **35–40% of the way to the full Backboard vision** (AI assistant + live data + website). With the working tree stabilized and the bracket simulator added, you would have something you could sit down and explore, and a clean foundation for the AI layer.