// pages/playoffs.js — Playoff odds: the real seeded bracket (exact series math
// once a postseason exists) or a simulated rest-of-season + bracket from a
// cutoff date. Every number comes from the `playoff_odds` tool envelope; the
// evidence card is the stored replay validation (`validation_report`).

import { getTools, getPlayoffOdds, getValidationReport } from "../api.js";

export const meta = {
  title: "Playoffs",
  subtitle: "Play-in, bracket and title odds built on the frozen production model.",
};

const SEASONS = [2025, 2024, 2023, 2022, 2021, 2020, 2018, 2017, 2016, 2015, 2014];
const ROUND_COLUMNS = [
  { key: "made_playoffs", label: "Make R1" },
  { key: "won_first_round", label: "Win R1" },
  { key: "won_conf_semifinals", label: "Conf finals" },
  { key: "won_conf_finals", label: "Finals" },
  { key: "champion", label: "Title" },
];

export function render(container) {
  container.innerHTML = `
    <div class="card fade-in">
      <div class="card-header"><div><h2>What does this do?</h2></div></div>
      <div id="po-about"><div class="skeleton" style="height:50px;"></div></div>
    </div>

    <div class="card fade-in">
      <div class="card-header">
        <div>
          <h2>Run playoff odds</h2>
          <p class="card-subtitle">
            Pick a season. "Real bracket" uses the actual seeds once the postseason
            started; "Project from a date" simulates the rest of the regular season and
            then the play-in and bracket, using only what was known on that date.
          </p>
        </div>
      </div>
      <div class="grid-2">
        <div class="field">
          <label for="po-season">Season</label>
          <div class="select-wrap">
            <select class="select" id="po-season">
              ${SEASONS.map((s) => `<option value="${s}">${s}-${String(s + 1).slice(-2)}</option>`).join("")}
            </select>
          </div>
        </div>
        <div class="field">
          <label for="po-mode">Mode</label>
          <div class="select-wrap">
            <select class="select" id="po-mode">
              <option value="actual">Real bracket (after the regular season)</option>
              <option value="asof">Project from a date</option>
            </select>
          </div>
        </div>
        <div class="field" id="po-date-field" style="display:none">
          <label for="po-date">Cutoff date</label>
          <input type="date" class="input" id="po-date">
        </div>
      </div>
      <div class="predict-actions">
        <button class="btn" id="po-run">Run playoff odds</button>
        <span id="po-status" class="text-muted"></span>
      </div>
      <div id="po-error" class="mt-1"></div>
    </div>

    <div id="po-result"></div>

    <div class="card fade-in">
      <div class="card-header">
        <div>
          <h2>How well has this worked?</h2>
          <p class="card-subtitle">Stored replay of the 2014-15 to 2025-26 postseasons (bubble excluded), strength frozen when each regular season ended.</p>
        </div>
      </div>
      <div id="po-evidence"><div class="skeleton" style="height:70px;"></div></div>
    </div>
  `;

  const seasonSelect = container.querySelector("#po-season");
  const modeSelect = container.querySelector("#po-mode");
  const dateField = container.querySelector("#po-date-field");
  const dateInput = container.querySelector("#po-date");

  const syncDate = () => {
    const season = Number(seasonSelect.value);
    // `.field` sets its own display, which beats the hidden attribute.
    dateField.style.display = modeSelect.value === "asof" ? "" : "none";
    if (!dateInput.value || !dateInput.value.startsWith(String(season + 1))) {
      dateInput.value = `${season + 1}-02-01`;
    }
  };
  seasonSelect.addEventListener("change", syncDate);
  modeSelect.addEventListener("change", syncDate);
  syncDate();

  loadAbout(container.querySelector("#po-about"));
  loadEvidence(container.querySelector("#po-evidence"));
  wireRun(container, { seasonSelect, modeSelect, dateInput });
}

