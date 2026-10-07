// pages/sandbox.js — the Sandbox: build any hypothetical roster scenario
// (trades between any teams, signings of any player from any season since
// 1985-86, releases, injuries, minutes changes), see each changed team's
// estimated change in strength immediately, then simulate the whole league's
// season and playoffs with and without the moves.
//
// The page never computes a result: rosters come from sandbox_rosters, the
// instant estimate from sandbox_preview and the season from sandbox_simulate
// (src/sandbox.py). The only client-side logic is bookkeeping: which players
// are on which team after the moves so far, so the builder lists the right
// people. Scenarios persist in this browser and can be shared as a link.

import { getPlayerProfile, getSandboxRosters, previewScenario, resolvePlayer, simulateScenario } from "../api.js";
import { teamColor } from "../teamColors.js";
import { readableInk } from "../colorInk.js";
import { dumbbellRows, wireChartTooltips } from "../components/charts.js";
import { playerThumb, wireTornPhoto } from "../components/tornPhoto.js";
import { apiMoves as stripNames, decodeScenario, encodeScenario } from "../components/scenario.js";
import {
  debounce, emptyState, errorBanner, escapeHtml, fixed, pct, safe, seasonLabel, signed, signedPoints, unwrap,
} from "../components/format.js";

export const meta = {
  title: "Sandbox",
  subtitle: "Make any trade, sign anyone from any era, then play the season out.",
};

const STORAGE_KEY = "backboard.sandbox.v1";
const MOVE_TABS = [
  ["trade", "Trade"],
  ["sign", "Sign anyone"],
  ["release", "Release"],
  ["injury", "Injury"],
  ["minutes", "Minutes"],
];

// Ready-made scenarios for the next-season mode (ids from the 2025-26 rosters).
const PRESETS = [
  {
    label: "Jokic ↔ Giannis",
    moves: [{ type: "trade", assets: [
      { person_id: 203999, from_team_id: 1610612743, to_team_id: 1610612749 },
      { person_id: 203507, from_team_id: 1610612749, to_team_id: 1610612743 },
    ] }],
  },
  {
    label: "1990-91 Jordan joins the Spurs",
    moves: [{ type: "sign", person_id: 893, to_team_id: 1610612759, from_season: 1990 }],
  },
  {
    label: "Wembanyama to the Knicks",
    moves: [{ type: "trade", assets: [{ person_id: 1641705, from_team_id: 1610612759, to_team_id: 1610612752 }] }],
  },
  {
    label: "SGA misses half the season",
    moves: [{ type: "injury", person_id: 1628983, team_id: 1610612760, games_missed: 41 }],
  },
];

let sb = null; // page state: { mode, season, moves, rosters, seasons, ... }

export function render(container, ctx = {}) {
  sb = loadState(ctx);
  container.innerHTML = `
    <div class="card fade-in sandbox-intro">
      <div class="card-header"><div><h2>Pick a season</h2>
        <p class="card-subtitle">Every move is valued with the same box-score model as the player pages, then the season is
          simulated twice with the same luck: once as it is, once with your moves. The difference is your scenario.</p></div></div>
      <div class="sandbox-mode">
        <div class="segmented" role="group" aria-label="Season type">
          <button class="seg" data-mode="next" aria-pressed="false">Next season</button>
          <button class="seg" data-mode="replay" aria-pressed="false">Replay a past season</button>
        </div>
        <div class="field compact-field" id="sb-season-field" hidden><label for="sb-season">Season</label>
          <div class="select-wrap"><select class="select" id="sb-season"></select></div></div>
        <p class="text-muted" id="sb-mode-note"></p>
      </div>
      <div class="quick-picks" id="sb-presets"></div>
    </div>
    <div class="sandbox-grid">
      <div class="card fade-in" id="sb-builder"></div>
      <div class="card fade-in sandbox-ledger" id="sb-ledger"></div>
    </div>
    <div id="sb-results"></div>
  `;
  container.querySelectorAll("[data-mode]").forEach((b) => b.addEventListener("click", () => {
    if (b.dataset.mode === sb.mode) return;
    if (sb.moves.length && !window.confirm("Switching seasons clears the moves in your scenario. Continue?")) return;
    sb.mode = b.dataset.mode;
    sb.season = null;
    sb.moves = [];
    sb.results = null;
    boot(container, ctx);
  }));
  container.querySelector("#sb-season").addEventListener("change", (event) => {
    if (sb.moves.length && !window.confirm("Switching seasons clears the moves in your scenario. Continue?")) {
      event.target.value = sb.season;
      return;
    }
    sb.season = Number(event.target.value);
    sb.moves = [];
    sb.results = null;
    boot(container, ctx);
  });
  boot(container, ctx);
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

function loadState(ctx) {
  const base = { mode: "next", season: null, moves: [], tab: "trade", trade: null, rosters: null, results: null,
    storyIndex: 0, storySide: "scenario", nSimulations: 1000, transfer: "calibrated", preview: null };
  const shared = ctx.query?.get?.("s");
  if (shared) {
    try {
      return { ...base, ...decodeScenario(shared) };
    } catch (err) {
      /* fall through to the saved scenario */
    }
  }
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
    if (saved) Object.assign(base, { mode: saved.mode || "next", season: saved.season ?? null, moves: saved.moves || [] });
  } catch (err) {
    /* storage unavailable: start empty */
  }
  base.focusPlayer = Number(ctx.query?.get?.("player")) || null;
  return base;
}

function saveState() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ mode: sb.mode, season: sb.season, moves: sb.moves }));
  } catch (err) {
    /* private mode: the scenario just isn't remembered */
  }
}

function shareLink() {
  return `${location.origin}${location.pathname}#/sandbox?s=${encodeScenario(sb)}`;
}

const rosterCache = {};

