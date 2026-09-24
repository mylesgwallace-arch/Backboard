// pages/dashboard.js — overview + entry points into the other tools, plus a
// preserved "ask a question / run a raw tool" panel (the original
// web/index.html capability) so no existing functionality is lost while the
// structured UI grows.

import { getHealth, getTools, ask, runTool, ingest } from "../api.js";
import { renderHero as heroMarkup, wireHero } from "../components/hero.js";

export const meta = {
  title: "Dashboard",
  subtitle: "System status and quick access to the analytics tools.",
};

const QUICK_LINKS = [
  {
    path: "/matchups",
    title: "Matchup Predictor",
    description: "Predict any NBA matchup with the production model.",
    available: true,
  },
  {
    path: "/teams",
    title: "Team Explorer",
    description: "Browse team records, form, Elo rating, and head-to-head history.",
    available: true,
  },
  {
    path: "/simulator",
    title: "Season Simulator",
    description: "Monte Carlo season projection: standings, seeds, playoff odds.",
    available: true,
  },
  {
    path: "/head-to-head",
    title: "Head-to-Head",
    description: "Compare any two franchises' record, all-time and season by season.",
    available: true,
  },
  {
    path: "/player-impact",
    title: "Player Impact",
    description: "Association-only diagnostics for a player's on/off impact.",
    available: false,
  },
  {
    path: "/league-predictions",
    title: "League Predictions",
    description: "League-wide standings and playoff-field projections.",
    available: true,
  },
  {
    path: "/assistant",
    title: "Assistant",
    description: "Ask a plain-language question and get an answer traced to a tool call.",
    available: true,
  },
];

// Holdout accuracy of the production model (elo_boosted_ensemble) from
// models/baseline_metrics.json: metrics.elo_boosted_ensemble.accuracy =
// 0.6508 over test_games = 13,332 (chronological 20% holdout). The API
// doesn't expose this file, so update these two values if the model changes.
const HOLDOUT_ACCURACY = "65.1%";
const HOLDOUT_GAMES = "13,332";

/** Landing hero, mounted by main.js above the page (replaces the masthead). */
export function renderHero(slot) {
  slot.innerHTML = heroMarkup({
    tag: "Gameday edition",
    headline: ["Know", "WHO", "Wins."],
    deck: "Win probabilities for any NBA matchup from a validated model, plus team form and full-season simulations.",
    cta: { href: "#/matchups", label: "Predict a matchup" },
    stat: { value: HOLDOUT_ACCURACY, label: `Picks right on ${HOLDOUT_GAMES} held-out games` },
    // Ambient loop printed into the orange slab (see web/media/README.md).
    // H.264 first (smaller here); VP9 for browsers without H.264.
    footage: {
      poster: "/media/hero-loop-poster.jpg",
      sources: [
        { src: "/media/hero-loop.mp4", type: 'video/mp4; codecs="avc1.64001E"' },
        { src: "/media/hero-loop.webm", type: 'video/webm; codecs="vp9"' },
      ],
    },
  });
  wireHero(slot);
}

