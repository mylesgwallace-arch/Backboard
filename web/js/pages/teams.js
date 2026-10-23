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
  getTeamSchedule,
  getOddsHistory,
  getInjuryReport,
  getStandings,
  projectRestOfSeason,
} from "../api.js";
import { emphasisLineChart, wireEmphasisChart } from "../components/charts.js";
import { csvButton, wireCsv } from "../components/csv.js";
import { favoriteTeam, setFavoriteTeam } from "../components/prefs.js";
import { toast } from "../components/toast.js";
import { safe, seasonLabel, unwrap } from "../components/format.js";
import { fetchTeams, createTeamSelect } from "../components/teamSelect.js";
import { renderComparisonRow } from "../components/comparisonBar.js";
import { renderSeedProbabilityBars } from "../components/seedProbabilities.js";
import { teamColor } from "../teamColors.js";
import { clashingPair, teamSlabStyle } from "../colorInk.js";

export const meta = {
  title: "Team Explorer",
  subtitle: "Records, recent form, Elo strength, and season projections for any current NBA franchise.",
};

// The season in progress (or the latest with results), from the standings
// tool's default; read once per visit.
let CURRENT_SEASON = null;
const TABS = ["Overview", "Schedule", "Performance", "Projections", "Head-to-Head"];

// Streak stamp on the team header, derived from the team_form envelope this
// page already loads (last-10 win rate). See web/DESIGN-BRIEF.md.
const HOT_STREAK_MIN = 0.7;
const COLD_STREAK_MAX = 0.3;

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
  if (CURRENT_SEASON == null) {
    const standings = unwrap(await safe(getStandings()));
    CURRENT_SEASON = standings.data?.season ?? new Date().getFullYear() - (new Date().getMonth() < 8 ? 1 : 0);
  }

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

  wireTabs(body, { Schedule: () => loadSchedulePanel(body, teamId, ctx) });
  wireFavorite(body, teamId);
  wireProjectionsTab(body, teamId);
  wireHeadToHeadTab(body, teams, teamId, ctx);
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
    ${renderHeader({ team, color, form })}
    ${renderKeyMetrics({ recordSeason, recordAllTime, form, elo })}

    <div class="card fade-in">
      <div class="toolbar tab-strip" id="team-tabs" role="tablist" aria-label="Team sections">
        ${TABS.map(
          (tab, i) => `<button class="btn ${i === 0 ? "" : "btn-secondary"}" data-tab="${tab}" role="tab" aria-selected="${i === 0}">${tab}</button>`
        ).join("")}
      </div>

      <div data-tab-panel="Overview" role="tabpanel">
        ${renderOverviewPanel({ recordAllTime, recordSeason, form })}
      </div>
      <div data-tab-panel="Schedule" role="tabpanel" style="display:none;">
        <div class="skeleton" style="height:300px;"></div>
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

function renderHeader({ team, color, form }) {
  const name = team?.full_name || "Unknown team";
  const city = team?.city || "";
  const abbrev = team?.abbreviation || "";
  const streak = deriveStreak(form);
  return `
    <section class="card team-hero fade-in" style="${teamSlabStyle(color.primary)} --team-alt:${color.secondary};" aria-labelledby="team-hero-name">
      <div class="team-hero-body">
        <div class="team-hero-id">
          <div class="team-hero-city">${escapeHtml(city)}</div>
          <h2 class="team-hero-name" id="team-hero-name">${escapeHtml(name)}</h2>
        </div>
        <div class="team-hero-actions">
          ${streak ? `<p class="stamp stamp--tilt team-streak">${streak}</p>` : ""}
          <span class="badge team-abbrev-stamp">${escapeHtml(abbrev)}</span>
          <button type="button" class="btn btn-secondary fav-btn" id="team-fav-btn" aria-pressed="false">★ My team</button>
          <button class="btn" id="predict-from-team-btn">Predict a matchup ▸</button>
        </div>
      </div>
    </section>
  `;
}

