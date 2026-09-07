// pages/teams.js — Team Explorer: select a team, see its header/branding,
// key metrics, and drill into Overview / Performance / Projections /
// Head-to-Head tabs. Every number on this page comes directly from an
// existing backend tool envelope (team_record, team_form, team_elo_rating,
// team_projection, head_to_head) — nothing is computed or invented here.

import {
  getTeamRecord,
  getTeamForm,
  getTeamEloRating,
  getTeamProjection,
  getHeadToHead,
} from "../api.js";
import { fetchTeams, createTeamSelect } from "../components/teamSelect.js";
import { renderComparisonRow } from "../components/comparisonBar.js";
import { renderSeedProbabilityBars } from "../components/seedProbabilities.js";
import { teamColor } from "../teamColors.js";

export const meta = {
  title: "Team Explorer",
  subtitle: "Records, recent form, Elo strength, and season projections for any current NBA franchise.",
};

const CURRENT_SEASON = 2025; // Latest season present in the repository's data (see PROJECT_CONTEXT.md).
const TABS = ["Overview", "Performance", "Projections", "Head-to-Head"];

export function render(container, ctx = {}) {
  container.innerHTML = `
    <div class="card fade-in">
      <div class="card-header">
        <div>
          <h2>Select a team</h2>
          <p class="card-subtitle">Choose any current NBA franchise to explore its analytics.</p>
        </div>
      </div>
      <div id="team-select-mount" style="max-width:360px;"></div>
    </div>
    <div id="team-explorer-body"></div>
  `;

  const selectMount = container.querySelector("#team-select-mount");
  const body = container.querySelector("#team-explorer-body");

  const teamSelect = createTeamSelect({
    id: "explorer-team-select",
    labelText: "Team",
    placeholder: "Select a team…",
  });
  selectMount.appendChild(teamSelect.root);

  let teams = [];
  const presetTeamId = ctx.query?.get ? ctx.query.get("team") : null;

  body.innerHTML = emptyState("Select a team above to see its analytics.");

  fetchTeams()
    .then((fetchedTeams) => {
      teams = fetchedTeams;
      teamSelect.setTeams(teams);
      if (presetTeamId && teams.some((t) => String(t.team_id) === presetTeamId)) {
        teamSelect.selectEl.value = presetTeamId;
        loadTeam(body, teams, Number(presetTeamId), ctx);
      }
    })
    .catch((err) => {
      body.innerHTML = errorBanner(`Could not load the team list: ${err.message}`);
    });

  teamSelect.selectEl.addEventListener("change", () => {
    const teamId = Number(teamSelect.selectEl.value);
    if (!teamId) {
      body.innerHTML = emptyState("Select a team above to see its analytics.");
      return;
    }
    loadTeam(body, teams, teamId, ctx);
  });
}

// ---------------------------------------------------------------------------
// Data loading
// ---------------------------------------------------------------------------

async function loadTeam(body, teams, teamId, ctx) {
  body.innerHTML = renderLoadingShell();
  const team = teams.find((t) => t.team_id === teamId);

  const [recordAllTime, recordSeason, form, elo] = await Promise.all([
    getTeamRecord({ teamId }),
    getTeamRecord({ teamId, season: CURRENT_SEASON }),
    getTeamForm({ teamId }),
    getTeamEloRating({ teamId }),
  ]);

  body.innerHTML = renderTeamExplorer({
    team,
    teams,
    teamId,
    recordAllTime: envelopeData(recordAllTime),
    recordSeason: envelopeData(recordSeason),
    form: envelopeData(form),
    elo: envelopeData(elo),
    ctx,
  });

  wireTabs(body);
  wireProjectionsTab(body, teamId);
  wireHeadToHeadTab(body, teams, teamId);
  wirePredictButton(body, team, ctx);
}

