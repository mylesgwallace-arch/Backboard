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
import { createPlaceholderPage } from "./pages/placeholder.js";

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
};

const playerImpactPage = createPlaceholderPage({
  title: "Player Impact",
  subtitle: "Association-only diagnostics for a player's on/off impact.",
  description:
    "Explore a player's estimated association with team performance, and layer a player scenario onto a specific matchup prediction.",
  backedByTools: ["player_impact", "player_scenario"],
});

const ROUTES = [
  { path: "/dashboard", label: "Dashboard", icon: ICONS.dashboard, page: dashboardPage },
  { path: "/matchups", label: "Matchups", icon: ICONS.matchups, page: matchupsPage },
  { path: "/teams", label: "Teams", icon: ICONS.teams, page: teamsPage },
  { path: "/simulator", label: "Season Simulator", icon: ICONS.simulator, page: simulatorPage },
  { path: "/player-impact", label: "Player Impact", icon: ICONS.playerImpact, page: playerImpactPage, badge: "Soon" },
  { path: "/league-predictions", label: "League Predictions", icon: ICONS.league, page: leaguePage },
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

function mountRoute(path) {
  const route = ROUTES.find((r) => r.path === path) || ROUTES[0];
  setActiveNav(route.path);
  setTopbar(route);
  const content = document.querySelector("#app-content");
  route.page.render(content, { navigate, query: currentQuery() });
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
  startRouter("/matchups"); // Matchup Predictor is the primary landing experience.
}

document.addEventListener("DOMContentLoaded", init);
