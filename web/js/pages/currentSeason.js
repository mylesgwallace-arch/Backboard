// pages/currentSeason.js — "where things stand now": data freshness, every
// team's current strength as the production model sees it (plus the
// experimental roster-adjusted version), each team's offseason arrivals and
// departures, and — once the upcoming schedule is ingested — the preseason
// projection and title odds. All numbers come from tool envelopes
// (data_status, team_strength, team_roster, project_rest_of_season,
// playoff_odds).

import {
  getDataStatus,
  getTeamStrength,
  getTeamRoster,
  projectRestOfSeason,
  getPlayoffOdds,
} from "../api.js";

export const meta = {
  title: "Current Season",
  subtitle: "Where every team stands today, who moved this offseason, and the season ahead.",
};

export function render(container) {
  container.innerHTML = `
    <div class="card fade-in">
      <div class="card-header"><div><h2>Data freshness</h2>
        <p class="card-subtitle">Nothing is fetched automatically; this is what the database holds right now.</p></div></div>
      <div id="cs-status"><div class="skeleton" style="height:70px;"></div></div>
    </div>
    <div id="cs-projection"></div>
    <div class="card fade-in">
      <div class="card-header">
        <div>
          <h2>Team strength today</h2>
          <p class="card-subtitle">
            Chance to beat an average opponent (mean of home and road win probabilities against every
            other team) from the frozen production model, using each team's latest data. The second
            column recomputes the roster-derived model inputs from this offseason's transactions.
          </p>
        </div>
      </div>
      <div id="cs-strength"><div class="skeleton" style="height:320px;"></div></div>
    </div>
    <div id="cs-roster"></div>
  `;
  loadStatus(container);
  loadStrength(container);
}

async function loadStatus(container) {
  const mount = container.querySelector("#cs-status");
  const res = await safe(getDataStatus());
  if (!res.ok || res.data?.status !== "success") {
    mount.innerHTML = errorBanner(res.data?.error?.message || "Status unavailable.");
    return;
  }
  const s = res.data.data;
  const scheduleLoaded = s.upcoming_season_regular_season_games_in_database > 0;
  mount.innerHTML = `
    <div class="stat-grid">
      ${tile("Latest completed game", (s.latest_completed_game || "—").slice(0, 10))}
      ${tile("Latest model feature row", (s.latest_feature_row || "—").slice(0, 10))}
      ${tile("Latest roster transaction", s.latest_transaction || "—")}
      ${tile(`${s.upcoming_season}-${String(s.upcoming_season + 1).slice(-2)} schedule`,
             scheduleLoaded ? `${s.upcoming_season_regular_season_games_in_database} games loaded` : "not loaded")}
    </div>
    ${scheduleLoaded ? "" : `
      <div class="info-banner scouting-note mt-2">
        <span class="note-label">To project the ${s.upcoming_season}-${String(s.upcoming_season + 1).slice(-2)} season</span>
        <p class="model-insight">Ingest its schedule: <code>${escapeHtml(s.how_to_update.schedule)}</code>.
        The page will then offer the preseason projection and title odds.</p>
      </div>`}
  `;
  if (scheduleLoaded) renderProjectionCard(container, s.upcoming_season, s.today);
}

function renderProjectionCard(container, season, today) {
  const mount = container.querySelector("#cs-projection");
  mount.innerHTML = `
    <div class="card fade-in">
      <div class="card-header"><div><h2>${season}-${String(season + 1).slice(-2)} projection</h2>
        <p class="card-subtitle">Real results so far plus simulated remaining games, strength frozen today; then the play-in and bracket.</p></div></div>
      <div class="predict-actions">
        <button class="btn" id="cs-run">Project the season</button>
        <span class="text-muted" id="cs-run-status"></span>
      </div>
      <div id="cs-run-result"></div>
    </div>`;
  const btn = mount.querySelector("#cs-run");
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    mount.querySelector("#cs-run-status").textContent = "Simulating…";
    const [proj, odds] = await Promise.all([
      safe(projectRestOfSeason({ season, asOf: today })),
      safe(getPlayoffOdds({ season, asOf: today })),
    ]);
    btn.disabled = false;
    mount.querySelector("#cs-run-status").textContent = "";
    const out = mount.querySelector("#cs-run-result");
    if (!proj.ok || proj.data?.status !== "success") {
      out.innerHTML = errorBanner(proj.data?.error?.message || "Projection failed.");
      return;
    }
    const titles = new Map((odds.data?.data?.teams || []).map((t) => [t.teamId, t.p_champion]));
    const rows = [...proj.data.data.projection.projected_standings].sort((a, b) => b.mean_wins - a.mean_wins);
    out.innerHTML = `
      <table class="data-table mt-1">
        <thead><tr><th scope="col">Team</th><th scope="col" class="num">Record</th>
          <th scope="col" class="num">Mean wins</th><th scope="col" class="num">5-95%</th>
          <th scope="col" class="num">Top-6 odds</th><th scope="col" class="num">Title</th></tr></thead>
        <tbody>${rows.map((r) => `
          <tr><td class="team">${escapeHtml(r.teamName || r.teamId)}</td>
            <td class="num">${r.current_wins}-${r.current_losses}</td>
            <td class="num">${r.mean_wins.toFixed(1)}</td>
            <td class="num">${r.p5_wins.toFixed(0)}–${r.p95_wins.toFixed(0)}</td>
            <td class="num">${pct(r.direct_playoff_probability, 0)}</td>
            <td class="num">${titles.has(r.teamId) ? pct(titles.get(r.teamId)) : "—"}</td></tr>`).join("")}
        </tbody>
      </table>
      <p class="text-muted mt-1">Backtested preseason error is about 8.7 wins per team (2022-2025); the ranges are calibrated to cover roughly 90% of outcomes.</p>`;
  });
}

