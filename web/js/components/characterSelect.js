// components/characterSelect.js — "Choose your player": a fighting-game
// select screen over every player of a season. A grid of portrait tiles
// (stars first), a cursor that slides between them, and a big preview that
// slams in with the hovered player's ratings. The whole arena takes on that
// player's team colors. Enter locks the choice in.
//
// 1P mode (the Players page) calls onSelect(card). 2P mode (Compare) locks
// P1, hands the cursor to P2, and calls onComplete(p1, p2).
//
// Ratings are the player_cards tool's: percentiles among the season's
// qualified players (shown as 0-99), not a new model. Players outside the
// season grid are one search away (resolve_player, any era).

import { getPlayerCards, resolvePlayer } from "../api.js";
import { teamColor } from "../teamColors.js";
import { readableInk } from "../colorInk.js";
import { fetchTeams } from "./teamSelect.js";
import { playerThumb, tornPhoto, wireTornPhoto } from "./tornPhoto.js";
import { reduceMotion } from "./motion.js";
import { debounce, errorBanner, escapeHtml, safe, seasonLabel, unwrap } from "./format.js";

const GRID_LIMIT = 48;
const RATING_LABELS = {
  scoring: "Scoring", shooting: "Shooting", playmaking: "Playmaking",
  rebounding: "Rebounding", defense: "Defense (box)", impact: "Impact",
};
const cardCache = {};

function cards(season) {
  const key = season ?? "default";
  cardCache[key] ||= safe(getPlayerCards({ season })).then(unwrap).then((res) => {
    if (!res.data) delete cardCache[key];
    return res;
  });
  return cardCache[key];
}

