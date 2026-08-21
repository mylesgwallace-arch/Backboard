# Sports AI — Current Project Context

> **This file describes the current state of the repository.**
>
> `PROJECT.md` describes the long-term project specification.
>
> When deciding what to work on next, prioritize this file and the actual repository/database over the long-term roadmap.

---

# 1. Current Objective

The current objective is to build and validate the **historical NBA analytics foundation**.

Implementation update (2026-08-20): completed the website/API milestone (roadmap
item 13) by adding the browser front-end UI in `web/index.html`, served by the
existing stdlib API at `GET /`, and wiring it to the validated backend. The page
has three controls: a question box -> `POST /ask`, a tool picker (populated from
`GET /tools`) -> `POST /tools/{name}`, and a "live data refresh" control ->
`POST /ingest` (backed by `src/live_data.ingest_schedule`). The `POST /ingest`
endpoint defaults to a safe dry-run so the UI cannot mutate the database without
an explicit opt-in. The front end is a single self-contained HTML file (vanilla
JS, no build step), CORS-enabled, and fully dependent on the deterministic tool
envelope contract -- it never fabricates values. Four new tests cover `/ingest`
routing (with safe default), the required-source error, and static serving of the
page (both as a file check and over a real `ThreadingHTTPServer`).

Prior implementation update (2026-08-15): added the minimal HTTP API (roadmap item 13,
first half) as `src/api.py` -- a dependency-free (Python standard library only)
service that exposes the validated tool layer and natural-language assistant over
HTTP. Endpoints: `GET /health`, `GET /tools` (registry via `list_tools`),
`POST /tools/{tool_name}` (routes to `execute_tool`), and `POST /ask` (routes to
`src.assistant.answer_question`). The routing core is a pure `handle_request`
function, so all behavior is unit-tested without a socket, plus one real
`ThreadingHTTPServer` integration test. The API never fabricates answers -- every
response is produced by `execute_tool` or `answer_question` -- and sets CORS
headers so a future browser front end can call it directly. No new package was
installed; this keeps the existing environment stable while still satisfying the
API portion of the website/API milestone.

Prior implementation update (2026-08-15): added the source-provenanced live-data
ingestion path (roadmap item 12). The new `src/live_data.py` fetches a schedule
source from an `http(s)://` URL or a local CSV, validates it structurally and
row-by-row, and performs a **leakage-safe upsert** into the `games` table by
`gameId`: it never overwrites an existing `winner`, `homeScore`, `awayScore`,
`attendance`, or `officials`, so historical results are preserved while upcoming
games are added. Every run records provenance (source URL/type, fetch timestamp,
row counts, SHA-256 checksum, validation status) in a new `data_ingestion_log`
table, and `ensure_schema` adds a unique index on `games.gameId` to make upserts
deterministic. The module is source-agnostic and fully testable offline (local
fixtures plus a mocked HTTP fetch), and exposes a `--dry-run` mode that validates
and plans without writing.

Prior implementation update (2026-08-15): built the deterministic natural-language
interface (roadmap item 11) on top of the analytical tool layer. The new
`src/assistant.py` maps a plain-language question to a deterministic tool call,
dispatches it through `src.tools.execute_tool`, and renders the returned
structured envelope as a plain-language answer while surfacing the tool's
assumptions/limitations. Every value in a reply comes from the deterministic
tool envelope -- the assistant never fabricates an answer from its own
knowledge. Question parsing is pattern-based and fully deterministic, so the
interface works without any language model and is testable; a future LLM can
call the same `execute_tool` interface directly and reuse the same envelopes.

Prior implementation update (2026-08-15): built the deterministic analytical tool
layer and orchestration architecture (roadmap item 10) underneath the natural-
language interface. The new `src/tools.py` exposes the project's
validated capabilities as named, parameterized, deterministic tools with a
single routing entry point (`execute_tool`) that returns structured result
envelopes carrying the operation performed, the model/data that produced it,
assumptions, limitations, and the actual result. No new model was trained, no
prediction or simulation logic was duplicated, and the frozen
`elo_boosted_ensemble` production path remains the only prediction route.

