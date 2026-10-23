// main.js — app shell: the Limelight nav, the page head, route mounting, and
// the site-wide conveniences (search, theme, keyboard shortcuts, motion).
//
// Scalability note: adding a new page means (1) creating a page module with
// `render(container, ctx)` [+ optional `meta` and `renderHero`], (2) adding
// one entry to ROUTES below, and (3) adding its path to APP_PAGES in
// src/api.py (a test keeps the two lists equal). Pages on the bar get
// `bar: true`; the rest are listed under "More" in their `group`. Page
// modules load on first visit (and are prefetched on hover).

import { startRouter, onRouteChange, navigate, currentQuery } from "./router.js";
import { getHealth } from "./api.js";
import { initSearch, openSearch } from "./components/search.js";
import { applyTheme, favoriteTeam, setSound, setTheme, soundOn, theme } from "./components/prefs.js";
import { alertsOn, initAlerts, openAlertSettings, toggleAlerts } from "./components/alerts.js";
import { toast } from "./components/toast.js";
import { countUpAll, reduceMotion, reveal } from "./components/motion.js";

const icon = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">${d}</svg>`;

const ICONS = {
  home: icon('<path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 22V12h6v10"/>'),
  tonight: icon('<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5Z"/><path d="M15 3.5v3M13.5 5h3"/>'),
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
  standings: icon('<path d="M4 5h16M4 10h16M4 15h10M4 20h7"/><path d="m17 16 2 2 3-4"/>'),
  leaders: icon('<path d="M4 20V11h4v9M10 20V5h4v15M16 20v-6h4v6"/>'),
  compare: icon('<path d="M8 3 3 8l5 5M16 11l5 5-5 5"/><path d="M3 8h13M21 16H8"/>'),
  report: icon('<path d="M6 3h12v18l-3-2-3 2-3-2-3 2Z"/><path d="m9 10 2 2 4-4M9 15h6"/>'),
  gallery: icon('<rect x="3" y="4" width="8" height="7"/><rect x="13" y="4" width="8" height="7"/><rect x="3" y="13" width="8" height="7"/><rect x="13" y="13" width="8" height="7"/>'),
  market: icon('<path d="M3 17l5-5 4 3 8-8"/><path d="M15 7h5v5"/><path d="M3 21h18"/>'),
  awards: icon('<circle cx="12" cy="9" r="5.5"/><path d="m9 14-1.5 7L12 19l4.5 2L15 14"/><path d="m12 6.5.9 1.8 2 .3-1.4 1.4.3 2-1.8-1-1.8 1 .3-2-1.4-1.4 2-.3Z"/>'),
  lottery: icon('<circle cx="12" cy="11" r="7.5"/><circle cx="9.5" cy="9.5" r="1.6"/><circle cx="14" cy="12.5" r="1.6"/><circle cx="10" cy="14" r="1.2"/><path d="M12 18.5V22M9 22h6"/>'),
  recap: icon('<path d="M5 3h11l3 3v15H5Z"/><path d="M8 8h7M8 12h8M8 16h5"/>'),
  pickem: icon('<path d="M4 12.5 9 17 20 6"/><circle cx="12" cy="12" r="10"/>'),
  bell: icon('<path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4Z"/><path d="M10 20a2 2 0 0 0 4 0"/>'),
  search: icon('<circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.5 15.5 5 5"/>'),
  sun: icon('<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>'),
  moon: icon('<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5Z"/>'),
};

const lazy = (load) => {
  let promise = null;
  return () => (promise ||= load());
};

