// pages/playerImpact.js — find a player by name (`resolve_player`) and show
// the association-only impact diagnostic (`player_impact`). The diagnostic is
// descriptive, not a causal forecast of a trade or signing; the page says so
// next to the numbers, using the tool's own limitations text.

import { getTools, resolvePlayer, getPlayerImpact } from "../api.js";

export const meta = {
  title: "Player Impact",
  subtitle: "Look up any player and read their association-only impact diagnostic.",
};

const state = { lastQuery: "", candidates: [] };

export function render(container) {
  container.innerHTML = `
    <div class="card fade-in">
      <div class="card-header">
        <div>
          <h2>Find a player</h2>
          <p class="card-subtitle">Full or partial names work ("steph curry", "Jokic"). If several players match you choose; nothing is picked for you.</p>
        </div>
      </div>
      <form id="pi-form" class="grid-2">
        <div class="field">
          <label for="pi-name">Player name</label>
          <input class="input" id="pi-name" autocomplete="off" placeholder="e.g. Stephen Curry">
        </div>
        <div class="field">
          <label for="pi-before">Only games before (optional)</label>
          <input type="date" class="input" id="pi-before">
        </div>
      </form>
      <div class="predict-actions">
        <button class="btn" id="pi-search" type="submit" form="pi-form">Search</button>
        <span id="pi-status" class="text-muted"></span>
      </div>
      <div id="pi-error" class="mt-1"></div>
      <div id="pi-candidates" class="mt-1"></div>
    </div>
    <div id="pi-result"></div>
    <div class="card fade-in">
      <details class="collapsible">
        <summary>What this diagnostic is (and is not)</summary>
        <div id="pi-about" class="mt-1"><div class="skeleton" style="height:40px;"></div></div>
      </details>
    </div>
  `;

  const form = container.querySelector("#pi-form");
  const nameInput = container.querySelector("#pi-name");
  const beforeInput = container.querySelector("#pi-before");
  const status = container.querySelector("#pi-status");
  const errorMount = container.querySelector("#pi-error");
  const candidatesMount = container.querySelector("#pi-candidates");
  const resultMount = container.querySelector("#pi-result");

  loadAbout(container.querySelector("#pi-about"));

  if (state.lastQuery) nameInput.value = state.lastQuery;

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = nameInput.value.trim();
    if (!name) return;
    state.lastQuery = name;
    errorMount.innerHTML = "";
    candidatesMount.innerHTML = "";
    resultMount.innerHTML = "";
    status.textContent = "Searching…";
    let res;
    try {
      res = await resolvePlayer({ name, limit: 8 });
    } catch (err) {
      res = { ok: false, data: { error: { message: `Network error: ${err.message}` } } };
    }
    status.textContent = "";
    const data = res.data?.data;
    if (!res.ok || res.data?.status !== "success" || !data) {
      errorMount.innerHTML = errorBanner(res.data?.error?.message || "No player found.");
      return;
    }
    state.candidates = data.candidates;
    if (data.person_id) {
      showImpact(data.candidates[0], beforeInput.value, resultMount, errorMount);
      return;
    }
    candidatesMount.innerHTML = `
      <p class="text-muted">${data.match_count} players match "${escapeHtml(name)}"${data.match_count > data.candidates.length ? ` (top ${data.candidates.length} shown)` : ""}. Pick one:</p>
      <div class="feature-chip-row">
        ${data.candidates.map((c, i) => `
          <button type="button" class="chip chip-btn" data-candidate="${i}">
            ${escapeHtml(c.full_name)} <span class="text-muted">${c.from_year ?? "?"}–${c.to_year ?? "now"} · ${c.regular_season_games} gp</span>
          </button>`).join("")}
      </div>`;
    candidatesMount.querySelectorAll("[data-candidate]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const candidate = state.candidates[Number(btn.dataset.candidate)];
        showImpact(candidate, beforeInput.value, resultMount, errorMount);
      });
    });
  });
}

async function showImpact(candidate, before, resultMount, errorMount) {
  errorMount.innerHTML = "";
  resultMount.innerHTML = `<div class="card fade-in"><div class="skeleton" style="height:160px;"></div></div>`;
  let res;
  try {
    res = await getPlayerImpact({ personId: candidate.person_id, before: before || null });
  } catch (err) {
    res = { ok: false, data: { error: { message: `Network error: ${err.message}` } } };
  }
  const envelope = res.data;
  if (!res.ok || envelope?.status !== "success") {
    resultMount.innerHTML = "";
    errorMount.innerHTML = errorBanner(envelope?.error?.message || "The diagnostic is unavailable.");
    return;
  }
  const { diagnostic, confidence } = envelope.data;
  resultMount.innerHTML = `
    <div class="card fade-in">
      <div class="card-header">
        <div>
          <h2>${escapeHtml(candidate.full_name)}</h2>
          <p class="card-subtitle">
            personId ${candidate.person_id} · ${candidate.from_year ?? "?"}–${candidate.to_year ?? "now"} ·
            ${candidate.positions?.length ? candidate.positions.join("/") : "position n/a"} ·
            ${candidate.regular_season_games} regular-season games in the database
          </p>
        </div>
        <span class="badge ${confidence === "moderate" ? "positive" : "accent"}">${escapeHtml(confidence)} confidence</span>
      </div>
      <div class="stat-grid">
        ${tile("Estimated team net-rating change (if added)", signed(diagnostic.estimated_net_rating_change, 2))}
        ${tile("Player net rating (minutes-weighted)", signed(diagnostic.player_net_rating, 1))}
        ${tile("Expected minutes", fixed(diagnostic.expected_minutes, 1))}
        ${tile("Prior games used", diagnostic.prior_games)}
      </div>
      <div class="info-banner scouting-note mt-2">
        <span class="note-label">Read this first</span>
        <p class="model-insight">
          Association only: this is the player's minutes-weighted on-court net rating over the
          last ${diagnostic.window} games${diagnostic.before ? ` before ${escapeHtml(diagnostic.before)}` : ""},
          scaled by expected minutes against a 0.0 reference. It is <strong>not</strong> a causal
          forecast of what would happen if a team acquired this player, and it is not a feature of
          the prediction model.
        </p>
      </div>
    </div>`;
}

async function loadAbout(mount) {
  try {
    const res = await getTools();
    const tool = (res.data?.tools || []).find((t) => t.name === "player_impact");
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

function tile(label, value) {
  return `<div class="stat-tile"><div class="stat-label">${escapeHtml(label)}</div><div class="stat-value">${escapeHtml(value ?? "—")}</div></div>`;
}

function signed(value, digits) {
  if (value == null) return "—";
  const text = Number(value).toFixed(digits);
  return value > 0 ? `+${text}` : text;
}

function fixed(value, digits) {
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