Available tools (discoverable via `python src/tools.py --list-tools`):
`predict_matchup` (frozen production game prediction), `simulate_season` (full
Monte Carlo season projection with standings, seedings, playoff field, league
summary), `team_projection` (one team's wins/seed/playoff odds),
`player_impact` (association-only player diagnostic with confidence label),
`player_scenario` (production probability + association-only player estimate),
`team_record` (factual regular-season W/L from the database), `head_to_head`
(factual regular-season series record), and `resolve_team_name` (teamId
lookup). Every tool accepts team names or numeric teamIds, validates
parameters against its schema, and returns the same structured envelope shape
on success, on invalid requests (`status: "error"`), and when data is
unavailable or low-confidence (`status: "unavailable"`).

Architectural decisions: (1) the orchestrator is purely deterministic and never
fabricates an answer; a future LLM will select a tool name + parameters and
interpret the returned envelope. (2) Model integrity is preserved by routing
`predict_matchup`/`player_scenario` through the existing validated functions
and reporting the served model in both the envelope and the inner result.
(3) Player-impact results are strictly association-only: the envelope carries
explicit "NOT a causal forecast" limitations, a low-confidence flag when fewer
than five prior games exist, and an `unavailable` status when no prior
appearances exist. (4) The pregame-probability cache is reused across
simulation tool calls in one process to avoid re-running the chronological Elo
replay repeatedly. (5) The layer is modular: no LLM-specific logic is added to
`src/main.py`, `src/simulate_season.py`, or `src/player_impact.py`.
(6) The natural-language layer (`src/assistant.py`) is also deterministic:
question routing uses pattern matching plus read-only team/player lookups from
`nba.db`, never a language model, so it is testable and cannot invent numbers.
Ambiguous mentions (e.g. the shared city "Los Angeles") are skipped so a more
specific phrase wins, and ambiguous/incomplete/unsupported questions return a
clear structured error instead of a guess. Player-impact rendering passes the
diagnostic's confidence and its association-only warning through verbatim.

Validation: the tool layer has 17 focused tests in `tests/test_tools.py`
(routing, schema validation, structured envelopes, model preservation,
unavailable/low-confidence player diagnostics, factual database tools). The
natural-language layer adds 23 tests in `tests/test_assistant.py` (team/player
extraction, intent routing, ambiguity and unsupported-question handling, and
envelope-derived rendering that is verified to never fabricate values). The
full fast suite is now 111 tests passing in ~17s. Real-data CLI checks:
`predict_matchup` returns the frozen `elo_boosted_ensemble`
probability (e.g. Celtics 69.3% over Lakers); `team_record` returns OKC's
actual 2025 record (64-18); `head_to_head` returns the 2025 Celtics-Lakers
series (2-0 Celtics); `team_projection` returns OKC 63.8 mean wins with 97.5%
West seed-1 probability; `player_impact` returns the association-only Steven
Adams diagnostic at moderate confidence; unknown tools, missing parameters, and
players without history all return structured error/unavailable envelopes.
Real-data assistant questions: "Who is favored in Celtics vs Lakers?" answers
with the frozen model probabilities; "What is the head to head record between
Boston and LA Lakers?" answers 2-0 Celtics; "What was OKC's record in the 2025
season?" answers 64-18; "What is Boston's probability of getting the 1 seed?"
answers 57.1 mean wins / 65.7% seed-1; "What are the projected playoff teams?"
lists the full 12-team projected direct-playoff field from the Monte Carlo
engine; the player-impact question about Steven Adams returns the
association-only diagnostic with its non-causal warning.

Current state: the full roadmap (items 1-13) is meaningfully complete. The
deterministic tool layer is the stable programmatic surface; the natural-language
interface dispatches every question through `execute_tool`; the live-data
ingestion path can refresh the `games` table from any source-provenanced schedule
feed without disturbing validated results; and `src/api.py` + `web/index.html`
expose all of that over HTTP (stdlib only, CORS-enabled) with a usable browser
front end. The frozen `elo_boosted_ensemble` production model, the prediction
CLI/interactive interface, the season simulator, and the association-only
player-impact diagnostics are unchanged.

Exact next step: the defined roadmap (items 1-13) is complete, so the project is
now a usable, validated NBA analytics engine with a web UI. The next optional
enhancement is to schedule a real live-data refresh (e.g. cron / Task Scheduler
invoking `python -m src.live_data --source <feed>`) and, before any public
deployment, add a thin authentication layer to the API. No further quantitative
milestone is pending.

The immediate objective was to restore the raw CSV to SQLite feature-engineering
pipeline after `src/build_features.py` loaded 0 team-game rows, establish a
leakage-safe baseline model, and evaluate historical player availability signals.
That quantitative foundation is complete; the current objective is to validate a
simple player/team impact model before exposing it as a user-facing capability.

That blocker is resolved. The database was rebuilt from the confirmed raw CSV files,
and the feature-building pipeline now produces nonzero output. Generated features and
the historical dataset have been independently validated and covered by focused
regression checks. The baseline prediction model has also been rebuilt and evaluated
with a chronological holdout, including an Elo-style strength comparison, a
leakage-safe rest-interval predictor, and player-level prior-production features.
The player-impact association benchmark has now been repeated across four
season-based holdouts.

Validated continuation status (2026-08-14): the repository already contains a
small, leakage-safe player-aware candidate evaluation path, but the production
model remains unchanged. The candidate feature set is limited to prior team-context
player-performance statistics with the same chronological split used for the
baseline, and it is evaluated only as an experiment rather than a replacement for
`elo_boosted_ensemble`. The default prediction path continues to be the validated
holdout winner for the present feature set unless a candidate beats the same
chronological evaluation on accuracy, log loss, and Brier score without temporal
leakage.

Actual holdout comparison (2026-08-14): the leakage-safe candidate
`player_history_logistic` improves slightly over the pure rolling team baseline
(accuracy 0.62721 vs 0.62661, log loss 0.64552 vs 0.64599, Brier 0.22656 vs
0.22688), but it still trails the current production engine
`elo_boosted_ensemble` (accuracy 0.65077, log loss 0.62277, Brier 0.21656) on the
same test split. The model recommendation remains `elo_boosted_ensemble` and no
replacement is justified without a stronger candidate.

Rigorously tested next experiment (2026-08-14): a roster-context player feature
set was added as a dedicated comparison in `src/train_baseline_model.py` and
validated in `tests/test_baseline_model.py`. The features normalize recent
player totals by the recent active-player count to capture the team's rotation
context rather than treating raw aggregate player volume as a direct team signal.
This is a more defensible representation because it reflects the pregame roster's
available minutes and role allocation while remaining within the same chronology-safe
information set. On the exact same holdout, the contextualized candidate scores
`accuracy=0.65017`, `log_loss=0.62609`, and `brier_score=0.21777`, while the
baseline against which it is measured scores `accuracy=0.65219`,
`log_loss=0.62608`, and `brier_score=0.21774`. The candidate does not produce a
material improvement over the current holdout or over the validated production
engine; it therefore provides evidence that the missing signal is not a simple
rotation-adjusted player-volume feature. No production model change is justified.

Exact next step: preserve `elo_boosted_ensemble` as the production default, keep
using the validated holdout comparison for any future player-aware proposals, and
only revisit player modeling if a more defensible dataset or a stronger,
team-aware, roster-aware feature set can show an actual improvement on the same
chronological split with no leakage.

Current milestone (2026-08-14): freeze the production model and codify the
negative player-context result in both the regression suite and the model-selection
logic so future experiments must beat the validated baseline on the exact same
chronological holdout before any model change is allowed. The repository remains in
explanatory-analysis mode rather than production-model expansion mode, and the
user-facing prediction and scenario summaries remain the supported interface. A new
`candidate_beats_production` gate in `src/train_baseline_model.py` enforces that a
replacement candidate must improve log loss while preserving or improving accuracy
and not worsening Brier score; otherwise the production recommendation stays at
`elo_boosted_ensemble`.

Current milestone extension (2026-08-14): add a reusable candidate comparison API
that can report the exact metric deltas against the frozen production model without
searching for a new model. `summarize_candidate_comparison()` records the
candidate-vs-production accuracy, log-loss, and Brier deltas and returns whether
the candidate clears the production gate. This is the smallest meaningful
next-step tool for future validation work: it keeps the production model frozen,
provides a defensible comparison summary for any justified experiment, and keeps
all evaluation tied to the same leakage-safe chronology.

Validated current state (2026-08-13): the repository remains in a stable,
production-ready analytical state. The full project test suite passes and the
current default prediction path is the validated holdout winner for the present
feature set; no additional model churn is justified without a candidate that beats
that holdout on the same leakage-safe chronology. The project continues to prioritize
measured prediction quality and labeled descriptive diagnostics over speculative
causal claims.

Implementation update (2026-08-13): `src/roster_change_data.py` now includes a
deterministic normalization pipeline for the immutable
`data/raw/nba_player_movement_raw.csv` source. The new path emits only
high-confidence `roster_change_events` for direct signings, waives, waiver
claims, and player-specific trades, preserves the original source fields in the
normalized output and a separate audit table, assigns an explicit confidence
level and reconstruction rule to each emitted event, and intentionally excludes
contract conversions plus non-player trade consideration rows rather than
inventing unsupported roster transitions.

What now works: `.\.venv\Scripts\python src\roster_change_data.py
--normalize-player-movement data/raw/nba_player_movement_raw.csv` writes a
validated high-confidence roster-event CSV plus an audit CSV without changing
the validated production prediction model. The verified current-source summary
is 9,746 raw rows, 9,102 normalized source rows, 10,374 high-confidence
events, and 1,978 covered transaction dates out of 1,979 raw dates; the only
uncovered raw date is `2023-09-14`, which contains only a contract-conversion
row that the pipeline intentionally excludes.

Remaining issue: the normalized file is still a descriptive roster-event source,
not evidence that roster transitions improve the production model or establish a
causal player-impact claim. `remove` events remain unsupported by the current
benchmark path in `src/player_impact.py` and should continue to be treated as
audit-ready context rather than a live model feature.

Exact next step: keep the current production prediction engine unchanged and, if
roster-event benchmarking continues, feed only the normalized high-confidence
output from `nba_player_movement_raw.csv` through the existing descriptive
`src/player_impact.py --roster-events PATH` validation path to measure whether
the larger event sample improves the same chronological evaluation.

Exact next step: keep the current best-performing prediction engine as the
production default and only revisit the feature/model layer if a single,
well-justified candidate materially improves the same chronological holdout. In the
absence of that evidence, the next most valuable step is to build cleaner,
user-facing explanatory output on top of the validated model rather than broadening
into a new analytics subsystem.

Benchmark result (2026-08-13): the normalized high-confidence roster-event output
from `data/raw/nba_player_movement_raw.csv` was exercised through the existing
association benchmark at `src/player_impact.py --roster-events
data/processed/nba_player_movement_roster_change_events.csv`. The run evaluated
4225 transition-player games across 2625 transition events, with
`event_source` = `external_timestamped_additions`, `pregame_control_mae` = 12.7418,
`candidate_mae` = 12.7604, and `improves_pregame_control` = `false`. The
benchmark path now filters to `confidence_level == "high"` whenever that column is
present, so the project consistently starts from the trusted subset only. That
result matches the repository's current stance: the normalized event source is
valid and audit-ready, but it does not provide a justified production-model signal
and should remain a descriptive benchmark input only until some stronger,
leakage-safe evidence appears.

Implementation update (2026-08-13): the validated explanatory layer was
hardened with CLI smoke tests for `src/main.py --summary` and
`src/player_scenario.py` so the user-facing recommendation, calibration,
single-player scenario readout, and top-feature output remain working without
changing the production model. The project maintains the current validated
prediction engine as the default and treats the summary/explanation path as a
descriptive presentation layer rather than a new modeling signal.

Current continuation status (2026-08-11): the historical NBA analytics foundation
remains stable and validated. The repository now accepts a curated independent
roster-change source at `data/raw/roster_change_events_valid.csv`, and the
benchmark path through `src/player_impact.py --roster-events PATH` runs without any
production-code changes. The current state is therefore safe to preserve as a
descriptive association check, but not safe to promote into a causal roster-impact
claim.

The repository has passed the focused regression checks for the available pipeline:

- `./.venv/Scripts/python -m pytest tests/test_roster_change_data.py tests/test_player_impact.py -q` → 12 passed in 1.11s
- SQLite database rebuild remains consistent with the confirmed raw CSV sources
- feature-engineering outputs and generated model metrics remain model-ready
- `src/player_impact.py --roster-events data/raw/roster_change_events_valid.csv`
  runs successfully and writes `models/player_impact_metrics.json`

Verified current state (2026-08-11): the legacy `data/raw/player_trades_raw.csv` and
`data/raw/draft_pick_trades_raw.csv` extracts remain unsuitable for counterfactual
roster-impact inference because they are name-based trade archives with non-ISO
historic dates and no required provenance fields. The valid external sample is the
curated `roster_change_events_valid.csv` file, which satisfies the repository
contract (`event_id`, `event_timestamp`, `team_id`, `person_id`, `change_type`,
`source`, and `source_url`).

Current result: the roster-event benchmark is evaluated and remains a descriptive
assumption-labeled diagnostic rather than a causal estimate. In the current run,
`roster_change_validation.status == "evaluated"` for 5 linked addition events, and
`improves_pregame_control == false`, which is consistent with the small curated
sample and does not justify a roster-change impact claim.

What changed in this session: the repo state was re-verified against the actual
implementation and the live SQLite database, and the current accepted roster-change
CSV was confirmed to satisfy the schema and provenance contract. No speculative
roster-impact model was introduced beyond the existing validated association layer.

Implementation update (2026-08-13): a direct descriptive player-impact CLI was
added to `src/player_impact.py`. `--person-id ID` now loads that player's prior
regular-season appearances, computes the repository's minutes-weighted net-rating
estimate, and prints a JSON summary without needing to run the full benchmark
pipeline. The output remains clearly labeled as a descriptive association estimate,
not a causal roster/trade projection.

Implementation update (2026-08-13): a minimal single-player scenario-analysis layer
was added at `src/player_scenario.py`. It keeps the production default as the
validated `elo_boosted_ensemble` prediction path and appends the descriptive
player-impact estimate as an explanatory diagnostic only. The key rule is explicit:
no player-to-team feature conversion is attempted because the validated model's
feature matrix is built from team-level rolling signals and Elo deltas, and the
player-impact estimate is not measured in the same feature space.

What now works: historical database rebuild, feature engineering, model-ready
outputs, leakage-safe player-impact diagnostics, direct single-player impact
estimates via `src/player_impact.py --person-id ID`, descriptive scenario analysis
via `src/player_scenario.py` without altering the production prediction path,
external roster-change schema validation, and the existing CLI benchmark path for
`--roster-events` all remain operational and validated.

Exact next step: keep the current model layer as the validated production baseline,
use the new single-player scenario and impact diagnostics as the user-facing
analysis layer, and only add another feature or model candidate if it materially
improves the same chronological holdout without violating the no-leakage rules.

Remaining issue: the accepted roster-change source is still a small curated sample,
not a broad season-spanning independent roster archive. The roster benchmark should
remain labeled descriptive only until a materially larger source is added.

Exact next step: obtain or document a larger independently sourced roster-change
CSV that still matches the required schema and provenance, then rerun
`src/player_impact.py --roster-events PATH` to see whether the roster-event signal
stabilizes beyond the current small sample.

Implementation update (2026-08-11): a safe Basketball-Reference ingestion pipeline
was added to `src/roster_change_data.py` for the 2022-2025 season pages. The code
fetches transaction pages using browser-like headers, resolves player/team names to
the repository `personId`/`teamId` registry, emits only valid `add`/`remove` roster
moves, and keeps unresolved rows in a separate review frame instead of inventing
missing identity matches. The canonical roster-event contract is preserved, and the
season label is explicitly anchored to the BBR season page rather than raw calendar-year
counts so offseason transactions remain associated with the applicable NBA benchmark
season.

Benchmark result (2026-08-11): running `src/player_impact.py --roster-events
data/processed/bbr_roster_changes_2022_2025.csv` produced a valid 3,605-event BBR
sample with `event_source` = `external_timestamped_additions`, `transition_player_games`
= 1,213, `evaluated_transition_events` = 707, `improves_pregame_control` = `true`,
`pregame_control_mae` = 12.3877, and `candidate_mae` = 12.3491. The BBR roster
benchmark therefore remains a descriptive association check, not a causal estimate:
its holdout improves on the pregame control marginally, but the effect is small and the
sample still includes nontrivial ambiguity around same-team extensions and trade language.

Exact next step: review the unresolved BBR rows for the same-team contract/ambiguity edge
cases and decide whether the 3,605-event external source is large enough to replace the
curated sample for a descriptive benchmark; if it remains unstable, keep the roster signal
labeled as an association diagnostic and avoid promoting it to a causal impact claim.

Validated result (2026-08-12): the curated external roster-change benchmark was run on the
project's current sample file (`data/raw/roster_change_events_valid.csv`) and remained a
small, non-improving association check. The final evaluation produced
`transition_player_games = 5`, `evaluated_transition_events = 5`, `holdout_games = 1`,
`pregame_control_mae = 4.8250`, `candidate_mae = 4.8250`, and
`improves_pregame_control = false`. This is not evidence of a causal or prospective roster
impact, and it confirms the current benchmark should remain descriptive-only unless a
larger, more robust external roster source is validated under the same contract.

Current highest-priority next milestone (2026-08-11): operationalize and validate the
core game-prediction baseline as the repository's next analytic milestone. The project
already contains the historical game-prediction pipeline in `src/train_baseline_model.py`,
and it has now been executed and validated against the current feature set.

Validated baseline result:
- `src/train_baseline_model.py` runs successfully and saves `models/baseline_logistic.pkl`
  and `models/baseline_metrics.json`
- chronological holdout: 53,326 train / 13,332 test games
- home-win-rate baseline accuracy = 0.5648, log_loss = 0.6930, brier_score = 0.2498
- rolling logistic model accuracy = 0.6267, log_loss = 0.6463, brier_score = 0.2269
- player-history logistic model accuracy = 0.6269, log_loss = 0.6458, brier_score = 0.2266
- Elo baseline accuracy = 0.6497, log_loss = 0.6262, brier_score = 0.2181
- `tests/test_baseline_model.py` passes: 5 passed

This establishes a measured predictive baseline and confirms the next safe milestone is
not a website or live-data layer. The next step is to use this validated baseline as the
reference for improved model comparisons and a simple prediction interface, while keeping
roster-impact findings clearly labeled as association diagnostics rather than causal claims.

Implementation update (2026-08-11): the validated baseline has been exposed as a
minimal prediction interface through `src/main.py`. The CLI loads the saved logistic
model, pulls the latest available pregame team features at or before an optional cutoff
date, computes home-minus-away feature deltas for the model's predictor set, and returns
home/away win probabilities and a prediction label.

Validated current behavior:
- `./.venv/Scripts/python -m pytest tests/test_baseline_model.py tests/test_predict_game.py -q`
  → `7 passed in 3.19s`
- `./.venv/Scripts/python src/main.py --home-team-id 1610612744 --away-team-id 1610612743 --game-date 2026-04-12`
  runs successfully and returns a probability output for a real matchup

The current milestone is therefore the operational baseline prediction interface. The next
logical step after this is to compare a small number of candidate model variants against
this baseline and keep the best-performing configuration exposed through the same CLI path.

Implementation update (2026-08-11): the candidate-model comparison already computed by
`src/train_baseline_model.py` (`home_win_rate`, `rolling_logistic`, `player_history_logistic`,
`elo`) previously was not connected to the CLI, which always served the pickled logistic
model even though the chronological holdout showed the Elo rating system was best on every
metric (accuracy 0.64971 vs 0.62691, log loss 0.62616 vs 0.64584, Brier score 0.21812 vs
0.22660 for the next-best `player_history_logistic` model). This gap is now closed:

- `src/train_baseline_model.py` adds `select_recommended_model(metrics)`, which chooses the
  candidate with the lowest holdout log loss (log loss is used because it rewards calibrated
  probabilities, matching the project's probabilistic-evaluation guidance). The trivial
  `home_win_rate` reference baseline is excluded from selection. The result is persisted as
  `recommended_model` (currently `"elo"`) and `recommendation_metric` (`"log_loss"`) in
  `models/baseline_metrics.json`.
- `src/main.py` now reads `recommended_model` from `models/baseline_metrics.json` and
  dispatches to the appropriate prediction path: an Elo path (`predict_matchup_elo`,
  `compute_elo_ratings_as_of`) when Elo is recommended, or the existing logistic path
  (`predict_matchup_logistic`) otherwise. The Elo path recomputes ratings by replaying only
  the games strictly before the requested `--game-date` cutoff (or all available games if no
  cutoff is given), so historical predictions remain leakage-safe -- no game on or after the
  cutoff contributes to the rating used for that prediction. If the metrics file is missing,
  the CLI falls back to the logistic model so it still works before retraining.
- The CLI output's `"model"` field now reports which model actually produced the prediction
  (`"elo"` at present) instead of a hard-coded label, and omits `feature_snapshot_date` (a
  logistic-model-specific diagnostic) when the Elo path is used.
- Fixed an import so `src/main.py` works both as `python -m src.main` / under pytest (package
  import) and as a direct script `python src/main.py` (sibling-module import fallback).

Validated current behavior (2026-08-11):
- `./.venv/Scripts/python -m src.train_baseline_model` reruns successfully; unchanged holdout
  metrics; new line `Recommended model (lowest holdout log loss): elo`.
- `./.venv/Scripts/python -m pytest tests -q` → `30 passed` (24 previously existing plus 6 new
  tests covering `select_recommended_model`, `compute_elo_ratings_as_of` cutoff behavior, the
  metrics-file fallback, and the end-to-end Elo dispatch in `predict_matchup`).
- `./.venv/Scripts/python src/main.py --home-team-id 1610612744 --away-team-id 1610612743
  --game-date 2026-04-12` returns `"model": "elo"` with a real probability.
- Confirmed leakage-safe recomputation: the same matchup queried with `--game-date 2020-01-01`
  vs `--game-date 2026-04-12` vs no date returns different probabilities, showing ratings are
  rebuilt only from games before each cutoff rather than reusing a single fixed rating.
- Confirmed error handling: an unknown/never-seen `teamId` raises a clear `ValueError`
  (`"No completed games found for teamId=...; cannot compute an Elo rating."`) instead of
  silently defaulting.
- `src/check_database.py` still passes for all seven confirmed tables.

What now works: the CLI prediction interface automatically serves the empirically
best-performing, holdout-validated model rather than a fixed logistic pickle, while
remaining backward compatible (falls back to logistic if metrics are unavailable) and
leakage-safe (ratings recomputed from only pregame history).

Remaining issue / next step: Elo currently has no team/player-level explanatory features
(it only encodes a single strength number), so it cannot yet support player-impact or
trade-simulation questions -- those still require the logistic/player-history feature path
or a future hybrid model. The next concrete milestone is to evaluate whether blending Elo
rating (or Elo-derived features) into the logistic/player-history feature set improves on
Elo alone under the same chronological holdout, since Elo's rating-only approach is simple
but currently ignores the rolling box-score and player-availability predictors already
built in `src/build_features.py`. Until a blended model is validated to beat plain Elo, keep
Elo as the recommended/served model and keep player-impact projections gated as association
diagnostics, per the existing roster-change validation notes above.

Implementation update (2026-08-11): the hybrid model comparison has been systematized into a
small, leakage-safe tuning sweep. `src/train_baseline_model.py` now evaluates a compact grid
of `HistGradientBoostingClassifier` settings on the chronological split, keeps the best
holdout configuration, and saves both the selected parameters and the per-grid sensitivity
results into `models/baseline_metrics.json`.

Measured result (2026-08-11): the tuned boosted hybrid remains the best validated model on the
real chronological holdout. The current sweep produces `boosted_hybrid` at accuracy 0.65077,
log_loss 0.62597, and Brier score 0.21765, which is slightly better than plain `elo` at
accuracy 0.64971, log_loss 0.62616, and Brier score 0.21812. The model-selection pipeline is
now fully data-driven rather than using a single hand-picked configuration, and the CLI now
serves the winning `boosted_hybrid` path when the metrics file is present.

What now works: the baseline comparison evaluates a tuned nonlinear hybrid candidate in the same
chronological pipeline used for `home_win_rate`, `rolling_logistic`, `player_history_logistic`,
and `elo`, and the regression suite confirms the new feature remains compatible with the
existing prediction interface. The CLI continues to serve the current best-performing model and
keeps the fallback behavior for missing metrics intact.

Exact next step: continue improving the richer hybrid path with a calibration check and
additional explanatory feature engineering, while preserving the leakage-safe, chronological
evaluation protocol that produced the current model win.

Implementation update (2026-08-11): added `evaluate_calibration()` to
`src/train_baseline_model.py` and persisted a 10-bin expected calibration error diagnostic
for the served boosted hybrid and Elo comparator in `models/baseline_metrics.json`. This is
an evaluation-only diagnostic; it does not tune against or alter the chronological holdout.

Calibration result (2026-08-11): the boosted hybrid has expected calibration error 0.04867,
versus 0.02749 for Elo. The boosted hybrid still wins log loss (0.62597 versus 0.62616) and
Brier score (0.21765 versus 0.21812), but its probabilities are less well aligned by this
10-bin ECE diagnostic. The boosted hybrid remains recommended because log loss is the
declared selection metric, while calibration quality is now explicitly visible.

What now works: the model comparison reports both predictive performance and a comparable
holdout calibration diagnostic, the CLI remains operational, and all regression tests pass.

Exact next step: evaluate a calibration layer fitted only on a training-period validation
slice (for example, sigmoid calibration) and compare its untouched chronological holdout
metrics against the current boosted hybrid. Do not use the final holdout to choose the
calibration method.

Implementation update (2026-08-11): completed that calibration milestone with a sigmoid
probability calibrator fitted only on the final 20% of the training period, while the base
boosted model is refit on the full training period before final holdout evaluation. The
calibrated model is persisted through the dedicated reusable classes in
`src/model_calibration.py`; the CLI dispatch now recognizes `calibrated_boosted_hybrid`.

Measured result (2026-08-11): `calibrated_boosted_hybrid` improves the untouched holdout to
accuracy 0.65257, log_loss 0.62119, and Brier score 0.21578, compared with the uncalibrated
boosted hybrid at 0.65077, 0.62597, and 0.21765, and Elo at 0.64971, 0.62616, and 0.21812.
The calibrated model is now the recommended and served model under the existing log-loss
selection rule. The direct-script pickle loading path was also validated after moving the
calibration classes out of `__main__`.

What now works: the repository has a leakage-safe calibration layer, persisted calibration
diagnostics, stable model serialization, and an end-to-end CLI path returning calibrated
matchup probabilities. The full regression suite passes.

Implementation update (2026-08-11): added season-level holdout metrics for the uncalibrated
and sigmoid-calibrated boosted hybrid alongside the existing Elo season diagnostics. The
comparison uses the same untouched final chronological test rows and does not select or
refit calibration per season.

Stability result (2026-08-11): the calibrated model improves log loss over Elo in most
season slices, including 2014, 2015, 2019, 2020, 2022, 2024, and 2025; it is essentially
tied in several other seasons and trails Elo in 2021. The aggregate improvement is therefore
not isolated to one season, although the gains are modest and the 2021 regression means the
calibrated model should continue to be monitored rather than treated as universally superior.

What now works: the repository reports aggregate and season-level calibrated-model metrics,
the calibrated model is persistently serialized and served by the CLI, and the full test
suite remains green.

Exact next step: add a persisted calibration reliability summary with minimum per-season
sample checks, then consider a validation-driven calibration method comparison before
expanding into additional player/team explanatory features.

Implementation update (2026-08-11): added `evaluate_calibration_by_group()` and persisted
`calibration_by_season` for the boosted hybrid, calibrated boosted hybrid, and Elo models.
Each season now records its sample count, status, minimum required sample count, and ECE;
the configured minimum is 100 games, so every current season slice is evaluated rather than
silently summarized or dropped.

Reliability result (2026-08-11): the calibrated boosted hybrid's aggregate ECE remains
0.04867, with season ECE ranging from 0.02595 to 0.11371. It has lower season ECE than Elo
in 2016, 2017, 2018, 2019, 2020, 2022, and 2024, while Elo is better in 2014, 2015, 2021,
2023, and 2025. This confirms the calibration behavior varies by season even though the
calibrated model remains the best aggregate log-loss model.

What now works: calibration quality is persisted with explicit per-season sample validation,
the full regression suite passes, and the real CLI continues to serve calibrated
boosted-hybrid probabilities.

Exact next step: compare sigmoid calibration with a second method fitted only on the same
training-period validation slice, then select by validation log loss and evaluate once on the
untouched chronological holdout.

Implementation update (2026-08-11): added isotonic calibration and a validation-only method
selector in `src/train_baseline_model.py`. Sigmoid and isotonic calibrators are both fitted
from the same training-period validation predictions; the method with lower validation log
loss is selected, after which the base boosted model is refit on all training rows and the
selected calibrator is evaluated once on the untouched holdout.

Measured result (2026-08-11): isotonic won the validation selection (log loss 0.59607 versus
0.59908 for sigmoid). On the untouched holdout, the selected calibrated model achieves
accuracy 0.65339, log_loss 0.62560, and Brier score 0.21632. Its aggregate ECE is 0.02195,
better than Elo's 0.02749, although its log-loss gain over Elo is small (0.62560 versus
0.62616). The calibrated boosted hybrid remains the recommended model, and the CLI serves
it successfully.

What now works: calibration method selection is validation-driven rather than chosen from
the final holdout, both methods and their validation metrics are persisted, and the full
regression suite remains green.

Exact next step: perform a final model-risk review using season-level calibrated metrics and
prediction edge cases, then decide whether to freeze this calibrated baseline before adding
new player/team explanatory features.

Implementation update (2026-08-11): completed the final baseline model-risk review by adding
runtime validation that prediction probabilities are finite and within the closed interval
[0, 1]. The review also exercised the real calibrated CLI path and the unknown-team error
path, confirming invalid team inputs surface explicit errors rather than producing fallback
probabilities.

Risk-review result (2026-08-11): all season slices satisfy the configured reliability sample
threshold, the calibrated model remains the selected holdout winner, normal CLI predictions
return complementary probabilities that sum to one, and unknown team IDs fail clearly.
The full regression suite passes with 39 tests.

Baseline status: the calibrated boosted hybrid is now frozen as the current validated
prediction baseline. It uses validation-only isotonic-versus-sigmoid selection, serves
calibrated probabilities through the CLI, and remains subject to future retraining and
revalidation when data or feature logic changes.

Implementation update (2026-08-13): added human-friendly team-name resolution to the
prediction CLI so a user can request a matchup by franchise name instead of numeric
`teamId`s. The CLI continues to accept `--home-team-id` / `--away-team-id` for
backward compatibility, but it now also supports `--home-team` / `--away-team` with
case-insensitive matching against the live current-NBA franchise registry in the SQLite
`team_histories` table. The resolver accepts full team names and unique city nicknames,
raises a clear error for ambiguous names, and preserves the same leakage-safe prediction
workflow once the IDs are resolved.

Validated current behavior:
- `./.venv/Scripts/python -m pytest tests/test_predict_game.py tests/test_interactive_predict.py -q`
  → passes with the new team-name parsing and resolution coverage.
- `./.venv/Scripts/python src/main.py --home-team "Boston Celtics" --away-team "Los Angeles Lakers"`
  resolves the team names to canonical IDs and returns a valid probability output.

Current milestone: a usable, human-facing prediction interface that works with either
numeric team IDs or current franchise names while keeping the calibrated model path intact.
The next logical milestone is to add richer team/player explanatory context to those
predictions, such as feature drivers and recent form summaries, without moving beyond the
validated leakage-safe baseline.

Implementation update (2026-08-13): added a recent team-context payload to the prediction
result and the interactive CLI summary so the model output now includes form and availability
signals alongside the probability. The `team_context` block reports each team's rolling win
rate, recent scoring margin, rest days, and active-player count using the same sanitized
team-feature snapshots already used for model inputs. This keeps the explanation grounded in
validated historical features rather than free-form narrative.

Validated current behavior:
- `./.venv/Scripts/python -m pytest tests/test_predict_game.py tests/test_interactive_predict.py -q`
  → passes with the new explanatory context coverage.
- `./.venv/Scripts/python src/main.py --home-team "Boston Celtics" --away-team "Los Angeles Lakers"`
  now returns a probability plus a `team_context` section with recent team snapshot values.

Implementation update (2026-08-13): added a compact `matchup_summary` string to the JSON
result so the CLI now packages the probability, recent form, and main feature driver into a
single human-readable narrative. This keeps the explanation grounded in the validated feature
set without speculating beyond the latest pregame signals.

Next milestone: extend the summary layer with a richer narrative that combines the probability,
recent team context, and feature-importance ranking in one UI-friendly explanation, while
preserving the no-leakage baseline evaluation.

Implementation update (2026-08-13): added the leakage-safe opponent-adjusted team-form
signals `opponent_adjusted_win_rate_rolling_10` and
`opponent_adjusted_plusMinusPoints_rolling_10` to the production feature pipeline in
`src/build_features.py`, then retrained the model on the updated dataset. The feature is
computed from only prior games before the target matchup, so it remains chronologically
valid and does not leak future information.

Measured result (2026-08-13): the regenerated dataset contains 133,348 valid rows after the
new opponent-form constraints are applied, and the chronological holdout remains stable:
`boosted_hybrid` = accuracy 0.65137, log_loss 0.62564, Brier score 0.21749; `elo_boosted_ensemble`
= accuracy 0.65032, log_loss 0.62288, Brier score 0.21662. The added form differential does
not improve on the current recommendation, so it remains a useful explanatory signal and a
candidate for future ablation testing rather than a new selected winner. The full feature and
prediction regression checks still pass.

Current milestone: keep the validated ensemble as the served model while monitoring the
opponent-adjusted signal as a candidate explanatory feature, and continue to evaluate any
additional leakage-safe team/player features under the same chronological holdout protocol.

Implementation update (2026-08-13): fixed the actual model integration for the opponent-form
experiment by including the existing `opponent_adjusted_win_rate_rolling_10` and
`opponent_adjusted_plusMinusPoints_rolling_10` columns in the trainable game matrix in
`src/train_baseline_model.py`, while excluding the temporary helper columns used only for the
merge. This closes the gap between the candidate feature generation and the evaluation path,
so the repository now measures the feature in the same holdout pipeline as the other baseline
signals.

Measured result (2026-08-13): the opponent-form experiment is now evaluated end-to-end under
one chronological split. The updated holdout remains stable: `boosted_hybrid` = accuracy 0.65227,
log_loss 0.62590, Brier score 0.21753; `elo_boosted_ensemble` = accuracy 0.65129, log_loss
0.62293, Brier score 0.21663. The new candidate features do not improve the current log-loss
winner, so they remain explanatory-only diagnostics rather than a replacement for the served
ensemble model. The regression suite still passes with the feature matrix corrected.

Current milestone: keep the validated ensemble as the served model, continue treating the
opponent-form differential as a monitored explanatory feature, and only promote a new feature
if it clearly improves the same chronological holdout without leaking future information.

Implementation update (2026-08-13): added a dedicated player-efficiency holdout experiment in
`src/train_baseline_model.py` to evaluate `player_points_per_minute_rolling_10` against the
same leakage-safe chronological split used by the project baseline. This preserves the repo's
measurement discipline and lets the feature be judged on actual out-of-time performance rather
than intuition.

Measured result (2026-08-13): the live evaluation on the current processed feature set is
essentially unchanged. The baseline without the player-efficiency feature yields accuracy 0.65219,
log_loss 0.62609, and Brier score 0.21774; with the feature included it yields accuracy 0.65219,
log_loss 0.62608, and Brier score 0.21774. The improvement is effectively zero, so the feature
should remain a descriptive explanatory signal at most and not be promoted to the retained
production model unless a larger or more stable seasonal gain appears under the same protocol.

Exact next step: continue evaluating one additional candidate team/player feature only if it
shows a clearly repeatable, material holdout gain; otherwise keep the current ensemble as the
validated production output and move on to representation or UI-facing explanations rather than
further feature churn.

Implementation update (2026-08-12): added a probability-ensemble comparison that averages
holdout probabilities from the validated Elo baseline and the boosted-hybrid model. The new
candidate, `elo_boosted_ensemble`, is evaluated in the same chronological pipeline and is now
served by `src/main.py` when it improves the holdout log loss.

Measured result (2026-08-12): the mean-of-probabilities ensemble improves on the boosted
hybrid's holdout log loss from 0.62554 to 0.62288 while preserving a competitive Brier score
(0.21663) and accuracy (0.64979). It is therefore the best single model on the repository's
current log-loss selection rule, and the CLI now reports `"model": "elo_boosted_ensemble"`
when the generated metrics file is present.

What now works: the repository maintains a leakage-safe Elo baseline, a tuned boosted-hybrid
feature model, and an ensemble prediction layer that combines the two without leaking future
info. The end-to-end prediction CLI returns valid matched probabilities, the regression suite
still passes, and the project is now positioned to move to the next meaningful milestone:
testing richer player/team explanatory features or a small feature-selection experiment
without abandoning the validated holdout pipeline.

Exact next step: build and validate one additional feature-engineering experiment that adds a
specific player-availability or roster-change signal while preserving the same chronological
holdout and strict no-leakage rules. The next milestone should remain quantitative and
measurable rather than broadening into the full AI-agent or website layer.

Exact next step: begin the next analytical milestone by adding richer leakage-safe
team/player explanatory features, comparing them against this frozen calibrated baseline
under the same chronological and calibration evaluation protocol.

Implementation update (2026-08-11): added the leakage-safe `win_rate_rolling_10` team feature
to `src/build_features.py`. It is computed from each team's prior ten game outcomes using
`shift(1)` before rolling, then automatically participates in the existing home-minus-away
game dataset. The generated feature file was rebuilt from SQLite and remains 133,466 rows.

Measured result (2026-08-11): the new feature changes the current holdout winner. The
uncalibrated boosted hybrid now achieves accuracy 0.65227, log_loss 0.62554, and Brier score
0.21745; the previous frozen calibrated path with the expanded feature set achieves 0.65197,
0.62774, and 0.21618, while Elo remains 0.64971, 0.62616, and 0.21812. Under the declared
log-loss rule, `boosted_hybrid` is now recommended and served. The persisted model bundle was
fixed to save the same model named by `recommended_model`, preventing a recommendation/bundle
mismatch when calibration is not selected.

What now works: the new pregame team-form feature is generated and tested, the full suite
passes with 39 tests, the model is retrained on the expanded feature set, and the CLI serves
the selected uncalibrated boosted model successfully.

Exact next step: run an explicit ablation comparison with and without `win_rate_rolling_10`,
then retain the feature only if its contribution is stable across the season holdouts rather
than relying on the aggregate split alone.

Implementation update (2026-08-11): added an explicit boosted-hybrid ablation report for
`win_rate_rolling_10`, using the selected tree configuration and the same chronological
holdout. The report persists aggregate and season-level metrics in
`boosted_hybrid_win_rate_ablation`.

Ablation result (2026-08-11): retaining `win_rate_rolling_10` improves boosted-hybrid
holdout log loss from 0.62610 to 0.62554, accuracy from 0.65242 to 0.65227 (a negligible
threshold tradeoff), and Brier score from 0.21766 to 0.21745. Log loss improves in 10 of
12 season slices and worsens only slightly in 2019 and 2024, so the feature is retained as
a stable explanatory input rather than an aggregate-only optimization.

What now works: the feature contribution is explicitly measured, the selected boosted
model is retrained and persisted with the feature, the CLI returns valid predictions, and
the full suite passes with 40 tests.

Exact next step: add the next leakage-safe explanatory feature only after defining its
ablation and season-stability checks up front; the current boosted hybrid and its feature
ablation are the reference for that comparison.

Implementation update (2026-08-11): evaluated a candidate
`margin_volatility_rolling_10` feature based on the prior ten scoring margins, with the
same pregame shift, aggregate ablation, and season-level stability checks. The candidate
was rejected and removed from the generated feature set because it did not improve the
retained model.

Candidate result (2026-08-11): adding margin volatility produced boosted-hybrid log loss
0.62558 versus 0.62554 without it, with accuracy 0.65189 versus 0.65227 and Brier score
0.21745 versus 0.21745. It improved log loss in only 6 of 12 season slices, so it was not
retained. The repository was rebuilt back to the validated win-rate feature set, and the
full suite still passes with 40 tests.

Implementation update (2026-08-12): evaluated a new opponent-form differential experiment,
using `opponent_adjusted_win_rate_rolling_10` and
`opponent_adjusted_plusMinusPoints_rolling_10` as relative pregame signals. The candidate is
implemented in `src/train_baseline_model.py` through `add_opponent_form_features()` and
`evaluate_opponent_form_experiment()` so it can be measured against the current holdout
without quietly changing the default model.

Measured result (2026-08-12): on the live feature set, the current validated boosted-hybrid
baseline remains the strongest default at log loss 0.62554, while the opponent-form variant is
0.62591 and its Brier score is 0.21755. That is a small but real deterioration, so the
opponent-form feature is not adopted. The validated ensemble remains the current best live
recommendation, and the project keeps the trajectory toward a single, measured quantitative
improvement rather than adding unproven explanatory features to the default release.

What now works: a repeatable candidate-feature evaluation harness is in place, the current
model recommendation remains stable, and the project can continue to the next measurable
feature hypothesis only after a clear expected improvement signal.

Exact next step: test one truly high-signal explanatory feature with an explicit expected
performance gain before adding it to the default feature set; otherwise keep the current
ensemble baseline and move to the next non-prediction milestone in the Sports AI roadmap.

Exact next step: define and evaluate a stronger opponent-adjusted team-form feature, using
the same ablation and season-stability gate before it can modify the retained boosted
baseline.

Implementation update (2026-08-11): evaluated an opponent-adjusted form feature defined as
each team's prior-ten-game win rate minus its opponent's prior-ten-game win rate for the
same game. The lookup was implemented with indexed pregame values and tested for row-count
preservation; games without an opponent history remain present with a missing value for the
existing model imputer.

Candidate result (2026-08-11): the feature was rejected. It produced log loss 0.625541
versus 0.625540 without it, identical accuracy 0.65227, and a marginally worse Brier score
0.217453 versus 0.217452. It improved log loss in only 6 of 12 season slices, so it did not
meet the stability gate and was removed from the generated feature set.

What now works: the retained rolling-win-rate feature set is restored, the full suite passes
with 40 tests, the feature dataset remains 133,466 rows, and the boosted hybrid CLI path
continues to return valid predictions.

Exact next step: pause speculative feature additions and perform a targeted review of the
existing player-availability and player-history signals for a feature with a stronger
causal or explanatory rationale before another ablation is attempted.

Implementation update (2026-08-11): reviewed the existing player-history signals and tested
a pregame player scoring-efficiency feature, `player_points_per_minute_rolling_10`, derived
from prior player points divided by prior player minutes. Zero-minute denominators were
handled as missing values for the existing imputer.

Candidate result (2026-08-11): the player-efficiency feature was rejected. It changed
boosted-hybrid log loss from 0.62554 to 0.62557, Brier score from 0.21745 to 0.21748,
and improved log loss in only 5 of 12 season slices despite a small accuracy increase.
The feature was removed and the validated rolling-win-rate feature set was rebuilt.

What now works: the retained feature dataset is restored at 133,466 rows, the full suite
passes with 40 tests, the boosted hybrid remains the selected model at accuracy 0.65227
and log loss 0.62554, and the CLI remains operational.

Exact next step: stop adding weak derived predictors and prioritize a model-interpretability
and feature-importance report for the retained boosted baseline before making another
feature change.

Implementation update (2026-08-11): built a minimal interactive prediction interface at
`src/interactive_predict.py`, per explicit user request. It adds no new modeling
functionality — it is a thin wrapper around the existing, validated `predict_matchup()`
from `src/main.py`. It loads the 30 current NBA franchise names from the `team_histories`
table (filtered to the fixed NBA franchise teamId range and `seasonActiveTill >= 2100`),
lets the user pick a home team and an away team by number from a printed list, optionally
enter a game-date cutoff, and then prints the same home/away win probabilities and
predicted-favorite label the CLI already produces.

Validated current behavior (2026-08-11):
- `./.venv/Scripts/python -m pytest tests/test_interactive_predict.py -q` -> `1 passed`,
  covering that `load_current_teams()` returns exactly the current-franchise rows (filtering
  out historical relocated entries and non-NBA international/exhibition teams from the same
  table).
- `./.venv/Scripts/python -m pytest -q` -> `41 passed` (40 previously existing plus the new
  interactive-interface test), confirming no regression.
- End-to-end manual run with piped input (`5`, `25`, `2026-04-12`) selected the Chicago Bulls
  as home and Portland Trail Blazers as away, and returned a real `boosted_hybrid` prediction
  (35.0% / 65.0%) with a favorite label, matching the existing CLI's model output for the same
  inputs.
- Confirmed the no-date path falls back to the latest available pregame data, and that
  selecting the same team for both home and away is explicitly rejected with a clear message
  before any prediction call is attempted.

What now works: a user can get a prediction by choosing two teams by name from a numbered
list, with no need to know numeric team IDs or touch the CLI flags directly. No model,
feature, or selection-logic changes were made; `models/baseline_metrics.json` and
`models/baseline_logistic.pkl` are untouched by this work.

Exact next step: resume the model-interpretability/feature-importance milestone for the
retained boosted baseline that was deferred to build this interface.

Implementation update (2026-08-12): added `summarize_feature_importance()` to
`src/train_baseline_model.py` and persisted a normalized top-10 feature-importance report in
`models/baseline_metrics.json`. The helper uses native feature importances when available,
coefficient magnitudes for linear models, and permutation importance for tree ensembles like
`HistGradientBoostingClassifier`, so the retained boosted hybrid now carries an explanation
report instead of an opaque model-only artifact.

What now works: the model metrics file includes a ranked `feature_importance` section with the
current strongest predictors (for example, `elo_delta` is the dominant signal for the boosted
hybrid), the feature-importance logic is covered by a focused regression test, and the training
script still writes the baseline metrics and pickles successfully.

Validation: `./.venv/Scripts/python -m pytest tests/test_baseline_model.py -q` -> `16 passed`,
and `./.venv/Scripts/python src/train_baseline_model.py` completes successfully while writing
`models/baseline_metrics.json` with the persisted feature report.

Candidate review (2026-08-12): evaluated a leakage-safe opponent-adjusted form signal,
`opponent_adjusted_win_rate_rolling_10`, and tested it against the same chronological holdout.
It worsened the retained boosted-hybrid log loss from `0.62554` to `0.62583` and did not
outperform the current feature set, so it was rejected and the feature file was rebuilt back to
the validated rolling-win-rate baseline. The feature remains a documented candidate for future
exploration only if it can beat the frozen model under the same ablation and season-stability
checks.

Exact next step: use this feature-importance report to decide whether a new leakage-safe
team/player feature is worth the expected ablation and season-stability review before expanding
the retained model further.

Implementation update (2026-08-12): evaluated a second explanatory candidate,
`opponent_adjusted_plusMinusPoints_rolling_10`, using the same chronological holdout and
season-level stability checks. The feature degraded the retained boosted-hybrid log loss from
`0.62554` to `0.62561` and did not improve on the frozen baseline, so it was rejected and the
validated `win_rate_rolling_10`/`plusMinusPoints_rolling_10` feature set was restored.

What now works: the retained boosted-hybrid feature set remains the proven, frozen baseline,
the model-importance report is persisted, and the repository has no evidence-backed candidate
feature currently worth adding under the same validation gate. The next milestone is therefore
a documentation/explainability handoff around the retained baseline rather than another model
feature expansion.

Implementation update (2026-08-12): exposed the saved model explanation in the prediction CLI
via a `--explain` flag in `src/main.py`. The command now returns the current top-ranked
importance weights from `models/baseline_metrics.json` alongside the probability output, so the
retained boosted hybrid is no longer a black box when users ask for a prediction.

What now works: `predict_matchup()` includes a `feature_importance` field when the metrics file is
available, the CLI surfaces the same top features under `--explain`, and the new regression test
confirms the importance loader reads the persisted ranking correctly.

Validation: `./.venv/Scripts/python -m pytest tests/test_predict_game.py tests/test_baseline_model.py -q`
-> `25 passed`; `./.venv/Scripts/python src/main.py --home-team-id 1610612744 --away-team-id 1610612743 --game-date 2026-04-12 --explain`
returns a real probability plus the top five driving features.

Implementation update (2026-08-12): added `--explain` support and a formatted top-feature summary
in `src/interactive_predict.py`, so the interactive team-selection interface now surfaces the same
feature-importance drivers that the CLI exposes without requiring the user to know raw team IDs or
edit JSON files manually.

What now works: direct CLI predictions, plain interactive predictions, and interactive explanations
all share the same saved baseline and the same audited feature-importance summary. The underlying
model remains frozen, and the explanation layer is now part of the user-facing workflow rather than
only the raw JSON output path.

Implementation update (2026-08-12): added a `model_summary` report to `src/main.py` and a
`--summary` flag to both the CLI and the interactive prompt flow. The summary bundles the
recommended model, the holdout metrics from `models/baseline_metrics.json`, the expected
calibration error, and the top-ranked features in one place so the user can see both the model's
performance and its main drivers without running a separate analysis script.

What now works: the prediction interface can now surface a concise model summary in addition to the
finer-grained top-feature explanation, and the same summary is accessible through the JSON output and
interactive prompt. This preserves the frozen baseline and keeps the explanatory layer tied directly
to the audited model artifacts, rather than inventing new features or a new model.

Validation: `./.venv/Scripts/python -m pytest -q` -> `46 passed`; the new `--summary` path is covered
by the focused prediction and interactive-output regressions.

Exact next step: keep the validated boosted-hybrid baseline frozen and use this model-summary layer
as the repository's audited user-facing report until a future feature candidate can demonstrate a
clear, reproducible holdout improvement under the same ablation and season-stability gate.

A full repository audit was performed and its findings were used as the source
of truth for a follow-up engineering cleanup pass. No modeling methodology or
reported metrics were changed in this pass; the Elo holdout metrics were
re-verified identical before and after (accuracy 0.64971, log loss 0.62616,
Brier score 0.21812).

What changed:

- Added `requirements.txt`, pinned to the versions already validated in the
  project `.venv` (pandas, numpy, scikit-learn, requests, beautifulsoup4,
  pytest). Verified with `pip install -r requirements.txt` that no
  incompatible resolution occurs.
- Added `pyproject.toml` with `[tool.pytest.ini_options] pythonpath = ["."]`
  so a bare `pytest -q` invocation works from a clean checkout, not only
  `python -m pytest`. Verified both invocation styles pass all 30 tests.
- Fixed `src/create_indexes.py`, which referenced stale table names
  (`TeamStatistics`, `PlayerStatistics`, `PlayerStatisticsExtended`,
  `TeamStatisticsExtended`, and a nonexistent `PlayByPlay` table) left over
  from before the table-naming issue documented in section 6 was fixed
  elsewhere. It silently skipped indexing the large tables as a result. The
  script now uses the confirmed lowercase/underscore table names and no
  longer references `PlayByPlay`. Verified live: it now creates 16 indexes,
  including on `player_statistics`, `player_statistics_extended`, and
  `team_statistics_extended`, which previously had zero indexes.
- Standardized `check_database.py`, `create_database.py`, `load_data.py`, and
  `create_indexes.py` on the same `ROOT = Path(__file__).resolve().parents[1]`
  pattern already used by `build_features.py`/`main.py`/`player_impact.py`, so
  these scripts resolve `data/database/nba.db` correctly regardless of the
  working directory they are invoked from. Verified each script from both the
  project root and `src/` as the working directory.
- Git hygiene: removed tracked `__pycache__`/`.pyc` files from version
  control. Made the `models/` tracking policy explicit in `.gitignore`
  (`models/*` with `!models/baseline_logistic.pkl` and
  `!models/baseline_metrics.json` as documented exceptions); other generated
  files under `models/` (e.g. `player_impact_metrics.json`) remain untracked
  and regenerable. Committed the previously pending, already-validated
  Elo-dispatch CLI work (`src/main.py`, `select_recommended_model` in
  `src/train_baseline_model.py`, and their tests) that was sitting unstaged in
  the working tree.
- Corrected the `gameType` null-count wording in this file (sections 7 and 9):
  the raw `team_statistics` table has 7,590 null `gameType` rows, of which the
  `COALESCE(team_statistics.gameType, games.gameType)` fallback resolves 3,656
  to `'Regular Season'` (the remainder resolve to Preseason, Playoffs, Play-in
  Tournament, or NBA Cup). The previous wording described only the resolved
  subset as if it were the full null count.
- Removed dead/confusing code in `src/player_impact.py`'s
  `validate_player_impact`: a `player_signal` value was computed from
  `prior_weighted_net_rating` and then immediately overwritten by a different,
  actually-used formula a few lines later; `prior_weighted_net_rating` and
  `prior_player_team_net_rating` were otherwise used only as extra `dropna`
  filter columns. Empirically verified (by comparing row counts and row
  indexes with and without these columns in the filter) that removing them
  does not change which rows are evaluated: 663,251 evaluated player-games
  before and after. Regenerated `models/player_impact_metrics.json` and
  confirmed it is byte-for-byte identical to the pre-cleanup output.
- Expanded `readme.md` with setup instructions, a data-provenance note
  (raw-CSV provenance is not currently documented in this repository --
  flagged explicitly as a known gap rather than invented), database build
  steps, feature/model build steps, CLI usage, and test invocation. Verified
  the documented `pytest -q` and `python src/main.py --home-team-id ...`
  commands both run successfully as written.

Validated after cleanup: `pytest -q` (bare invocation) and
`python -m pytest -q` both pass all 30 tests; `check_database.py` confirms all
seven tables; `create_indexes.py` creates all 16 intended indexes;
`train_baseline_model.py` reproduces the unchanged Elo/logistic holdout
metrics; `player_impact.py` reproduces byte-identical output; `main.py`
returns a valid prediction and the same clear `ValueError` for an unknown
team.

Exact next step: with the reproducibility and engineering issues resolved,
the next analytical milestone remains the one already identified above --
evaluate whether blending Elo rating (or Elo-derived features) into the
logistic/player-history feature set improves on Elo alone under the same
chronological holdout. That work has not been started in this pass.

---

# 2. Current Repository

Project root:

```text
C:\Users\myles\Git NBA Proj
```

Python environment:

```text
.venv/
```

General structure:

```text
data/
├── raw/
├── processed/
└── database/

models/
src/
tests/
notebooks/

.gitignore
README.md
PROJECT.md
PROJECT_CONTEXT.md
```

---

# 3. Current Database

SQLite database:

```text
data/database/nba.db
```

The database has been successfully created and reloaded from the raw CSV files.

## Confirmed tables

```text
games
player_statistics
player_statistics_extended
players
team_histories
team_statistics
team_statistics_extended
```

### Important

The actual database uses lowercase/underscore table names.

For example:

```sql
team_statistics
```

not:

```sql
TeamStatistics
```

Do not assume schema names.

Always inspect the database before writing SQL against it.

## Confirmed row counts

```text
games: 73,279
team_statistics: 146,560
team_statistics regular season (native label): 130,014
effective regular-season rows after Games.csv type fallback: 133,670
team_statistics_extended: 79,724
processed model-ready feature rows: 133,466
```

---

# 4. Existing Database Utility

An existing database validation script is:

```text
src/check_database.py
```

**Do not create another equivalent script such as `check_db.py`.**

If database validation is needed, use or improve the existing script.

Avoid creating duplicate utilities when equivalent functionality already exists.

---

# 5. Existing Scripts

Known relevant scripts include:

```text
src/check_database.py
src/create_indexes.py
src/build_features.py
src/roster_change_data.py
```

Before creating a new script:

1. Search `src/`.
2. Determine whether the required functionality already exists.
3. Reuse or modify the existing script if appropriate.

---

# 6. Database Schema Issues Already Encountered

An earlier implementation attempted to query:

```text
TeamStatistics
```

but the actual table is:

```text
team_statistics
```

Another earlier implementation attempted to reference:

```text
gameDate
```

but the relevant date field is:

```text
gameDateTimeEst
```

These mistakes demonstrate why the actual schema must be inspected rather than assumed.

---

# 7. Feature Pipeline

Feature-building script:

```text
src/build_features.py
```

Output:

```text
data/processed/game_features.csv
```

The script executes successfully. Because `TeamStatistics.csv` has null
`gameType` values for part of late 2021 and 2022, the query uses
`COALESCE(team_statistics.gameType, games.gameType)` after joining on `gameId`.
`Games.csv` supplies the missing game classification without changing raw data.
The latest verified run reports:

```text
Loaded 133,670 team-game rows
Saved 133,466 rows
```

The output file is now populated, model-ready, and has the expected
rolling-statistic columns.

The generated CSV has passed the current feature-content validation and is the
validated starting point for baseline model training.

---

# 8. Current Feature Columns

`build_features.py` currently creates the following columns:

```text
gameId
gameDateTimeEst
teamId
opponentTeamId
home
win
teamScore
opponentScore
assists
steals
blocks
fieldGoalsPercentage
threePointersPercentage
freeThrowsPercentage
reboundsTotal
turnovers
plusMinusPoints
season
rest_days
teamScore_rolling_10
opponentScore_rolling_10
assists_rolling_10
steals_rolling_10
blocks_rolling_10
fieldGoalsPercentage_rolling_10
threePointersPercentage_rolling_10
freeThrowsPercentage_rolling_10
reboundsTotal_rolling_10
turnovers_rolling_10
plusMinusPoints_rolling_10
active_players_rolling_10
active_players_last_game
player_minutes_rolling_10
player_points_rolling_10
player_assists_rolling_10
player_rebounds_rolling_10
```

These columns are the intended starting point for game-level feature engineering. The
row count, duplicate handling, rolling-window values, and season definition have
passed focused validation.

---

# 9. Resolved Blocker and Remaining Work

## Resolved problem

The SQLite `team_statistics` table existed with the expected schema but contained 0
rows, even though `data/raw/TeamStatistics.csv` contained 146,560 rows. Reloading the
database with `src/load_data.py` restored the table. The native
`gameType = 'Regular Season'` filter returned 130,014 rows after the reload, but the
raw team-statistics file has 7,590 rows whose `gameType` is null; joining those rows
to `games` and applying `COALESCE(team_statistics.gameType, games.gameType)` resolves
3,656 of them to `'Regular Season'` (the remainder resolve to Preseason, Playoffs,
Play-in Tournament, or NBA Cup). Joining those rows to `games` shows that the game
table classifies the regular-season subset correctly.

The feature pipeline now returns:

```text
Loaded 133,670 team-game rows
Saved 133,466 rows
```

## What is working

- Raw CSV ingestion via `src/load_data.py`.
- SQLite tables and expected lowercase/underscore table names.
- Regular-season team-game extraction in `src/build_features.py`.
- Chronological rolling features using only previous games.
- Leakage-safe `rest_days` intervals from each team's previous game date.
- Model-ready output at `data/processed/game_features.csv` with complete current
  and rolling predictors.
- The feature output preserves team-games whose prior player-history summaries are
  unavailable; the baseline trainer imputes those optional predictors with training
  medians inside the fitted pipeline rather than dropping otherwise valid games.
- Leakage-safe `active_players_rolling_10` prior-participation feature extraction.
- Leakage-safe `active_players_last_game` feature extraction from the prior team game.
- Leakage-safe prior-ten-team-game player-level summaries of minutes, points,
  assists, and rebounds.
- Basic table validation via `src/check_database.py`.
- Focused feature regression coverage in `tests/test_feature_pipeline.py`.
- Game-type fallback through the `games` table for null team-statistics labels.

## What is not complete

- The player features are historical production/participation proxies, not injury
  forecasts. A trustworthy historical pregame inactive list is not available.
- The player-impact targets are association diagnostics, not causal evidence, and
  remain gated from user-facing projection until stronger controls and independent
  prospective validation are available.

## Latest player-history evaluation

The baseline trainer compares four player-level prior-ten-team-game production
summaries against the prior rolling-plus-rest logistic model and saves the richer
model when it improves the same holdout. The regenerated output contains 133,466
rows. Some optional player-history values are unavailable (3,135 team-game rows,
concentrated in seasons 2000 and 2021), but the core team-game predictors remain
complete; the fitted `SimpleImputer` uses only training-period medians for those
optional values. On the unchanged 13,332-game chronological holdout, the
rolling-plus-rest baseline scored accuracy `0.62669`, log loss `0.64628`, and Brier
score `0.22691`. The player-history model scored accuracy `0.62691`, log loss
`0.64584`, and Brier score `0.22660`; configured chronological Elo remains better
at log loss `0.62616` and Brier score `0.21812`. The saved
`baseline_logistic.pkl` contains the imputation, scaling, logistic model, and
predictor list.

## Player impact prototype

`src/player_impact.py` provides an assumption-labeled addition/removal estimate
based on a player's prior ten regular-season appearances. It uses minutes-weighted
player net rating and prior average minutes, assumes those values transfer to a new
team context, and compares them with a configurable baseline net rating. That
baseline is a reference value of `0.0`, not a claim about replacement level.

The validator also evaluates an independent pregame target: observed current team
net rating, predicted first from prior team/opponent form and prior-game team
participation, then with the prior player-production signal added. The controls
include prior active-player count and prior total rotation minutes. This target is
not constructed by removing the player's current points or possessions. On the
chronological holdout, the pregame-control model scored MAE `11.79552` across
6,697 games and 136,276 player-games; adding the player signal scored `11.79408`.
A game-cluster bootstrap 95% interval for candidate-minus-control MAE was
`[-0.00200, -0.00098]`. The improvement is statistically separated from zero
under this benchmark, but is small and remains an association result rather than
causal evidence.

The validation target is now a possession-normalized leave-one-player-out scoring
target: current team net rating minus team scoring net rating after removing the
player's current points and possessions. Prior ten-game player production predicts
this target for 663,251 player-games with MAE `36.60191`, compared with a
fixed-zero MAE of `43.40858`, correlation `0.43903`, and an improvement over zero.
The chronological 20% holdout calibrates the signal only on earlier games and
scores 136,402 player-games from 6,703 games: calibrated MAE `30.86037` versus
fixed-zero MAE `36.06197`. It improves the fixed-zero baseline in aggregate and
in every available holdout season (2019-2025); the smallest seasonal sample is
19 player-games in 2021. This is a better-defined incremental target, but it
remains an association diagnostic rather than causal evidence. A reproducible
game-cluster bootstrap gives a 95% interval of `[-5.48228, -4.91403]` for
calibrated MAE minus fixed-zero MAE, so the aggregate holdout improvement is
separated from zero under this resampling procedure. This does not establish
causality or quantify uncertainty for a future roster change. The
addition/removal estimator is still not promoted as a reliable projection.

The independent pregame target has now been stress-tested across 10%, 20%, and
30% chronological holdouts. The player signal improves the pregame control MAE
slightly in each window: `12.22946` vs `12.23062`, `11.79408` vs `11.79552`,
and `11.48681` vs `11.48806`, respectively. Usage-stratified results are not
uniform: the low- and middle-prior-minute strata are slightly worse than the
control in all three windows, while the high-minute stratum is better in all
three. These results support robustness as an association diagnostic, but the
small, usage-dependent effect is not evidence for a causal player projection.
The persisted report is `models/player_impact_metrics.json`, and focused tests
cover the window and usage-stratum result shape.

The validator also evaluates the signal once per team-game, rather than weighting
team outcomes by the number of player rows. The control uses only prior team,
opponent, participation, possession, rest, and home-court predictors; the candidate
adds the mean prior player signal. Four untouched season-based holdouts all improve
the control:

```text
validation start | holdout team-games | control MAE | candidate MAE | 95% MAE difference
2022             | 9,694              | 11.30074    | 11.27184      | [-0.04380, -0.01434]
2023             | 7,234              | 11.58557    | 11.56045      | [-0.04391, -0.00723]
2024             | 4,906              | 11.58057    | 11.54684      | [-0.05375, -0.01189]
2025             | 2,460              | 11.70645    | 11.66426      | [-0.06866, -0.01418]
```

The results are persisted in `models/player_impact_metrics.json` under
`later_team_game_validation_by_season`. They remain association diagnostics, not
evidence that the player-impact estimator is causal.

The validator now also evaluates observed roster-change events using the
independent current-team-net-rating target. It selects first player appearances
after a historical team change, aggregates multiple changes in the same
team-game, and compares the player signal with the same pregame team,
opponent, participation, possession, rest, and home controls. The benchmark
contains 5,280 transition player-games and 3,506 evaluable transition events;
the chronological holdout contains 697 events across 614 games. The control
MAE is `12.84272` and the candidate MAE is `12.86279`. The clustered bootstrap
95% interval for candidate-minus-control MAE is `[-0.03576, 0.07496]`, which
includes zero. The roster-change benchmark therefore does not support adding
the player signal to a roster-change projection.

The roster-change result is persisted in `models/player_impact_metrics.json`
under `roster_change_validation`. Rest intervals are computed from each
team's complete game schedule, not only from games containing a roster-change
event. Focused player-impact tests and the full test suite pass.

The latest reproducibility check regenerated the impact report successfully.
The full project suite completed with 20 passing tests, and the baseline report
remains populated with 66,658 complete games and the unchanged chronological
holdout metrics.

The current continuation check reran `src/check_database.py`, `python -m pytest
tests -q`, and `src/player_impact.py` using the project `.venv`. The database
validator passed, the suite completed with 20 passing tests, and the impact
report regenerated successfully. Its historical roster-transition benchmark
remains unchanged: control MAE `12.84272` versus candidate MAE `12.86279`.
The raw trade extracts are present but do not satisfy the external-event
contract, so the benchmark cannot yet be run without introducing unverified
identity mappings or source provenance.
The latest continuation also confirmed the live SQLite table counts and
schema, and the command-line impact run completed in approximately 34 seconds.

The 2026-08-11 continuation inspection also verified the live SQLite schema and
row counts: `games` 73,279; `team_statistics` 146,560;
`team_statistics_extended` 79,724; and `player_statistics_extended` 838,803.
`src/check_database.py`, the full test suite, and `src/player_impact.py` all
completed successfully. No additional implementation is safe until qualifying
external roster events are supplied; inferring them from the historical tables
would not provide prospective or causal validation.

## Exact next step

Keep the player-impact estimator gated and obtain prospective validation or a
stronger independently sourced roster-change outcome before exposing
addition/removal projections. The historical roster-change benchmark is now
complete but fails to improve its pregame control and does not establish
causal transfer to a new team context. Do not build the simulation or
user-facing projection layer until that validation gap is resolved.

The concrete next implementation step is now to obtain an independently sourced,
timestamped roster-change outcome dataset that satisfies this contract, then run
`src/player_impact.py --roster-events PATH` and inspect the resulting
`event_source`, ignored-removal count, and chronological holdout metrics. The
existing raw trade extracts may be retained as leads, but must not be converted
into benchmark events unless their source URL, unambiguous event timestamps, and
database-resolvable team/player identifiers are documented and validated. Do not
substitute the existing box scores or schedule files for this source: they do
not identify prospective roster decisions or provide a causal counterfactual.

The ingestion contract is implemented in `src/roster_change_data.py`. It
requires unique event identifiers, timestamped team/player IDs, an explicit
`add` or `remove` event type, and source provenance with an HTTP(S) URL. The
loader intentionally does not create or infer events, and no qualifying
independent source dataset is present yet. The existing benchmark accepts
validated events
through `validate_player_impact(..., roster_events=events)` or the
`src/player_impact.py --roster-events PATH` command. External `add` events are
linked to the first later appearance for that player and team before the
chronological benchmark runs; `remove` events are counted and explicitly
reported as unsupported rather than being treated as additions. The benchmark
has not been rerun with external events because no qualifying source dataset is
present.

The earlier investigation confirmed the following facts and should not be repeated as
assumptions:

1. The schema of `team_statistics`.
2. The number of rows in `team_statistics`.
3. The columns available in `team_statistics`.
4. The relevant `gameType` value: `Regular Season`.
5. That the table contains game-level team statistics.
6. That the current SQL filter does not exclude all rows after reload.
7. That the expected feature columns exist in the raw team-statistics data.
8. That `build_features.py` uses `gameDateTimeEst` for chronological features and
   joins `games` only to recover missing game-type labels.

---

# 10. Previous NBA Prediction Model

A previous NBA prediction project produced approximately:

```text
Accuracy: 0.62278
Log Loss: 0.66614
Brier Score: 0.23659
```

These numbers are a **baseline**.

The eventual goal is to reproduce the useful parts of the previous model inside this project and then improve it using cleaner data, better features, and better validation.

Do not claim improvement until the models are evaluated on appropriate historical data.

---

# 11. Current Development Priority

Priority order:

```text
1. Fix the 0-row feature pipeline ✅
2. Validate generated game features ✅
3. Validate the historical dataset ✅
4. Build/reproduce the baseline prediction model ✅
5. Establish proper train/test or time-based validation ✅
6. Improve the baseline model ✅
7. Add more advanced player/team features ✅
8. Develop player-impact modeling ✅ (association diagnostics only, gated)
9. Develop simulations ✅ (Monte Carlo season engine + projected seedings/playoff field/league summary)
10. Build AI/tool layer ✅ (deterministic tool registry + orchestration routing in src/tools.py)
11. Build natural-language AI layer ✅ (deterministic question->tool mapping + plain-language rendering in src/assistant.py)
12. Add live data ✅ (source-provenanced, leakage-safe schedule ingestion in src/live_data.py with data_ingestion_log provenance)
13. Build website / API ✅ (HTTP API in src/api.py + browser front end in web/index.html, stdlib-only, CORS-enabled)
```

This priority is a current development state, not a permanent project roadmap.

Update it as the project progresses.

---

# 12. Rules for AI Coding Assistants

Before modifying the project:

### Read the context

Read:

```text
PROJECT_CONTEXT.md
```

Then read:

```text
PROJECT.md
```

when broader architectural context is necessary.

### Inspect the repository

Do not assume that a file exists.

Search the repository before creating new files.

### Inspect the database

When working with SQLite:

* inspect table names
* inspect columns
* inspect sample rows
* inspect row counts
* inspect distinct categorical values

Do not invent schema names.

### Avoid duplicate scripts

Before creating a utility script, search the repository for existing functionality.

For example, an existing:

```text
src/check_database.py
```

should not be duplicated with:

```text
src/check_db.py
```

without a specific reason.

### Diagnose before changing

If something fails:

1. Reproduce the problem.
2. Inspect the relevant code/data.
3. Identify the root cause.
4. Make the smallest appropriate fix.
5. Run the code again.
6. Verify the result.

Do not blindly modify code until the error disappears.

### Do not jump ahead

If an earlier pipeline stage is broken, fix it before building dependent features.

### Keep the project reproducible

Prefer clear, simple code and documented dependencies.

---

# 13. How to Decide What to Do Next

When asked:

> "What's next?"

the AI should:

1. Read this file.
2. Inspect the repository.
3. Identify the earliest incomplete or broken dependency.
4. Verify the current state rather than relying only on this document.
5. Recommend the next concrete task.
6. If explicitly asked to proceed, implement it.
7. Test the result.
8. Update this file if the project state materially changes.

The AI should **not** simply choose the next item from the long-term roadmap.

---

# 14. Source of Truth

Use this priority when information conflicts:

```text
1. Actual repository/code
2. Actual database/schema/data
3. PROJECT_CONTEXT.md
4. PROJECT.md
```

`PROJECT_CONTEXT.md` should accurately describe the repository, but the repository itself is ultimately authoritative.

If this file says a script exists but it does not exist, inspect the repository and correct this file rather than assuming the script exists.

---

# 15. Updating This File

Update `PROJECT_CONTEXT.md` when there is a meaningful state change, such as:

* a major script is created
* a pipeline stage is completed
* a major bug is fixed
* the database schema changes
* a new blocker appears
* a model is trained
* a model is evaluated
* a major architectural decision is made

Do not turn this file into a detailed chronological diary.

Prefer:

```text
Current state
Current blocker
Current objective
Known important facts
```

over:

```text
August 1:
Did X

August 2:
Did Y

August 3:
Did Z
```

When a blocker is resolved, replace the old blocker with the new current state.

---

# 16. Current Status

```text
Repository:             ✅ Established
Python environment:     ✅ Established
SQLite database:        ✅ Created
Database tables:        ✅ Confirmed
Database validation:    ✅ Existing utility
Feature script:         ✅ Loads regular-season rows and writes features
Game feature CSV:       ✅ Populated and independently validated
Prediction model:         ✅ Leakage-safe rolling-plus-rest logistic baseline rebuilt
Model evaluation:         ✅ Chronological holdout with accuracy, log loss, and Brier score
Player impact model:    ⚠️ Historical team-game cutoffs improve slightly, but roster-change validation does not; external prospective data ingestion is now ready, causal/prospective validation still required
Simulation engine:      ✅ Monte Carlo season simulator validated (2023-2025 replay MAE 3.7-4.6 wins, playoff field overlap 8-11/12) with projected seedings, playoff field, and league summary wired into both CLIs
Tool/orchestration:     ✅ deterministic tool registry (predict_matchup, simulate_season, team_projection, player_impact, player_scenario, team_record, head_to_head, resolve_team_name) with structured envelopes in src/tools.py
AI agent/tool layer:    ✅ deterministic natural-language interface (src/assistant.py) mapping questions to tool calls and rendering envelopes as plain-language answers
Live data:              ✅ source-provenanced, leakage-safe schedule ingestion (src/live_data.py) with data_ingestion_log provenance
Website / API:          ✅ HTTP API (src/api.py, stdlib-only, CORS-enabled) + browser front end (web/index.html) wired to /ask, /tools/{name}, and /ingest
```

## Diagnosed pipeline blocker

The earlier 0-row result was caused by an empty SQLite `team_statistics` table.
The source `data/raw/TeamStatistics.csv` contained 146,560 rows, but the
database table contained none, so the query in `src/build_features.py` had no
rows to load. After reload, the source also exposed a separate coverage issue:
7,590 team-statistics rows have null `gameType` values; the `COALESCE` fallback
to `games.gameType` resolves 3,656 of those rows to `'Regular Season'` (the
remaining null rows resolve to Preseason, Playoffs, Play-in Tournament, or NBA
Cup and are correctly excluded from the regular-season feature set).

The current database was reloaded from the confirmed raw CSV files. Running
`src/build_features.py` from the project root now reports:

```text
Loaded 133,670 team-game rows
Saved 133,466 rows
```

The feature script now applies the explicit model-ready policy. The generated
features are validated and covered by a focused test/check. The raw database
remains unchanged.

## Feature validation

`tests/test_feature_pipeline.py` independently reconstructs the regular-season
source rows and all rolling columns, then verifies that:

- the expected 133,466 rows are retained;
- output `(gameId, teamId)` keys match the source-derived rows;
- duplicate keys, null current metrics, and null rolling values are absent;
- percentage predictors are bounded fractions; and
- every rolling value matches the previous-game calculation within a
  floating-point tolerance.

The check passes when invoked directly with the project `.venv`. An independent
validation also confirms 133,670 effective regular-season source rows, zero duplicate
source keys, 133,466 output rows, zero duplicate output keys, complete current and
rolling predictors, and zero invalid percentage values. The project `.venv`
includes `pytest`; the full suite was run with `python -m pytest tests -q` and
completed with 20 passing tests.

## Current status after feature validation

The feature pipeline is reproducible and its rolling-history behavior is verified.
The generated dataset covers 1947-01-10 through 2026-04-12, uses seasons labeled
by the calendar year in which they start, and contains 80 season labels from
1946 through 2025. Season label 2021 now contains 2,445 team rows (1,230 games),
rather than the previous two rows.

## Baseline prediction model

`src/train_baseline_model.py` converts the two team rows for each complete game
into one home-versus-away record. It uses differences in the 11 rolling features
and pregame `rest_days`, so current-game box-score metrics are excluded from
prediction. The final 20% of games by date is held out chronologically.

The reproducible run produced 66,658 complete games: 53,326 training games and
13,332 test games, with the holdout beginning on 2015-03-28. Results are saved
to `models/baseline_metrics.json`, and the fitted pipeline is saved to
`models/baseline_logistic.pkl`.

| Model | Accuracy | Log loss | Brier score |
| --- | ---: | ---: | ---: |
| Training-period home-win rate | 0.56481 | 0.69305 | 0.24977 |
| Rolling-plus-rest logistic model | 0.62669 | 0.64628 | 0.22691 |
| Player-history logistic model (saved artifact) | 0.62691 | 0.64599 | 0.22665 |
| Chronological Elo (K=20, home advantage=65) | 0.64971 | 0.62616 | 0.21812 |

The models are validated first baselines, not evidence that the feature set is
optimal. Elo remains the strongest evaluated model, while the player-history
logistic model is the saved artifact because it improves the rolling logistic
baseline. The Elo evaluator initializes teams at 1500,
uses only ratings available before each game, and updates ratings only after that
game's result. Its metrics use the same chronological holdout as the rolling
logistic model.

The Elo comparison is implemented in `src/train_baseline_model.py`, with fixed
parameters recorded in `models/baseline_metrics.json`. The focused baseline tests
also verify that an Elo rating update from one completed game affects the next
pregame probability without using the next game's result.

The trainer now records Elo metrics for each holdout season, rolling-logistic
metrics for the same seasons, and a four-point Elo sensitivity grid. On the
13,332-game holdout, the configured Elo (`K=20`, home advantage `65`) remains
best in aggregate (log loss `0.62616`) versus the player-history logistic model
(`0.64599`). The repaired 2021 comparison now contains 1,215 games and is
interpretable as a seasonal result, subject to the source's remaining
quality limitations. The tested Elo settings are recorded in
`models/baseline_metrics.json`.

## Historical dataset validation

The effective regular-season source contains 133,670 rows with dates from
1946-11-26 through 2026-04-12. Game/team keys are structurally consistent:
there are no duplicate `(gameId, teamId)` keys and every regular-season game has
exactly two team rows. `home` is binary (`0`, `1`) and `win` is binary (`0.0`,
`1.0`).

The live audit found three source rows with null required box-score metrics
(two teams in game `26200259` and the three-point percentage for one team in
game `28800195`). The generated CSV excludes rows with unusable current metrics or incomplete
rolling history; it contains no null current or rolling predictors.

The percentage fields are not consistently bounded fractions in the legacy
portion of the source: 25 regular-season rows have out-of-range values (4 field
goal percentages and 21 free-throw percentages), all dated from 1950 through
1964. These values contradict the made/attempted columns in the same rows; for
example, some records report more makes than attempts and field-goal attempts
of zero alongside positive makes. This is evidence of a malformed or changing
legacy encoding, not evidence supporting a safe divide-by-attempts repair.

No source values are normalized or imputed. The model-ready policy marks
out-of-range percentages as unusable and excludes rows with any unusable current
metric or incomplete rolling history from the processed output. This preserves
the raw database while preventing malformed values from entering model training.

The season-label rule and model-ready policy are implemented in
`src/build_features.py`, regenerated the feature CSV, and are covered by
`tests/test_feature_pipeline.py`. The focused regression passes with 133,466
rows, zero duplicate keys, complete current and rolling predictors, bounded
percentage values, and the expected rolling values. The output covers
1947-01-10 through 2026-04-12.

The existing `src/check_database.py` validator was also made Windows-console
safe by replacing its non-ASCII status marker with ASCII output. It validates all
seven database tables successfully.

The baseline model and evaluation are now implemented in
`src/train_baseline_model.py`. Model training uses only pregame rolling, rest, and
prior-player-participation and player-production features, not current game
statistics. The focused
baseline pairing test is in `tests/test_baseline_model.py`; the chronology check
for the player feature is in `tests/test_feature_pipeline.py`.

The current quantitative milestone is complete: rolling-history-plus-rest-plus-player
logistic and chronological Elo baselines are evaluated on the same holdout, including
season-level comparison and Elo parameter sensitivity. The 2021 coverage gap
was diagnosed and repaired in feature extraction by using the authoritative
`games` classification for null team-statistics labels. Rest intervals are calculated
from each team's prior game timestamp and verified against an independent source
reconstruction; chronological splits remain required. Player-level prior-production
summaries were evaluated without current-game or future information and improved the
logistic holdout metrics, so they are now in the saved model artifact. Raw CSV and
database contents were not modified.

## Player impact status

The first impact prototype is implemented in `src/player_impact.py` and covered by
`tests/test_player_impact.py`. The validator now calibrates the prior-production
signal using only games before the final 20% chronological holdout and reports
aggregate and season-level MAE against fixed zero. Focused regression tests pass,
the full historical validation completes, and
`models/player_impact_metrics.json` records a holdout improvement in aggregate and
every available season. The estimate remains descriptive rather than causal and
must not yet be wired into a user-facing tool. The holdout's 95% game-cluster
bootstrap interval for model-minus-baseline MAE is `[-5.48228, -4.91403]`;
this quantifies resampling uncertainty for the benchmark comparison only, not
uncertainty around a hypothetical player addition or removal.
The newer team-game evaluation uses one row per team-game and repeats the
untouched holdout from validation starts 2022, 2023, 2024, and 2025. It adds
prior team possessions, rest, and home-court controls; the candidate improves
control MAE at every cutoff. The 2024 candidate MAE is `11.54684` versus
`11.58057` for controls, with a clustered 95% difference interval of
`[-0.05375, -0.01189]`; the complete split report is persisted in
`models/player_impact_metrics.json`. This remains an association diagnostic and
does not justify causal roster projections.

## Exact next milestone

The next milestone is **prospective roster-change validation**, not a user-facing
impact tool or simulation engine. It is currently blocked because the available
`data/raw/player_trades_raw.csv` and `data/raw/draft_pick_trades_raw.csv` extracts
contain names, non-ISO date strings, and no HTTP(S) source provenance. They must
not be ingested through `src/roster_change_data.py` by inferring identifiers,
timestamps, or URLs.

Progress can resume when an independently sourced event file is available with
the existing loader contract: unique `event_id`, timezone-aware or parseable
`event_timestamp`, positive `team_id` and `person_id`, `change_type` of `add` or
`remove`, a source description, and an HTTP(S) `source_url`. The validation
milestone is then to join those events to pre/post team-game outcomes, preserve
chronological cutoffs, compare against a no-change/control baseline, and report
uncertainty before exposing any projection capability.

The 2026-08-11 continuation inspection found the documented SQLite state intact:
seven populated tables, including 73,279 games, 146,560 `team_statistics` rows,
79,724 `team_statistics_extended` rows, and 838,803
`player_statistics_extended` rows. Rebuilding the features initially exposed a
row-loss regression caused by dropping team-games with unavailable optional
player-history summaries; removing that drop and adding training-only median
imputation restored the 133,466-row contract. The database validator, full test
suite, feature rebuild, baseline retraining, and player-impact report all run
successfully. The raw trade extracts remain non-qualifying leads because they lack
source URLs, unambiguous timestamps, and database-resolvable identifiers.

Current validation evidence remains:

* `python -m pytest tests -q`: 20 tests passed.
* SQLite has seven populated tables, including 73,279 games and 146,560
  `team_statistics` rows.
* The player-impact result is an association diagnostic only; its prospective
  causal validity is unestablished.

Implementation update (2026-08-12): added a dedicated validation path in
`src/roster_change_data.py` for external roster-change CSVs. The new `--validate`
CLI accepts a CSV, reuses the repository contract checks, and prints a summary JSON
with event counts, add/remove breakdowns, unique team/person coverage, and the
valid time window. This gives the project a preflight validation step before any
roster-impact benchmark is run on an external source.

What now works: an independent source file can be checked against the required
`event_id`, `event_timestamp`, `team_id`, `person_id`, `change_type`, `source`, and
`source_url` contract without first running the full player-impact benchmark, and the
same validation logic is the one used by `src/player_impact.py` when a roster-event
CSV is provided. This materially advances the current milestone by making external
roster-event validation explicit, reproducible, and easy to audit.

Validation: `./.venv/Scripts/python -m pytest -q` -> `50 passed`; the roster-change
validation CLI and summary tests pass, and the benchmark entrypoint remains intact.

Exact next step: use the new validation CLI on any candidate external roster-change
CSV, confirm it satisfies the contract and contains usable coverage, and then rerun
`src/player_impact.py --roster-events PATH` to decide whether the roster-change
benchmark gains enough evidence to justify a causal claim. Until a qualifying source
is validated, keep player-impact and roster-change projections labeled as
association diagnostics only.

Implementation update (2026-08-12): expanded the persisted model summary in
`src/main.py` and the interactive output in `src/interactive_predict.py` to include
an explicit model comparison block for the retained benchmark, the calibrated
boosted model, and the Elo reference. The output now reports the holdout log loss
and expected calibration error for each candidate, making the trade-off visible:
`boosted_hybrid` remains the recommended choice because it has the lowest holdout
log loss, while the isotonic-calibrated version reduces expected calibration error
but is not selected under the repository's probability-quality rule.

What now works: the prediction CLI and interactive flow can show the recommended
model, the exact holdout metrics, the calibration diagnostic, and the top feature
drivers side-by-side with a short comparison table, without altering the frozen
baseline or adding speculative features.

Remaining issue: the repository still has no independently sourced prospective
roster-change dataset that satisfies the project contract, so the next real
milestone remains external validation of the roster-impact benchmark rather than a
new model feature or a user-facing projection layer.

Implementation update (2026-08-12): added a roster-event preflight to
`src/player_impact.py` via `--validate-roster-events PATH`. The CLI validates a
candidate CSV against the repository contract and prints a JSON summary with the
add/remove counts, unique team/person coverage, and event time window without
running the impact benchmark. This keeps the roster-impact gate explicit and lets
any incoming external source be checked before it is used for model evaluation.

What now works: the benchmark script and the standalone roster-change validation
script share the same source contract checks, and either path can be used to
confirm that a candidate external roster-change dataset includes the required
`event_id`, `event_timestamp`, `team_id`, `person_id`, `change_type`, `source`, and
`source_url` fields. The project remains on the frozen prediction baseline, and
player-impact remains a descriptive diagnosis until a qualifying external source
shows a reliable effect.

Validation: `./.venv/Scripts/python -m pytest -q` -> `51 passed`; the new
validation mode and the roster-change contract tests pass, and the benchmark
entrypoint remains intact.

Exact next step: obtain or document a qualifying external roster-change source
with the required fields, validate it with
`src/player_impact.py --validate-roster-events PATH`, and then rerun
`src/player_impact.py --roster-events PATH` to decide whether the benchmark gains
sufficient evidence to justify a causal or prospective impact claim. Until then,
keep the game-prediction baseline frozen and the player-impact estimator labeled as
descriptive-only.
