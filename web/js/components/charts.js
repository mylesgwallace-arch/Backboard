// components/charts.js — small hand-built SVG charts for the Players and
// Sandbox pages, following the dataviz rules: thin marks with 4px rounded
// data ends, one baseline, hairline solid gridlines, selective direct labels,
// a hover/focus tooltip on every mark, and text in text tokens (never the
// series color). Colors are the design-system tokens; the two-series pair
// (chalk gray vs spray orange) and the polarity pair (court green vs signal
// red) were checked for color-vision-deficiency separation on asphalt-900.
//
// Every chart is paired with a table or labeled values on the page, so the
// tooltip enhances and never gates.

import { escapeHtml } from "./format.js";

const COLORS = {
  grid: "rgba(244, 241, 234, 0.12)",
  axis: "rgba(244, 241, 234, 0.32)",
  text: "#c2bdb2",
  muted: "#9d998f",
  base: "#9d998f",
  accent: "#ff5b14",
  positive: "#4fd887",
  negative: "#ff4b3e",
  surface: "#18191b",
};

let tooltipEl = null;

function tooltip() {
  if (!tooltipEl) {
    tooltipEl = document.createElement("div");
    tooltipEl.className = "chart-tooltip";
    tooltipEl.setAttribute("role", "status");
    tooltipEl.hidden = true;
    document.body.appendChild(tooltipEl);
  }
  return tooltipEl;
}

/** rows: [{value, label, key?}] — values lead, labels follow; textContent only. */
function showTooltip(event, title, rows) {
  const tip = tooltip();
  tip.replaceChildren();
  const head = document.createElement("div");
  head.className = "chart-tooltip-title";
  head.textContent = title;
  tip.appendChild(head);
  for (const row of rows) {
    const line = document.createElement("div");
    line.className = "chart-tooltip-row";
    if (row.key) {
      const key = document.createElement("span");
      key.className = "chart-tooltip-key";
      key.style.background = row.key;
      line.appendChild(key);
    }
    const value = document.createElement("strong");
    value.textContent = row.value;
    line.appendChild(value);
    const label = document.createElement("span");
    label.textContent = ` ${row.label}`;
    line.appendChild(label);
    tip.appendChild(line);
  }
  tip.hidden = false;
  const rect = event.target.getBoundingClientRect?.() || { left: event.clientX, top: event.clientY, width: 0 };
  const x = event.clientX ?? rect.left + rect.width / 2;
  const y = event.clientY ?? rect.top;
  const width = tip.offsetWidth;
  const left = Math.min(Math.max(8, x + 14), window.innerWidth - width - 8);
  tip.style.left = `${left}px`;
  tip.style.top = `${Math.max(8, y - tip.offsetHeight - 12)}px`;
}

function hideTooltip() {
  if (tooltipEl) tooltipEl.hidden = true;
}

/** Attach hover + keyboard-focus tooltips to `[data-tip]` marks inside `root`. */
export function wireChartTooltips(root, lookup) {
  root.querySelectorAll("[data-tip]").forEach((mark) => {
    const open = (event) => {
      const info = lookup(mark.dataset.tip);
      if (!info) return;
      if (event.type === "focus") {
        const rect = mark.getBoundingClientRect();
        event = { clientX: rect.left + rect.width / 2, clientY: rect.top, target: mark };
      }
      showTooltip(event, info.title, info.rows);
      mark.classList.add("is-hot");
    };
    const close = () => {
      hideTooltip();
      mark.classList.remove("is-hot");
    };
    mark.addEventListener("pointermove", open);
    mark.addEventListener("pointerleave", close);
    mark.addEventListener("focus", open);
    mark.addEventListener("blur", close);
  });
}

/** A column path with a 4px rounded data end and a square baseline. */
function columnPath(x, width, y0, y1) {
  const r = Math.min(4, width / 2, Math.abs(y1 - y0));
  if (y1 < y0) {
    return `M${x},${y0} V${y1 + r} Q${x},${y1} ${x + r},${y1} H${x + width - r} Q${x + width},${y1} ${x + width},${y1 + r} V${y0} Z`;
  }
  return `M${x},${y0} V${y1 - r} Q${x},${y1} ${x + r},${y1} H${x + width - r} Q${x + width},${y1} ${x + width},${y1 - r} V${y0} Z`;
}

function niceTicks(min, max, count = 4) {
  const span = max - min || 1;
  const raw = span / count;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= raw) || raw;
  const ticks = [];
  for (let t = Math.ceil(min / step) * step; t <= max + 1e-9; t += step) ticks.push(Number(t.toFixed(6)));
  return ticks;
}