async function boot(container, ctx) {
  const builder = container.querySelector("#sb-builder");
  const ledger = container.querySelector("#sb-ledger");
  builder.innerHTML = `<div class="skeleton" style="height:320px;"></div>`;
  ledger.innerHTML = `<div class="skeleton" style="height:200px;"></div>`;
  container.querySelector("#sb-results").innerHTML = "";
  const key = `${sb.mode}:${sb.season ?? "default"}`;
  if (!rosterCache[key]) {
    rosterCache[key] = unwrap(await safe(getSandboxRosters({ mode: sb.mode, season: sb.season ?? undefined })));
  }
  const res = rosterCache[key];
  if (!res.data) {
    builder.innerHTML = errorBanner(res.message);
    ledger.innerHTML = "";
    return;
  }
  sb.season = res.data.season;
  sb.seasons = res.data.seasons;
  sb.hostLabel = res.data.host_season_label;
  sb.rosters = res.data.teams;
  rosterCache[`${sb.mode}:${sb.season}`] = res;
  renderModeControls(container);
  if (sb.focusPlayer) focusOnPlayer(sb.focusPlayer);
  sb.focusPlayer = null;
  renderBuilder(builder, container, ctx);
  renderLedger(ledger, container, ctx);
  saveState();
}

function renderModeControls(container) {
  container.querySelectorAll("[data-mode]").forEach((b) => {
    const on = b.dataset.mode === sb.mode;
    b.classList.toggle("is-on", on);
    b.setAttribute("aria-pressed", String(on));
    if (b.dataset.mode === "next") b.textContent = `Next season · ${seasonLabel(sb.seasons.next)}`;
  });
  const field = container.querySelector("#sb-season-field");
  field.hidden = sb.mode !== "replay";
  const select = container.querySelector("#sb-season");
  select.innerHTML = [...sb.seasons.replay].reverse()
    .map((s) => `<option value="${s}" ${s === sb.season ? "selected" : ""}>${seasonLabel(s)}</option>`).join("");
  container.querySelector("#sb-mode-note").textContent = sb.mode === "next"
    ? `Rosters as the ${sb.hostLabel} season ended (each player on their last team), team strength frozen there, ${sb.hostLabel} matchups reused as the schedule.`
    : `The real ${seasonLabel(sb.season)} rosters, schedule and game-by-game odds; your moves happen before opening night.`;
  const presets = container.querySelector("#sb-presets");
  presets.innerHTML = sb.mode === "next"
    ? `<span class="text-muted">Try</span>${PRESETS.map((p, i) => `<button type="button" class="chip chip-btn" data-preset="${i}">${escapeHtml(p.label)}</button>`).join("")}`
    : "";
  presets.querySelectorAll("[data-preset]").forEach((b) => b.addEventListener("click", () => {
    sb.moves = PRESETS[Number(b.dataset.preset)].moves.map((m) => JSON.parse(JSON.stringify(m)));
    sb.results = null;
    renderLedger(container.querySelector("#sb-ledger"), container);
    renderBuilder(container.querySelector("#sb-builder"), container);
    container.querySelector("#sb-results").innerHTML = "";
  }));
}

// ---------------------------------------------------------------------------
// Bookkeeping: who is where after the moves so far (names and teams only)
// ---------------------------------------------------------------------------

function teamById(teamId) {
  return sb.rosters.find((t) => t.team_id === Number(teamId));
}

function teamAbbr(teamId) {
  return teamById(teamId)?.abbreviation || String(teamId);
}

function currentRosters() {
  const rosters = new Map(sb.rosters.map((t) => [t.team_id, t.players.map((p) => ({ ...p }))]));
  const take = (personId) => {
    let found = null;
    for (const players of rosters.values()) {
      const index = players.findIndex((p) => p.person_id === personId);
      if (index >= 0) found = players.splice(index, 1)[0];
    }
    return found;
  };
  for (const move of sb.moves) {
    if (move.type === "trade") {
      const moving = move.assets.map((a) => ({ asset: a, player: take(a.person_id) }));
      moving.forEach(({ asset, player }) => {
        if (player) rosters.get(asset.to_team_id)?.push({ ...player, note: `from ${teamAbbr(asset.from_team_id)}` });
      });
    } else if (move.type === "sign") {
      const player = take(move.person_id);
      rosters.get(move.to_team_id)?.push({
        person_id: move.person_id, name: move.name || player?.name || `#${move.person_id}`,
        mpg: player?.mpg ?? null, box_impact: player?.box_impact ?? null,
        note: `signed · ${seasonLabel(move.from_season)}`,
      });
    } else if (move.type === "release") {
      const players = rosters.get(move.team_id) || [];
      const index = players.findIndex((p) => p.person_id === move.person_id);
      if (index >= 0) players.splice(index, 1);
    }
  }
  return rosters;
}

function playerName(personId) {
  for (const team of sb.rosters) {
    const player = team.players.find((p) => p.person_id === personId);
    if (player) return player.name;
  }
  const signed = sb.moves.find((m) => m.type === "sign" && m.person_id === personId);
  return signed?.name || `#${personId}`;
}

function focusOnPlayer(personId) {
  const rosters = currentRosters();
  for (const [teamId, players] of rosters) {
    if (players.some((p) => p.person_id === personId)) {
      sb.tab = "trade";
      const other = sb.rosters.find((t) => t.team_id !== teamId)?.team_id;
      sb.trade = { teams: [teamId, other], picks: { [personId]: other } };
      return;
    }
  }
  sb.tab = "sign";
  sb.signPrefill = personId;
}

// ---------------------------------------------------------------------------
// Builder
// ---------------------------------------------------------------------------

function teamOptions(selected, exclude = []) {
  return [...sb.rosters].sort((a, b) => a.team_name.localeCompare(b.team_name))
    .filter((t) => !exclude.includes(t.team_id) || t.team_id === selected)
    .map((t) => `<option value="${t.team_id}" ${t.team_id === selected ? "selected" : ""}>${escapeHtml(t.team_name)}</option>`).join("");
}

function renderBuilder(builder, container, ctx) {
  builder.innerHTML = `
    <div class="card-header"><div><h2>Build a move</h2>
      <p class="card-subtitle">Moves apply in order, before the season starts.</p></div></div>
    <div class="toolbar tab-strip" role="tablist" aria-label="Move type">
      ${MOVE_TABS.map(([key, label]) => `<button class="btn ${sb.tab === key ? "" : "btn-secondary"}" role="tab"
        aria-selected="${sb.tab === key}" data-move-tab="${key}">${label}</button>`).join("")}
    </div>
    <div id="sb-move-panel"></div>`;
  builder.querySelectorAll("[data-move-tab]").forEach((b) => b.addEventListener("click", () => {
    sb.tab = b.dataset.moveTab;
    renderBuilder(builder, container, ctx);
  }));
  const panel = builder.querySelector("#sb-move-panel");
  const add = (move) => {
    sb.moves.push(move);
    sb.results = null;
    container.querySelector("#sb-results").innerHTML = "";
    renderLedger(container.querySelector("#sb-ledger"), container, ctx);
    renderBuilder(builder, container, ctx);
  };
  ({ trade: renderTradeBuilder, sign: renderSignBuilder, release: renderSimpleBuilder,
    injury: renderSimpleBuilder, minutes: renderSimpleBuilder })[sb.tab](panel, add);
}

