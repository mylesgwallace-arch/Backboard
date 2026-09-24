// pages/whatIf.js — the What-if Lab: put another player's season (translated
// into the host team's era) in place of one real player, and see the projected
// change in net rating, wins and title odds. Everything comes from the
// `simulate_era_swap` tool; the page leads with the fact that this is a
// what-if estimate with low confidence, and shows the method and assumptions
// next to the numbers.

import { getTools, getTeamSeasonRoster, resolvePlayer, simulateEraSwap } from "../api.js";
import { fetchTeams } from "../components/teamSelect.js";

export const meta = {
  title: "What-if Lab",
  subtitle: "Cross-era roster swaps: a what-if simulation, never a historical fact.",
};

const FIRST_SEASON = 1985;
const LAST_SEASON = 2025;
const seasonOptions = (selected) =>
  Array.from({ length: LAST_SEASON - FIRST_SEASON + 1 }, (_, i) => LAST_SEASON - i)
    .map((s) => `<option value="${s}" ${s === selected ? "selected" : ""}>${s}-${String(s + 1).slice(-2)}</option>`)
    .join("");

export function render(container) {
  container.innerHTML = `
    <div class="info-banner scouting-note fade-in">
      <span class="note-label">Read this first</span>
      <p class="model-insight">
        These are model-based what-if estimates under stated assumptions, with <strong>low confidence</strong>.
        They are not claims about what would really have happened. Only about a fifth of a box-score-valued
        roster change showed up in real team results when this was checked on actual trades and signings,
        so the headline numbers are scaled accordingly.
      </p>
    </div>

    <div class="card fade-in">
      <div class="card-header"><div><h2>Build a swap</h2>
        <p class="card-subtitle">Seasons from 1985-86 on (complete box scores). Pick the host team-season, the
        player who leaves, and the player (and season) who takes those minutes.</p></div></div>
      <div class="grid-2">
        <div class="field"><label for="wi-team">Host team</label>
          <div class="select-wrap"><select class="select" id="wi-team"><option>Loading…</option></select></div></div>
        <div class="field"><label for="wi-season">Host season</label>
          <div class="select-wrap"><select class="select" id="wi-season">${seasonOptions(1992)}</select></div></div>
        <div class="field"><label for="wi-out">Player who leaves</label>
          <div class="select-wrap"><select class="select" id="wi-out"><option>Pick a team and season</option></select></div></div>
        <div class="field"><label for="wi-in">Player who joins (name)</label>
          <input class="input" id="wi-in" placeholder="e.g. Stephen Curry" autocomplete="off"></div>
        <div class="field"><label for="wi-in-season">Joining player's season</label>
          <div class="select-wrap"><select class="select" id="wi-in-season">${seasonOptions(2015)}</select></div></div>
        <div class="field"><label for="wi-method">Era translation</label>
          <div class="select-wrap"><select class="select" id="wi-method">
            <option value="zscore">Same standing in the league (z-score)</option>
            <option value="ratio">Same multiple of the league average (ratio)</option>
          </select></div></div>
      </div>
      <div class="predict-actions">
        <button class="btn" id="wi-run">Run what-if</button>
        <span class="text-muted" id="wi-status"></span>
      </div>
      <div id="wi-error" class="mt-1"></div>
      <div id="wi-candidates" class="mt-1"></div>
    </div>
    <div id="wi-result"></div>
    <div class="card fade-in"><details class="collapsible"><summary>How this works</summary>
      <div id="wi-about" class="mt-1"><div class="skeleton" style="height:40px;"></div></div></details></div>
  `;

  const teamSelect = container.querySelector("#wi-team");
  const seasonSelect = container.querySelector("#wi-season");
  const outSelect = container.querySelector("#wi-out");
  const refreshRoster = () => loadRoster(teamSelect.value, seasonSelect.value, outSelect);

  fetchTeams().then((teams) => {
    teamSelect.innerHTML = teams
      .map((t) => `<option value="${t.team_id}" ${t.name === "Bulls" ? "selected" : ""}>${escapeHtml(t.full_name)}</option>`)
      .join("");
    refreshRoster();
  }).catch(() => { teamSelect.innerHTML = "<option>Teams unavailable</option>"; });
  teamSelect.addEventListener("change", refreshRoster);
  seasonSelect.addEventListener("change", refreshRoster);
  loadAbout(container.querySelector("#wi-about"));
  container.querySelector("#wi-run").addEventListener("click", () => run(container, null));
}