// `short` is the label on the bar; `label` is used in the page kicker, the
// More slip and search; `description` says what the page is for (and how it
// differs from its neighbours) in the More slip and in search.
const ROUTES = [
  { path: "/dashboard", label: "Home", short: "Home", icon: ICONS.home, bar: true,
    load: lazy(() => import("./pages/dashboard.js")) },
  { path: "/tonight", label: "Tonight", short: "Tonight", icon: ICONS.tonight, bar: true,
    description: "Every game today: the model's pick, live win probability, finals",
    load: lazy(() => import("./pages/tonight.js")) },
  { path: "/matchups", label: "Matchups", short: "Matchups", icon: ICONS.matchups, bar: true,
    description: "Any two teams: win probability and projected score",
    load: lazy(() => import("./pages/matchups.js")) },
  { path: "/teams", label: "Teams", short: "Teams", icon: ICONS.teams, bar: true,
    description: "A franchise's strength, schedule, projections and history",
    load: lazy(() => import("./pages/teams.js")) },
  { path: "/players", label: "Players", short: "Players", icon: ICONS.players, bar: true,
    description: "Choose a player: seasons, game logs, percentiles, outlook",
    load: lazy(() => import("./pages/players.js")) },
  { path: "/sandbox", label: "Sandbox", short: "Sandbox", icon: ICONS.sandbox, bar: true,
    description: "Full scenarios: any trades, signings and injuries, then the season",
    load: lazy(() => import("./pages/sandbox.js")) },
  { path: "/playoffs", label: "Playoffs", short: "Playoffs", icon: ICONS.playoffs, bar: true,
    description: "Playoff and title odds, and how they moved",
    load: lazy(() => import("./pages/playoffs.js")) },
  { path: "/assistant", label: "Assistant", short: "Ask", icon: ICONS.ask, bar: true,
    description: "Ask in plain words; every number traced to its tool",
    load: lazy(() => import("./pages/assistant.js")) },
  { path: "/standings", label: "Standings", icon: ICONS.standings, group: "League",
    description: "Records, games behind, streaks and schedule difficulty",
    load: lazy(() => import("./pages/standings.js")) },
  { path: "/leaders", label: "League Leaders", icon: ICONS.leaders, group: "League",
    description: "Who leads in scoring, shooting and box impact",
    load: lazy(() => import("./pages/leaders.js")) },
  { path: "/report-card", label: "Model Report Card", icon: ICONS.report, group: "League",
    description: "How the model's pregame picks have actually done",
    load: lazy(() => import("./pages/reportCard.js")) },
  { path: "/current-season", label: "Current Season", icon: ICONS.currentSeason, group: "League",
    description: "Strength now, roster changes and injuries",
    load: lazy(() => import("./pages/currentSeason.js")) },
  { path: "/league-predictions", label: "League Predictions", icon: ICONS.league, group: "League",
    description: "The projected playoff picture at a glance",
    load: lazy(() => import("./pages/league.js")) },
  { path: "/simulator", label: "Season Simulator", icon: ICONS.season, group: "League",
    description: "Run the season yourself: replay or project from a date",
    load: lazy(() => import("./pages/simulator.js")) },
  { path: "/market", label: "Model vs Market", icon: ICONS.market, group: "League",
    description: "The model against the sportsbooks: gaps and a running score",
    load: lazy(() => import("./pages/market.js")) },
  { path: "/lottery", label: "Draft Lottery", icon: ICONS.lottery, group: "League",
    description: "Odds at every pick, and a lottery machine to run the draw",
    load: lazy(() => import("./pages/lottery.js")) },
  { path: "/recap", label: "Season Recap", icon: ICONS.recap, group: "League",
    description: "The season in data: upsets, surprises, best and worst calls",
    load: lazy(() => import("./pages/recap.js")) },
  { path: "/compare", label: "Compare Players", icon: ICONS.compare, group: "Players",
    description: "Two players, VS screen: ratings, numbers, career arcs",
    load: lazy(() => import("./pages/compare.js")) },
  { path: "/awards", label: "Awards Race", icon: ICONS.awards, group: "Players",
    description: "MVP, rookie, sixth man and most improved, week by week",
    load: lazy(() => import("./pages/awards.js")) },
  { path: "/player-impact", label: "Player Impact", icon: ICONS.playerImpact, group: "Players",
    description: "With and without a player (a diagnostic, not a forecast)",
    load: lazy(() => import("./pages/playerImpact.js")) },
  { path: "/what-if", label: "What-if Lab", icon: ICONS.whatIf, group: "Players",
    description: "Quick swap: one player's season, any era, on one team",
    load: lazy(() => import("./pages/whatIf.js")) },
  { path: "/head-to-head", label: "Head-to-Head", icon: ICONS.headToHead, group: "Teams",
    description: "A whole rivalry: every meeting, streaks, series, the model's record",
    load: lazy(() => import("./pages/headToHead.js")) },
  { path: "/gallery", label: "Scenario Gallery", icon: ICONS.gallery, group: "Sandbox",
    description: "Ready-made scenarios and the ones you saved",
    load: lazy(() => import("./pages/gallery.js")) },
  { path: "/pickem", label: "Pick'em", icon: ICONS.pickem, group: "Play",
    description: "Pick the night's winners against the model",
    load: lazy(() => import("./pages/pickem.js")) },
];