function rosterChecklist(teamId, players, picks, teams) {
  const colors = teamColor(teamId);
  return `
    <div class="trade-side">
      <div class="trade-side-head" style="--team-color:${colors.primary}; --team-ink:${readableInk(colors.primary)};">
        <span class="team-chip">${escapeHtml(teamAbbr(teamId))}</span> sends
      </div>
      <ul class="trade-roster">
        ${players.map((p) => {
          const checked = picks[p.person_id] != null;
          return `<li class="${checked ? "is-picked" : ""}">
            <label><input type="checkbox" data-pick="${p.person_id}" data-from="${teamId}" ${checked ? "checked" : ""}>
              <span class="trade-name">${escapeHtml(p.name)}${p.note ? ` <span class="text-muted">· ${escapeHtml(p.note)}</span>` : ""}</span>
              <span class="trade-meta">${p.mpg != null ? `${fixed(p.mpg)} mpg` : ""}${p.box_impact != null ? ` · ${signed(p.box_impact, 1)}` : ""}</span>
            </label>
            ${checked && teams.length > 2 ? `<label class="sr-only" for="dest-${p.person_id}">Destination for ${escapeHtml(p.name)}</label>
              <select class="select mini-select" id="dest-${p.person_id}" data-dest="${p.person_id}">
                ${teams.filter((t) => t !== teamId).map((t) => `<option value="${t}" ${picks[p.person_id] === t ? "selected" : ""}>to ${escapeHtml(teamAbbr(t))}</option>`).join("")}
              </select>` : ""}
          </li>`;
        }).join("") || `<li class="text-muted">No players left on this roster.</li>`}
      </ul>
    </div>`;
}

function renderTradeBuilder(panel, add) {
  const rosters = currentRosters();
  const ids = sb.rosters.map((t) => t.team_id);
  if (!sb.trade || !sb.trade.teams.every((t) => ids.includes(t))) {
    sb.trade = { teams: [1610612744, 1610612747].filter((t) => ids.includes(t)), picks: {} };
    if (sb.trade.teams.length < 2) sb.trade.teams = ids.slice(0, 2);
  }
  const trade = sb.trade;
  const picked = Object.keys(trade.picks).length;
  panel.innerHTML = `
    <div class="trade-teams">
      ${trade.teams.map((teamId, i) => `<div class="field compact-field"><label for="trade-team-${i}">Team ${String.fromCharCode(65 + i)}</label>
        <div class="select-wrap"><select class="select" id="trade-team-${i}" data-team-slot="${i}">${teamOptions(teamId, trade.teams)}</select></div></div>`).join("")}
      ${trade.teams.length < 3 ? `<button class="link-btn" id="trade-third">+ Add a third team</button>`
        : `<button class="link-btn" id="trade-drop-third">Remove the third team</button>`}
    </div>
    <p class="text-muted trade-hint">Tick who leaves each team.${trade.teams.length > 2 ? " Choose where each player goes." : " Players go to the other team."}
      Minutes per game and box impact (per 100 possessions, model estimate) are shown for reference.</p>
    <div class="trade-board trade-board--${trade.teams.length}">
      ${trade.teams.map((teamId) => rosterChecklist(teamId, rosters.get(teamId) || [], trade.picks, trade.teams)).join("")}
    </div>
    <div class="predict-actions">
      <button class="btn" id="trade-add" ${picked ? "" : "disabled"}>Add trade to scenario</button>
      <span class="text-muted">${picked ? `${picked} player${picked === 1 ? "" : "s"} moving` : "Nobody selected yet"}</span>
    </div>`;
  const rerender = () => renderTradeBuilder(panel, add);
  panel.querySelectorAll("[data-team-slot]").forEach((select) => select.addEventListener("change", () => {
    const slot = Number(select.dataset.teamSlot);
    const old = trade.teams[slot];
    trade.teams[slot] = Number(select.value);
    for (const [person, dest] of Object.entries(trade.picks)) {
      const from = [...rosters.entries()].find(([, players]) => players.some((p) => p.person_id === Number(person)))?.[0];
      if (from === old || dest === old) delete trade.picks[person];
    }
    rerender();
  }));
  panel.querySelector("#trade-third")?.addEventListener("click", () => {
    const next = sb.rosters.find((t) => !trade.teams.includes(t.team_id));
    trade.teams.push(next.team_id);
    rerender();
  });
  panel.querySelector("#trade-drop-third")?.addEventListener("click", () => {
    const dropped = trade.teams.pop();
    for (const [person, dest] of Object.entries(trade.picks)) {
      const from = [...rosters.entries()].find(([, players]) => players.some((p) => p.person_id === Number(person)))?.[0];
      if (from === dropped || dest === dropped) delete trade.picks[person];
    }
    for (const person of Object.keys(trade.picks)) {
      const from = [...rosters.entries()].find(([, players]) => players.some((p) => p.person_id === Number(person)))?.[0];
      trade.picks[person] = trade.teams.find((t) => t !== from);
    }
    rerender();
  });
  panel.querySelectorAll("[data-pick]").forEach((box) => box.addEventListener("change", () => {
    const person = Number(box.dataset.pick);
    const from = Number(box.dataset.from);
    if (box.checked) trade.picks[person] = trade.teams.find((t) => t !== from);
    else delete trade.picks[person];
    rerender();
  }));
  panel.querySelectorAll("[data-dest]").forEach((select) => select.addEventListener("change", () => {
    trade.picks[Number(select.dataset.dest)] = Number(select.value);
  }));
  panel.querySelector("#trade-add").addEventListener("click", () => {
    const assets = Object.entries(trade.picks).map(([person, to]) => {
      const from = [...rosters.entries()].find(([, players]) => players.some((p) => p.person_id === Number(person)))?.[0];
      return { person_id: Number(person), from_team_id: from, to_team_id: to, name: playerName(Number(person)) };
    });
    trade.picks = {};
    add({ type: "trade", assets });
  });
}