function envelopeData(res) {
  if (!res || !res.ok || !res.data || res.data.status !== "success") {
    return { available: false, message: res?.data?.error?.message || null };
  }
  return { available: true, data: res.data.data };
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function renderLoadingShell() {
  return `
    <div class="card fade-in">
      <div class="skeleton" style="height:90px;"></div>
    </div>
    <div class="card fade-in">
      <div class="stat-grid">
        ${Array(4).fill('<div class="skeleton" style="height:70px;"></div>').join("")}
      </div>
    </div>
  `;
}

function emptyState(message) {
  return `<div class="empty-state">${escapeHtml(message)}</div>`;
}

function errorBanner(message) {
  return `<div class="error-banner">⚠ ${escapeHtml(message)}</div>`;
}

function renderTeamExplorer({ team, teamId, recordAllTime, recordSeason, form, elo, ctx }) {
  const color = teamColor(teamId);

  return `
    ${renderHeader({ team, color })}
    ${renderKeyMetrics({ recordSeason, recordAllTime, form, elo })}

    <div class="card fade-in">
      <div class="toolbar" style="border-bottom:1px solid var(--border); padding-bottom:0.8rem; margin-bottom:1rem;" id="team-tabs">
        ${TABS.map(
          (tab, i) => `<button class="btn ${i === 0 ? "" : "btn-secondary"}" data-tab="${tab}">${tab}</button>`
        ).join("")}
      </div>

      <div data-tab-panel="Overview">
        ${renderOverviewPanel({ recordAllTime, recordSeason, form })}
      </div>
      <div data-tab-panel="Performance" style="display:none;">
        ${renderPerformancePanel({ form })}
      </div>
      <div data-tab-panel="Projections" style="display:none;">
        ${renderProjectionsPanel({ teamId })}
      </div>
      <div data-tab-panel="Head-to-Head" style="display:none;">
        ${renderHeadToHeadPanel()}
      </div>
    </div>
  `;
}

function renderHeader({ team, color }) {
  const name = team?.full_name || "Unknown team";
  const city = team?.city || "";
  const abbrev = team?.abbreviation || "";
  return `
    <div class="card fade-in" style="border-top:3px solid ${color.primary};">
      <div style="display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:1rem;">
        <div>
          <div class="text-muted" style="font-size:0.75rem; text-transform:uppercase; letter-spacing:0.06em;">${escapeHtml(city)}</div>
          <h2 style="font-size:1.4rem; margin-top:0.2rem;">${escapeHtml(name)}</h2>
        </div>
        <span class="badge" style="border-color:${color.primary}; color:${color.primary};">${escapeHtml(abbrev)}</span>
        <button class="btn" id="predict-from-team-btn">Predict a matchup ▸</button>
      </div>
    </div>
  `;
}

function renderKeyMetrics({ recordSeason, recordAllTime, form, elo }) {
  const seasonRecord = recordSeason.available
    ? `${recordSeason.data.wins}-${recordSeason.data.losses}`
    : "—";
  const allTimeRecord = recordAllTime.available
    ? `${recordAllTime.data.wins}-${recordAllTime.data.losses}`
    : "—";
  const eloValue = elo.available ? elo.data.elo_rating.toFixed(0) : "—";
  const formLabel = form.available
    ? `${(form.data.win_rate_rolling_10 * 100).toFixed(0)}%`
    : "—";

  return `
    <div class="card fade-in">
      <div class="section-title">Key metrics</div>
      <div class="stat-grid">
        <div class="stat-tile">
          <div class="stat-label">Elo rating</div>
          <div class="stat-value">${eloValue}</div>
        </div>
        <div class="stat-tile">
          <div class="stat-label">${CURRENT_SEASON} record</div>
          <div class="stat-value">${seasonRecord}</div>
        </div>
        <div class="stat-tile">
          <div class="stat-label">All-time record</div>
          <div class="stat-value" style="font-size:1.1rem;">${allTimeRecord}</div>
        </div>
        <div class="stat-tile">
          <div class="stat-label">Recent win rate (L10)</div>
          <div class="stat-value">${formLabel}</div>
        </div>
      </div>
      ${!elo.available ? `<p class="text-muted mt-1" style="font-size:0.76rem;">Elo rating unavailable: ${escapeHtml(elo.message || "no completed games found.")}</p>` : ""}
    </div>
  `;
}

function renderOverviewPanel({ recordAllTime, recordSeason, form }) {
  const snapshot = form.available
    ? `<p class="text-muted mt-1" style="font-size:0.78rem;">Form snapshot as of ${escapeHtml(form.data.snapshot_date)}.</p>`
    : "";
  return `
    <div class="stat-grid">
      <div class="stat-tile">
        <div class="stat-label">${CURRENT_SEASON} season</div>
        <div class="stat-value">${recordSeason.available ? `${recordSeason.data.wins}W – ${recordSeason.data.losses}L` : "—"}</div>
      </div>
      <div class="stat-tile">
        <div class="stat-label">All-time (regular season)</div>
        <div class="stat-value">${recordAllTime.available ? `${recordAllTime.data.wins}W – ${recordAllTime.data.losses}L` : "—"}</div>
      </div>
      <div class="stat-tile">
        <div class="stat-label">All-time games played</div>
        <div class="stat-value">${recordAllTime.available ? recordAllTime.data.games : "—"}</div>
      </div>
      <div class="stat-tile">
        <div class="stat-label">Points differential (L10)</div>
        <div class="stat-value">${form.available ? formatSigned(form.data.plusMinusPoints_rolling_10) : "—"}</div>
      </div>
    </div>
    ${snapshot}
    <p class="text-muted mt-2" style="font-size:0.78rem;">
      Records are factual regular-season results from the repository database.
      The Elo rating and recent-form snapshot come from the same pregame
      inputs used by the production prediction model.
    </p>
  `;
}

function renderPerformancePanel({ form }) {
  if (!form.available) {
    return emptyState(form.message || "Recent form is unavailable for this team.");
  }
  const d = form.data;
  return `
    <div class="section-title">Rolling form (last 10 games), as of ${escapeHtml(d.snapshot_date)}</div>
    <div class="stat-grid">
      <div class="stat-tile">
        <div class="stat-label">Win rate</div>
        <div class="stat-value">${(d.win_rate_rolling_10 * 100).toFixed(0)}%</div>
      </div>
      <div class="stat-tile">
        <div class="stat-label">Points scored / game</div>
        <div class="stat-value">${d.teamScore_rolling_10.toFixed(1)}</div>
      </div>
      <div class="stat-tile">
        <div class="stat-label">Points allowed / game</div>
        <div class="stat-value">${d.opponentScore_rolling_10.toFixed(1)}</div>
      </div>
      <div class="stat-tile">
        <div class="stat-label">Point differential</div>
        <div class="stat-value">${formatSigned(d.plusMinusPoints_rolling_10)}</div>
      </div>
      <div class="stat-tile">
        <div class="stat-label">Rest days entering last game</div>
        <div class="stat-value">${d.rest_days.toFixed(1)}</div>
      </div>
      <div class="stat-tile">
        <div class="stat-label">Active players (last game)</div>
        <div class="stat-value">${d.active_players_last_game.toFixed(0)}</div>
      </div>
    </div>
    <p class="text-muted mt-2" style="font-size:0.78rem;">
      This is the same rolling pregame snapshot the production model uses for
      this team's next game — a descriptive input, not a prediction.
    </p>
  `;
}

function renderProjectionsPanel() {
  return `
    <div id="projections-panel-body">
      <p class="text-muted" style="font-size:0.85rem;">
        Run the validated Monte Carlo season simulator for this team's
        ${CURRENT_SEASON} season. The first run in a session replays the
        entire schedule and may take up to a minute; results are cached
        after that.
      </p>
      <button class="btn mt-1" id="run-projection-btn">Run season projection</button>
      <div id="projection-result-mount" class="mt-2"></div>
    </div>
  `;
}

function renderHeadToHeadPanel() {
  return `
    <p class="text-muted" style="font-size:0.85rem;">
      Compare this team's regular-season history against another franchise.
    </p>
    <div class="field" style="max-width:360px;">
      <label for="h2h-opponent-select">Opponent</label>
      <div class="select-wrap">
        <select class="select" id="h2h-opponent-select">
          <option value="">Select an opponent…</option>
        </select>
      </div>
    </div>
    <button class="btn mt-1" id="run-h2h-btn" disabled>Compare</button>
    <div id="h2h-result-mount" class="mt-2"></div>
  `;
}

// ---------------------------------------------------------------------------
// Interaction wiring
// ---------------------------------------------------------------------------

function wireTabs(body) {
  const tabButtons = body.querySelectorAll("#team-tabs [data-tab]");
  tabButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      tabButtons.forEach((b) => b.classList.add("btn-secondary"));
      btn.classList.remove("btn-secondary");
      TABS.forEach((tab) => {
        const panel = body.querySelector(`[data-tab-panel="${tab}"]`);
        if (panel) panel.style.display = tab === btn.dataset.tab ? "" : "none";
      });
    });
  });
}