async function loadAbout(mount) {
  try {
    const res = await getTools();
    const tool = (res.data?.tools || []).find((t) => t.name === "playoff_odds");
    if (!tool) throw new Error("missing");
    mount.innerHTML = `
      <p>${escapeHtml(tool.description)}</p>
      <div class="grid-2 mt-2">
        <div><div class="section-title">Assumptions</div>
          <ul class="plain-list">${tool.assumptions.map((a) => `<li>${escapeHtml(a)}</li>`).join("")}</ul></div>
        <div><div class="section-title">Limitations</div>
          <ul class="plain-list">${tool.limitations.map((l) => `<li>${escapeHtml(l)}</li>`).join("")}</ul></div>
      </div>`;
  } catch (err) {
    mount.innerHTML = `<p class="text-muted">Tool details are currently unavailable.</p>`;
  }
}

async function loadEvidence(mount) {
  try {
    const res = await getValidationReport("playoffs");
    const report = res.data?.data?.report;
    if (!report) throw new Error("missing");
    const games = report.games || {};
    const series = report.series || {};
    const bracket = report.bracket_summary || {};
    mount.innerHTML = `
      <div class="stat-grid">
        ${tile("Playoff games scored", games.count)}
        ${tile("Game log loss (model · baseline)", `${fmt(games.model_log_loss, 3)} · ${fmt(games.baseline_log_loss, 3)}`)}
        ${tile("Series log loss (model · baseline)", `${fmt(series.model_log_loss, 3)} · ${fmt(series.baseline_log_loss, 3)}`)}
        ${tile("Series picked right (model · higher seed)", `${pct(series.model_accuracy)} · ${pct(series.baseline_accuracy)}`)}
        ${tile("Avg title odds given to the champion", pct(bracket.mean_title_probability_given_to_actual_champion))}
        ${tile("Champion was the model favorite", pct(bracket.champion_was_model_favorite_share, 0))}
      </div>
      <p class="text-muted mt-2">
        Lower log loss is better. Baselines: every home team at the 2003-2014 playoff
        home-win rate, and the higher seed at the 2003-2014 series win rate. A uniform
        guess would give each champion 6.3%.
      </p>`;
  } catch (err) {
    mount.innerHTML = `<p class="text-muted">The stored playoff validation is unavailable.</p>`;
  }
}

function wireRun(container, { seasonSelect, modeSelect, dateInput }) {
  const btn = container.querySelector("#po-run");
  const status = container.querySelector("#po-status");
  const errorMount = container.querySelector("#po-error");
  const resultMount = container.querySelector("#po-result");

  btn.addEventListener("click", async () => {
    const season = Number(seasonSelect.value);
    const asOf = modeSelect.value === "asof" ? dateInput.value : null;
    errorMount.innerHTML = "";
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Running…';
    status.textContent = "The first run in a session loads the model inputs (a few seconds).";
    resultMount.innerHTML = `<div class="card fade-in"><div class="skeleton" style="height:260px;"></div></div>`;
    let res;
    try {
      res = await getPlayoffOdds({ season, asOf });
    } catch (err) {
      res = { ok: false, data: { error: { message: `Network error: ${err.message}` } } };
    }
    btn.disabled = false;
    btn.textContent = "Run playoff odds";
    status.textContent = "";
    if (!res.ok || res.data?.status !== "success") {
      resultMount.innerHTML = "";
      errorMount.innerHTML = `<div class="error-banner">⚠ ${escapeHtml(res.data?.error?.message || "The request failed.")}</div>`;
      return;
    }
    resultMount.innerHTML = renderResult(res.data.data);
  });
}

function normalizeRows(result) {
  return result.teams.map((row) => {
    const get = (key) => (row[key] != null ? row[key] : row[`p_${key}`]);
    return {
      teamId: row.teamId,
      name: row.teamName || String(row.teamId),
      conference: row.conference,
      seed: row.seed ?? null,
      wins: row.regular_season_wins ?? row.mean_wins ?? null,
      playIn: row.p_play_in ?? null,
      actual: row.actual_result || null,
      made_playoffs: get("made_playoffs"),
      won_first_round: get("won_first_round"),
      won_conf_semifinals: get("won_conf_semifinals"),
      won_conf_finals: get("won_conf_finals"),
      champion: get("champion"),
    };
  });
}