function renderSignBuilder(panel, add) {
  const latest = sb.seasons.latest_completed;
  panel.innerHTML = `
    <p class="text-muted trade-hint">Anyone with NBA minutes since 1985-86, from any season. A season from another era is
      translated into ${escapeHtml(sb.mode === "next" ? sb.hostLabel : seasonLabel(sb.season))} terms first (same standing
      relative to their league). A player already on a roster leaves that team.</p>
    <form id="sign-form" class="player-search" autocomplete="off" role="search">
      <label class="sr-only" for="sign-name">Player to sign</label>
      <input class="input" id="sign-name" placeholder="Search any player, e.g. Hakeem Olajuwon">
      <button class="btn btn-secondary" type="submit">Search</button>
    </form>
    <div id="sign-results" class="player-results"></div>
    <div id="sign-choice"></div>`;
  const input = panel.querySelector("#sign-name");
  const results = panel.querySelector("#sign-results");
  const choice = panel.querySelector("#sign-choice");
  const pick = (candidate) => {
    results.innerHTML = "";
    const first = Math.max(candidate.from_year ?? 1985, 1985);
    const last = Math.min(candidate.to_year ?? latest, latest);
    if (first > last) {
      choice.innerHTML = errorBanner(`${candidate.full_name} has no seasons from 1985-86 on, which the box-score model needs.`);
      return;
    }
    const seasons = [];
    for (let s = last; s >= first; s -= 1) seasons.push(s);
    choice.innerHTML = `
      <div class="sign-card">
        ${playerThumb({ personId: candidate.person_id, name: candidate.full_name })}
        <div class="sign-card-body">
          <strong>${escapeHtml(candidate.full_name)}</strong>
          <div class="grid-2 mt-1">
            <div class="field compact-field"><label for="sign-season">Which season of theirs</label>
              <div class="select-wrap"><select class="select" id="sign-season">
                ${seasons.map((s) => `<option value="${s}">${seasonLabel(s)}</option>`).join("")}</select></div></div>
            <div class="field compact-field"><label for="sign-team">Signs with</label>
              <div class="select-wrap"><select class="select" id="sign-team">${teamOptions(sb.rosters[0].team_id)}</select></div></div>
          </div>
          <div class="predict-actions"><button class="btn" id="sign-add">Add signing to scenario</button></div>
        </div>
      </div>`;
    wireTornPhoto(choice);
    choice.querySelector("#sign-add").addEventListener("click", () => add({
      type: "sign", person_id: candidate.person_id, name: candidate.full_name,
      to_team_id: Number(choice.querySelector("#sign-team").value),
      from_season: Number(choice.querySelector("#sign-season").value),
    }));
  };
  const search = async (name) => {
    if (name.trim().length < 2) {
      results.innerHTML = "";
      return;
    }
    const res = unwrap(await safe(resolvePlayer({ name: name.trim(), limit: 6 })));
    if (input.value.trim() !== name.trim()) return;
    if (!res.data) {
      results.innerHTML = `<p class="text-muted">${escapeHtml(res.message)}</p>`;
      return;
    }
    results.innerHTML = `<ul class="player-result-list">${res.data.candidates.map((c, i) => `<li><button type="button" class="player-result" data-cand="${i}">
      ${playerThumb({ personId: c.person_id, name: c.full_name })}
      <span class="player-result-name">${escapeHtml(c.full_name)}</span>
      <span class="player-result-meta">${c.from_year ?? "?"}–${c.to_year ?? "now"}</span></button></li>`).join("")}</ul>`;
    wireTornPhoto(results);
    results.querySelectorAll("[data-cand]").forEach((b) => b.addEventListener("click", () => pick(res.data.candidates[Number(b.dataset.cand)])));
  };
  const typed = debounce((value) => search(value), 260);
  input.addEventListener("input", () => typed(input.value));
  panel.querySelector("#sign-form").addEventListener("submit", (event) => {
    event.preventDefault();
    search(input.value);
  });
  if (sb.signPrefill) {
    // Arrived from a player page for someone on no current roster: offer to sign them.
    const personId = sb.signPrefill;
    sb.signPrefill = null;
    safe(getPlayerProfile(personId)).then((res) => {
      const profile = unwrap(res).data;
      if (!profile) return;
      const seasons = profile.seasons_played || [];
      pick({ person_id: personId, full_name: profile.bio.name, from_year: seasons[0], to_year: seasons[seasons.length - 1] });
    });
  }
}