function wireProjectionsTab(body, teamId) {
  const btn = body.querySelector("#run-projection-btn");
  const mount = body.querySelector("#projection-result-mount");
  if (!btn) return;

  btn.addEventListener("click", async () => {
    btn.disabled = true;
    const original = btn.textContent;
    btn.innerHTML = '<span class="spinner spinner-dark"></span> Running simulation… this may take up to a minute';
    mount.innerHTML = "";

    const res = await getTeamProjection({ teamId, season: CURRENT_SEASON });
    btn.disabled = false;
    btn.textContent = original;

    if (!res.ok || res.data?.status !== "success") {
      mount.innerHTML = errorBanner(
        res.data?.error?.message || "The season projection request failed."
      );
      return;
    }

    mount.innerHTML = renderProjectionResult(res.data.data.projection, res.data.data.n_simulations);
  });
}

function renderProjectionResult(projection, nSimulations) {
  return `
    <div class="stat-grid">
      <div class="stat-tile">
        <div class="stat-label">Projected wins (mean)</div>
        <div class="stat-value">${projection.mean_wins.toFixed(1)}</div>
      </div>
      <div class="stat-tile">
        <div class="stat-label">Win range (5th–95th pct)</div>
        <div class="stat-value" style="font-size:1.1rem;">${projection.p5_wins.toFixed(0)}–${projection.p95_wins.toFixed(0)}</div>
      </div>
      <div class="stat-tile">
        <div class="stat-label">Direct playoff probability</div>
        <div class="stat-value">${(projection.direct_playoff_probability * 100).toFixed(0)}%</div>
      </div>
      <div class="stat-tile">
        <div class="stat-label">Conference</div>
        <div class="stat-value" style="font-size:1.1rem;">${escapeHtml(projection.conference)}</div>
      </div>
    </div>
    <div class="section-title mt-2">Projected conference seed probability</div>
    ${renderSeedProbabilityBars(projection)}
    <p class="text-muted mt-2" style="font-size:0.76rem;">
      Based on ${nSimulations} Monte Carlo simulations of the ${CURRENT_SEASON} schedule
      using the validated production model. Descriptive projection, not a guarantee.
    </p>
  `;
}