function deriveStreak(form) {
  if (!form?.available) return "";
  const rate = form.data.win_rate_rolling_10;
  if (typeof rate !== "number") return "";
  if (rate >= HOT_STREAK_MIN) return "Hot streak";
  if (rate <= COLD_STREAK_MAX) return "Cold streak";
  return "";
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
          <div class="stat-label">${seasonLabel(CURRENT_SEASON)} record</div>
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
        <div class="stat-label">${seasonLabel(CURRENT_SEASON)} season</div>
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
        ${seasonLabel(CURRENT_SEASON)} season. The first run in a session replays the
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

function wireTabs(body, loaders = {}) {
  const tabButtons = body.querySelectorAll("#team-tabs [data-tab]");
  const loaded = new Set();
  tabButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      tabButtons.forEach((b) => {
        b.classList.add("btn-secondary");
        b.setAttribute("aria-selected", "false");
      });
      btn.classList.remove("btn-secondary");
      btn.setAttribute("aria-selected", "true");
      const tab = btn.dataset.tab;
      if (loaders[tab] && !loaded.has(tab)) {
        loaded.add(tab);
        loaders[tab]();
      }
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

    // A season in progress is projected forward from today's record; a
    // finished one falls back to the validated game-by-game replay.
    const today = new Date().toISOString().slice(0, 10);
    const forward = await safe(projectRestOfSeason({ season: CURRENT_SEASON, asOf: today, teamId }));
    const row = forward.ok && forward.data?.status === "success" ? forward.data.data.team_projection : null;
    let result = null;
    if (row && row.games_remaining > 0) {
      result = { projection: row, n: forward.data.data.projection.n_simulations ?? 1000, mode: "forward" };
    } else {
      const res = await getTeamProjection({ teamId, season: CURRENT_SEASON });
      if (res.ok && res.data?.status === "success") {
        result = { projection: res.data.data.projection, n: res.data.data.n_simulations, mode: "replay" };
      } else {
        mount.innerHTML = errorBanner(res.data?.error?.message || "The season projection request failed.");
      }
    }
    btn.disabled = false;
    btn.textContent = original;
    if (result) mount.innerHTML = renderProjectionResult(result.projection, result.n, result.mode);
  });
}