const BAR = ROUTES.filter((route) => route.bar);
const MORE = ROUTES.filter((route) => !route.bar);
const GROUPS = [...new Set(MORE.map((route) => route.group))];

// ---------------------------------------------------------------------------
// The Limelight nav: a light bar that slides to the active tab, with its beam
// falling on the icon (a plain-JS port of the street-zine nav).
// ---------------------------------------------------------------------------

function buildNav() {
  const nav = document.querySelector("#zn");
  nav.innerHTML = `
    ${BAR.map((route) => `<a class="zi" href="${route.path}" data-path="${route.path}" title="${route.label}">
      ${route.icon}<span>${route.short}</span></a>`).join("")}
    <div class="zmore-wrap">
      <button class="zi" type="button" id="zmore" aria-expanded="false" aria-controls="zmenu" title="More pages">
        ${ICONS.more}<span>More</span></button>
    </div>
    <div class="zlime" aria-hidden="true"></div>`;
  document.body.insertAdjacentHTML("beforeend", `<div class="zmenu" id="zmenu" hidden>
    <button type="button" class="zmenu-search" data-action="search">${ICONS.search}<span>Search players, teams, pages</span><kbd>/</kbd></button>
    <button type="button" class="zmenu-search zmenu-theme" data-action="theme">${ICONS.sun}<span>Switch edition (day / night)</span></button>
    <button type="button" class="zmenu-search zmenu-alerts" data-action="alerts">${ICONS.bell}<span>Alerts: close finishes, your team's odds</span></button>
    <div class="zmenu-groups">
      ${GROUPS.map((group) => `<section class="zmenu-group" aria-label="${group}">
        <h3>${group}</h3>
        <ul>${MORE.filter((r) => r.group === group).map((route) => `<li><a href="${route.path}" data-path="${route.path}">
          ${route.icon}<span><b>${route.label}</b><small>${route.description}</small></span></a></li>`).join("")}</ul>
      </section>`).join("")}
    </div>
  </div>`);

  document.querySelectorAll(".zi[data-path], .zmenu a").forEach((el) => {
    el.addEventListener("click", closeMenu);
    el.addEventListener("pointerenter", () => ROUTES.find((r) => r.path === el.dataset.path)?.load(), { once: true });
  });
  document.querySelector('.zmenu-search[data-action="search"]').addEventListener("click", () => {
    closeMenu();
    openSearch();
  });
  document.querySelector('.zmenu-search[data-action="theme"]').addEventListener("click", () => {
    closeMenu();
    document.querySelector("#ztheme")?.click();
  });
  document.querySelector('.zmenu-search[data-action="alerts"]').addEventListener("click", () => {
    closeMenu();
    openAlertSettings();
  });

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

function buildTools() {
  const tools = document.querySelector("#ztools");
  tools.innerHTML = `
    <button type="button" class="ztool" id="zsearch" title="Search (/ or Ctrl K)" aria-label="Search">
      ${ICONS.search}<span class="ztool-label">Search</span><kbd>/</kbd></button>
    <button type="button" class="ztool" id="ztheme" aria-label="Switch theme"></button>`;
  tools.querySelector("#zsearch").addEventListener("click", openSearch);
  const themeButton = tools.querySelector("#ztheme");
  const paint = () => {
    const light = theme() === "light";
    themeButton.innerHTML = light ? ICONS.moon : ICONS.sun;
    themeButton.title = light ? "Switch to the black (night) edition" : "Switch to the newsprint (day) edition";
    themeButton.setAttribute("aria-pressed", String(light));
  };
  themeButton.addEventListener("click", () => {
    setTheme(theme() === "light" ? "dark" : "light");
    paint();
  });
  paint();
}

function positionMenu() {
  const more = document.querySelector("#zmore");
  const menu = document.querySelector("#zmenu");
  const rect = more.getBoundingClientRect();
  const width = menu.offsetWidth || 320;
  menu.style.left = `${Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8))}px`;
}

function openMenu() {
  const menu = document.querySelector("#zmenu");
  menu.hidden = false;
  positionMenu();
  document.querySelector("#zmore").setAttribute("aria-expanded", "true");
  requestAnimationFrame(() => menu.classList.add("is-open"));
  menu.querySelector("button, a")?.focus();
}

function closeMenu() {
  const menu = document.querySelector("#zmenu");
  if (!menu || menu.hidden) return;
  menu.hidden = true;
  menu.classList.remove("is-open");
  document.querySelector("#zmore").setAttribute("aria-expanded", "false");
}

let activeItem = null;

function placeLime(animate) {
  const lime = document.querySelector(".zlime");
  if (!lime || !activeItem) return;
  lime.classList.toggle("ready", animate && !reduceMotion());
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

function setPageHead(route, page) {
  const meta = page.meta || { title: route.label, subtitle: "" };
  const words = String(meta.title).split(" ");
  const last = words.pop();
  const title = document.querySelector("#topbar-title");
  title.innerHTML = `${words.length ? `${escapeHtml(words.join(" "))} ` : ""}<span class="hl">${escapeHtml(last)}</span>`;
  document.querySelector("#topbar-kicker").textContent = route.group
    ? `${route.group} · ${route.label}` : `Backboard · ${route.label}`;
  document.querySelector("#topbar-subtitle").textContent = meta.subtitle || "";
  // Pages may refine this once their data loads (e.g. a player's name).
  document.title = route.path === "/dashboard" ? "Backboard — NBA Analytics" : `${meta.title} · Backboard`;
}

// Optional cover: a page module may export `renderHero(slot, ctx)`. When it
// does, the cover (with its own <h1>) stands in for the page head.
function setHero(page, ctx) {
  const slot = document.querySelector("#app-hero");
  const head = document.querySelector("#page-head");
  const hasHero = typeof page.renderHero === "function";
  slot.hidden = !hasHero;
  head.hidden = hasHero;
  if (hasHero) page.renderHero(slot, ctx);
  else slot.innerHTML = "";
}

// A page can drop the page head for a full-screen layout of its own (the
// character select, the VS screen): ctx.setHeadless(true) during render.
function setHeadless(on) {
  const head = document.querySelector("#page-head");
  const main = document.querySelector("#app-main");
  if (on) head.hidden = true;
  else head.hidden = !document.querySelector("#app-hero").hidden;
  main.classList.toggle("is-headless", Boolean(on));
}

let mountToken = 0;
let mountedPath = null;

async function mountRoute(path, kind) {
  const route = ROUTES.find((r) => r.path === path) || ROUTES[0];
  const token = ++mountToken;
  // A query-only change (picking a player, a team) keeps the scroll and skips
  // the page transition.
  const newPage = route.path !== mountedPath;
  mountedPath = route.path;
  closeMenu();
  setActiveNav(route.path);
  const content = document.querySelector("#app-content");
  let page;
  try {
    page = await route.load();
  } catch (err) {
    content.innerHTML = `<div class="error-banner" role="alert">⚠ This page failed to load (${escapeHtml(err.message)}). Reload to try again.</div>`;
    return;
  }
  if (token !== mountToken) return; // a newer navigation won
  const ctx = { navigate, query: currentQuery(), setHeadless };
  const swap = () => {
    setPageHead(route, page);
    setHero(page, ctx);
    setHeadless(false);
    page.render(content, ctx);
    if (newPage && kind !== "replace" && kind !== "load") window.scrollTo({ top: 0, behavior: "instant" });
  };
  const animated = newPage && (kind === "push" || kind === "pop");
  if (animated && document.startViewTransition && !reduceMotion()) {
    document.documentElement.dataset.nav = kind === "pop" ? "back" : "forward";
    document.startViewTransition(swap);
  } else {
    swap();
  }
  if (kind === "load" || animated) document.querySelector("#app-main")?.focus({ preventScroll: true });
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

// ---------------------------------------------------------------------------
// Site-wide motion: sheets drop in as they scroll into view, big numbers
// marked data-count count up once. Pages render asynchronously, so new
// sheets are picked up as they appear.
// ---------------------------------------------------------------------------

function watchContent() {
  const content = document.querySelector("#app-content");
  const pick = () => {
    const sheets = content.querySelectorAll(":scope > .card, :scope > section > .card, :scope > div > .card, .reveal-me");
    const below = [...sheets].filter((el) => el.getBoundingClientRect().top > window.innerHeight * 0.9);
    reveal(below);
    countUpAll(content);
  };
  let queued = false;
  new MutationObserver(() => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      pick();
    });
  }).observe(content, { childList: true, subtree: true });
}