/**
 * Career arc: one column per season of box impact (polarity: above / below a
 * league-average player). Labels only the peak and the latest season.
 * seasons: [{season_label, age, box_impact, wins_added, mpg, games}]
 */
export function careerArcChart(seasons, { peakLabel } = {}) {
  if (!seasons?.length) return "";
  const width = 760;
  const height = 230;
  const pad = { top: 26, right: 12, bottom: 34, left: 38 };
  const values = seasons.map((s) => s.box_impact ?? 0);
  const lo = Math.min(0, ...values) - 0.3;
  const hi = Math.max(0, ...values) + 0.5;
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const y = (v) => pad.top + ((hi - v) / (hi - lo)) * plotH;
  const band = plotW / seasons.length;
  const barW = Math.min(24, Math.max(4, band - 2));
  const ticks = niceTicks(lo, hi, 4);
  const last = seasons.length - 1;
  const marks = seasons.map((s, i) => {
    const v = s.box_impact ?? 0;
    const x = pad.left + i * band + (band - barW) / 2;
    const color = v >= 0 ? COLORS.positive : COLORS.negative;
    const labelled = s.season_label === peakLabel || i === last;
    const labelY = v >= 0 ? y(v) - 6 : y(v) + 14;
    return `
      <path d="${columnPath(x, barW, y(0), y(v))}" fill="${color}" class="chart-mark"
        data-tip="${i}" tabindex="0" aria-label="${escapeHtml(s.season_label)}: box impact ${v.toFixed(2)}"></path>
      ${labelled ? `<text x="${x + barW / 2}" y="${labelY}" text-anchor="middle" class="chart-label">${v > 0 ? "+" : ""}${v.toFixed(1)}</text>` : ""}`;
  });
  const every = Math.ceil(seasons.length / 10);
  const xLabels = seasons.map((s, i) => (i % every === 0 || i === last)
    ? `<text x="${pad.left + i * band + band / 2}" y="${height - 12}" text-anchor="middle" class="chart-axis-label">${escapeHtml(s.season_label.slice(2))}</text>`
    : "").join("");
  return `
    <svg class="chart" viewBox="0 0 ${width} ${height}" role="img"
      aria-label="Box impact by season, ${escapeHtml(seasons[0].season_label)} to ${escapeHtml(seasons[last].season_label)}">
      ${ticks.map((t) => `<line x1="${pad.left}" x2="${width - pad.right}" y1="${y(t)}" y2="${y(t)}" stroke="${COLORS.grid}" />
        <text x="${pad.left - 6}" y="${y(t) + 4}" text-anchor="end" class="chart-axis-label">${t > 0 ? "+" : ""}${t}</text>`).join("")}
      <line x1="${pad.left}" x2="${width - pad.right}" y1="${y(0)}" y2="${y(0)}" stroke="${COLORS.axis}" />
      ${marks.join("")}
      ${xLabels}
    </svg>`;
}

export function careerArcTooltip(seasons) {
  return (index) => {
    const s = seasons[Number(index)];
    if (!s) return null;
    return {
      title: `${s.season_label}${s.age != null ? ` · age ${s.age}` : ""}`,
      rows: [
        { value: `${s.box_impact > 0 ? "+" : ""}${s.box_impact?.toFixed(2)}`, label: "box impact / 100" },
        { value: `${s.wins_added > 0 ? "+" : ""}${s.wins_added?.toFixed(1)}`, label: "wins added" },
        { value: `${s.mpg?.toFixed(1)} mpg`, label: `in ${s.games} games` },
      ],
    };
  };
}

/**
 * Game log trend: each game's game score (gray dots) and the 10-game rolling
 * average (orange line). Two series -> a legend sits above the chart.
 */