export function render(container, { navigate } = {}) {
  container.innerHTML = `
    <div id="dashboard-headline-mount">${renderHeadlineSkeleton()}</div>

    <div class="card fade-in">
      <div class="card-header">
        <div>
          <h2>Platform status</h2>
          <p class="card-subtitle">Live status of the analytics API and the production model.</p>
        </div>
      </div>
      <div id="health-mount" class="stat-grid">
        ${statTileSkeleton("Service")}
        ${statTileSkeleton("Production model")}
        ${statTileSkeleton("Tools available")}
      </div>
    </div>

    <div class="card fade-in">
      <div class="card-header">
        <div>
          <h2>Explore the platform</h2>
          <p class="card-subtitle">Jump into a structured analytics tool.</p>
        </div>
      </div>
      <div class="quick-grid">
        ${QUICK_LINKS.map(renderQuickCard).join("")}
      </div>
    </div>

    <div class="card fade-in">
      <details class="collapsible">
        <summary>Advanced: ask a question or run a raw tool</summary>
        <div class="grid-2">
          <div>
            <div class="field">
              <label for="ask-input">Ask a question</label>
              <textarea class="input" id="ask-input" rows="2" placeholder="Who is favored in Celtics vs Lakers?"></textarea>
            </div>
            <button class="btn mt-1" id="ask-btn">Ask</button>
            <pre class="output-pane mt-1" id="ask-output">// answer appears here</pre>
          </div>
          <div>
            <div class="field">
              <label for="raw-tool-select">Run a tool directly</label>
              <div class="select-wrap">
                <select class="select" id="raw-tool-select"></select>
              </div>
            </div>
            <div class="field mt-1">
              <label for="raw-tool-params">Parameters (JSON)</label>
              <textarea class="input" id="raw-tool-params" rows="3">{"home_team": "Boston Celtics", "away_team": "Los Angeles Lakers"}</textarea>
            </div>
            <button class="btn mt-1" id="raw-tool-btn">Run tool</button>
            <pre class="output-pane mt-1" id="raw-tool-output">// tool envelope appears here</pre>
          </div>
        </div>
        <div class="mt-2" style="padding-top:1rem; border-top:1px dashed var(--border);">
          <div class="field">
            <label for="ingest-source">Live data refresh — schedule source (URL or local CSV)</label>
            <input type="text" class="input" id="ingest-source" value="data/raw/LeagueSchedule25_26.csv">
          </div>
          <label class="text-muted mt-1" style="display:flex; align-items:center; gap:0.4rem; font-size:0.8rem;">
            <input type="checkbox" id="ingest-dry-run" checked>
            Dry-run (validate + plan only, no database writes)
          </label>
          <button class="btn mt-1" id="ingest-btn">Ingest</button>
          <pre class="output-pane mt-1" id="ingest-output">// ingestion manifest (provenance) appears here</pre>
        </div>
      </details>
    </div>
  `;

  QUICK_LINKS.forEach((link) => {
    const card = container.querySelector(`[data-quick-link="${link.path}"]`);
    if (card && navigate) {
      card.addEventListener("click", () => navigate(link.path));
      // Posters are focusable links, so Enter opens them too.
      card.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          navigate(link.path);
        }
      });
    }
  });

  loadHealth(container);
  wireAssistantPanel(container);
}

function renderQuickCard(link) {
  return `
    <div class="quick-card" data-quick-link="${link.path}" role="link" tabindex="0">
      <h3>${link.title}${link.available ? "" : ' <span class="badge">Soon</span>'}</h3>
      <p>${link.description}</p>
    </div>
  `;
}

function renderHeadlineSkeleton() {
  return `
    <div class="card clipping front-page fade-in" aria-hidden="true">
      <div class="skeleton" style="height:28px; width:200px;"></div>
      <div class="skeleton mt-2" style="height:72px; width:60%;"></div>
      <div class="skeleton mt-1" style="height:18px; width:50%;"></div>
    </div>
  `;
}

/**
 * Front-page headline derived from the /health and /tools responses this page
 * already fetches (no extra requests): the engine is online, online with an
 * empty tool rack, or down (alert state with caution tape).
 */
function renderStatusHeadline(state, { model, toolCount, message } = {}) {
  if (state === "down") {
    return `
      <section class="card clipping front-page front-page--alert fade-in" aria-labelledby="dashboard-headline">
        <div class="clipping-masthead">
          <span class="kicker">Front page</span>
          <span class="clipping-meta">Platform status · /health</span>
        </div>
        <div class="front-page-lead">
          <p class="stamp stamp--tilt front-page-tag">Alert</p>
          <h2 class="tabloid-headline" id="dashboard-headline">API <span class="hl-mark">down</span> <span class="scrawl">timeout!</span></h2>
          <p class="deck">
            The analytics API isn't answering, so predictions, team data and simulations can't load.
            Start the server with <code>python src/api.py</code>, then reload this page.
          </p>
          ${message ? `<p class="clipping-meta mt-1">Details: ${escapeText(message)}</p>` : ""}
        </div>
      </section>
    `;
  }
  if (state === "empty") {
    return `
      <section class="card clipping front-page fade-in" aria-labelledby="dashboard-headline">
        <div class="clipping-masthead">
          <span class="kicker">Front page</span>
          <span class="clipping-meta">Platform status · /health + /tools</span>
        </div>
        <div class="front-page-lead">
          <p class="stamp stamp--tilt stamp--ink front-page-tag">Check</p>
          <h2 class="tabloid-headline" id="dashboard-headline">Tool rack <span class="hl-mark">empty</span></h2>
          <p class="deck">The API is up, but it reported <strong>0</strong> registered tools.</p>
        </div>
      </section>
    `;
  }
  return `
    <section class="card clipping front-page fade-in" aria-labelledby="dashboard-headline">
      <div class="clipping-masthead">
        <span class="kicker">Front page</span>
        <span class="clipping-meta">Platform status · /health + /tools</span>
      </div>
      <div class="front-page-lead">
        <p class="stamp stamp--tilt front-page-tag">Live</p>
        <h2 class="tabloid-headline" id="dashboard-headline">Engine <span class="hl-mark">online</span> <span class="scrawl">game on!</span></h2>
        <p class="deck">
          The production model <code>${escapeText(model || "unknown")}</code> is serving
          <strong>${toolCount}</strong> analytics tools. Start with a matchup, a team, or a full-season simulation.
        </p>
      </div>
    </section>
  `;
}

