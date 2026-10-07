// pages/dashboard.js — Home: the street-zine cover (torn-letter masthead over
// the dunk montage), the toolbox of torn cards that lead into every page, the
// live engine status, and the preserved "ask a question / run a raw tool"
// panel (the original web/index.html capability).

import { getHealth, getTools, ask, runTool, ingest } from "../api.js";

export const meta = {
  title: "Backboard",
  subtitle: "NBA win probabilities, team and player analytics, and a what-if Sandbox.",
};

// Holdout accuracy of the production model (elo_boosted_ensemble) from
// models/baseline_metrics.json: metrics.elo_boosted_ensemble.accuracy =
// 0.6508 over test_games = 13,332 (chronological 20% holdout). The API
// doesn't expose this file, so update these two values if the model changes.
const HOLDOUT_ACCURACY = "65.1";
const HOLDOUT_GAMES = "13,332";

// Every card's big number is a fact about the engine or the data it covers.
// `big: null` is filled from the live /tools registry.
const TOOLBOX = [
  { path: "/matchups", kick: "Matchups", big: 870, unit: "", h: "Run any game", r: -1.6, cls: "",
    p: "Win chance, projected score and why it leans, for every home and away pairing of the 30 teams." },
  { path: "/players", kick: "Players", big: 1946, year: true, unit: "→ now", h: "Every player", r: 1.4, cls: "y",
    p: "Every season and game since 1946-47, percentiles, the most similar seasons in history and a projection." },
  { path: "/sandbox", kick: "Sandbox", big: 41, unit: "seasons", h: "Make any move", r: -1.1, cls: "r2",
    p: "Trade anyone, sign anyone from any era, then play out next season or replay any season since 1985-86." },
  { path: "/teams", kick: "Teams", big: 30, unit: "", h: "Team explorer", r: 1.2, cls: "",
    p: "Record, recent form, Elo, projections and head-to-head history for every franchise." },
  { path: "/simulator", kick: "Season", big: 1000, unit: "sims", h: "Season and playoffs", r: -1.3, cls: "",
    p: "Monte Carlo seasons: standings, seeds, the play-in, the bracket and title odds." },
  { path: "/assistant", kick: "Ask", big: null, unit: "tools", h: "Ask and audit", r: 1.6, cls: "y",
    p: "Ask in plain words. Every answer is traced to the tool call and the model that produced it." },
];

/** Deterministic pseudo-random numbers for the torn letter tiles. */
function rng(seed) {
  let state = seed % 2147483647 || 1;
  return () => {
    state = (state * 16807) % 2147483647;
    return (state - 1) / 2147483646;
  };
}

function tiles(word, seed) {
  const random = rng(seed);
  const faces = ["w", "k", "w", "r", "w", "k"];
  return [...word].map((ch) => {
    const face = faces[Math.floor(random() * faces.length)];
    const serif = random() < 0.16 ? " f2" : "";
    const rot = ((random() - 0.5) * 5).toFixed(1);
    const y = ((random() - 0.5) * 0.06).toFixed(3);
    const s = (0.96 + random() * 0.08).toFixed(2);
    return `<span class="tile ${face}${serif}" style="--r:${rot}deg;--y:${y}em;--s:${s}em">${ch}</span>`;
  }).join("");
}

const reduceMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Count a number up once, when it first scrolls into view. */
function countUpOnView(el, to, year = false) {
  const show = (v) => { el.textContent = year ? String(Math.round(v)) : Math.round(v).toLocaleString("en-US"); };
  if (reduceMotion() || !("IntersectionObserver" in window)) {
    show(to);
    return;
  }
  show(0);
  const observer = new IntersectionObserver((entries) => {
    if (!entries.some((entry) => entry.isIntersecting)) return;
    observer.disconnect();
    const start = performance.now();
    const step = (now) => {
      const k = Math.min((now - start) / 900, 1);
      show(to * (1 - Math.pow(1 - k, 3)));
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }, { threshold: 0.3 });
  observer.observe(el);
}

const ARROW = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" style="width:22px;height:22px;fill:none;stroke:currentColor;stroke-width:3.4;stroke-linecap:square"><path d="M3 12h15M12 5l7 7-7 7"/></svg>';

function wireGoLinks(root, navigate) {
  root.querySelectorAll("[data-go]").forEach((link) => link.addEventListener("click", (event) => {
    if (!navigate || event.metaKey || event.ctrlKey || event.shiftKey) return;
    event.preventDefault();
    navigate(link.dataset.go);
  }));
}

/** The cover, mounted by main.js above the page (replaces the page head). */
export function renderHero(slot, { navigate } = {}) {
  slot.innerHTML = `
    <header class="cover" aria-labelledby="cover-title">
      <div class="cover-bg" aria-hidden="true">
        <img src="/media/zine/still-166.jpg" alt="" width="696" height="720">
        <video id="cover-video" muted loop playsinline preload="none" poster="/media/zine/still-166.jpg" tabindex="-1">
          <source src="/media/zine/montage.mp4" type="video/mp4">
        </video>
      </div>
      <button class="bgpp" id="cover-play" type="button" data-state="paused" aria-label="Play background footage">
        <svg class="ic-pause" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M7 5h4v14H7zM13 5h4v14h-4z"/></svg>
        <svg class="ic-play" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M8 5v14l11-7z"/></svg>
        <span class="t-pause">Pause footage</span><span class="t-play">Play footage</span>
      </button>
      <div class="wrap">
        <div class="mastcol">
          <h1 class="mast" id="cover-title" aria-label="Backboard. Know who wins.">
            <span class="row" aria-hidden="true">${tiles("BACK", 4)}</span>
            <span class="row" aria-hidden="true">${tiles("BOARD", 9)}</span>
          </h1>
          <p class="say" aria-hidden="true">Know who <em>wins.</em></p>
          <p class="sub">Win probabilities for any NBA matchup, every player's story, and a sandbox for any what-if, from a model that shows its work.</p>
          <div class="go">
            <a class="btn" href="#/matchups" data-go="/matchups">Predict a matchup ${ARROW}</a>
            <a class="link" href="#inside" id="cover-inside">See what is inside</a>
          </div>
          <p class="cover-proof"><b>${HOLDOUT_ACCURACY}%</b> picks right on ${HOLDOUT_GAMES} held-out games</p>
        </div>
      </div>
    </header>`;

  const video = slot.querySelector("#cover-video");
  const toggle = slot.querySelector("#cover-play");
  const sync = () => {
    const paused = video.paused;
    toggle.dataset.state = paused ? "paused" : "playing";
    toggle.setAttribute("aria-label", paused ? "Play background footage" : "Pause background footage");
  };
  video.style.opacity = "0";
  video.addEventListener("playing", () => { video.style.opacity = "1"; });
  video.addEventListener("play", sync);
  video.addEventListener("pause", sync);
  toggle.addEventListener("click", () => {
    if (video.paused) video.play().catch(() => {});
    else video.pause();
    sync();
  });
  // Footage plays only while the cover is on screen, and never by itself for
  // reduced motion or Save-Data (the button still starts it).
  const saveData = navigator.connection?.saveData;
  if (!reduceMotion() && !saveData && "IntersectionObserver" in window) {
    new IntersectionObserver((entries) => entries.forEach((entry) => {
      if (entry.isIntersecting) video.play().catch(() => {});
      else video.pause();
    })).observe(video);
  }
  wireGoLinks(slot, navigate);
  slot.querySelector("#cover-inside").addEventListener("click", (event) => {
    event.preventDefault();
    document.querySelector("#inside")?.scrollIntoView({ behavior: reduceMotion() ? "auto" : "smooth" });
  });
}

export function render(container, { navigate } = {}) {
  container.innerHTML = `
    <section class="home-band white" id="inside" aria-labelledby="inside-title">
      <div class="wrap">
        <h2 class="h2" id="inside-title">What's <span class="hl">inside</span></h2>
        <p class="lede">One engine, every page. Use the bar at the top to jump between them.</p>
        <div class="toolbox">
          ${TOOLBOX.map((card) => `
            <a class="scrap tool ${card.cls}" href="#${card.path}" data-go="${card.path}" style="--r:${card.r}deg">
              <span class="kick">${card.kick}</span>
              <span class="big"><span data-count="${card.big ?? ""}" ${card.year ? "data-year" : ""} ${card.big == null ? 'id="tool-count"' : ""}>${card.big == null ? "—" : card.year ? card.big : card.big.toLocaleString("en-US")}</span>${card.unit ? ` <small>${card.unit}</small>` : ""}</span>
              <h3>${card.h}</h3>
              <p>${card.p}</p>
              <span class="more">Open ${card.kick}</span>
            </a>`).join("")}
        </div>
      </div>
    </section>

    <section class="home-band" aria-labelledby="status-title">
      <div class="wrap">
        <h2 class="h2" id="status-title">Under the <span class="hl">hood</span></h2>
        <div id="dashboard-headline-mount" style="margin-top:2.4rem">${renderHeadlineSkeleton()}</div>
        <div class="card fade-in" style="margin-top:2.6rem">
          <div class="card-header"><div><h2>Platform status</h2>
            <p class="card-subtitle">Live status of the analytics API and the production model.</p></div></div>
          <div id="health-mount" class="stat-grid stat-grid--3">
            ${statTileSkeleton("Service")}
            ${statTileSkeleton("Production model")}
            ${statTileSkeleton("Tools available")}
          </div>
        </div>
        <div class="card fade-in" style="margin-top:2.6rem">
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
            <div class="mt-2" style="padding-top:1rem; border-top:2px dashed var(--border);">
              <div class="field">
                <label for="ingest-source">Live data refresh — schedule source (URL or local CSV)</label>
                <input type="text" class="input" id="ingest-source" value="data/raw/LeagueSchedule25_26.csv">
              </div>
              <label class="text-muted mt-1" style="display:flex; align-items:center; gap:0.4rem; font-size:0.85rem;">
                <input type="checkbox" id="ingest-dry-run" checked>
                Dry-run (validate + plan only, no database writes)
              </label>
              <button class="btn mt-1" id="ingest-btn">Ingest</button>
              <pre class="output-pane mt-1" id="ingest-output">// ingestion manifest (provenance) appears here</pre>
            </div>
          </details>
        </div>
      </div>
    </section>

    <section class="home-band closer" aria-labelledby="closer-title">
      <div class="wrap">
        <h2 class="h2" id="closer-title">Pick a game.<br>Argue with <span class="hl">it.</span></h2>
        <div class="go" style="margin-top:2.4rem">
          <a class="btn" href="#/matchups" data-go="/matchups">Predict a matchup ${ARROW}</a>
          <a class="link" href="#/sandbox" data-go="/sandbox">Or break the league in the Sandbox</a>
        </div>
      </div>
    </section>
  `;

  wireGoLinks(container, navigate);
  container.querySelectorAll("[data-count]").forEach((el) => {
    if (el.dataset.count) countUpOnView(el, Number(el.dataset.count), "year" in el.dataset);
  });

  loadHealth(container);
  wireAssistantPanel(container);
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
 * empty tool rack, or down (alert state).
 */
function renderStatusHeadline(state, { model, toolCount, message } = {}) {
  if (state === "down") {
    return `
      <section class="card clipping front-page front-page--alert fade-in" aria-labelledby="dashboard-headline">
        <i class="tape" style="left: 42%; top: -18px; --tr: 3deg"></i>
        <div class="clipping-masthead">
          <span class="kicker">Front page</span>
          <span class="clipping-meta">Platform status · /health</span>
        </div>
        <div class="front-page-lead">
          <p class="stamp stamp--tilt stamp--ink front-page-tag">Alert</p>
          <h2 class="tabloid-headline" id="dashboard-headline">Engine <span class="hl-mark">down</span> <span class="scrawl">timeout!</span></h2>
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
        <i class="tape" style="left: 42%; top: -18px; --tr: 3deg"></i>
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
      <i class="tape" style="left: 42%; top: -18px; --tr: 3deg"></i>
      <div class="clipping-masthead">
        <span class="kicker">Front page</span>
        <span class="clipping-meta">Platform status · /health + /tools</span>
      </div>
      <div class="front-page-lead">
        <p class="stamp stamp--tilt front-page-tag">Live</p>
        <h2 class="tabloid-headline" id="dashboard-headline">Engine <span class="hl-mark">online</span> <span class="scrawl">game on!</span></h2>
        <p class="deck">
          The production model <code>${escapeText(model || "unknown")}</code> is serving
          <strong>${toolCount}</strong> analytics tools. Start with a matchup, a player, or a what-if in the Sandbox.
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
    const count = container.querySelector("#tool-count");
    if (count && toolCount) countUpOnView(count, toolCount);
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
        <div class="stat-value" style="font-size:1.25rem;">${escapeText(health.model || "—")}</div>
      </div>
      <div class="stat-tile">
        <div class="stat-label">Tools available</div>
        <div class="stat-value">${toolCount}</div>
      </div>
    `;
  } catch (err) {
    headlineMount.innerHTML = renderStatusHeadline("down", { message: err.message });
    mount.innerHTML = `<div class="error-banner">Could not reach the API: ${escapeText(err.message)}</div>`;
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