export function gameTrendChart(games) {
  if (!games?.length) return "";
  const width = 760;
  const height = 220;
  const pad = { top: 14, right: 14, bottom: 28, left: 36 };
  const values = games.map((g) => g.game_score ?? 0);
  const lo = Math.min(0, ...values);
  const hi = Math.max(...values, 10) + 2;
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const x = (i) => pad.left + (games.length === 1 ? plotW / 2 : (i / (games.length - 1)) * plotW);
  const y = (v) => pad.top + ((hi - v) / (hi - lo)) * plotH;
  const ticks = niceTicks(lo, hi, 4);
  const rolling = games
    .map((g, i) => (g.rolling_10?.game_score != null ? [x(i), y(g.rolling_10.game_score)] : null))
    .filter(Boolean);
  const line = rolling.length > 1 ? `<path d="M${rolling.map(([px, py]) => `${px.toFixed(1)},${py.toFixed(1)}`).join(" L")}"
      fill="none" stroke="${COLORS.accent}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />` : "";
  const dots = games.map((g, i) => `
    <g class="chart-hit" data-tip="${i}" tabindex="0" aria-label="Game ${g.n}: game score ${g.game_score}">
      <rect x="${x(i) - Math.max(4, plotW / games.length / 2)}" y="${pad.top}" width="${Math.max(8, plotW / games.length)}" height="${plotH}" fill="transparent" />
      <circle cx="${x(i)}" cy="${y(g.game_score ?? 0)}" r="4" fill="${COLORS.base}" stroke="${COLORS.surface}" stroke-width="2" class="chart-mark" />
    </g>`).join("");
  return `
    <div class="chart-legend" aria-hidden="true">
      <span><i class="legend-dot" style="background:${COLORS.base}"></i>Game score, each game</span>
      <span><i class="legend-line" style="background:${COLORS.accent}"></i>10-game rolling average</span>
    </div>
    <svg class="chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Game score by game with a 10-game rolling average">
      ${ticks.map((t) => `<line x1="${pad.left}" x2="${width - pad.right}" y1="${y(t)}" y2="${y(t)}" stroke="${COLORS.grid}" />
        <text x="${pad.left - 6}" y="${y(t) + 4}" text-anchor="end" class="chart-axis-label">${t}</text>`).join("")}
      ${line}
      ${dots}
      <text x="${pad.left}" y="${height - 8}" class="chart-axis-label">Game 1</text>
      <text x="${width - pad.right}" y="${height - 8}" text-anchor="end" class="chart-axis-label">Game ${games.length}</text>
    </svg>`;
}

export function gameTrendTooltip(games) {
  return (index) => {
    const g = games[Number(index)];
    if (!g) return null;
    const rows = [
      { value: `${g.game_score}`, label: "game score", key: COLORS.base },
      { value: `${g.pts} pts · ${g.trb ?? "—"} reb · ${g.ast} ast`, label: g.mp != null ? `in ${g.mp} min` : "" },
    ];
    if (g.rolling_10?.game_score != null) rows.splice(1, 0, { value: `${g.rolling_10.game_score}`, label: "10-game average", key: COLORS.accent });
    return { title: `${g.date} · ${g.home ? "vs" : "@"} ${g.opponent} · ${g.win ? "W" : "L"} ${g.score || ""}`, rows };
  };
}

/**
 * Aging curve (league-wide, relative to the peak) with this player's age
 * marked. A single series: no legend box; the title names it.
 */
export function agingCurveChart(curve, playerAge) {
  if (!curve?.length) return "";
  const width = 760;
  const height = 210;
  const pad = { top: 18, right: 14, bottom: 30, left: 40 };
  const ages = curve.map((c) => c.age);
  const values = curve.map((c) => c.relative_box_impact);
  const lo = Math.min(...values) - 0.1;
  const hi = 0.2;
  const minAge = Math.min(...ages);
  const maxAge = Math.max(...ages);
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const x = (a) => pad.left + ((a - minAge) / (maxAge - minAge)) * plotW;
  const y = (v) => pad.top + ((hi - v) / (hi - lo)) * plotH;
  const ticks = niceTicks(lo, 0, 3);
  const path = curve.map((c, i) => `${i ? "L" : "M"}${x(c.age).toFixed(1)},${y(c.relative_box_impact).toFixed(1)}`).join(" ");
  const here = curve.find((c) => c.age === playerAge);
  const peak = curve.reduce((best, c) => (c.relative_box_impact > best.relative_box_impact ? c : best), curve[0]);
  return `
    <svg class="chart" viewBox="0 0 ${width} ${height}" role="img"
      aria-label="League aging curve: box impact relative to the peak age (${peak.age})">
      ${ticks.map((t) => `<line x1="${pad.left}" x2="${width - pad.right}" y1="${y(t)}" y2="${y(t)}" stroke="${COLORS.grid}" />
        <text x="${pad.left - 6}" y="${y(t) + 4}" text-anchor="end" class="chart-axis-label">${t > 0 ? "+" : ""}${t}</text>`).join("")}
      <path d="${path}" fill="none" stroke="${COLORS.base}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />
      ${curve.map((c) => `<g class="chart-hit" data-tip="${c.age}" tabindex="0" aria-label="Age ${c.age}: ${c.relative_box_impact} vs peak">
          <rect x="${x(c.age) - plotW / curve.length / 2}" y="${pad.top}" width="${plotW / curve.length}" height="${plotH}" fill="transparent" /></g>`).join("")}
      <text x="${x(peak.age)}" y="${y(peak.relative_box_impact) - 8}" text-anchor="middle" class="chart-label">peak ${peak.age}</text>
      ${here ? `<circle cx="${x(here.age)}" cy="${y(here.relative_box_impact)}" r="6" fill="${COLORS.accent}" stroke="${COLORS.surface}" stroke-width="2" />
        <text x="${x(here.age)}" y="${y(here.relative_box_impact) + 22}" text-anchor="middle" class="chart-label">now ${here.age}</text>` : ""}
      ${[minAge, Math.round((minAge + maxAge) / 2), maxAge].map((a) => `<text x="${x(a)}" y="${height - 10}" text-anchor="middle" class="chart-axis-label">age ${a}</text>`).join("")}
    </svg>`;
}

