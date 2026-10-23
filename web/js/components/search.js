// components/search.js — one search box for the whole site, opened with "/"
// or Ctrl/⌘-K (or the search button): pages by name, teams by name, city or
// abbreviation (list_teams), and players by name (resolve_player). Arrow keys
// move, Enter opens, Esc closes. It is a dialog: focus stays inside while it
// is open and returns to where it was when it closes.

import { resolvePlayer } from "../api.js";
import { fetchTeams } from "./teamSelect.js";
import { debounce, escapeHtml, safe, unwrap } from "./format.js";
import { teamColor } from "../teamColors.js";

let pages = [];
let navigateTo = null;
let root = null;
let input = null;
let list = null;
let results = [];
let active = 0;
let lastFocus = null;
let query = "";

export function initSearch({ routes, navigate }) {
  pages = routes;
  navigateTo = navigate;
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
        <input class="search-input" type="search" placeholder="Search players, teams and pages" autocomplete="off"
          role="combobox" aria-expanded="true" aria-controls="search-list" aria-autocomplete="list" spellcheck="false">
        <kbd>Esc</kbd>
      </div>
      <ul class="search-list" id="search-list" role="listbox"></ul>
      <p class="search-hint"><kbd>↑</kbd><kbd>↓</kbd> move · <kbd>Enter</kbd> open · <kbd>/</kbd> or <kbd>Ctrl K</kbd> from anywhere</p>
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
    results = [...results.filter((r) => r.kind !== "Player"), ...found];
    paint();
  }, 220);
  input.addEventListener("input", () => {
    query = input.value.trim();
    local(query).then(() => players(query));
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

async function local(text) {
  const lower = text.toLowerCase();
  const pageHits = pages
    .filter((p) => !lower || p.label.toLowerCase().includes(lower) || (p.description || "").toLowerCase().includes(lower))
    .slice(0, lower ? 5 : 8)
    .map((p) => ({ kind: "Page", label: p.label, meta: p.description || "", path: p.path }));
  let teamHits = [];
  if (lower.length >= 2) {
    try {
      const teams = await fetchTeams();
      teamHits = teams
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
  results = [...teamHits, ...players, ...pageHits];
  active = 0;
  paint();
}

function paint() {
  if (!results.length) {
    list.innerHTML = `<li class="search-empty">${query.length >= 3 ? "Nothing matches yet…" : "Type a player, team or page."}</li>`;
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
  navigateTo(result.path, result.query || null);
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
