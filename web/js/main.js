// main.js — app shell: the Limelight nav, the page head, and route mounting.
//
// Scalability note: adding a new page means (1) creating a page module with
// `render(container, ctx)` [+ optional `meta` and `renderHero`], and (2)
// adding one entry to ROUTES below. Pages on the bar get `bar: true`; the
// rest are listed under "More". No other file needs to change.

import { startRouter, onRouteChange, navigate, currentQuery } from "./router.js";
import { getHealth } from "./api.js";
import * as dashboardPage from "./pages/dashboard.js";
import * as matchupsPage from "./pages/matchups.js";
import * as teamsPage from "./pages/teams.js";
import * as simulatorPage from "./pages/simulator.js";
import * as leaguePage from "./pages/league.js";
import * as assistantPage from "./pages/assistant.js";
import * as headToHeadPage from "./pages/headToHead.js";
import * as playoffsPage from "./pages/playoffs.js";
import * as playerImpactPage from "./pages/playerImpact.js";
import * as currentSeasonPage from "./pages/currentSeason.js";
import * as whatIfPage from "./pages/whatIf.js";
import * as playersPage from "./pages/players.js";
import * as sandboxPage from "./pages/sandbox.js";

const icon = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">${d}</svg>`;

const ICONS = {
  home: icon('<path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 22V12h6v10"/>'),
  matchups: icon('<path d="M13 2 4 14h6l-1 8 9-12h-6l1-8Z"/>'),
  teams: icon('<circle cx="9" cy="8" r="3"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><circle cx="17.5" cy="9" r="2.4"/><path d="M15.7 13.2A5.6 5.6 0 0 1 21.5 20"/>'),
  players: icon('<rect x="4" y="3" width="16" height="18" rx="1"/><circle cx="12" cy="10" r="3"/><path d="M7.5 18a4.5 4.5 0 0 1 9 0"/>'),
  sandbox: icon('<path d="M4 19h16"/><path d="M6 19v-4h4v4M14 19v-7h4v7"/><path d="M9 9l3-5 3 5M12 4v8"/>'),
  season: icon('<rect x="3.5" y="5" width="17" height="15" rx="1.5"/><path d="M3.5 10h17M8 3v4M16 3v4"/><path d="M8 15h3M13 15h3"/>'),
  playoffs: icon('<path d="M3 4h5v5H3M3 15h5v5H3M8 6.5h3v11H8M11 12h4M16 9.5h5v5h-5"/>'),
  ask: icon('<path d="M4 5.5h16v10.2H9.8L5 20V15.7H4Z"/><path d="M8 9.6h8M8 12.6h5"/>'),
  more: icon('<circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/>'),
  currentSeason: icon('<rect x="3.5" y="5" width="17" height="15" rx="1.5"/><path d="M3.5 10h17M8 3v4M16 3v4"/><circle cx="12" cy="15" r="1.6"/>'),
  headToHead: icon('<circle cx="6" cy="12" r="3.2"/><circle cx="18" cy="12" r="3.2"/><path d="M9.2 12h5.6"/>'),
  league: icon('<path d="M7 4h10v4a5 5 0 0 1-10 0V4Z"/><path d="M7 5H4a1 1 0 0 0-1 1v1a4 4 0 0 0 4 4M17 5h3a1 1 0 0 1 1 1v1a4 4 0 0 1-4 4"/><path d="M12 13v3M9 20h6M10 17h4"/>'),
  playerImpact: icon('<circle cx="9" cy="8" r="3.2"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M18 8v6M15 11h6"/>'),
  whatIf: icon('<path d="M7 7h10l-3-3M17 17H7l3 3"/><circle cx="12" cy="12" r="1.4"/>'),
};

