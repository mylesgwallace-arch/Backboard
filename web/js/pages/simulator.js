// pages/simulator.js — Season Simulator: run the existing validated Monte
// Carlo season engine (the `simulate_season` tool, backed by
// src/simulate_season.py's `project_season`) and present projected
// standings, the projected playoff field, and a league summary as a
// polished dashboard. No simulation logic lives here — every number comes
// directly from the `simulate_season` tool envelope.

import { getTools, getSeasonSimulation } from "../api.js";
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
      </div>
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
          <ul style="margin:0; padding-left:1.1rem; font-size:0.8rem; color:var(--text-secondary);">
            ${tool.assumptions.map((a) => `<li>${escapeHtml(a)}</li>`).join("")}
          </ul>
        </div>
        <div>
          <div class="section-title">Limitations</div>
          <ul style="margin:0; padding-left:1.1rem; font-size:0.8rem; color:var(--text-secondary);">
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

  btn.addEventListener("click", async () => {
    const season = Number(seasonSelect.value);
    const nSimulations = Number(countSelect.value);

    errorMount.innerHTML = "";
    btn.disabled = true;
    const originalLabel = btn.textContent;
    btn.innerHTML = '<span class="spinner"></span> Running simulation…';
    statusEl.textContent =
      "This may take up to a minute the first time, while the model replays the full season history. It will be much faster on later runs.";
    resultMount.innerHTML = renderResultSkeleton();

    let res;
    try {
      res = await getSeasonSimulation({ season, nSimulations });
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

  return `
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
  return `
    <div>
      <div class="section-title">${title}</div>
      <table style="width:100%; border-collapse:collapse; font-size:0.84rem;">
        <thead>
          <tr style="text-align:left; color:var(--text-secondary); font-size:0.72rem; text-transform:uppercase; letter-spacing:0.04em;">
            <th style="padding:0.4rem 0.3rem;">Seed</th>
            <th style="padding:0.4rem 0.3rem;">Team</th>
            <th style="padding:0.4rem 0.3rem; text-align:right;">Mean wins</th>
            <th style="padding:0.4rem 0.3rem; text-align:right;">Playoff odds</th>
          </tr>
        </thead>
        <tbody>
          ${rows.map((row, index) => renderStandingsRow(row, index, teamById)).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderStandingsRow(row, index, teamById) {
  const team = teamById.get(row.teamId);
  const oddsColor = playoffOddsColor(row.direct_playoff_probability);
  return `
    <tr class="standings-row" data-team-id="${row.teamId}" style="border-top:1px solid var(--border); cursor:pointer;">
      <td style="padding:0.5rem 0.3rem; color:var(--text-secondary);">${index + 1}</td>
      <td style="padding:0.5rem 0.3rem; font-weight:600;">${escapeHtml(team?.full_name || row.teamId)}</td>
      <td style="padding:0.5rem 0.3rem; text-align:right;">${row.mean_wins.toFixed(1)}</td>
      <td style="padding:0.5rem 0.3rem;">
        <div style="display:flex; align-items:center; gap:0.5rem; justify-content:flex-end;">
          <span>${(row.direct_playoff_probability * 100).toFixed(0)}%</span>
          <div class="compare-track" style="width:60px;">
            <div class="compare-fill home" style="width:${row.direct_playoff_probability * 100}%; background:${oddsColor};"></div>
          </div>
        </div>
      </td>
    </tr>
    <tr class="standings-detail-row" data-detail-for="${row.teamId}" style="display:none;">
      <td colspan="4" style="padding:0.6rem 0.3rem 1rem;">
        <div class="stat-grid" style="grid-template-columns:repeat(3, 1fr);">
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
    row.addEventListener("click", (event) => {
      if (event.target.closest("[data-view-team]")) return;
      const teamId = row.dataset.teamId;
      const detail = resultMount.querySelector(`.standings-detail-row[data-detail-for="${teamId}"]`);
      if (!detail) return;
      const isOpen = detail.style.display !== "none";
      detail.style.display = isOpen ? "none" : "table-row";
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
