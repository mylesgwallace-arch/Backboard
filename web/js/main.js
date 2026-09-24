// main.js — app shell: sidebar navigation, topbar, and route mounting.
//
// Scalability note: adding a new page later means (1) creating a page module
// with `render(container, ctx)` [+ optional `meta`], and (2) adding one entry
// to ROUTES below. No other file needs to change. A future natural-language/
// LLM interface can be added the same way (its own route + page module) and
// would call the exact same `src/api.js` functions — i.e. the same backend
// tool layer — as the structured UI, per the project's architecture.

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

const ICONS = {
  dashboard:
    '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="3" width="7" height="7" rx="1.2"/><rect x="14" y="3" width="7" height="7" rx="1.2"/><rect x="3" y="14" width="7" height="7" rx="1.2"/><rect x="14" y="14" width="7" height="7" rx="1.2"/></svg>',
  matchups:
    '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M13 2 4 14h6l-1 8 9-12h-6l1-8Z"/></svg>',
  teams:
    '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="9" cy="8" r="3"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><circle cx="17.5" cy="9" r="2.4"/><path d="M15.7 13.2A5.6 5.6 0 0 1 21.5 20"/></svg>',
  simulator:
    '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M4 20V10M12 20V4M20 20v-7"/></svg>',
  playerImpact:
    '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="9" cy="8" r="3.2"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M18 8v6M15 11h6" stroke-linecap="round"/></svg>',
  league:
    '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M7 4h10v4a5 5 0 0 1-10 0V4Z"/><path d="M7 5H4a1 1 0 0 0-1 1v1a4 4 0 0 0 4 4M17 5h3a1 1 0 0 1 1 1v1a4 4 0 0 1-4 4"/><path d="M12 13v3M9 20h6M10 17h4" stroke-linecap="round"/></svg>',
  assistant:
    '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M4 5.5h16v10.2H9.8L5 20V15.7H4Z"/><path d="M8 9.6h8M8 12.6h5" stroke-linecap="round"/></svg>',
  headToHead:
    '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="6" cy="12" r="3.2"/><circle cx="18" cy="12" r="3.2"/><path d="M9.2 12h5.6" stroke-linecap="round" stroke-dasharray="1.6 2.4"/></svg>',
  whatIf:
    '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M7 7h10l-3-3M17 17H7l3 3"/><circle cx="12" cy="12" r="1.4"/></svg>',
  currentSeason:
    '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><rect x="3.5" y="5" width="17" height="15" rx="1.5"/><path d="M3.5 10h17M8 3v4M16 3v4"/><circle cx="12" cy="15" r="1.6"/></svg>',
  playoffs:
    '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M3 4h5v5H3M3 15h5v5H3M8 6.5h3v11H8M11 12h4M16 9.5h5v5h-5"/></svg>',
};

const ROUTES = [
  { path: "/dashboard", label: "Dashboard", icon: ICONS.dashboard, page: dashboardPage },
  { path: "/current-season", label: "Current Season", icon: ICONS.currentSeason, page: currentSeasonPage },
  { path: "/matchups", label: "Matchups", icon: ICONS.matchups, page: matchupsPage },
  { path: "/teams", label: "Teams", icon: ICONS.teams, page: teamsPage },
  { path: "/head-to-head", label: "Head-to-Head", icon: ICONS.headToHead, page: headToHeadPage },
  { path: "/simulator", label: "Season Simulator", icon: ICONS.simulator, page: simulatorPage },
  { path: "/playoffs", label: "Playoffs", icon: ICONS.playoffs, page: playoffsPage },
  { path: "/player-impact", label: "Player Impact", icon: ICONS.playerImpact, page: playerImpactPage },
  { path: "/what-if", label: "What-if Lab", icon: ICONS.whatIf, page: whatIfPage },
  { path: "/league-predictions", label: "League Predictions", icon: ICONS.league, page: leaguePage },
  { path: "/assistant", label: "Assistant", icon: ICONS.assistant, page: assistantPage },
];

function buildSidebar() {
  const nav = document.querySelector("#sidebar-nav");
  nav.innerHTML = `
    <div class="nav-section-label">Analytics</div>
    ${ROUTES.map(
      (route) => `
        <a class="nav-item" href="#${route.path}" data-path="${route.path}">
          ${route.icon.replace("<svg ", '<svg aria-hidden="true" focusable="false" ')}
          <span class="nav-label">${route.label}</span>
          ${route.badge ? `<span class="nav-badge">${route.badge}</span>` : ""}
        </a>
      `
    ).join("")}
  `;
  // Nav items are real links (keyboard-focusable); the click handler still
  // routes through navigate() exactly as before.
  nav.querySelectorAll(".nav-item").forEach((el) => {
    el.addEventListener("click", (event) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
      event.preventDefault();
      navigate(el.dataset.path);
    });
  });
}

function setActiveNav(path) {
  document.querySelectorAll(".nav-item").forEach((el) => {
    const isActive = el.dataset.path === path;
    el.classList.toggle("active", isActive);
    if (isActive) {
      el.setAttribute("aria-current", "page");
    } else {
      el.removeAttribute("aria-current");
    }
  });
}

function setTopbar(route) {
  const meta = route.page.meta || { title: route.label, subtitle: "" };
  document.querySelector("#topbar-title").textContent = meta.title;
  document.querySelector("#topbar-subtitle").textContent = meta.subtitle || "";
}

// Optional page hero: a page module may export `renderHero(slot, ctx)`.
// When it does, the hero (with its own <h1>) stands in for the masthead.
function setHero(route, ctx) {
  const slot = document.querySelector("#app-hero");
  const topbar = document.querySelector(".app-topbar");
  const hasHero = typeof route.page.renderHero === "function";
  slot.hidden = !hasHero;
  topbar.hidden = hasHero;
  if (hasHero) {
    route.page.renderHero(slot, ctx);
  } else {
    slot.innerHTML = "";
  }
}

function mountRoute(path) {
  const route = ROUTES.find((r) => r.path === path) || ROUTES[0];
  const ctx = { navigate, query: currentQuery() };
  setActiveNav(route.path);
  setTopbar(route);
  setHero(route, ctx);
  const content = document.querySelector("#app-content");
  route.page.render(content, ctx);
}

async function loadSidebarStatus() {
  const pill = document.querySelector("#sidebar-status");
  try {
    const res = await getHealth();
    if (res.ok && res.data?.status === "ok") {
      pill.innerHTML = `<span class="status-dot ok"></span> API online · ${res.data.model}`;
    } else {
      pill.innerHTML = `<span class="status-dot error"></span> API unreachable`;
    }
  } catch (err) {
    pill.innerHTML = `<span class="status-dot error"></span> API unreachable`;
  }
}

function init() {
  buildSidebar();
  loadSidebarStatus();
  onRouteChange(mountRoute);
  // The Dashboard's hero is the landing page; its one CTA leads to the
  // Matchup Predictor.
  startRouter("/dashboard");
}

document.addEventListener("DOMContentLoaded", init);