function renderResult(result) {
  const rows = normalizeRows(result);
  const actualMode = Boolean(result.field);
  const top = [...rows].sort((a, b) => b.champion - a.champion).slice(0, 8);
  const maxTitle = Math.max(...top.map((r) => r.champion), 0.01);
  const subtitle = actualMode
    ? `Actual seeds; strength frozen ${escapeHtml(result.strength_frozen_at)}; exact series math.`
    : `Simulated from ${escapeHtml(result.as_of)} (${result.games_completed} games played, ${result.games_remaining} left); ${result.n_simulations.toLocaleString()} simulations; ties broken at random.`;
  return `
    <div class="card fade-in">
      <div class="card-header">
        <div>
          <h2>Title odds — ${result.season}-${String(result.season + 1).slice(-2)}</h2>
          <p class="card-subtitle">${subtitle}</p>
        </div>
      </div>
      ${top.map((row) => `
        <div class="compare-row seed-row">
          <div class="compare-row-head">
            <span class="metric-name">${escapeHtml(row.name)}${row.actual ? ` <span class="text-muted">· ${escapeHtml(row.actual)}</span>` : ""}</span>
            <span>${pct(row.champion)}</span>
          </div>
          <div class="compare-track"><div class="compare-fill home" style="width:${(row.champion / maxTitle) * 100}%; background: var(--accent);"></div></div>
        </div>`).join("")}
    </div>
    <div class="card fade-in">
      <div class="card-header">
        <div>
          <h2>Round by round</h2>
          <p class="card-subtitle">Probability of reaching each stage${actualMode ? "; seeds 7-10 went through the play-in" : ""}.</p>
        </div>
      </div>
      <div class="grid-2">
        ${["East", "West"].map((conf) => renderConferenceTable(conf, rows.filter((r) => r.conference === conf), actualMode, result.play_in)).join("")}
      </div>
    </div>`;
}

function renderConferenceTable(conference, rows, actualMode, playIn) {
  const sorted = actualMode
    ? [...rows].sort((a, b) => a.seed - b.seed)
    : [...rows].sort((a, b) => b.made_playoffs - a.made_playoffs || b.champion - a.champion);
  const shown = actualMode ? sorted : sorted.slice(0, 12);
  return `
    <div>
      <div class="section-title">${conference}ern Conference</div>
      <table class="data-table">
        <thead><tr>
          <th scope="col">${actualMode ? "Seed" : "#"}</th>
          <th scope="col">Team</th>
          <th scope="col" class="num">${actualMode ? "W" : "Mean W"}</th>
          ${!actualMode && playIn ? '<th scope="col" class="num">Play-in</th>' : ""}
          ${ROUND_COLUMNS.map((c) => `<th scope="col" class="num">${c.label}</th>`).join("")}
        </tr></thead>
        <tbody>
          ${shown.map((row, index) => `
            <tr>
              <td class="rank">${actualMode ? row.seed : index + 1}</td>
              <td class="team">${escapeHtml(row.name)}${row.actual ? `<div class="text-muted" style="font-size:0.78rem;">${escapeHtml(row.actual)}</div>` : ""}</td>
              <td class="num">${row.wins != null ? fmt(row.wins, actualMode ? 0 : 1) : "—"}</td>
              ${!actualMode && playIn ? `<td class="num">${pct(row.playIn, 0)}</td>` : ""}
              ${ROUND_COLUMNS.map((c) => `<td class="num">${pct(row[c.key], c.key === "champion" ? 1 : 0)}</td>`).join("")}
            </tr>`).join("")}
        </tbody>
      </table>
    </div>`;
}

function tile(label, value) {
  return `<div class="stat-tile"><div class="stat-label">${escapeHtml(label)}</div><div class="stat-value" style="font-size:1.1rem;">${escapeHtml(value ?? "—")}</div></div>`;
}

function pct(value, digits = 1) {
  if (value == null || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

function fmt(value, digits = 1) {
  if (value == null || Number.isNaN(value)) return "—";
  return Number(value).toFixed(digits);
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}
