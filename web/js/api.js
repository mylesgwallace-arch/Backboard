// api.js — thin fetch wrapper over the existing stdlib backend (src/api.py).
//
// This module is the ONLY place that knows the HTTP contract. Every page/
// component calls these functions instead of calling fetch() directly, so
// the backend contract can evolve (or be swapped for a future LLM-orchestrated
// layer) without touching page code. No prediction/simulation logic lives
// here — everything is delegated to the backend's deterministic tool layer.

const BASE = ""; // same-origin: served by src/api.py at http://127.0.0.1:8000/

async function parseResponse(response) {
  const text = await response.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch (err) {
      data = { raw: text };
    }
  }
  return { ok: response.ok, status: response.status, data };
}

async function getJSON(path) {
  const response = await fetch(BASE + path);
  return parseResponse(response);
}

async function postJSON(path, body) {
  const response = await fetch(BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return parseResponse(response);
}

export function getHealth() {
  return getJSON("/health");
}

export function getTools() {
  return getJSON("/tools");
}

export function runTool(name, parameters = {}) {
  return postJSON("/tools/" + encodeURIComponent(name), { parameters });
}

export function ask(question, { mode, context } = {}) {
  const body = { question };
  if (mode) body.mode = mode;
  if (context) body.context = context;
  return postJSON("/ask", body);
}

export function ingest(source, dryRun = true) {
  return postJSON("/ingest", { source, dry_run: dryRun });
}

// Convenience wrappers over specific tools used by the structured UI. These
// simply call runTool() with the right name/parameters — no logic duplication.

export function listTeams() {
  return runTool("list_teams", {});
}

export function predictMatchup({ homeTeamId, awayTeamId, gameDate }) {
  const parameters = {
    home_team_id: homeTeamId,
    away_team_id: awayTeamId,
  };
  if (gameDate) {
    parameters.game_date = gameDate;
  }
  return runTool("predict_matchup", parameters);
}

export function getTeamRecord({ teamId, season }) {
  const parameters = { team_id: teamId };
  if (season != null) parameters.season = season;
  return runTool("team_record", parameters);
}

export function getHeadToHead({ teamAId, teamBId, season }) {
  const parameters = { team_a_id: teamAId, team_b_id: teamBId };
  if (season != null) parameters.season = season;
  return runTool("head_to_head", parameters);
}

export function getTeamForm({ teamId, asOf }) {
  const parameters = { team_id: teamId };
  if (asOf) parameters.as_of = asOf;
  return runTool("team_form", parameters);
}

export function getTeamEloRating({ teamId, asOf }) {
  const parameters = { team_id: teamId };
  if (asOf) parameters.as_of = asOf;
  return runTool("team_elo_rating", parameters);
}

export function getTeamProjection({ teamId, season, nSimulations, randomState }) {
  const parameters = { team_id: teamId, season };
  if (nSimulations != null) parameters.n_simulations = nSimulations;
  if (randomState != null) parameters.random_state = randomState;
  return runTool("team_projection", parameters);
}

export function getSeasonSimulation({ season, nSimulations, randomState }) {
  const parameters = { season };
  if (nSimulations != null) parameters.n_simulations = nSimulations;
  if (randomState != null) parameters.random_state = randomState;
  return runTool("simulate_season", parameters);
}

export function projectRestOfSeason({ season, asOf, teamId, nSimulations }) {
  const parameters = { season, as_of: asOf };
  if (teamId != null) parameters.team_id = teamId;
  if (nSimulations != null) parameters.n_simulations = nSimulations;
  return runTool("project_rest_of_season", parameters);
}

export function predictMargin({ homeTeamId, awayTeamId, gameDate }) {
  const parameters = { home_team_id: homeTeamId, away_team_id: awayTeamId };
  if (gameDate) parameters.game_date = gameDate;
  return runTool("predict_margin", parameters);
}

export function getPlayoffOdds({ season, asOf, teamId, nSimulations }) {
  const parameters = { season };
  if (asOf) parameters.as_of = asOf;
  if (teamId != null) parameters.team_id = teamId;
  if (nSimulations != null) parameters.n_simulations = nSimulations;
  return runTool("playoff_odds", parameters);
}

export function getDataStatus() {
  return runTool("data_status", {});
}

export function getTeamStrength({ asOf, rosterAdjusted } = {}) {
  const parameters = {};
  if (asOf) parameters.as_of = asOf;
  if (rosterAdjusted) parameters.roster_adjusted = true;
  return runTool("team_strength", parameters);
}

export function getTeamRoster({ teamId, asOf }) {
  const parameters = { team_id: teamId };
  if (asOf) parameters.as_of = asOf;
  return runTool("team_roster", parameters);
}

export function getTeamSeasonRoster({ teamId, season }) {
  return runTool("team_season_roster", { team_id: teamId, season });
}

export function simulateEraSwap({ teamId, season, outPersonId, inPlayer, inPersonId, inSeason, method }) {
  const parameters = { team_id: teamId, season, out_person_id: outPersonId, in_season: inSeason };
  if (inPersonId != null) parameters.in_person_id = inPersonId;
  else parameters.in_player = inPlayer;
  if (method) parameters.method = method;
  return runTool("simulate_era_swap", parameters);
}

export function getValidationReport(component) {
  return runTool("validation_report", { component });
}

export function resolvePlayer({ name, season, limit }) {
  const parameters = { name };
  if (season != null) parameters.season = season;
  if (limit != null) parameters.limit = limit;
  return runTool("resolve_player", parameters);
}

export function getPlayerImpact({ personId, before, window }) {
  const parameters = { person_id: personId };
  if (before) parameters.before = before;
  if (window != null) parameters.window = window;
  return runTool("player_impact", parameters);
}