function renderSimpleBuilder(panel, add) {
  const kind = sb.tab;
  const rosters = currentRosters();
  sb.simpleTeam ??= sb.rosters[0].team_id;
  const players = rosters.get(sb.simpleTeam) || [];
  if (!players.some((p) => p.person_id === sb.simplePlayer)) sb.simplePlayer = players[0]?.person_id;
  const selectedPlayer = players.find((p) => p.person_id === sb.simplePlayer);
  const copy = {
    release: "Take a player off the roster for the whole season (waived, retired, holding out). Teammates absorb the minutes.",
    injury: "The player misses games; teammates absorb those minutes.",
    minutes: "Set a player's minutes per game, and how many games they play (raise it for a fully healthy season). Teammates' minutes adjust to fill the game.",
  }[kind];
  panel.innerHTML = `
    <p class="text-muted trade-hint">${copy}</p>
    <div class="grid-2">
      <div class="field compact-field"><label for="simple-team">Team</label>
        <div class="select-wrap"><select class="select" id="simple-team">${teamOptions(sb.simpleTeam)}</select></div></div>
      <div class="field compact-field"><label for="simple-player">Player</label>
        <div class="select-wrap"><select class="select" id="simple-player">
          ${players.map((p) => `<option value="${p.person_id}" ${p.person_id === sb.simplePlayer ? "selected" : ""}>${escapeHtml(p.name)}${p.mpg != null ? ` · ${fixed(p.mpg)} mpg` : ""}${p.games != null ? `, ${p.games} g` : ""}</option>`).join("")}
        </select></div></div>
    </div>
    ${kind === "injury" ? `<div class="field mt-1"><label for="simple-games">Games missed: <output id="simple-games-out">20</output></label>
      <input type="range" id="simple-games" min="1" max="82" value="20" class="range"></div>` : ""}
    ${kind === "minutes" ? `<div class="grid-2 mt-1">
      <div class="field"><label for="simple-mpg">Minutes per game: <output id="simple-mpg-out">${Math.round(selectedPlayer?.mpg ?? 30)}</output></label>
        <input type="range" id="simple-mpg" min="0" max="44" step="1" value="${Math.round(selectedPlayer?.mpg ?? 30)}" class="range"></div>
      <div class="field"><label for="simple-played">Games played: <output id="simple-played-out">${selectedPlayer?.games ?? 70}</output></label>
        <input type="range" id="simple-played" min="1" max="82" step="1" value="${selectedPlayer?.games ?? 70}" class="range"></div>
    </div>` : ""}
    <div class="predict-actions"><button class="btn" id="simple-add" ${players.length ? "" : "disabled"}>Add to scenario</button></div>`;
  panel.querySelector("#simple-team").addEventListener("change", (event) => {
    sb.simpleTeam = Number(event.target.value);
    renderSimpleBuilder(panel, add);
  });
  const mirror = (id) => panel.querySelector(`#${id}`)?.addEventListener("input", (event) => {
    panel.querySelector(`#${id}-out`).textContent = event.target.value;
  });
  mirror("simple-games");
  mirror("simple-mpg");
  mirror("simple-played");
  panel.querySelector("#simple-player")?.addEventListener("change", (event) => {
    sb.simplePlayer = Number(event.target.value);
    if (kind === "minutes") renderSimpleBuilder(panel, add);
  });
  panel.querySelector("#simple-add").addEventListener("click", () => {
    const personId = Number(panel.querySelector("#simple-player").value);
    const move = { type: kind, person_id: personId, team_id: sb.simpleTeam, name: playerName(personId) };
    if (kind === "injury") move.games_missed = Number(panel.querySelector("#simple-games").value);
    if (kind === "minutes") {
      move.mpg = Number(panel.querySelector("#simple-mpg").value);
      move.games = Number(panel.querySelector("#simple-played").value);
    }
    add(move);
  });
}

// ---------------------------------------------------------------------------
// Ledger + instant preview
// ---------------------------------------------------------------------------

function describeMove(move) {
  const name = (id, fallback) => fallback || playerName(id);
  if (move.type === "trade") {
    const byDest = {};
    move.assets.forEach((a) => (byDest[a.to_team_id] ||= []).push(name(a.person_id, a.name)));
    return `<strong>Trade</strong> · ${Object.entries(byDest).map(([to, names]) =>
      `${escapeHtml(names.join(", "))} → ${escapeHtml(teamAbbr(to))}`).join("; ")}`;
  }
  if (move.type === "sign") {
    return `<strong>Sign</strong> · ${escapeHtml(name(move.person_id, move.name))} (${seasonLabel(move.from_season)}) → ${escapeHtml(teamAbbr(move.to_team_id))}`;
  }
  if (move.type === "release") return `<strong>Release</strong> · ${escapeHtml(name(move.person_id, move.name))} (${escapeHtml(teamAbbr(move.team_id))})`;
  if (move.type === "injury") return `<strong>Injury</strong> · ${escapeHtml(name(move.person_id, move.name))} misses ${move.games_missed} games`;
  return `<strong>Minutes</strong> · ${escapeHtml(name(move.person_id, move.name))} plays ${move.mpg} a game${move.games ? ` in ${move.games} games` : ""}`;
}

function apiMoves() {
  // Names are for display only; the backend takes ids.
  return stripNames(sb.moves);
}

function renderLedger(ledger, container, ctx) {
  saveState();
  ledger.innerHTML = `
    <div class="card-header"><div><h2>Your scenario</h2>
      <p class="card-subtitle">${escapeHtml(sb.mode === "next" ? `${seasonLabel(sb.season)} projection` : `${seasonLabel(sb.season)} replay`)}</p></div></div>
    ${sb.moves.length ? `<ol class="move-list">${sb.moves.map((move, i) => `<li><span>${describeMove(move)}</span>
        <button class="icon-btn" data-remove="${i}" aria-label="Remove move ${i + 1}">✕</button></li>`).join("")}</ol>
      <div class="toolbar"><button class="link-btn" id="sb-clear">Clear all</button><button class="link-btn" id="sb-share">Copy share link</button>
        <span class="text-muted" id="sb-share-note" role="status"></span></div>`
      : emptyState("No moves yet. Build one on the left, or try a ready-made scenario above.")}
    <div id="sb-preview" aria-live="polite"></div>
    <div class="sim-controls">
      <div class="field compact-field"><label for="sb-n">Simulations</label>
        <div class="select-wrap"><select class="select" id="sb-n">
          ${[500, 1000, 2000].map((n) => `<option value="${n}" ${n === sb.nSimulations ? "selected" : ""}>${n.toLocaleString()} seasons</option>`).join("")}
        </select></div></div>
      <div class="field compact-field"><label for="sb-transfer">How much carries over</label>
        <div class="select-wrap"><select class="select" id="sb-transfer">
          <option value="calibrated" ${sb.transfer === "calibrated" ? "selected" : ""}>Calibrated (validated)</option>
          <option value="full" ${sb.transfer === "full" ? "selected" : ""}>Full box score (optimistic)</option>
        </select></div></div>
    </div>
    <button class="btn btn-block" id="sb-run" ${sb.moves.length ? "" : "disabled"}>Simulate the season ▸</button>
    <p class="text-muted sim-note" id="sb-run-note">${sb.mode === "replay"
      ? "The first replay in a session loads every game's odds (about a minute); later runs take seconds." : "Takes a few seconds."}</p>`;
  ledger.querySelectorAll("[data-remove]").forEach((b) => b.addEventListener("click", () => {
    sb.moves.splice(Number(b.dataset.remove), 1);
    sb.results = null;
    container.querySelector("#sb-results").innerHTML = "";
    renderLedger(ledger, container, ctx);
    renderBuilder(container.querySelector("#sb-builder"), container, ctx);
  }));
  ledger.querySelector("#sb-clear")?.addEventListener("click", () => {
    sb.moves = [];
    sb.results = null;
    container.querySelector("#sb-results").innerHTML = "";
    renderLedger(ledger, container, ctx);
    renderBuilder(container.querySelector("#sb-builder"), container, ctx);
  });
  ledger.querySelector("#sb-share")?.addEventListener("click", async () => {
    const note = ledger.querySelector("#sb-share-note");
    try {
      await navigator.clipboard.writeText(shareLink());
      note.textContent = "Link copied.";
    } catch (err) {
      note.textContent = "Copy failed; the link is in the address bar.";
      history.replaceState(null, "", shareLink());
    }
  });
  ledger.querySelector("#sb-n").addEventListener("change", (e) => { sb.nSimulations = Number(e.target.value); });
  ledger.querySelector("#sb-transfer").addEventListener("change", (e) => { sb.transfer = e.target.value; });
  ledger.querySelector("#sb-run").addEventListener("click", () => runSimulation(container));
  if (sb.moves.length) refreshPreview(ledger.querySelector("#sb-preview"));
}

