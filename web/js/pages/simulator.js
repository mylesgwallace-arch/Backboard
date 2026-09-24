// pages/simulator.js — Season Simulator: run the existing validated Monte
// Carlo season engine (the `simulate_season` tool, backed by
// src/simulate_season.py's `project_season`) and present projected
// standings, the projected playoff field, and a league summary as a
// polished dashboard. No simulation logic lives here — every number comes
// directly from the `simulate_season` tool envelope.

import { getTools, getSeasonSimulation, projectRestOfSeason } from "../api.js";
import { fetchTeams } from "../components/teamSelect.js";
import { renderSeedProbabilityBars } from "../components/seedProbabilities.js";
import { renderLeagueSummaryCard } from "../components/leagueSummary.js";
import { renderPlayoffField } from "../components/playoffField.js";

export const meta = {
  title: "Season Simulator",
  subtitle: "Monte Carlo season projections: standings, seeds, and playoff odds from the validated production model.",
};

// These are the seasons src/simulate_season.py's own VALIDATION_SEASONS
// replays and compares against actual outcomes (see README §5), so they are
// the seasons this page exposes rather than an arbitrary free-text season.
const SEASON_OPTIONS = [2025, 2024, 2023];
const DEFAULT_SEASON = 2025;
const SIMULATION_OPTIONS = [200, 500, 1000, 2000];
const DEFAULT_SIMULATIONS = 1000;

export function render(container, ctx = {}) {
  container.innerHTML = `
    <div class="card fade-in">
      <div class="card-header">
        <div>
          <h2>What does this do?</h2>
        </div>
      </div>
      <div id="sim-about-body">
        <div class="skeleton" style="height:50px;"></div>
      </div>
    </div>

    <div class="card fade-in">
      <div class="card-header">
        <div>
          <h2>Run a season simulation</h2>
          <p class="card-subtitle">Choose a season and a number of simulations, then run the simulator.</p>
        </div>
      </div>
      <div class="grid-2">
        <div class="field">
          <label for="sim-season-select">Season</label>
          <div class="select-wrap">
            <select class="select" id="sim-season-select">
              ${SEASON_OPTIONS.map(
                (s) => `<option value="${s}" ${s === DEFAULT_SEASON ? "selected" : ""}>${s}-${String(s + 1).slice(-2)}</option>`
              ).join("")}
            </select>
          </div>
        </div>
        <div class="field">
          <label for="sim-count-select">Number of simulations</label>
          <div class="select-wrap">
            <select class="select" id="sim-count-select">
              ${SIMULATION_OPTIONS.map(
                (n) => `<option value="${n}" ${n === DEFAULT_SIMULATIONS ? "selected" : ""}>${n.toLocaleString()}</option>`
              ).join("")}
            </select>
          </div>
        </div>
        <div class="field">
          <label for="sim-mode-select">Mode</label>
          <div class="select-wrap">
            <select class="select" id="sim-mode-select">
              <option value="replay">Game-by-game replay (knows the season as it happened)</option>
              <option value="asof">Project from a date (only what was known then)</option>
            </select>
          </div>
        </div>
        <div class="field" id="sim-date-field" style="display:none">
          <label for="sim-date-input">Cutoff date</label>
          <input type="date" class="input" id="sim-date-input">
        </div>
      </div>
      <p class="text-muted mt-1" id="sim-mode-note" style="font-size:0.85rem;"></p>
      <div class="predict-actions">
        <button class="btn" id="run-sim-btn">Run Season Simulation</button>
        <span id="sim-status" class="text-muted"></span>
      </div>
      <div id="sim-error-mount" class="mt-1"></div>
    </div>

    <div id="sim-result-mount"></div>
  `;

  loadAboutCard(container);
  wireRunButton(container, ctx);
}

// ---------------------------------------------------------------------------
// "What does this do?" — sourced live from the tool registry so the
// explanation always matches the backend's own description/assumptions/
// limitations rather than an invented methodology claim.
// ---------------------------------------------------------------------------

async function loadAboutCard(container) {
  const mount = container.querySelector("#sim-about-body");
  try {
    const res = await getTools();
    const tool = (res.data?.tools || []).find((t) => t.name === "simulate_season");
    if (!tool) {
      mount.innerHTML = `<p class="text-muted">Simulation details are currently unavailable.</p>`;
      return;
    }
    mount.innerHTML = `
      <p>${escapeHtml(tool.description)}</p>
      <div class="grid-2 mt-2">
        <div>
          <div class="section-title">Assumptions</div>
          <ul class="plain-list">
            ${tool.assumptions.map((a) => `<li>${escapeHtml(a)}</li>`).join("")}
          </ul>
        </div>
        <div>
          <div class="section-title">Limitations</div>
          <ul class="plain-list">
            ${tool.limitations.map((l) => `<li>${escapeHtml(l)}</li>`).join("")}
          </ul>
        </div>
      </div>
    `;
  } catch (err) {
    mount.innerHTML = `<p class="text-muted">Simulation details are currently unavailable.</p>`;
  }
}