async function loadStrength(container) {
  const mount = container.querySelector("#cs-strength");
  const res = await safe(getTeamStrength({ rosterAdjusted: true }));
  if (!res.ok || res.data?.status !== "success") {
    mount.innerHTML = errorBanner(res.data?.error?.message || "Strength table unavailable.");
    return;
  }
  const data = res.data.data;
  mount.innerHTML = `
    <table class="data-table">
      <thead><tr>
        <th scope="col">#</th><th scope="col">Team</th><th scope="col" class="num">Elo</th>
        <th scope="col" class="num">Beat avg (model)</th>
        <th scope="col" class="num">With roster moves</th>
        <th scope="col" class="num">In / out (min/g)</th>
      </tr></thead>
      <tbody>${data.teams.map((row, index) => {
        const c = row.roster_changes || {};
        return `
          <tr>
            <td class="rank">${index + 1}</td>
            <td class="team"><button type="button" class="row-toggle" data-team="${row.teamId}">${escapeHtml(row.teamName || row.teamId)}</button></td>
            <td class="num">${row.elo_rating.toFixed(0)}</td>
            <td class="num">${pct(row.win_probability_vs_average)}</td>
            <td class="num">${pct(row.roster_adjusted_win_probability_vs_average)}</td>
            <td class="num">+${fmt(c.minutes_per_game_arrived)} / −${fmt(c.minutes_per_game_departed)}</td>
          </tr>`;
      }).join("")}</tbody>
    </table>
    <p class="text-muted mt-1">
      ${data.transactions_applied} transactions applied (latest ${escapeHtml(data.latest_transaction || "—")}).
      The roster-adjusted column is bookkeeping, not a better forecast: in a 2015-2025 preseason backtest it did
      not beat the unadjusted model (the model puts little weight on roster-derived inputs). Free agents who
      have not signed elsewhere still count for their old team. Click a team for its arrivals and departures.
    </p>`;
  mount.querySelectorAll("[data-team]").forEach((btn) => {
    btn.addEventListener("click", () => showRoster(container, Number(btn.dataset.team), btn.textContent));
  });
}

async function showRoster(container, teamId, teamName) {
  const mount = container.querySelector("#cs-roster");
  mount.innerHTML = `<div class="card fade-in"><div class="skeleton" style="height:140px;"></div></div>`;
  const res = await safe(getTeamRoster({ teamId }));
  if (!res.ok || res.data?.status !== "success") {
    mount.innerHTML = `<div class="card fade-in">${errorBanner(res.data?.error?.message || "Roster unavailable.")}</div>`;
    return;
  }
  const roster = res.data.data.roster;
  const list = (players, label) => `
    <div>
      <div class="section-title">${label} (${players.length})</div>
      ${players.length ? `<table class="data-table">
        <thead><tr><th scope="col">Player</th><th scope="col" class="num">Min/g</th><th scope="col" class="num">Pts/g</th><th scope="col" class="num">Games</th></tr></thead>
        <tbody>${players.map((p) => `
          <tr><td class="team">${escapeHtml(p.name)}${p.no_nba_history ? ' <span class="text-muted">(no NBA box scores)</span>' : ""}</td>
            <td class="num">${fmt(p.player_minutes_rolling_10)}</td>
            <td class="num">${fmt(p.player_points_rolling_10)}</td>
            <td class="num">${p.games}</td></tr>`).join("")}
        </tbody></table>` : `<p class="text-muted">None recorded.</p>`}
    </div>`;
  mount.innerHTML = `
    <div class="card fade-in">
      <div class="card-header"><div><h2>${escapeHtml(teamName)} — roster moves</h2>
        <p class="card-subtitle">Since its last game on ${escapeHtml(roster.snapshot_game_date)}. Departures use their
        per-game numbers in the team's last 10 games; arrivals their last 10 games anywhere.</p></div></div>
      <div class="grid-2">${list(roster.arrived, "Arrived")}${list(roster.departed, "Departed")}</div>
    </div>`;
  mount.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function safe(promise) {
  try {
    return await promise;
  } catch (err) {
    return { ok: false, data: { error: { message: `Network error: ${err.message}` } } };
  }
}

function tile(label, value) {
  return `<div class="stat-tile"><div class="stat-label">${escapeHtml(label)}</div><div class="stat-value" style="font-size:1.1rem;">${escapeHtml(value ?? "—")}</div></div>`;
}

function pct(value, digits = 1) {
  return value == null ? "—" : `${(value * 100).toFixed(digits)}%`;
}

function fmt(value, digits = 1) {
  return value == null ? "—" : Number(value).toFixed(digits);
}

function errorBanner(message) {
  return `<div class="error-banner">⚠ ${escapeHtml(message)}</div>`;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}