function fold(text) {
  return String(text).normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

function splitName(name) {
  const parts = String(name).trim().split(/\s+/);
  const last = parts.pop() || "";
  return { first: parts.join(" "), last };
}

function rating(value) {
  return value == null ? "—" : String(Math.min(99, Math.round(value)));
}

/**
 * Mount the select screen into `root`. Options: mode ("1p" | "2p"),
 * season, team, locked (a card to pre-lock as P1 in 2P mode), title,
 * onSelect(card), onComplete(p1, p2), onSeason(season), onPickAllTime(personId).
 */
export function mountCharacterSelect(root, options = {}) {
  const mode = options.mode || "1p";
  const state = {
    season: options.season ?? null,
    team: options.team ?? null,
    query: "",
    showAll: false,
    data: null,
    visible: [],
    index: 0,
    locked: options.locked ? [options.locked] : [],
    busy: false,
  };

  root.innerHTML = `
    <section class="cs-arena" data-mode="${mode}" aria-labelledby="cs-title">
      <div class="cs-bg" aria-hidden="true"></div>
      <div class="cs-flash" aria-hidden="true"></div>
      <header class="cs-head">
        <div>
          <p class="cs-kicker" id="cs-kicker">Loading the roster…</p>
          <h2 class="cs-title" id="cs-title">${options.title || (mode === "2p" ? "Choose your <span class=\"hl\">fighters</span>" : "Choose your <span class=\"hl\">player</span>")}</h2>
        </div>
        <div class="cs-controls">
          <div class="cs-search-wrap">
            <label class="sr-only" for="cs-search">Find a player</label>
            <input class="input cs-search" id="cs-search" placeholder="Type a name, any era" autocomplete="off" spellcheck="false">
          </div>
          <div class="select-wrap"><label class="sr-only" for="cs-season">Season</label>
            <select class="select cs-season" id="cs-season"><option>…</option></select></div>
          <button type="button" class="btn btn-secondary cs-random" id="cs-random" title="Random player (R)">
            <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8" cy="8" r="1.4"/><circle cx="16" cy="16" r="1.4"/><circle cx="12" cy="12" r="1.4"/></svg>Random</button>
        </div>
      </header>
      <div class="cs-teams" id="cs-teams" role="toolbar" aria-label="Filter by team"></div>
      <div class="cs-stage">
        <div class="cs-preview cs-preview--p1" id="cs-p1" aria-live="polite"></div>
        <div class="cs-grid-wrap">
          <div class="cs-grid" id="cs-grid" role="group" aria-label="Players"></div>
          <div class="cs-cursor cs-cursor--p1" id="cs-cursor" aria-hidden="true"><span>${mode === "2p" && state.locked.length ? "P2" : "P1"}</span></div>
          <div class="cs-foot" id="cs-foot"></div>
          <div class="cs-alltime" id="cs-alltime" aria-live="polite"></div>
        </div>
        ${mode === "2p" ? `<div class="cs-preview cs-preview--p2" id="cs-p2" aria-live="polite"></div>` : ""}
      </div>
      <p class="cs-help"><kbd>←</kbd><kbd>→</kbd><kbd>↑</kbd><kbd>↓</kbd> move · <kbd>Enter</kbd> choose · <kbd>R</kbd> random${mode === "2p" ? " · <kbd>Esc</kbd> undo" : ""} ·
        ratings are percentiles among the season's qualified players</p>
    </section>`;

  const arena = root.querySelector(".cs-arena");
  const grid = root.querySelector("#cs-grid");
  const cursor = root.querySelector("#cs-cursor");
  const search = root.querySelector("#cs-search");

  // ---- data ---------------------------------------------------------------

  async function load() {
    grid.innerHTML = Array.from({ length: 18 }, () => `<span class="cs-tile is-skeleton"><span class="skeleton"></span></span>`).join("");
    const res = await cards(state.season);
    if (!root.isConnected) return;
    if (!res.data) {
      grid.innerHTML = errorBanner(res.message);
      return;
    }
    state.data = res.data;
    state.season = res.data.season;
    root.querySelector("#cs-season").innerHTML = res.data.seasons.map((s) =>
      `<option value="${s}" ${s === res.data.season ? "selected" : ""}>${seasonLabel(s)}</option>`).join("");
    root.querySelector("#cs-kicker").textContent =
      `${res.data.season_label} · ${res.data.cards.length} players · ratings among ${res.data.qualified_players} qualified`;
    await paintTeams();
    filter();
  }

  async function paintTeams() {
    let teams = [];
    try {
      teams = (await fetchTeams()).slice().sort((a, b) => a.abbreviation.localeCompare(b.abbreviation));
    } catch (err) {
      teams = [];
    }
    const strip = root.querySelector("#cs-teams");
    strip.innerHTML = `<button type="button" class="cs-team ${state.team ? "" : "is-on"}" data-team="" aria-pressed="${!state.team}">All</button>
      ${teams.map((t) => {
        const c = teamColor(t.team_id);
        return `<button type="button" class="cs-team ${state.team === t.team_id ? "is-on" : ""}" data-team="${t.team_id}"
          aria-pressed="${state.team === t.team_id}" title="${escapeHtml(t.full_name)}"
          style="--chip:${c.primary}; --chip-ink:${readableInk(c.primary)}; --chip-alt:${c.secondary};">${escapeHtml(t.abbreviation)}</button>`;
      }).join("")}`;
    strip.querySelectorAll("[data-team]").forEach((button) => button.addEventListener("click", () => {
      state.team = button.dataset.team ? Number(button.dataset.team) : null;
      strip.querySelectorAll("[data-team]").forEach((b) => {
        const on = b === button;
        b.classList.toggle("is-on", on);
        b.setAttribute("aria-pressed", String(on));
      });
      state.showAll = false;
      filter();
    }));
  }

  function filter() {
    const all = state.data?.cards || [];
    const q = fold(state.query.trim());
    let list = all.filter((c) => (!state.team || c.team_id === state.team) && (!q || fold(c.name).includes(q)));
    const total = list.length;
    if (!state.showAll && !state.team && !q) list = list.slice(0, GRID_LIMIT);
    state.visible = list;
    state.index = Math.min(state.index, Math.max(0, list.length - 1));
    paintGrid(total);
    allTimeSearch(q, total);
  }

  // ---- grid ---------------------------------------------------------------

  function tile(card, i) {
    const colors = teamColor(card.team_id);
    const { first, last } = splitName(card.name);
    const lockedAs = state.locked.findIndex((c) => c.person_id === card.person_id);
    return `<button type="button" class="cs-tile ${lockedAs >= 0 ? `is-locked-p${lockedAs + 1}` : ""}" id="cs-tile-${card.person_id}" data-index="${i}"
        tabindex="${i === state.index ? 0 : -1}" style="--tile:${colors.primary}; --tile-alt:${colors.secondary}; --tile-ink:${readableInk(colors.primary)}; --i:${Math.min(i, 30)};"
        aria-label="${escapeHtml(card.name)}, ${escapeHtml(card.team || "")}, impact ${rating(card.ratings.impact)}">
      <span class="cs-tile-ovr">${rating(card.overall)}</span>
      <span class="cs-tile-team">${escapeHtml(card.team || "")}</span>
      ${playerThumb({ personId: card.person_id, name: card.name })}
      <span class="cs-tile-name"><small>${escapeHtml(first)}</small>${escapeHtml(last)}</span>
    </button>`;
  }

  function paintGrid(total) {
    const foot = root.querySelector("#cs-foot");
    if (!state.visible.length) {
      grid.innerHTML = `<p class="cs-empty">${state.query ? "No one by that name this season." : "No players."}</p>`;
      cursor.hidden = true;
      foot.innerHTML = "";
      return;
    }
    cursor.hidden = false;
    grid.innerHTML = state.visible.map(tile).join("");
    grid.classList.remove("is-in");
    void grid.offsetWidth; // restart the cascade
    grid.classList.add("is-in");
    wireTornPhoto(grid);
    grid.querySelectorAll(".cs-tile").forEach((el) => {
      el.addEventListener("pointerenter", () => focusTile(Number(el.dataset.index), { focus: false }));
      el.addEventListener("focus", () => focusTile(Number(el.dataset.index), { focus: false }));
      el.addEventListener("click", () => choose(Number(el.dataset.index)));
    });
    foot.innerHTML = state.visible.length < total
      ? `<span>Showing the top ${state.visible.length} of ${total} by wins added</span><button type="button" class="link-btn" id="cs-all">Show everyone</button>`
      : `<span>${total} player${total === 1 ? "" : "s"}</span>`;
    foot.querySelector("#cs-all")?.addEventListener("click", () => {
      state.showAll = true;
      filter();
    });
    requestAnimationFrame(() => focusTile(state.index, { focus: false, instant: true }));
  }

  function moveCursor(el, instant) {
    const wrap = grid.parentElement.getBoundingClientRect();
    const rect = el.getBoundingClientRect();
    cursor.classList.toggle("no-anim", Boolean(instant) || reduceMotion());
    cursor.style.width = `${rect.width}px`;
    cursor.style.height = `${rect.height}px`;
    cursor.style.transform = `translate(${rect.left - wrap.left}px, ${rect.top - wrap.top}px)`;
  }

  function focusTile(index, { focus = true, instant = false } = {}) {
    const tiles = grid.querySelectorAll(".cs-tile");
    const el = tiles[index];
    if (!el) return;
    if (tiles[state.index]) tiles[state.index].tabIndex = -1;
    el.tabIndex = 0;
    const changed = index !== state.index || instant;
    state.index = index;
    if (focus) el.focus({ preventScroll: false });
    moveCursor(el, instant);
    if (changed || !arena.dataset.shown) preview(state.visible[index]);
  }

  window.addEventListener("resize", onResize);
  function onResize() {
    if (!root.isConnected) {
      window.removeEventListener("resize", onResize);
      return;
    }
    const el = grid.querySelectorAll(".cs-tile")[state.index];
    if (el) moveCursor(el, true);
  }

  function columns() {
    const tiles = [...grid.querySelectorAll(".cs-tile")];
    if (tiles.length < 2) return 1;
    const top = tiles[0].offsetTop;
    const index = tiles.findIndex((t) => t.offsetTop !== top);
    return index === -1 ? tiles.length : index;
  }

  // Like a fighting game, the arrows drive the cursor as soon as the screen
  // is up: keys are read page-wide (not only when a tile has focus), except
  // while typing in a field or when a dialog is open.
  function onKey(event) {
    if (!root.isConnected) {
      document.removeEventListener("keydown", onKey);
      return;
    }
    const target = event.target;
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(target?.tagName || "") || target?.isContentEditable;
    if (typing || event.metaKey || event.ctrlKey || event.altKey || document.documentElement.classList.contains("has-overlay")) return;
    if (!state.visible.length) return;
    const cols = columns();
    const max = state.visible.length - 1;
    const moves = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: cols, ArrowUp: -cols };
    if (event.key in moves) {
      event.preventDefault();
      focusTile(Math.max(0, Math.min(max, state.index + moves[event.key])));
    } else if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      focusTile(event.key === "Home" ? 0 : max);
    } else if (event.key === "Enter" && !target.closest?.("button, a")) {
      event.preventDefault();
      choose(state.index);
    } else if (event.key === "r" || event.key === "R") {
      event.preventDefault();
      random();
    } else if (event.key === "Escape" && mode === "2p" && state.locked.length && !state.busy) {
      event.preventDefault();
      unlock();
    }
  }
  document.addEventListener("keydown", onKey);

  // ---- preview ------------------------------------------------------------

  function previewMarkup(card, slot) {
    const colors = teamColor(card.team_id);
    const { first, last } = splitName(card.name);
    const locked = state.locked[slot - 1]?.person_id === card.person_id;
    return `
      <div class="cs-portrait">
        ${tornPhoto({ personId: card.person_id, name: card.name, teamColor: colors.primary, teamAlt: colors.secondary })}
        <span class="cs-slot">P${slot}</span>
        ${locked ? `<p class="stamp stamp--tilt cs-locked">Locked in</p>` : ""}
      </div>
      <div class="cs-info">
        <p class="cs-meta">${escapeHtml([card.team, card.position, card.age != null ? `age ${card.age}` : null].filter(Boolean).join(" · "))}</p>
        <h3 class="cs-name"><small>${escapeHtml(first)}</small>${escapeHtml(last)}</h3>
        <div class="cs-ovr" title="Box impact percentile among qualified players">
          <b>${rating(card.overall)}</b><span>Impact<br>percentile</span>
        </div>
        <ul class="cs-bars">
          ${Object.entries(RATING_LABELS).map(([key, label], i) => `
            <li style="--d:${i * 55}ms"><span class="cs-bar-label">${label}</span>
              <span class="cs-bar"><span class="cs-bar-fill" style="--w:${card.ratings[key] ?? 0}%"></span></span>
              <b>${rating(card.ratings[key])}</b></li>`).join("")}
        </ul>
        <p class="cs-line"><b>${card.ppg}</b> pts · <b>${card.rpg}</b> reb · <b>${card.apg}</b> ast · <b>${card.mpg}</b> min · ${card.games} g</p>
        ${card.tags?.length ? `<p class="cs-tags">${card.tags.slice(0, 2).map((t) => `<span class="cs-tag">${escapeHtml(t)}</span>`).join("")}</p>` : ""}
      </div>`;
  }

  function slotFor() {
    return mode === "2p" && state.locked.length >= 1 ? 2 : 1;
  }

  function preview(card) {
    if (!card) return;
    const slot = slotFor();
    const mount = root.querySelector(slot === 2 ? "#cs-p2" : "#cs-p1");
    if (state.locked[slot - 1]) return;
    const colors = teamColor(card.team_id);
    if (slot === 1 || mode === "1p") {
      arena.style.setProperty("--sel", colors.primary);
      arena.style.setProperty("--sel-alt", colors.secondary);
    } else {
      arena.style.setProperty("--sel2", colors.primary);
    }
    arena.dataset.shown = "1";
    mount.innerHTML = previewMarkup(card, slot);
    mount.style.setProperty("--team", colors.primary);
    mount.style.setProperty("--team-alt", colors.secondary);
    mount.classList.remove("is-new");
    void mount.offsetWidth;
    mount.classList.add("is-new");
    wireTornPhoto(mount);
  }

  // ---- choosing -----------------------------------------------------------

  function flash() {
    const el = arena.querySelector(".cs-flash");
    el.classList.remove("is-on");
    void el.offsetWidth;
    el.classList.add("is-on");
  }

  function choose(index) {
    const card = state.visible[index];
    if (!card || state.busy) return;
    if (index !== state.index) focusTile(index, { focus: false });
    state.busy = true;
    const slot = slotFor();
    state.locked[slot - 1] = card;
    const mount = root.querySelector(slot === 2 ? "#cs-p2" : "#cs-p1");
    mount.innerHTML = previewMarkup(card, slot);
    mount.classList.add("is-locked");
    wireTornPhoto(mount);
    grid.querySelectorAll(".cs-tile")[index]?.classList.add(`is-locked-p${slot}`, "is-picked");
    flash();
    const wait = reduceMotion() ? 0 : 650;
    setTimeout(() => {
      state.busy = false;
      if (mode === "1p") {
        options.onSelect?.(card);
      } else if (state.locked.length === 2) {
        options.onComplete?.(state.locked[0], state.locked[1]);
      } else {
        cursor.classList.replace("cs-cursor--p1", "cs-cursor--p2");
        cursor.querySelector("span").textContent = "P2";
        arena.classList.add("is-p2");
        const next = state.visible.findIndex((c) => c.person_id !== card.person_id);
        focusTile(next >= 0 ? next : 0, { focus: true, instant: true });
        preview(state.visible[state.index]);
      }
    }, wait);
  }

  function unlock() {
    const slot = state.locked.length;
    const card = state.locked.pop();
    const mount = root.querySelector(slot === 2 ? "#cs-p2" : "#cs-p1");
    mount.classList.remove("is-locked");
    grid.querySelectorAll(`.is-locked-p${slot}`).forEach((el) => el.classList.remove(`is-locked-p${slot}`, "is-picked"));
    if (slot === 1) {
      cursor.classList.replace("cs-cursor--p2", "cs-cursor--p1");
      cursor.querySelector("span").textContent = "P1";
      arena.classList.remove("is-p2");
      const p2 = root.querySelector("#cs-p2");
      if (p2) p2.innerHTML = "";
    }
    if (card) preview(card);
  }

  function random() {
    const count = state.visible.length;
    if (!count || state.busy) return;
    const target = Math.floor(Math.random() * count);
    if (reduceMotion()) {
      focusTile(target);
      choose(target);
      return;
    }
    state.busy = true;
    let step = 0;
    const steps = 14;
    const spin = () => {
      const index = step === steps ? target : Math.floor(Math.random() * count);
      focusTile(index, { focus: false });
      step += 1;
      if (step <= steps) setTimeout(spin, 40 + step * step * 1.3);
      else {
        state.busy = false;
        focusTile(target);
        choose(target);
      }
    };
    spin();
  }

  // ---- search, season -----------------------------------------------------

  const allTimeMount = root.querySelector("#cs-alltime");
  const lookup = debounce(async (q) => {
    const res = unwrap(await safe(resolvePlayer({ name: q, limit: 8 })));
    if (fold(state.query.trim()) !== q || !res.data) return;
    const shown = new Set(state.visible.map((c) => c.person_id));
    const others = res.data.candidates.filter((c) => !shown.has(c.person_id));
    allTimeMount.innerHTML = others.length ? `
      <p class="cs-alltime-title">Any era</p>
      <ul class="player-result-list">${others.map((c) => `<li><button type="button" class="player-result" data-person="${c.person_id}">
        ${playerThumb({ personId: c.person_id, name: c.full_name })}
        <span class="player-result-name">${escapeHtml(c.full_name)}</span>
        <span class="player-result-meta">${c.from_year ?? "?"}–${c.to_year ?? "now"}${c.positions?.length ? ` · ${c.positions.join("-")}` : ""}</span>
      </button></li>`).join("")}</ul>` : "";
    wireTornPhoto(allTimeMount);
    allTimeMount.querySelectorAll("[data-person]").forEach((button) =>
      button.addEventListener("click", () => options.onPickAllTime?.(Number(button.dataset.person))));
  }, 260);

  function allTimeSearch(q) {
    if (q.length < 3 || !options.onPickAllTime) {
      allTimeMount.innerHTML = "";
      return;
    }
    lookup(q);
  }

  search.addEventListener("input", () => {
    state.query = search.value;
    state.index = 0;
    filter();
  });
  search.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && state.visible.length) {
      event.preventDefault();
      choose(state.index);
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      grid.querySelectorAll(".cs-tile")[state.index]?.focus();
    }
  });
  root.querySelector("#cs-season").addEventListener("change", (event) => {
    state.season = Number(event.target.value);
    state.index = 0;
    options.onSeason?.(state.season);
    load();
  });
  root.querySelector("#cs-random").addEventListener("click", random);

  if (state.locked.length && mode === "2p") {
    const mount = root.querySelector("#cs-p1");
    mount.innerHTML = previewMarkup(state.locked[0], 1);
    mount.classList.add("is-locked");
    cursor.classList.replace("cs-cursor--p1", "cs-cursor--p2");
    arena.classList.add("is-p2");
    wireTornPhoto(mount);
  }
  load();
  return { focus: () => grid.querySelector(".cs-tile[tabindex='0']")?.focus() };
}

/** Cached cards for a season (for pages that need one card without the screen). */
export function seasonCards(season) {
  return cards(season);
}
