// components/search.js — the command palette, opened with "/" or Ctrl/⌘-K
// (or the search button): pages by name, teams by name, city or abbreviation
// (list_teams), players by name (resolve_player), and commands (theme,
// sounds, alerts, your team, copy link…). "A vs B" offers the matchup and the
// rivalry; anything else can be sent to the assistant. Starting with ">"
// lists commands only. Arrow keys move, Enter runs, Esc closes. It is a
// dialog: focus stays inside while it is open and returns to where it was
// when it closes.

import { resolvePlayer } from "../api.js";
import { fetchTeams } from "./teamSelect.js";
import { debounce, escapeHtml, safe, unwrap } from "./format.js";
import { teamColor } from "../teamColors.js";

let pages = [];
let commands = [];
let navigateTo = null;
let root = null;
let input = null;
let list = null;
let results = [];
let active = 0;
let lastFocus = null;
let query = "";

export function initSearch({ routes, navigate, actions = [] }) {
  pages = routes;
  navigateTo = navigate;
  commands = actions;
  document.addEventListener("keydown", (event) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "") ||
      document.activeElement?.isContentEditable;
    if ((event.key === "k" || event.key === "K") && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      isOpen() ? closeSearch() : openSearch();
    } else if (event.key === "/" && !typing && !isOpen()) {
      event.preventDefault();
      openSearch();
    }
  });
}

function isOpen() {
  return root && !root.hidden;
}

function build() {
  root = document.createElement("div");
  root.className = "search-overlay";
  root.hidden = true;
  root.innerHTML = `
    <div class="search-scrim" data-close></div>
    <div class="search-panel" role="dialog" aria-modal="true" aria-label="Search Backboard">
      <div class="search-field">
        <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.5 15.5 5 5"/></svg>
        <input class="search-input" type="search" placeholder="Search players, teams, pages, or type > for commands" autocomplete="off"
          role="combobox" aria-expanded="true" aria-controls="search-list" aria-autocomplete="list" spellcheck="false">
        <kbd>Esc</kbd>
      </div>
      <ul class="search-list" id="search-list" role="listbox"></ul>
      <p class="search-hint"><kbd>↑</kbd><kbd>↓</kbd> move · <kbd>Enter</kbd> open · <kbd>&gt;</kbd> commands · try "Celtics vs Knicks" · <kbd>Ctrl K</kbd> from anywhere</p>
    </div>`;
  document.body.appendChild(root);
  input = root.querySelector(".search-input");
  list = root.querySelector(".search-list");
  root.querySelector("[data-close]").addEventListener("click", closeSearch);
  const players = debounce(async (text) => {
    if (text.length < 3) return;
    const res = unwrap(await safe(resolvePlayer({ name: text, limit: 6 })));
    if (text !== query || !res.data) return;
    const found = res.data.candidates.map((c) => ({
      kind: "Player", label: c.full_name,
      meta: `${c.from_year ?? "?"}–${c.to_year ?? "now"}${c.positions?.length ? ` · ${c.positions.join("-")}` : ""}`,
      path: "/players", query: { id: c.person_id },
    }));
    // Players slot in after teams and matchups, before pages and commands.
    const lead = results.filter((r) => ["Matchup", "Rivalry", "Team"].includes(r.kind));
    const rest = results.filter((r) => !["Matchup", "Rivalry", "Team", "Player"].includes(r.kind));
    results = [...lead, ...found, ...rest];
    paint();
  }, 220);
  input.addEventListener("input", () => {
    query = input.value.trim();
    local(query).then(() => (query.startsWith(">") ? null : players(query)));
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!results.length) return;
      active = (active + (event.key === "ArrowDown" ? 1 : -1) + results.length) % results.length;
      paint();
    } else if (event.key === "Enter") {
      event.preventDefault();
      choose(results[active]);
    } else if (event.key === "Escape") {
      event.preventDefault();
      closeSearch();
    } else if (event.key === "Tab") {
      event.preventDefault(); // keep focus in the dialog
    }
  });
  list.addEventListener("click", (event) => {
    const item = event.target.closest("[data-index]");
    if (item) choose(results[Number(item.dataset.index)]);
  });
  list.addEventListener("pointermove", (event) => {
    const item = event.target.closest("[data-index]");
    if (item && Number(item.dataset.index) !== active) {
      active = Number(item.dataset.index);
      paint();
    }
  });
}

function commandHits(text) {
  const lower = text.replace(/^>\s*/, "").toLowerCase();
  const labelOf = (c) => (typeof c.label === "function" ? c.label() : c.label);
  return commands
    .filter((c) => (c.when ? c.when() : true))
    .filter((c) => !lower || `${labelOf(c)} ${c.keywords || ""}`.toLowerCase().includes(lower))
    .map((c) => ({ kind: "Command", label: labelOf(c), meta: c.meta || "", run: c.run }));
}