function escapeText(value) {
  return String(value).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function statTileSkeleton(label) {
  return `
    <div class="stat-tile">
      <div class="stat-label">${label}</div>
      <div class="skeleton mt-1" style="height:22px;"></div>
    </div>
  `;
}

async function loadHealth(container) {
  const mount = container.querySelector("#health-mount");
  const headlineMount = container.querySelector("#dashboard-headline-mount");
  try {
    const [healthRes, toolsRes] = await Promise.all([getHealth(), getTools()]);
    const health = healthRes.data || {};
    const toolCount = (toolsRes.data?.tools || []).length;
    const online = healthRes.ok && health.status === "ok";
    headlineMount.innerHTML = renderStatusHeadline(
      online ? (toolCount > 0 ? "online" : "empty") : "down",
      { model: health.model, toolCount }
    );
    mount.innerHTML = `
      <div class="stat-tile">
        <div class="stat-label">Service</div>
        <div class="stat-value">
          <span class="status-dot ${healthRes.ok ? "ok" : "error"}"></span>
          ${healthRes.ok ? "Online" : "Unavailable"}
        </div>
      </div>
      <div class="stat-tile">
        <div class="stat-label">Production model</div>
        <div class="stat-value" style="font-size:1rem;">${health.model || "—"}</div>
      </div>
      <div class="stat-tile">
        <div class="stat-label">Tools available</div>
        <div class="stat-value">${toolCount}</div>
      </div>
    `;
  } catch (err) {
    headlineMount.innerHTML = renderStatusHeadline("down", { message: err.message });
    mount.innerHTML = `<div class="error-banner">Could not reach the API: ${err.message}</div>`;
  }
}

function wireAssistantPanel(container) {
  const askInput = container.querySelector("#ask-input");
  const askBtn = container.querySelector("#ask-btn");
  const askOutput = container.querySelector("#ask-output");

  askBtn.addEventListener("click", async () => {
    const question = askInput.value.trim();
    if (!question) return;
    askOutput.textContent = "Loading…";
    try {
      const res = await ask(question);
      askOutput.textContent = JSON.stringify(res.data, null, 2);
    } catch (err) {
      askOutput.textContent = `Error: ${err.message}`;
    }
  });

  const toolSelect = container.querySelector("#raw-tool-select");
  const toolParams = container.querySelector("#raw-tool-params");
  const toolBtn = container.querySelector("#raw-tool-btn");
  const toolOutput = container.querySelector("#raw-tool-output");

  getTools()
    .then((res) => {
      const tools = res.data?.tools || [];
      toolSelect.innerHTML = tools
        .map((tool) => `<option value="${tool.name}">${tool.name}</option>`)
        .join("");
    })
    .catch((err) => {
      toolOutput.textContent = `Failed to load tools: ${err.message}`;
    });

  toolBtn.addEventListener("click", async () => {
    const name = toolSelect.value;
    let parameters;
    try {
      parameters = JSON.parse(toolParams.value || "{}");
    } catch (err) {
      toolOutput.textContent = `Invalid JSON parameters: ${err.message}`;
      return;
    }
    toolOutput.textContent = "Loading…";
    try {
      const res = await runTool(name, parameters);
      toolOutput.textContent = JSON.stringify(res.data, null, 2);
    } catch (err) {
      toolOutput.textContent = `Error: ${err.message}`;
    }
  });

  const ingestSource = container.querySelector("#ingest-source");
  const ingestDryRun = container.querySelector("#ingest-dry-run");
  const ingestBtn = container.querySelector("#ingest-btn");
  const ingestOutput = container.querySelector("#ingest-output");

  ingestBtn.addEventListener("click", async () => {
    ingestOutput.textContent = "Loading…";
    try {
      const res = await ingest(ingestSource.value, ingestDryRun.checked);
      ingestOutput.textContent = JSON.stringify(res.data, null, 2);
    } catch (err) {
      ingestOutput.textContent = `Error: ${err.message}`;
    }
  });
}