// `short` is the label on the bar; `label` is used in the page kicker and menu.
const ROUTES = [
  { path: "/dashboard", label: "Home", short: "Home", icon: ICONS.home, page: dashboardPage, bar: true },
  { path: "/matchups", label: "Matchups", short: "Matchups", icon: ICONS.matchups, page: matchupsPage, bar: true },
  { path: "/teams", label: "Teams", short: "Teams", icon: ICONS.teams, page: teamsPage, bar: true },
  { path: "/players", label: "Players", short: "Players", icon: ICONS.players, page: playersPage, bar: true },
  { path: "/sandbox", label: "Sandbox", short: "Sandbox", icon: ICONS.sandbox, page: sandboxPage, bar: true },
  { path: "/simulator", label: "Season Simulator", short: "Season", icon: ICONS.season, page: simulatorPage, bar: true },
  { path: "/playoffs", label: "Playoffs", short: "Playoffs", icon: ICONS.playoffs, page: playoffsPage, bar: true },
  { path: "/assistant", label: "Assistant", short: "Ask", icon: ICONS.ask, page: assistantPage, bar: true },
  { path: "/current-season", label: "Current Season", icon: ICONS.currentSeason, page: currentSeasonPage },
  { path: "/head-to-head", label: "Head-to-Head", icon: ICONS.headToHead, page: headToHeadPage },
  { path: "/league-predictions", label: "League Predictions", icon: ICONS.league, page: leaguePage },
  { path: "/player-impact", label: "Player Impact", icon: ICONS.playerImpact, page: playerImpactPage },
  { path: "/what-if", label: "What-if Lab", icon: ICONS.whatIf, page: whatIfPage },
];

const BAR = ROUTES.filter((route) => route.bar);
const MORE = ROUTES.filter((route) => !route.bar);

// ---------------------------------------------------------------------------
// The Limelight nav: a light bar that slides to the active tab, with its beam
// falling on the icon (a plain-JS port of the street-zine nav).
// ---------------------------------------------------------------------------

function buildNav() {
  const nav = document.querySelector("#zn");
  nav.innerHTML = `
    ${BAR.map((route) => `<a class="zi" href="#${route.path}" data-path="${route.path}" title="${route.label}">
      ${route.icon}<span>${route.short}</span></a>`).join("")}
    <div class="zmore-wrap">
      <button class="zi" type="button" id="zmore" aria-expanded="false" aria-controls="zmenu" title="More pages">
        ${ICONS.more}<span>More</span></button>
    </div>
    <div class="zlime" aria-hidden="true"></div>`;
  document.body.insertAdjacentHTML("beforeend", `<ul class="zmenu" id="zmenu" hidden>
    ${MORE.map((route) => `<li><a href="#${route.path}" data-path="${route.path}">${route.icon}${route.label}</a></li>`).join("")}
  </ul>`);

  const go = (event, path) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
    event.preventDefault();
    closeMenu();
    navigate(path);
  };
  document.querySelectorAll(".zi[data-path], .zmenu a").forEach((el) =>
    el.addEventListener("click", (event) => go(event, el.dataset.path)));

  const more = document.querySelector("#zmore");
  const menu = document.querySelector("#zmenu");
  more.addEventListener("click", () => (menu.hidden ? openMenu() : closeMenu()));
  document.addEventListener("click", (event) => {
    if (!menu.hidden && !menu.contains(event.target) && !more.contains(event.target)) closeMenu();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !menu.hidden) {
      closeMenu();
      more.focus();
    }
  });
  window.addEventListener("resize", () => {
    placeLime(false);
    if (!menu.hidden) positionMenu();
  });
  if (document.fonts?.ready) document.fonts.ready.then(() => placeLime(false));
}

function positionMenu() {
  const more = document.querySelector("#zmore");
  const menu = document.querySelector("#zmenu");
  const rect = more.getBoundingClientRect();
  const width = menu.offsetWidth || 240;
  menu.style.left = `${Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8))}px`;
}

function openMenu() {
  const menu = document.querySelector("#zmenu");
  menu.hidden = false;
  positionMenu();
  document.querySelector("#zmore").setAttribute("aria-expanded", "true");
  menu.querySelector("a")?.focus();
}