async function loadRoster(teamId, season, select) {
  select.innerHTML = "<option>Loading roster…</option>";
  const res = await safe(getTeamSeasonRoster({ teamId: Number(teamId), season: Number(season) }));
  if (!res.ok || res.data?.status !== "success") {
    select.innerHTML = `<option value="">${escapeHtml(res.data?.error?.message || "Roster unavailable")}</option>`;
    return;
  }
  select.innerHTML = res.data.data.players.map((p) =>
    `<option value="${p.person_id}">${escapeHtml(p.name)} — ${p.minutes_per_game} min, ${p.points_per_game} pts (${p.games} g)</option>`
  ).join("");
}

async function run(container, inPersonId) {
  const status = container.querySelector("#wi-status");
  const errorMount = container.querySelector("#wi-error");
  const candidatesMount = container.querySelector("#wi-candidates");
  const resultMount = container.querySelector("#wi-result");
  const button = container.querySelector("#wi-run");
  const inName = container.querySelector("#wi-in").value.trim();
  const inSeason = Number(container.querySelector("#wi-in-season").value);
  errorMount.innerHTML = "";
  candidatesMount.innerHTML = "";
  if (!inPersonId) {
    if (!inName) { errorMount.innerHTML = errorBanner("Name the player who joins."); return; }
    const lookup = await safe(resolvePlayer({ name: inName, season: inSeason, limit: 6 }));
    const found = lookup.data?.data;
    if (!lookup.ok || lookup.data?.status !== "success" || !found) {
      errorMount.innerHTML = errorBanner(lookup.data?.error?.message || "No player found for that season.");
      return;
    }
    if (!found.person_id) {
      candidatesMount.innerHTML = `<p class="text-muted">Several players match; pick one:</p>
        <div class="feature-chip-row">${found.candidates.map((c) =>
          `<button type="button" class="chip chip-btn" data-person="${c.person_id}">${escapeHtml(c.full_name)}
            <span class="text-muted">${c.from_year ?? "?"}–${c.to_year ?? "now"}</span></button>`).join("")}</div>`;
      candidatesMount.querySelectorAll("[data-person]").forEach((b) =>
        b.addEventListener("click", () => run(container, Number(b.dataset.person))));
      return;
    }
    inPersonId = found.person_id;
  }
  button.disabled = true;
  status.textContent = "Simulating… the first run in a session loads the season model (about a minute).";
  resultMount.innerHTML = `<div class="card fade-in"><div class="skeleton" style="height:260px;"></div></div>`;
  const res = await safe(simulateEraSwap({
    teamId: Number(container.querySelector("#wi-team").value),
    season: Number(container.querySelector("#wi-season").value),
    outPersonId: Number(container.querySelector("#wi-out").value),
    inPersonId,
    inSeason,
    method: container.querySelector("#wi-method").value,
  }));
  button.disabled = false;
  status.textContent = "";
  if (!res.ok || res.data?.status !== "success") {
    resultMount.innerHTML = "";
    errorMount.innerHTML = errorBanner(res.data?.error?.message || "The what-if could not run.");
    return;
  }
  resultMount.innerHTML = renderReport(res.data.data);
}