function renderProjectionResult(projection, nSimulations, mode = "replay") {
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
      ${mode === "forward"
        ? `Projected from today's record (${projection.current_wins}-${projection.current_losses}, ${projection.games_remaining} games left) with ${nSimulations} simulations of the rest of the ${seasonLabel(CURRENT_SEASON)} schedule, team strength frozen today plus calibrated uncertainty.`
        : `Based on ${nSimulations} Monte Carlo simulations replaying the ${seasonLabel(CURRENT_SEASON)} schedule with the validated production model.`}
      Descriptive projection, not a guarantee.
    </p>
  `;
}

function wireHeadToHeadTab(body, teams, teamId, ctx) {
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
    const { first: teamBarColor, second: opponentBarColor } = clashingPair(
      teamColor(teamId),
      teamColor(opponentId)
    );
    mount.innerHTML = `
      <div class="compare-legend">
        <span class="team-chip" style="${teamSlabStyle(teamBarColor)}">${escapeHtml(teamName)}</span>
        <span class="team-chip" style="${teamSlabStyle(opponentBarColor)}">${escapeHtml(opponentName)}</span>
      </div>
      ${renderComparisonRow({
        label: `All-time regular-season head-to-head (${data.games} games)`,
        homeValue: data.team_a_wins,
        awayValue: data.team_b_wins,
        homeColor: teamBarColor,
        awayColor: opponentBarColor,
        formatValue: (v) => `${v} wins`,
      })}
      <button class="link-btn mt-1" id="h2h-trend-link">See the season-by-season trend ▸</button>
    `;

    const trendLink = mount.querySelector("#h2h-trend-link");
    if (trendLink && ctx?.navigate) {
      trendLink.addEventListener("click", () => {
        ctx.navigate("/head-to-head", { a: teamId, b: opponentId });
      });
    }
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

// ---------------------------------------------------------------------------
// Schedule tab: results so far, win chances in every game left, how the
// playoff and title odds moved, and who is on the injury report (from the
// team_schedule, odds_history and injury_report tools).
// ---------------------------------------------------------------------------

function wireFavorite(body, teamId) {
  const button = body.querySelector("#team-fav-btn");
  if (!button) return;
  const paint = () => {
    const on = favoriteTeam() === teamId;
    button.setAttribute("aria-pressed", String(on));
    button.textContent = on ? "★ Your team" : "☆ My team";
  };
  button.addEventListener("click", () => {
    const on = favoriteTeam() === teamId;
    setFavoriteTeam(on ? null : teamId);
    paint();
    toast(on ? "Your team is cleared" : "Saved as your team: it leads Home and Tonight");
  });
  paint();
}

async function loadSchedulePanel(body, teamId, ctx) {
  const panel = body.querySelector('[data-tab-panel="Schedule"]');
  const [scheduleRes, oddsRes, injuryRes] = await Promise.all([
    safe(getTeamSchedule({ teamId, season: CURRENT_SEASON })).then(unwrap),
    safe(getOddsHistory({ season: CURRENT_SEASON, teamId })).then(unwrap),
    safe(getInjuryReport(teamId)).then(unwrap),
  ]);
  if (!panel.isConnected) return;
  if (!scheduleRes.data) {
    panel.innerHTML = errorBanner(scheduleRes.message);
    return;
  }
  const sched = scheduleRes.data;
  const left = sched.remaining;
  const points = oddsRes.data?.teams?.[0]?.points || [];
  const injured = injuryRes.data?.teams?.[0]?.players || [];
  const series = [
    { id: "playoffs", label: "Make playoffs", points: points.map((p) => ({ x: p.as_of, y: p.p_made_playoffs })) },
    { id: "title", label: "Title", points: points.map((p) => ({ x: p.as_of, y: p.p_champion })) },
  ];
  panel.innerHTML = `
    <div class="stat-grid">
      <div class="stat-tile"><div class="stat-label">Record</div><div class="stat-value">${escapeHtml(sched.record)}</div></div>
      <div class="stat-tile"><div class="stat-label">Games left</div><div class="stat-value">${left.games}</div>
        <div class="stat-note">${left.home} home · ${left.road} road</div></div>
      <div class="stat-tile"><div class="stat-label">Expected wins left</div><div class="stat-value">${left.expected_wins}</div>
        <div class="stat-note">sum of win chances, strength as of ${escapeHtml(sched.as_of)}</div></div>
      <div class="stat-tile"><div class="stat-label">Back-to-backs left</div><div class="stat-value">${left.back_to_backs}</div></div>
    </div>
    ${injured.length ? `<div class="info-banner mt-2"><div><p class="note-label">Injury report</p>
      <ul class="plain-list">${injured.map((p) => `<li><strong>${escapeHtml(p.name)}</strong> · ${escapeHtml(p.status)}${p.injury ? ` (${escapeHtml(p.injury.toLowerCase())})` : ""}${p.return_date ? ` · back ${escapeHtml(p.return_date)}` : ""}</li>`).join("")}</ul>
      <p class="text-muted" style="font-size:0.82rem;">The model has no injury input: these win chances assume a normal roster.</p></div></div>` : ""}
    ${points.length > 1 ? `<h3 class="section-title mt-2">How the odds moved</h3>
      <div class="chart-legend" aria-hidden="true"><span><i class="legend-line" style="background:var(--chart-base)"></i>Make playoffs</span>
        <span><i class="legend-line" style="background:var(--chart-accent)"></i>Win the title</span></div>
      <div class="chart-wrap" id="team-odds-chart">${emphasisLineChart(series, { emphasis: "title", labelTop: 2, label: "Playoff and title odds over the season" })}</div>` : ""}
    <div class="stat-toolbar toolbar mt-2"><h3 class="section-title" style="margin:0; flex:1;">${seasonLabel(sched.season)} schedule</h3>${csvButton("team-sched-csv")}</div>
    <div class="table-scroll schedule-scroll"><table class="data-table sticky-first schedule-table" id="team-sched-table">
      <thead><tr><th scope="col">Date</th><th scope="col">Opponent</th><th scope="col">Result</th><th class="num" scope="col">Win chance</th><th scope="col">Record</th></tr></thead>
      <tbody>${sched.games.map((g) => `<tr class="${g.played ? "" : "is-upcoming"}">
        <th scope="row">${escapeHtml(g.date)}</th>
        <td><button class="row-toggle" data-opp="${g.opponent_id}">${g.home ? "vs" : "@"} ${escapeHtml(g.opponent)}</button></td>
        <td>${g.played ? `<span class="${g.result === "W" ? "result-w" : "result-l"}">${g.result}</span> ${escapeHtml(g.score || "")}` : "—"}</td>
        <td class="num" data-csv="${g.win_probability}"><span class="odds-cell"><span>${Math.round(g.win_probability * 100)}%</span>
          <span class="mini-prob"><span style="width:${g.win_probability * 100}%"></span></span></span></td>
        <td>${g.record_after || ""}</td></tr>`).join("")}</tbody>
    </table></div>
    <p class="table-note">Played games show the pregame chance the model gave; upcoming games use strength as of today.</p>`;
  if (points.length > 1) wireEmphasisChart(panel.querySelector("#team-odds-chart"), series, { emphasis: "title" });
  wireCsv(panel.querySelector("#team-sched-csv"), panel.querySelector("#team-sched-table"), `backboard-schedule-${teamId}-${sched.season}`);
  panel.querySelectorAll("[data-opp]").forEach((button) => button.addEventListener("click", () =>
    ctx.navigate("/teams", { team: button.dataset.opp })));
  const next = panel.querySelector("tr.is-upcoming");
  if (next) next.classList.add("is-next");
}