function closeMenu() {
  const menu = document.querySelector("#zmenu");
  if (!menu || menu.hidden) return;
  menu.hidden = true;
  document.querySelector("#zmore").setAttribute("aria-expanded", "false");
}

let activeItem = null;

function placeLime(animate) {
  const lime = document.querySelector(".zlime");
  if (!lime || !activeItem) return;
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  lime.classList.toggle("ready", animate && !reduce);
  // Measured against the nav (the More button sits inside its own wrapper).
  const nav = lime.parentElement;
  const item = activeItem.getBoundingClientRect();
  const left = item.left - nav.getBoundingClientRect().left - nav.clientLeft;
  lime.style.left = `${left + item.width / 2 - lime.offsetWidth / 2}px`;
}

function setActiveNav(path) {
  const inMore = MORE.some((route) => route.path === path);
  document.querySelectorAll(".zi[data-path], .zmenu a").forEach((el) => {
    if (el.dataset.path === path) el.setAttribute("aria-current", "page");
    else el.removeAttribute("aria-current");
  });
  const more = document.querySelector("#zmore");
  if (inMore) more.setAttribute("aria-current", "page");
  else more.removeAttribute("aria-current");
  const first = activeItem === null;
  activeItem = inMore ? more : document.querySelector(`.zi[data-path="${path}"]`);
  placeLime(!first);
}

// ---------------------------------------------------------------------------
// Page head: kicker, the big title with its last word on a torn red strip,
// and the lede.
// ---------------------------------------------------------------------------

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function setPageHead(route) {
  const meta = route.page.meta || { title: route.label, subtitle: "" };
  const words = String(meta.title).split(" ");
  const last = words.pop();
  const title = document.querySelector("#topbar-title");
  title.innerHTML = `${words.length ? `${escapeHtml(words.join(" "))} ` : ""}<span class="hl">${escapeHtml(last)}</span>`;
  const index = ROUTES.indexOf(route) + 1;
  document.querySelector("#topbar-kicker").textContent = `Page ${index} of ${ROUTES.length} · ${route.label}`;
  document.querySelector("#topbar-subtitle").textContent = meta.subtitle || "";
  // Pages may refine this once their data loads (e.g. a player's name).
  document.title = route.path === "/dashboard" ? "Backboard — NBA Analytics" : `${meta.title} · Backboard`;
}

// Optional cover: a page module may export `renderHero(slot, ctx)`. When it
// does, the cover (with its own <h1>) stands in for the page head.
function setHero(route, ctx) {
  const slot = document.querySelector("#app-hero");
  const head = document.querySelector("#page-head");
  const hasHero = typeof route.page.renderHero === "function";
  slot.hidden = !hasHero;
  head.hidden = hasHero;
  if (hasHero) route.page.renderHero(slot, ctx);
  else slot.innerHTML = "";
}

function mountRoute(path) {
  const route = ROUTES.find((r) => r.path === path) || ROUTES[0];
  const ctx = { navigate, query: currentQuery() };
  closeMenu();
  setActiveNav(route.path);
  setPageHead(route);
  setHero(route, ctx);
  route.page.render(document.querySelector("#app-content"), ctx);
}

async function loadStatus() {
  const pill = document.querySelector("#sidebar-status");
  try {
    const res = await getHealth();
    pill.innerHTML = res.ok && res.data?.status === "ok"
      ? `<span class="status-dot ok"></span> Engine online`
      : `<span class="status-dot error"></span> Engine offline`;
  } catch (err) {
    pill.innerHTML = `<span class="status-dot error"></span> Engine offline`;
  }
}

function init() {
  buildNav();
  loadStatus();
  onRouteChange((path) => {
    const previous = location.hash;
    mountRoute(path);
    // A new page starts at the top (query-only changes keep the scroll).
    if (previous.split("?")[0] !== init.lastPath) window.scrollTo({ top: 0, behavior: "instant" });
    init.lastPath = previous.split("?")[0];
  });
  startRouter("/dashboard");
}

document.addEventListener("DOMContentLoaded", init);