async function refreshPreview(mount) {
  mount.innerHTML = `<div class="skeleton" style="height:80px;"></div>`;
  const stamp = JSON.stringify(sb.moves);
  const res = unwrap(await safe(previewScenario({ mode: sb.mode, season: sb.season, moves: apiMoves() })));
  if (stamp !== JSON.stringify(sb.moves)) return;
  if (!res.data) {
    mount.innerHTML = errorBanner(res.message);
    const run = document.querySelector("#sb-run");
    if (run) run.disabled = true;
    return;
  }
  sb.preview = res.data;
  mount.innerHTML = `
    <div class="section-title mt-1">Instant estimate</div>
    <ul class="impact-list">${res.data.teams.map((t) => `<li>
      <details>
        <summary>
          <span class="team-chip" style="--team-color:${teamColor(t.team_id).primary}; --team-ink:${readableInk(teamColor(t.team_id).primary)};">${escapeHtml(teamAbbr(t.team_id))}</span>
          <span class="impact-figure ${t.delta_net_rating >= 0 ? "is-up" : "is-down"}">${signed(t.delta_net_rating, 1)}</span>
          <span class="impact-label">net rating / 100 · about ${signed(t.delta_wins_estimate, 1)} wins</span>
        </summary>
        <p class="text-muted impact-range">80% range ${signed(t.delta_net_rating_80pct[0], 1)} to ${signed(t.delta_net_rating_80pct[1], 1)}.
          Full box-score value ${signed(t.delta_net_rating_full, 1)} (unvalidated upper scenario).</p>
        <table class="data-table compact-table roster-delta">
          <thead><tr><th scope="col">Player</th><th scope="col" class="num">MPG</th><th scope="col" class="num">Impact</th></tr></thead>
          <tbody>${t.roster.filter((r) => r.mpg_after || r.mpg_before).slice(0, 14).map((r) => `<tr class="status-${r.status}">
            <td>${escapeHtml(r.name)}${r.status !== "returning" ? ` <span class="badge ${r.status === "departed" ? "" : "accent"}">${escapeHtml(r.status)}</span>` : ""}
              ${r.notes?.length ? `<div class="text-muted roster-note">${escapeHtml(r.notes.join("; "))}</div>` : ""}</td>
            <td class="num">${r.mpg_before ?? "—"} → ${r.mpg_after ?? "—"}</td>
            <td class="num">${r.status === "replacement" ? "—" : signed(r.box_impact, 1)}</td></tr>`).join("")}</tbody>
        </table>
      </details></li>`).join("")}</ul>
    <p class="text-muted impact-note">Calibrated: about ${Math.round(res.data.realization_factor * 100)}% of a box-score-valued change showed up
      in real team results when this was measured on actual roster moves. Open a team for its re-balanced minutes.</p>`;
  const run = document.querySelector("#sb-run");
  if (run) run.disabled = false;
}

// ---------------------------------------------------------------------------
// Simulation results
// ---------------------------------------------------------------------------

async function runSimulation(container) {
  const results = container.querySelector("#sb-results");
  const button = container.querySelector("#sb-run");
  button.disabled = true;
  button.textContent = "Simulating…";
  results.innerHTML = `<div class="card fade-in"><div class="skeleton" style="height:300px;"></div></div>`;
  const res = unwrap(await safe(simulateScenario({
    mode: sb.mode, season: sb.season, moves: apiMoves(), nSimulations: sb.nSimulations, transfer: sb.transfer,
  })));
  button.disabled = false;
  button.textContent = "Simulate the season ▸";
  if (!res.data) {
    results.innerHTML = errorBanner(res.message);
    return;
  }
  sb.results = res.data;
  sb.storyIndex = 0;
  sb.storySide = "scenario";
  renderResults(results);
  results.scrollIntoView({ behavior: "smooth", block: "start" });
}

function headline(data) {
  const changed = data.teams.filter((t) => t.changed);
  const verdict = data.trade_verdicts[0];
  if (verdict && verdict.sides.length >= 2) {
    const [best, ...rest] = verdict.sides;
    const worst = rest[rest.length - 1];
    if ((best.delta_wins ?? 0) - (worst.delta_wins ?? 0) < 1) {
      return { lead: `${shortName(best.team_name)} & ${shortName(worst.team_name)}`, verb: "break even", tail: "" };
    }
    return { lead: shortName(best.team_name), verb: "win", tail: "the trade" };
  }
  const biggest = [...changed].sort((a, b) => Math.abs(b.delta.mean_wins) - Math.abs(a.delta.mean_wins))[0];
  if (!biggest) return { lead: "No change", verb: "", tail: "" };
  const up = biggest.delta.mean_wins >= 0;
  return { lead: shortName(biggest.team_name), verb: up ? "climb" : "slide", tail: `${signed(biggest.delta.mean_wins, 1)} wins` };
}

function shortName(full) {
  const parts = String(full).split(" ");
  return parts.length > 1 && ["Trail", "Blazers"].includes(parts[parts.length - 1]) ? "Trail Blazers" : parts[parts.length - 1];
}

function favorite(teams, side) {
  return [...teams].sort((a, b) => (b[side].p_champion ?? 0) - (a[side].p_champion ?? 0))[0];
}

