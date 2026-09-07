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

export function ask(question) {
  return postJSON("/ask", { question });
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