export function agingCurveTooltip(curve) {
  return (age) => {
    const c = curve.find((item) => item.age === Number(age));
    if (!c) return null;
    return { title: `Age ${c.age}`, rows: [{ value: `${c.relative_box_impact > 0 ? "+" : ""}${c.relative_box_impact.toFixed(2)}`, label: "box impact vs the peak age" }] };
  };
}

/**
 * Percentile profile: one horizontal bar per stat (0-100) with a tick at the
 * league median (50). Single series in the player's team color with a chalk
 * inset outline so dark team colors stay visible on asphalt.
 */
export function percentileBars(items, color) {
  return `<div class="pctl-list">
    ${items.map((item, i) => {
      const value = item.percentile;
      const width = value == null ? 0 : Math.max(1.5, value);
      return `<div class="pctl-row" data-tip="${i}" tabindex="0">
        <span class="pctl-label">${escapeHtml(item.label)}</span>
        <span class="pctl-track"><span class="pctl-fill" style="width:${width}%; background:${color};"></span><span class="pctl-mid" aria-hidden="true"></span></span>
        <span class="pctl-value">${value == null ? "—" : Math.round(value)}</span>
      </div>`;
    }).join("")}
  </div>`;
}

/**
 * Baseline vs scenario dumbbell rows (Sandbox): gray dot = unchanged league,
 * orange dot = your scenario, thin bands = 10th-90th percentile ranges.
 * rows: [{label, base, scenario, baseRange, scenarioRange}]
 */
export function dumbbellRows(rows, { min, max, unit = "" }) {
  const span = max - min || 1;
  const pos = (v) => `${(((v - min) / span) * 100).toFixed(2)}%`;
  return `
    <div class="chart-legend" aria-hidden="true">
      <span><i class="legend-dot" style="background:${COLORS.base}"></i>Unchanged league</span>
      <span><i class="legend-dot" style="background:${COLORS.accent}"></i>Your scenario</span>
      <span><i class="legend-band"></i>10th–90th percentile</span>
    </div>
    <div class="dumbbell-list">
      ${rows.map((row, i) => `
        <div class="dumbbell-row" data-tip="${i}" tabindex="0">
          <span class="dumbbell-label">${escapeHtml(row.label)}</span>
          <span class="dumbbell-track">
            ${row.baseRange ? `<span class="dumbbell-band is-base" style="left:${pos(row.baseRange[0])}; width:calc(${pos(row.baseRange[1])} - ${pos(row.baseRange[0])});"></span>` : ""}
            ${row.scenarioRange ? `<span class="dumbbell-band is-scenario" style="left:${pos(row.scenarioRange[0])}; width:calc(${pos(row.scenarioRange[1])} - ${pos(row.scenarioRange[0])});"></span>` : ""}
            <span class="dumbbell-link" style="left:${pos(Math.min(row.base, row.scenario))}; width:calc(${pos(Math.max(row.base, row.scenario))} - ${pos(Math.min(row.base, row.scenario))});"></span>
            <span class="dumbbell-dot is-base" style="left:${pos(row.base)};"></span>
            <span class="dumbbell-dot is-scenario" style="left:${pos(row.scenario)};"></span>
          </span>
          <span class="dumbbell-value">${row.base.toFixed(1)} → <strong>${row.scenario.toFixed(1)}</strong>${unit}</span>
        </div>`).join("")}
      <div class="dumbbell-axis" aria-hidden="true"><span>${min}</span><span>${Math.round((min + max) / 2)}</span><span>${max}</span></div>
    </div>`;
}