/** "Celtics vs Knicks", "BOS @ NYK": the matchup and the rivalry. */
function versusHits(text, teams) {
  const match = text.match(/^(.+?)\s+(vs\.?|v\.?|versus|at|@)\s+(.+)$/i);
  if (!match) return [];
  const find = (name) => {
    const lower = name.trim().toLowerCase();
    return teams.find((t) => t.abbreviation.toLowerCase() === lower)
      || teams.find((t) => t.full_name.toLowerCase().includes(lower));
  };
  const a = find(match[1]);
  const b = find(match[3]);
  if (!a || !b || a.team_id === b.team_id) return [];
  const away = /^(at|@)$/i.test(match[2]);
  const home = away ? b : a;
  const visitor = away ? a : b;
  return [
    { kind: "Matchup", label: `${visitor.full_name} at ${home.full_name}`, meta: "win probability and projected score",
      path: "/matchups", query: { home: home.team_id, away: visitor.team_id }, color: teamColor(home.team_id).primary },
    { kind: "Rivalry", label: `${a.full_name} vs ${b.full_name}`, meta: "every meeting, streaks and series",
      path: "/head-to-head", query: { a: a.team_id, b: b.team_id }, color: teamColor(a.team_id).primary },
  ];
}

async function local(text) {
  const lower = text.toLowerCase();
  if (text.startsWith(">")) {
    results = commandHits(text);
    active = 0;
    paint();
    return;
  }
  const pageHits = pages
    .filter((p) => !lower || p.label.toLowerCase().includes(lower) || (p.description || "").toLowerCase().includes(lower))
    .slice(0, lower ? 5 : 8)
    .map((p) => ({ kind: "Page", label: p.label, meta: p.description || "", path: p.path }));
  let teamHits = [];
  let versus = [];
  if (lower.length >= 2) {
    try {
      const teams = await fetchTeams();
      versus = versusHits(text, teams);
      teamHits = versus.length ? [] : teams
        .filter((t) => `${t.full_name} ${t.abbreviation}`.toLowerCase().includes(lower))
        .slice(0, 5)
        .map((t) => ({ kind: "Team", label: t.full_name, meta: t.abbreviation, path: "/teams",
          query: { team: t.team_id }, color: teamColor(t.team_id).primary }));
    } catch (err) {
      teamHits = [];
    }
  }
  if (text !== query) return;
  const players = results.filter((r) => r.kind === "Player" && lower.length >= 3);
  const commandList = lower.length >= 2 ? commandHits(text).slice(0, 3) : [];
  const askHit = text.length >= 8 && /\s/.test(text)
    ? [{ kind: "Ask", label: `Ask: “${text}”`, meta: "the assistant answers with the tools", path: "/assistant", query: { q: text } }]
    : [];
  results = [...versus, ...teamHits, ...players, ...pageHits, ...commandList, ...askHit];
  active = 0;
  paint();
}

function paint() {
  if (!results.length) {
    list.innerHTML = `<li class="search-empty">${query.length >= 3 ? "Nothing matches yet…" : "Type a player, team or page, or > for commands."}</li>`;
    input.removeAttribute("aria-activedescendant");
    return;
  }
  list.innerHTML = results.map((r, i) => `
    <li role="option" id="search-opt-${i}" data-index="${i}" class="search-item ${i === active ? "is-active" : ""}" aria-selected="${i === active}">
      <span class="search-kind">${r.color ? `<i class="search-swatch" style="background:${r.color}"></i>` : ""}${r.kind}</span>
      <span class="search-label">${escapeHtml(r.label)}</span>
      <span class="search-meta">${escapeHtml(r.meta || "")}</span>
    </li>`).join("");
  input.setAttribute("aria-activedescendant", `search-opt-${active}`);
  list.querySelector(".is-active")?.scrollIntoView({ block: "nearest" });
}

function choose(result) {
  if (!result) return;
  closeSearch();
  if (result.run) result.run();
  else navigateTo(result.path, result.query || null);
}

export function openSearch() {
  if (!root) build();
  lastFocus = document.activeElement;
  root.hidden = false;
  document.documentElement.classList.add("has-overlay");
  input.value = "";
  query = "";
  results = [];
  local("");
  requestAnimationFrame(() => {
    root.classList.add("is-open");
    input.focus();
  });
}

export function closeSearch() {
  if (!isOpen()) return;
  root.classList.remove("is-open");
  root.hidden = true;
  document.documentElement.classList.remove("has-overlay");
  lastFocus?.focus?.();
}