function renderResults(mount) {
  const data = sb.results;
  const changed = data.teams.filter((t) => t.changed);
  const head = headline(data);
  const favBase = data.postseason_simulated ? favorite(data.teams, "baseline") : null;
  const favScenario = data.postseason_simulated ? favorite(data.teams, "scenario") : null;
  const newFavorite = favBase && favScenario && favBase.team_id !== favScenario.team_id;
  const lead = changed[0];
  const wins = changed.flatMap((t) => [...t.baseline.wins_10th_90th, ...t.scenario.wins_10th_90th]);
  const min = Math.max(0, Math.floor(Math.min(...wins) / 5) * 5);
  const max = Math.min(82, Math.ceil(Math.max(...wins) / 5) * 5);
  mount.innerHTML = `
    <section class="card clipping sandbox-headline fade-in" aria-labelledby="sb-headline">
      <div class="clipping-masthead"><span class="kicker">Sandbox · ${escapeHtml(data.season_label)} ${data.mode === "next" ? "projection" : "replay"}</span>
        <span class="clipping-meta">${data.n_simulations.toLocaleString()} seasons each way · ${data.transfer === "full" ? "full box-score transfer" : "calibrated"}</span></div>
      <h2 class="tabloid-headline" id="sb-headline">${escapeHtml(head.lead)} ${head.verb ? `<span class="scrawl">${escapeHtml(head.verb)}</span>` : ""} ${escapeHtml(head.tail)}</h2>
      ${lead ? `<p class="deck">${escapeHtml(lead.team_name)}: <strong>${fixed(lead.baseline.mean_wins)}</strong> → <strong>${fixed(lead.scenario.mean_wins)}</strong> wins
        ${lead.scenario.p_made_playoffs != null ? `· playoffs <strong>${pct(lead.baseline.p_made_playoffs, 0)}</strong> → <strong>${pct(lead.scenario.p_made_playoffs, 0)}</strong>
        · title <strong>${pct(lead.baseline.p_champion)}</strong> → <strong>${pct(lead.scenario.p_champion)}</strong>` : ""}.</p>` : ""}
      ${newFavorite ? `<p class="stamp stamp--tilt">New title favorite: ${escapeHtml(shortName(favScenario.team_name))}</p>` : ""}
      <p class="clipping-fineprint">A what-if simulation with low confidence, not a forecast of what will happen. Method and assumptions below.</p>
    </section>

    <div class="card fade-in">
      <div class="card-header"><div><h2>Teams you changed</h2>
        <p class="card-subtitle">Mean simulated wins with the 10th–90th percentile range, same random draws in both columns.</p></div></div>
      <div class="chart-wrap" id="sb-dumbbell">${dumbbellRows(changed.map((t) => ({
        label: t.abbreviation, base: t.baseline.mean_wins, scenario: t.scenario.mean_wins,
        baseRange: t.baseline.wins_10th_90th, scenarioRange: t.scenario.wins_10th_90th,
      })), { min, max, unit: " W" })}</div>
      <div class="table-scroll mt-1"><table class="data-table">
        <thead><tr><th scope="col">Team</th><th scope="col" class="num">Net rating</th><th scope="col" class="num">Wins</th>
          ${data.postseason_simulated ? `<th scope="col" class="num">Playoffs</th><th scope="col" class="num">Conf. finals</th><th scope="col" class="num">Title</th>` : ""}</tr></thead>
        <tbody>${changed.map((t) => {
          const effect = data.changed_teams.find((e) => e.team_id === t.team_id);
          return `<tr><th scope="row">${escapeHtml(t.team_name)}</th>
            <td class="num">${signed(data.transfer === "full" ? effect?.delta_net_rating_full : effect?.delta_net_rating, 1)}</td>
            <td class="num">${fixed(t.scenario.mean_wins)} <span class="delta ${t.delta.mean_wins >= 0 ? "is-up" : "is-down"}">${signed(t.delta.mean_wins, 1)}</span></td>
            ${data.postseason_simulated ? `
              <td class="num">${pct(t.scenario.p_made_playoffs, 0)} <span class="delta ${t.delta.p_made_playoffs >= 0 ? "is-up" : "is-down"}">${signedPoints(t.delta.p_made_playoffs, 0)}</span></td>
              <td class="num">${pct(t.scenario.p_won_conf_semifinals, 1)} <span class="delta ${t.delta.p_won_conf_semifinals >= 0 ? "is-up" : "is-down"}">${signedPoints(t.delta.p_won_conf_semifinals, 1)}</span></td>
              <td class="num">${pct(t.scenario.p_champion, 1)} <span class="delta ${t.delta.p_champion >= 0 ? "is-up" : "is-down"}">${signedPoints(t.delta.p_champion, 1)}</span></td>` : ""}
          </tr>`;
        }).join("")}</tbody></table></div>
      ${data.trade_verdicts.length ? `<div class="section-title mt-2">Who won the trade</div>
        ${data.trade_verdicts.map((v) => `<p class="verdict">${v.sides.map((s, i) => `${i === 0 ? "<strong>" : ""}${escapeHtml(s.team_name)} ${signed(s.delta_wins, 1)} wins${s.delta_title != null ? `, title ${signedPoints(s.delta_title, 1)}` : ""}${i === 0 ? "</strong>" : ""}`).join(" · ")}</p>
          ${v.note ? `<p class="table-note">${escapeHtml(v.note)}</p>` : ""}`).join("")}` : ""}
    </div>

    <div class="card fade-in">
      <div class="card-header"><div><h2>League table, with your moves</h2>
        <p class="card-subtitle">Every team, sorted by scenario wins. Changes are versus the same simulation without your moves
          (other teams move a little because they play the teams you changed).</p></div></div>
      <div class="grid-2">${["East", "West"].map((conf) => standingsTable(data, conf)).join("")}</div>
    </div>

    ${data.postseason_simulated ? `<div class="card fade-in" id="sb-story-card"></div>`
      : `<div class="info-banner scouting-note"><span class="note-label">Playoffs not simulated</span><p class="model-insight">${escapeHtml(data.postseason_note || "")}</p></div>`}

    <div class="card fade-in"><details class="collapsible"><summary>How this was simulated</summary>
      <div class="mt-1">
        <p>${escapeHtml(data.schedule_note)}</p>
        <div class="grid-2 mt-1">
          <div><div class="section-title">Assumptions</div><ul class="plain-list">${data.assumptions.map((a) => `<li>${escapeHtml(a)}</li>`).join("")}</ul></div>
          <div><div class="section-title">Limits</div><ul class="plain-list">${data.limitations.map((a) => `<li>${escapeHtml(a)}</li>`).join("")}</ul></div>
        </div>
        <p class="text-muted mt-1">Realization factor ${data.method.realization_factor} (80% range ${data.method.realization_factor_80pct.join("–")});
          margin sigma ${data.method.margin_sigma} points; ${data.method.wins_per_net_rating_point} wins per net-rating point;
          box-score team model held-out R² ${data.method.team_model_holdout_r2}.
          ${data.strength_sd ? `Preseason strength uncertainty: ${data.strength_sd} (log-odds SD per team).` : ""}</p>
      </div></details></div>`;
  wireChartTooltips(mount.querySelector("#sb-dumbbell"), (i) => {
    const t = changed[Number(i)];
    if (!t) return null;
    return { title: t.team_name, rows: [
      { value: `${fixed(t.baseline.mean_wins)} (${t.baseline.wins_10th_90th.join("–")})`, label: "unchanged", key: "#a5a49d" },
      { value: `${fixed(t.scenario.mean_wins)} (${t.scenario.wins_10th_90th.join("–")})`, label: "your scenario", key: "#ee3a1f" },
    ] };
  });
  if (data.postseason_simulated) renderStory(mount.querySelector("#sb-story-card"));
}