function wireHeadToHeadTab(body, teams, teamId) {
  const select = body.querySelector("#h2h-opponent-select");
  const runBtn = body.querySelector("#run-h2h-btn");
  const mount = body.querySelector("#h2h-result-mount");
  if (!select) return;

  teams
    .filter((t) => t.team_id !== teamId)
    .slice()
    .sort((a, b) => a.full_name.localeCompare(b.full_name))
    .forEach((t) => {
      const option = document.createElement("option");
      option.value = String(t.team_id);
      option.textContent = t.full_name;
      select.appendChild(option);
    });

  select.addEventListener("change", () => {
    runBtn.disabled = !select.value;
  });

  runBtn.addEventListener("click", async () => {
    const opponentId = Number(select.value);
    mount.innerHTML = `<div class="skeleton" style="height:60px;"></div>`;
    const res = await getHeadToHead({ teamAId: teamId, teamBId: opponentId });
    if (!res.ok || res.data?.status !== "success") {
      mount.innerHTML = errorBanner(res.data?.error?.message || "The head-to-head request failed.");
      return;
    }
    const data = res.data.data;
    const teamName = teams.find((t) => t.team_id === teamId)?.full_name || "This team";
    const opponentName = teams.find((t) => t.team_id === opponentId)?.full_name || "Opponent";
    mount.innerHTML = `
      <div class="compare-values" style="margin-bottom:0.6rem;">
        <span>${escapeHtml(teamName)}</span>
        <span>${escapeHtml(opponentName)}</span>
      </div>
      ${renderComparisonRow({
        label: `All-time regular-season head-to-head (${data.games} games)`,
        homeValue: data.team_a_wins,
        awayValue: data.team_b_wins,
        homeColor: teamColor(teamId).primary,
        awayColor: teamColor(opponentId).primary,
        formatValue: (v) => `${v} wins`,
      })}
    `;
  });
}

function wirePredictButton(body, team, ctx) {
  const btn = body.querySelector("#predict-from-team-btn");
  if (!btn || !team || !ctx.navigate) return;
  btn.addEventListener("click", () => {
    ctx.navigate("/matchups", { home: team.team_id });
  });
}

function formatSigned(value, digits = 1) {
  const rounded = value.toFixed(digits);
  return value > 0 ? `+${rounded}` : rounded;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}