function buildBackToTop() {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "to-top";
  button.title = "Back to the top";
  button.setAttribute("aria-label", "Back to the top");
  button.innerHTML = icon('<path d="M12 19V5M5 12l7-7 7 7"/>');
  button.hidden = true;
  button.addEventListener("click", () => window.scrollTo({ top: 0, behavior: reduceMotion() ? "instant" : "smooth" }));
  document.body.appendChild(button);
  let ticking = false;
  window.addEventListener("scroll", () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => {
      ticking = false;
      button.hidden = window.scrollY < window.innerHeight * 1.4;
    });
  }, { passive: true });
}

// ---------------------------------------------------------------------------
// Installable app and offline: the service worker (web/sw.js) keeps the last
// data each page loaded; when it answers from that copy, a bar says so.
// ---------------------------------------------------------------------------

let installPrompt = null;

function registerServiceWorker() {
  if (!("serviceWorker" in navigator) || !window.isSecureContext) return;
  navigator.serviceWorker.register("/sw.js").catch(() => {});
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    installPrompt = event;
  });
}

function watchConnection() {
  let bar = null;
  const show = (text) => {
    bar ||= Object.assign(document.createElement("div"), { className: "offline-bar", role: "status" });
    bar.textContent = text;
    if (!bar.isConnected) document.body.appendChild(bar);
  };
  const hide = () => bar?.remove();
  window.addEventListener("backboard:offline", (event) => {
    const saved = Date.parse(event.detail);
    const when = Number.isFinite(saved) ? new Date(saved).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "earlier";
    show(`Offline · showing data saved ${when}`);
  });
  window.addEventListener("offline", () => show("Offline · pages you have opened still work"));
  window.addEventListener("online", hide);
  onRouteChange(() => { if (navigator.onLine) hide(); });
}