function renderReport(data) {
  const r = data.report;
  const change = r.projected_change;
  const effect = data.effect;
  const lines = [
    ["Points / 100", "pts_100"], ["True shooting", "ts"], ["Shot attempts / 100", "tsa_100"],
    ["3PA / 100", "tpa_100"], ["Assists / 100", "ast_100"], ["Turnovers / 100", "tov_100"],
    ["Def. rebounds / 100", "drb_100"], ["Steals / 100", "stl_100"],
  ];
  const cell = (value, key) => value == null ? "—" : key === "ts" ? `${(value * 100).toFixed(1)}%` : value;
  const title = r.postseason?.title;
  return `
    <div class="card fade-in">
      <div class="card-header"><div><h2>${escapeHtml(r.headline)}</h2>
        <p class="card-subtitle">What-if estimate · confidence <strong>${escapeHtml(r.confidence.level)}</strong></p></div></div>
      <div class="stat-grid">
        ${tile("Net rating change / 100", `${signed(change.net_rating_per_100)} (${signed(change.net_rating_80pct_range[0])} to ${signed(change.net_rating_80pct_range[1])})`)}
        ${tile("Wins change", `${signed(change.wins, 1)} (${signed(change.wins_80pct_range[0], 1)} to ${signed(change.wins_80pct_range[1], 1)})`)}
        ${tile("Unchanged team (simulated / actual)", `${r.baseline_team.simulated_mean_wins} / ${r.baseline_team.actual_wins} wins`)}
        ${title ? tile("Title odds (real → swap)", `${pct(title[0])} → ${pct(title[1])}`) : tile("Playoffs", "not simulated")}
      </div>
      <p class="text-muted mt-1">If box-score value transferred in full (an unvalidated upper scenario): net rating
        ${signed(change.full_transfer_upper_scenario.net_rating_per_100)}, ${signed(change.full_transfer_upper_scenario.wins, 1)} wins.
        ${r.postseason?.note ? escapeHtml(r.postseason.note) : ""}</p>
    </div>
    <div class="card fade-in">
      <div class="card-header"><div><h2>Stat lines (per 100 possessions)</h2>
        <p class="card-subtitle">${escapeHtml(effect.in_player.name)}'s real ${effect.in_player.season}-${String(effect.in_player.season + 1).slice(-2)} line, the same line translated into the host season, and ${escapeHtml(effect.out_player.name)}, whose ${pct(effect.out_player.team_minutes_share)} of team minutes it takes.</p></div></div>
      <table class="data-table">
        <thead><tr><th scope="col">Stat</th><th scope="col" class="num">${escapeHtml(effect.in_player.name)} (real)</th>
          <th scope="col" class="num">Translated</th><th scope="col" class="num">${escapeHtml(effect.out_player.name)}</th></tr></thead>
        <tbody>${lines.map(([label, key]) => `<tr><td>${label}</td>
          <td class="num">${cell(effect.in_player.original_stat_line[key], key)}</td>
          <td class="num">${cell(effect.in_player.translated_stat_line[key], key)}</td>
          <td class="num">${cell(effect.out_player.stat_line[key], key)}</td></tr>`).join("")}</tbody>
      </table>
      <div class="section-title mt-2">Biggest drivers (before the realization factor)</div>
      <div class="feature-chip-row">${r.main_factors.map((f) =>
        `<span class="chip">${escapeHtml(f.feature)} <strong>${signed(f.delta_net_rating_full_transfer)}</strong></span>`).join("")}</div>
    </div>
    <div class="card fade-in">
      <div class="grid-2">
        <div><div class="section-title">Method</div><ul class="plain-list">${r.method.map((m) => `<li>${escapeHtml(m)}</li>`).join("")}</ul></div>
        <div><div class="section-title">Assumptions</div><ul class="plain-list">${r.assumptions.map((a) => `<li>${escapeHtml(a)}</li>`).join("")}</ul></div>
      </div>
      <div class="section-title mt-2">Why confidence is ${escapeHtml(r.confidence.level.toLowerCase())}</div>
      <ul class="plain-list">${r.confidence.reasons.map((x) => `<li>${escapeHtml(x)}</li>`).join("")}</ul>
      <p class="text-muted mt-1">${escapeHtml(r.what_it_means)}</p>
    </div>`;
}

async function loadAbout(mount) {
  const res = await safe(getTools());
  const tool = (res.data?.tools || []).find((t) => t.name === "simulate_era_swap");
  mount.innerHTML = tool
    ? `<p>${escapeHtml(tool.description)}</p><div class="section-title mt-2">Limitations</div>
       <ul class="plain-list">${tool.limitations.map((l) => `<li>${escapeHtml(l)}</li>`).join("")}</ul>`
    : `<p class="text-muted">Tool details are unavailable.</p>`;
}

async function safe(promise) {
  try { return await promise; } catch (err) {
    return { ok: false, data: { error: { message: `Network error: ${err.message}` } } };
  }
}
function tile(label, value) {
  return `<div class="stat-tile"><div class="stat-label">${escapeHtml(label)}</div><div class="stat-value" style="font-size:1.05rem;">${escapeHtml(value)}</div></div>`;
}
function signed(value, digits = 2) {
  if (value == null) return "—";
  const text = Number(value).toFixed(digits);
  return value > 0 ? `+${text}` : text;
}
function pct(value) { return value == null ? "—" : `${(value * 100).toFixed(1)}%`; }
function errorBanner(message) { return `<div class="error-banner">⚠ ${escapeHtml(message)}</div>`; }
function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