function standingsTable(data, conference) {
  const rows = data.teams.filter((t) => t.conference === conference)
    .sort((a, b) => b.scenario.mean_wins - a.scenario.mean_wins);
  const post = data.postseason_simulated;
  return `<div><div class="section-title">${conference}</div><div class="table-scroll"><table class="data-table standings-mini">
    <thead><tr><th scope="col" class="num">#</th><th scope="col">Team</th><th scope="col" class="num">Wins</th><th scope="col" class="num">Δ</th>
      ${post ? `<th scope="col" class="num">Playoffs</th><th scope="col" class="num">Title</th>` : ""}</tr></thead>
    <tbody>${rows.map((t, i) => `<tr class="${t.changed ? "is-changed" : ""} ${i === 6 ? "is-below-cutoff" : ""}">
      <td class="num rank">${i + 1}</td><td class="team">${escapeHtml(t.abbreviation)}</td>
      <td class="num">${fixed(t.scenario.mean_wins)}</td>
      <td class="num"><span class="delta ${t.delta.mean_wins >= 0 ? "is-up" : "is-down"}">${Math.abs(t.delta.mean_wins) < 0.05 ? "·" : signed(t.delta.mean_wins, 1)}</span></td>
      ${post ? `<td class="num">${pct(t.scenario.p_made_playoffs, 0)}</td><td class="num">${pct(t.scenario.p_champion, 1)}</td>` : ""}</tr>`).join("")}</tbody>
  </table></div><p class="table-note"><span class="cutoff-key"></span>Top six by mean wins; 7-10 go to the play-in</p></div>`;
}

function renderStory(card) {
  const data = sb.results;
  const stories = data.stories[sb.storySide];
  const story = stories[sb.storyIndex % stories.length];
  const champ = story.finals.champion;
  const colors = teamColor(champ.team_id);
  const label = (t) => escapeHtml(t.abbreviation || t.team_name);
  card.innerHTML = `
    <div class="card-header"><div><h2>One simulated season</h2>
      <p class="card-subtitle">A single draw from the ${data.n_simulations.toLocaleString()} (season ${sb.storyIndex + 1} of ${stories.length} shown here).
        One season is one possibility, not the prediction; the odds above are the prediction.</p></div>
      <div class="toolbar">
        <div class="segmented" role="group" aria-label="Which league">
          <button class="seg ${sb.storySide === "scenario" ? "is-on" : ""}" data-side="scenario">Your scenario</button>
          <button class="seg ${sb.storySide === "baseline" ? "is-on" : ""}" data-side="baseline">Unchanged</button>
        </div>
        <button class="btn btn-secondary" id="sb-next-story">Another season ▸</button>
      </div></div>
    <div class="champion-banner" style="--team-color:${colors.primary}; --team-ink:${readableInk(colors.primary)};">
      <span class="champion-kicker">Champions</span>
      <span class="champion-name">${escapeHtml(champ.team_name)}</span>
      <span class="champion-score">beat ${label(story.finals.winner === story.finals.higher.team_id ? story.finals.lower : story.finals.higher)} ${Math.max(...story.finals.score)}-${Math.min(...story.finals.score)} in the Finals</span>
    </div>
    <div class="grid-2 mt-2">${["East", "West"].map((conf) => {
      const c = story.conferences[conf];
      return `<div>
        <div class="section-title">${conf}</div>
        <ol class="story-standings">${c.standings.slice(0, 10).map((s) => `<li class="${s.seed === 7 ? "is-below-cutoff" : ""}">
          <span class="seed">${s.seed}</span><span>${escapeHtml(s.abbreviation)}</span><span class="num">${s.wins}-${(data.teams.find((t) => t.team_id === s.team_id)?.scenario.games ?? 82) - s.wins}</span></li>`).join("")}</ol>
        ${c.play_in.length ? `<p class="table-note">Play-in: ${c.play_in.map((g) => `${label(g.winner === g.home.team_id ? g.home : g.away)} beat ${label(g.winner === g.home.team_id ? g.away : g.home)}`).join(" · ")}</p>` : ""}
        <ul class="bracket-list">${c.rounds.map((r) => {
          const winnerHigher = r.winner === r.higher.team_id;
          const [w, l] = winnerHigher ? [r.higher, r.lower] : [r.lower, r.higher];
          const [ws, ls] = winnerHigher ? [r.higher_seed, r.lower_seed] : [r.lower_seed, r.higher_seed];
          return `<li><span class="bracket-round">${escapeHtml(r.round)}</span>
            <span><strong>(${ws}) ${label(w)}</strong> over (${ls}) ${label(l)} <span class="num">${Math.max(...r.score)}-${Math.min(...r.score)}</span>${winnerHigher ? "" : ' <span class="badge accent">upset</span>'}</span></li>`;
        }).join("")}</ul>
      </div>`;
    }).join("")}</div>`;
  card.querySelector("#sb-next-story").addEventListener("click", () => {
    sb.storyIndex = (sb.storyIndex + 1) % stories.length;
    renderStory(card);
  });
  card.querySelectorAll("[data-side]").forEach((b) => b.addEventListener("click", () => {
    sb.storySide = b.dataset.side;
    renderStory(card);
  }));
}