/** Commands in the palette (type ">" to list them). */
function paletteCommands() {
  return [
    { label: () => (theme() === "light" ? "Switch to the night edition" : "Switch to the day edition"), keywords: "theme dark light mode",
      run: () => document.querySelector("#ztheme")?.click() },
    { label: () => (alertsOn() ? "Turn alerts off" : "Turn alerts on"), keywords: "notifications alerts close games", run: () => toggleAlerts() },
    { label: "Alert settings…", keywords: "notifications alerts", run: openAlertSettings },
    { label: () => (soundOn() ? "Turn arcade sounds off" : "Turn arcade sounds on"), keywords: "sound audio mute",
      run: () => { setSound(!soundOn()); toast(soundOn() ? "Sounds on." : "Sounds off."); } },
    { label: "Go to my team", keywords: "favorite favourite", when: () => Boolean(favoriteTeam()),
      run: () => navigate("/teams", { team: favoriteTeam() }) },
    { label: "Copy a link to this page", keywords: "share url",
      run: () => navigator.clipboard?.writeText(location.href).then(() => toast("Link copied."), () => toast("Could not copy the link.", { tone: "warn" })) },
    { label: "Print this page", keywords: "pdf paper", run: () => window.print() },
    { label: "Install Backboard as an app", keywords: "pwa install home screen", when: () => Boolean(installPrompt),
      run: async () => { installPrompt.prompt(); await installPrompt.userChoice.catch(() => null); installPrompt = null; } },
    { label: "Run the draft lottery", keywords: "lottery draft", run: () => navigate("/lottery") },
    { label: "Make tonight's picks", keywords: "pickem pick em game", run: () => navigate("/pickem") },
  ];
}

function init() {
  applyTheme();
  buildNav();
  buildTools();
  buildBackToTop();
  initSearch({ routes: ROUTES, navigate, actions: paletteCommands() });
  registerServiceWorker();
  watchConnection();
  initAlerts();
  loadStatus();
  watchContent();
  onRouteChange((path, kind) => mountRoute(path, kind));
  startRouter("/dashboard", ROUTES.map((route) => route.path));
}

document.addEventListener("DOMContentLoaded", init);