// ---------------------------------------------------------------------------
// Run simulation
// ---------------------------------------------------------------------------

function wireRunButton(container, ctx) {
  const btn = container.querySelector("#run-sim-btn");
  const statusEl = container.querySelector("#sim-status");
  const errorMount = container.querySelector("#sim-error-mount");
  const resultMount = container.querySelector("#sim-result-mount");
  const seasonSelect = container.querySelector("#sim-season-select");
  const countSelect = container.querySelector("#sim-count-select");
  const modeSelect = container.querySelector("#sim-mode-select");
  const dateField = container.querySelector("#sim-date-field");
  const dateInput = container.querySelector("#sim-date-input");
  const modeNote = container.querySelector("#sim-mode-note");

  const syncMode = () => {
    const asOf = modeSelect.value === "asof";
    const season = Number(seasonSelect.value);
    // `.field` sets its own display, which beats the hidden attribute.
    dateField.style.display = asOf ? "" : "none";
    if (!dateInput.value || !dateInput.value.startsWith(String(season + 1))) {
      dateInput.value = `${season + 1}-01-15`;
    }
    modeNote.textContent = asOf
      ? "Games before the cutoff keep their real results; every later game uses the production model with team strength frozen at the cutoff, plus a calibrated per-team strength uncertainty. Backtested on 2022-2025: about 8.7 wins average error preseason, 4.0 at mid-season (no better than carrying current win% forward), 2.2 with a quarter of the season left."
      : "Replay mode gives each game the model's pregame probability, which already reflects every earlier result that season. It is a replay check, not a forecast made on one date.";
  };
  modeSelect.addEventListener("change", syncMode);
  seasonSelect.addEventListener("change", syncMode);
  syncMode();

  btn.addEventListener("click", async () => {
    const season = Number(seasonSelect.value);
    const nSimulations = Number(countSelect.value);
    const asOf = modeSelect.value === "asof" ? dateInput.value : null;

    errorMount.innerHTML = "";
    btn.disabled = true;
    const originalLabel = btn.textContent;
    btn.innerHTML = '<span class="spinner"></span> Running simulation…';
    statusEl.textContent = asOf
      ? "Projecting from the cutoff date (a few seconds the first time)."
      : "This may take up to a minute the first time, while the model replays the full season history. It will be much faster on later runs.";
    resultMount.innerHTML = renderResultSkeleton();

    let res;
    try {
      res = asOf
        ? await projectRestOfSeason({ season, asOf, nSimulations })
        : await getSeasonSimulation({ season, nSimulations });
    } catch (err) {
      btn.disabled = false;
      btn.textContent = originalLabel;
      statusEl.textContent = "";
      resultMount.innerHTML = "";
      errorMount.innerHTML = errorBanner(
        `Network error while contacting the API: ${err.message}`
      );
      return;
    }

    btn.disabled = false;
    btn.textContent = originalLabel;
    statusEl.textContent = "";

    if (!res.ok || res.data?.status !== "success") {
      resultMount.innerHTML = "";
      errorMount.innerHTML = errorBanner(
        res.data?.error?.message || "The season simulation request failed. Please try again."
      );
      return;
    }

    const projection = res.data.data.projection;
    const teams = await fetchTeams().catch(() => []);
    resultMount.innerHTML = renderResults(projection, teams);
    wireResultInteractions(resultMount, teams, ctx);
  });
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function renderResultSkeleton() {
  return `
    <div class="card fade-in">
      <div class="stat-grid">
        ${Array(4).fill('<div class="skeleton" style="height:70px;"></div>').join("")}
      </div>
    </div>
    <div class="card fade-in">
      <div class="skeleton" style="height:280px;"></div>
    </div>
  `;
}

function errorBanner(message) {
  return `<div class="error-banner">⚠ ${escapeHtml(message)}</div>`;
}

function renderResults(projection, teams) {
  const teamById = new Map(teams.map((t) => [t.team_id, t]));
  const east = projection.projected_standings.filter((r) => r.conference === "East");
  const west = projection.projected_standings.filter((r) => r.conference === "West");
  const cutoffNote = projection.as_of
    ? `<div class="info-banner scouting-note fade-in"><span class="note-label">Projected from ${escapeHtml(projection.as_of)}</span><p class="model-insight">${projection.games_completed} games already played (real results kept), ${projection.games_remaining} simulated. Strength uncertainty (log-odds SD) ${projection.strength_sd.toFixed(2)}.</p></div>`
    : "";

  return `
    ${cutoffNote}
    ${renderLeagueSummaryCard(projection.league_summary, teamById, projection.season, projection.n_simulations)}
    ${renderPlayoffField(projection.projected_seedings, teamById)}
    <div class="card fade-in">
      <div class="card-header">
        <div>
          <h2>Projected standings</h2>
          <p class="card-subtitle">Sorted by projected mean wins. Click a team row to see its full seed-probability breakdown.</p>
        </div>
      </div>
      <div class="grid-2">
        ${renderStandingsTable("Eastern Conference", east, teamById)}
        ${renderStandingsTable("Western Conference", west, teamById)}
      </div>
    </div>
  `;
}

function renderStandingsTable(title, rows, teamById) {
  const hasRecord = rows.some((row) => row.current_wins != null);
  return `
    <div>
      <div class="section-title">${title}</div>
      <table class="data-table standings-table">
        <thead>
          <tr>
            <th scope="col">Seed</th>
            <th scope="col">Team</th>
            ${hasRecord ? '<th scope="col" class="num">Record then</th>' : ""}
            <th scope="col" class="num">Mean wins</th>
            <th scope="col" class="num">Playoff odds</th>
          </tr>
        </thead>
        <tbody>
          ${rows.map((row, index) => renderStandingsRow(row, index, teamById, hasRecord)).join("")}
        </tbody>
      </table>
      <p class="table-note"><span class="cutoff-key" aria-hidden="true"></span> Direct-playoff line: top 6 seeds</p>
    </div>
  `;
}

function renderStandingsRow(row, index, teamById, hasRecord = false) {
  const team = teamById.get(row.teamId);
  const oddsColor = playoffOddsColor(row.direct_playoff_probability);
  // The dashed playoff-cutoff court line sits above the first team outside
  // the direct-playoff seeds (seed 7).
  const cutoffClass = index === 6 ? " is-below-cutoff" : "";
  return `
    <tr class="standings-row${cutoffClass}" data-team-id="${row.teamId}">
      <td class="rank">${index + 1}</td>
      <td class="team">
        <button type="button" class="row-toggle" aria-expanded="false">${escapeHtml(team?.full_name || row.teamName || row.teamId)}</button>
      </td>
      ${hasRecord ? `<td class="num">${row.current_wins}-${row.current_losses}</td>` : ""}
      <td class="num">${row.mean_wins.toFixed(1)}</td>
      <td class="num">
        <div class="odds-cell">
          <span>${(row.direct_playoff_probability * 100).toFixed(0)}%</span>
          <div class="compare-track" style="width:60px;">
            <div class="compare-fill home" style="width:${row.direct_playoff_probability * 100}%; background:${oddsColor};"></div>
          </div>
        </div>
      </td>
    </tr>
    <tr class="standings-detail-row" data-detail-for="${row.teamId}" style="display:none;">
      <td colspan="${hasRecord ? 5 : 4}">
        <div class="stat-grid stat-grid--3">
          <div class="stat-tile">
            <div class="stat-label">Median wins</div>
            <div class="stat-value" style="font-size:1.1rem;">${row.median_wins.toFixed(1)}</div>
          </div>
          <div class="stat-tile">
            <div class="stat-label">Win range (5th–95th pct)</div>
            <div class="stat-value" style="font-size:1.1rem;">${row.p5_wins.toFixed(0)}–${row.p95_wins.toFixed(0)}</div>
          </div>
          <div class="stat-tile">
            <div class="stat-label">Mean conference seed</div>
            <div class="stat-value" style="font-size:1.1rem;">${row.mean_conference_seed.toFixed(1)}</div>
          </div>
        </div>
        <div class="section-title mt-2">Seed probability breakdown</div>
        ${renderSeedProbabilityBars(row)}
        <button class="link-btn mt-1" data-view-team="${row.teamId}">View full team profile ▸</button>
      </td>
    </tr>
  `;
}

function playoffOddsColor(probability) {
  if (probability >= 0.75) return "var(--positive)";
  if (probability >= 0.25) return "var(--warning)";
  return "var(--text-muted)";
}

// ---------------------------------------------------------------------------
// Interaction wiring for rendered results
// ---------------------------------------------------------------------------

function wireResultInteractions(resultMount, teams, ctx) {
  resultMount.querySelectorAll(".standings-row").forEach((row) => {
    // The team name is a real <button> (keyboard + aria-expanded); its click
    // bubbles to this row handler, so mouse and keyboard share one path.
    row.addEventListener("click", (event) => {
      if (event.target.closest("[data-view-team]")) return;
      const teamId = row.dataset.teamId;
      const detail = resultMount.querySelector(`.standings-detail-row[data-detail-for="${teamId}"]`);
      if (!detail) return;
      const isOpen = detail.style.display !== "none";
      detail.style.display = isOpen ? "none" : "table-row";
      row.classList.toggle("is-open", !isOpen);
      row.querySelector(".row-toggle")?.setAttribute("aria-expanded", String(!isOpen));
    });
  });

  if (ctx.navigate) {
    resultMount.querySelectorAll("[data-view-team]").forEach((el) => {
      el.addEventListener("click", (event) => {
        event.stopPropagation();
        ctx.navigate("/teams", { team: el.dataset.viewTeam });
      });
    });
  }
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}
