// components/charts.js — small hand-built SVG charts for the Players and
// Sandbox pages, following the dataviz rules: thin marks with 4px rounded
// data ends, one baseline, hairline solid gridlines, selective direct labels,
// a hover/focus tooltip on every mark, and text in text tokens (never the
// series color). Marks take their colors from CSS classes (.c-grid, .c-base,
// .c-accent, .c-pos, .c-neg, .c-ring in styles.css), so a chart reads ink on
// a paper sheet and chalk on the black ground. The two-series pair (gray vs
// red) and the polarity pair (green vs red) were checked for color-vision-
// deficiency separation on both surfaces.
//
// Every chart is paired with a table or labeled values on the page, so the
// tooltip enhances and never gates.

import { escapeHtml } from "./format.js";

// Tooltip keys sit on the ink tooltip, so they use the chalk-side colors.
const KEYS = { base: "#a5a49d", accent: "#ee3a1f" };

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
    const labelled = s.season_label === peakLabel || i === last;
    const labelY = v >= 0 ? y(v) - 6 : y(v) + 14;
    return `
      <path d="${columnPath(x, barW, y(0), y(v))}" class="chart-mark ${v >= 0 ? "c-pos" : "c-neg"}"
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
      ${ticks.map((t) => `<line x1="${pad.left}" x2="${width - pad.right}" y1="${y(t)}" y2="${y(t)}" class="c-grid" />
        <text x="${pad.left - 6}" y="${y(t) + 4}" text-anchor="end" class="chart-axis-label">${t > 0 ? "+" : ""}${t}</text>`).join("")}
      <line x1="${pad.left}" x2="${width - pad.right}" y1="${y(0)}" y2="${y(0)}" class="c-axis" />
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
      fill="none" class="c-accent-line" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />` : "";
  const dots = games.map((g, i) => `
    <g class="chart-hit" data-tip="${i}" tabindex="0" aria-label="Game ${g.n}: game score ${g.game_score}">
      <rect x="${x(i) - Math.max(4, plotW / games.length / 2)}" y="${pad.top}" width="${Math.max(8, plotW / games.length)}" height="${plotH}" fill="transparent" />
      <circle cx="${x(i)}" cy="${y(g.game_score ?? 0)}" r="4" stroke-width="2" class="chart-mark c-base c-ring" />
    </g>`).join("");
  return `
    <div class="chart-legend" aria-hidden="true">
      <span><i class="legend-dot" style="background:var(--chart-base)"></i>Game score, each game</span>
      <span><i class="legend-line" style="background:var(--chart-accent)"></i>10-game rolling average</span>
    </div>
    <svg class="chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Game score by game with a 10-game rolling average">
      ${ticks.map((t) => `<line x1="${pad.left}" x2="${width - pad.right}" y1="${y(t)}" y2="${y(t)}" class="c-grid" />
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
      { value: `${g.game_score}`, label: "game score", key: KEYS.base },
      { value: `${g.pts} pts · ${g.trb ?? "—"} reb · ${g.ast} ast`, label: g.mp != null ? `in ${g.mp} min` : "" },
    ];
    if (g.rolling_10?.game_score != null) rows.splice(1, 0, { value: `${g.rolling_10.game_score}`, label: "10-game average", key: KEYS.accent });
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
      ${ticks.map((t) => `<line x1="${pad.left}" x2="${width - pad.right}" y1="${y(t)}" y2="${y(t)}" class="c-grid" />
        <text x="${pad.left - 6}" y="${y(t) + 4}" text-anchor="end" class="chart-axis-label">${t > 0 ? "+" : ""}${t}</text>`).join("")}
      <path d="${path}" fill="none" class="c-base-line" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />
      ${curve.map((c) => `<g class="chart-hit" data-tip="${c.age}" tabindex="0" aria-label="Age ${c.age}: ${c.relative_box_impact} vs peak">
          <rect x="${x(c.age) - plotW / curve.length / 2}" y="${pad.top}" width="${plotW / curve.length}" height="${plotH}" fill="transparent" /></g>`).join("")}
      <text x="${x(peak.age)}" y="${y(peak.relative_box_impact) - 8}" text-anchor="middle" class="chart-label">peak ${peak.age}</text>
      ${here ? `<circle cx="${x(here.age)}" cy="${y(here.relative_box_impact)}" r="6" class="c-accent c-ring" stroke-width="2" />
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
 * ink outline so light team colors stay visible on paper.
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
      <span><i class="legend-dot" style="background:var(--chart-base)"></i>Unchanged league</span>
      <span><i class="legend-dot" style="background:var(--chart-accent)"></i>Your scenario</span>
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

/**
 * Lines over time with emphasis: every series is a thin gray line, the
 * emphasized one is the accent, drawn last, with an end label (so no
 * categorical palette is needed for 30 teams). A crosshair snaps to the
 * nearest date; its tooltip lists the emphasized series and the top few.
 * series: [{id, label, points: [{x: "YYYY-MM-DD", y}]}]; y in 0..yMax.
 */
export function emphasisLineChart(series, { emphasis = null, yMin = 0, yMax = 1, format = (v) => `${Math.round(v * 100)}%`,
  label = "chart", labelTop = 3 } = {}) {
  const dates = [...new Set(series.flatMap((s) => s.points.map((p) => p.x)))].sort();
  if (dates.length < 1) return "";
  const width = 760;
  const height = 260;
  const pad = { top: 16, right: 64, bottom: 30, left: 44 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const t0 = Date.parse(dates[0]);
  const t1 = Date.parse(dates[dates.length - 1]);
  const x = (d) => pad.left + (t1 === t0 ? plotW / 2 : ((Date.parse(d) - t0) / (t1 - t0)) * plotW);
  const y = (v) => pad.top + (1 - (Math.min(yMax, Math.max(yMin, v)) - yMin) / (yMax - yMin)) * plotH;
  const ticks = niceTicks(yMin, yMax, 4);
  const pathOf = (s) => s.points.filter((p) => p.y != null)
    .map((p, i) => `${i ? "L" : "M"}${x(p.x).toFixed(1)},${y(p.y).toFixed(1)}`).join(" ");
  const last = (s) => [...s.points].reverse().find((p) => p.y != null);
  const ranked = [...series].sort((a, b) => (last(b)?.y ?? 0) - (last(a)?.y ?? 0));
  const labelled = new Set([emphasis, ...ranked.slice(0, labelTop).map((s) => s.id)].filter((id) => id != null));
  const background = series.filter((s) => s.id !== emphasis).map((s) => `
    <path d="${pathOf(s)}" fill="none" class="c-base-line ${labelled.has(s.id) ? "" : "is-faint"}" stroke-width="${labelled.has(s.id) ? 2 : 1.25}"
      stroke-linejoin="round" stroke-linecap="round" data-series="${escapeHtml(String(s.id))}" />`).join("");
  const hero = series.find((s) => s.id === emphasis);
  const heroPath = hero ? `<path d="${pathOf(hero)}" fill="none" class="c-accent-line" stroke-width="3" stroke-linejoin="round" stroke-linecap="round" />` : "";
  // End labels, nudged apart only by a minimum gap (with leader lines).
  const ends = [...labelled].map((id) => series.find((s) => s.id === id)).filter(Boolean)
    .map((s) => ({ s, p: last(s) })).filter((e) => e.p).sort((a, b) => y(a.p.y) - y(b.p.y));
  let floor = -Infinity;
  const endLabels = ends.map(({ s, p }) => {
    const target = y(p.y);
    const ly = Math.max(target, floor + 14);
    floor = ly;
    const isHero = s.id === emphasis;
    return `<line x1="${x(p.x) + 4}" x2="${width - pad.right + 6}" y1="${target}" y2="${ly}" class="c-grid" />
      ${isHero ? `<circle cx="${x(p.x)}" cy="${target}" r="4.5" class="c-accent c-ring" stroke-width="2" />` : ""}
      <text x="${width - pad.right + 9}" y="${ly + 4}" class="chart-label ${isHero ? "is-strong" : ""}">${escapeHtml(s.label)} ${format(p.y)}</text>`;
  }).join("");
  const stops = dates.map((d, i) => {
    const left = i === 0 ? pad.left : (x(dates[i - 1]) + x(d)) / 2;
    const right = i === dates.length - 1 ? width - pad.right : (x(d) + x(dates[i + 1])) / 2;
    return `<rect class="chart-hit" data-tip="${i}" tabindex="${i === dates.length - 1 ? 0 : -1}" x="${left}" y="${pad.top}" width="${Math.max(1, right - left)}" height="${plotH}" fill="transparent"
      aria-label="${escapeHtml(d)}" />`;
  }).join("");
  const xTicks = dates.length > 1 ? [0, Math.floor((dates.length - 1) / 2), dates.length - 1] : [0];
  return `
    <svg class="chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(label)}">
      ${ticks.map((t) => `<line x1="${pad.left}" x2="${width - pad.right}" y1="${y(t)}" y2="${y(t)}" class="c-grid" />
        <text x="${pad.left - 6}" y="${y(t) + 4}" text-anchor="end" class="chart-axis-label">${format(t)}</text>`).join("")}
      ${background}
      ${heroPath}
      ${endLabels}
      <line class="chart-crosshair c-axis" x1="0" x2="0" y1="${pad.top}" y2="${pad.top + plotH}" visibility="hidden" />
      ${stops}
      ${[...new Set(xTicks)].map((i) => `<text x="${x(dates[i])}" y="${height - 8}" text-anchor="${i === 0 ? "start" : i === dates.length - 1 ? "end" : "middle"}" class="chart-axis-label">${escapeHtml(shortDate(dates[i]))}</text>`).join("")}
    </svg>`;
}

function shortDate(iso) {
  const [yy, mm, dd] = String(iso).split("-").map(Number);
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return mm ? `${months[mm - 1]} ${dd}${yy ? ` '${String(yy).slice(2)}` : ""}` : iso;
}

/** Tooltip + crosshair for emphasisLineChart. */
export function wireEmphasisChart(root, series, { emphasis = null, format = (v) => `${Math.round(v * 100)}%`, top = 4 } = {}) {
  const dates = [...new Set(series.flatMap((s) => s.points.map((p) => p.x)))].sort();
  const svg = root.querySelector("svg.chart");
  const cross = svg?.querySelector(".chart-crosshair");
  wireChartTooltips(root, (index) => {
    const date = dates[Number(index)];
    const at = series.map((s) => ({ s, p: s.points.find((p) => p.x === date) })).filter((e) => e.p?.y != null)
      .sort((a, b) => b.p.y - a.p.y);
    const hero = at.find((e) => e.s.id === emphasis);
    const shown = [...(hero ? [hero] : []), ...at.filter((e) => e.s.id !== emphasis).slice(0, top)];
    const hit = svg.querySelector(`[data-tip="${index}"]`);
    if (cross && hit) {
      const cx = Number(hit.getAttribute("x")) + Number(hit.getAttribute("width")) / 2;
      cross.setAttribute("x1", cx);
      cross.setAttribute("x2", cx);
      cross.setAttribute("visibility", "visible");
    }
    return {
      title: shortDate(date),
      rows: shown.map((e) => ({ value: format(e.p.y), label: e.s.label, key: e.s.id === emphasis ? KEYS.accent : KEYS.base })),
    };
  });
  svg?.addEventListener("pointerleave", () => cross?.setAttribute("visibility", "hidden"));
}

/**
 * Calibration: for each confidence bin, how often the favorite really won
 * (y) against how confident the model was (x). The diagonal is perfect
 * calibration. bins: [{predicted, actual, games, bin: [lo, hi]}]
 */
export function calibrationChart(bins) {
  if (!bins?.length) return "";
  const size = 340;
  const pad = { top: 14, right: 16, bottom: 40, left: 46 };
  const plot = size - pad.left - pad.right;
  const lo = 0.45;
  const hi = 1.0;
  const sx = (v) => pad.left + ((v - lo) / (hi - lo)) * plot;
  const sy = (v) => pad.top + (1 - (v - lo) / (hi - lo)) * plot;
  const ticks = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0];
  return `
    <svg class="chart chart--square" viewBox="0 0 ${size} ${size}" role="img"
      aria-label="Calibration: how often the favorite won against the model's confidence">
      ${ticks.map((t) => `<line x1="${pad.left}" x2="${pad.left + plot}" y1="${sy(t)}" y2="${sy(t)}" class="c-grid" />
        <text x="${pad.left - 6}" y="${sy(t) + 4}" text-anchor="end" class="chart-axis-label">${Math.round(t * 100)}%</text>
        <text x="${sx(t)}" y="${pad.top + plot + 16}" text-anchor="middle" class="chart-axis-label">${Math.round(t * 100)}</text>`).join("")}
      <line x1="${sx(lo)}" y1="${sy(lo)}" x2="${sx(hi)}" y2="${sy(hi)}" class="c-axis" stroke-width="1.5" />
      <text x="${sx(0.93)}" y="${sy(0.97) - 6}" text-anchor="end" class="chart-axis-label">perfect</text>
      ${bins.map((b, i) => `<g class="chart-hit" data-tip="${i}" tabindex="0"
          aria-label="Model said ${Math.round(b.predicted * 100)}%, favorites won ${Math.round(b.actual * 100)}% of ${b.games} games">
          <circle cx="${sx(b.predicted)}" cy="${sy(Math.max(lo, b.actual))}" r="13" fill="transparent" />
          <line x1="${sx(b.predicted)}" x2="${sx(b.predicted)}" y1="${sy(b.predicted)}" y2="${sy(Math.max(lo, b.actual))}" class="c-accent-line" stroke-width="2" />
          <circle cx="${sx(b.predicted)}" cy="${sy(Math.max(lo, b.actual))}" r="${Math.max(4.5, Math.min(9, Math.sqrt(b.games) / 2.2))}" class="chart-mark c-accent c-ring" stroke-width="2" />
        </g>`).join("")}
      <text x="${pad.left + plot / 2}" y="${size - 6}" text-anchor="middle" class="chart-axis-label">Model's confidence in the favorite (%)</text>
    </svg>`;
}

export function calibrationTooltip(bins) {
  return (index) => {
    const b = bins[Number(index)];
    if (!b) return null;
    return {
      title: `Picks at ${Math.round(b.bin[0] * 100)}–${Math.round(b.bin[1] * 100)}%`,
      rows: [
        { value: `${(b.predicted * 100).toFixed(1)}%`, label: "average confidence", key: KEYS.base },
        { value: `${(b.actual * 100).toFixed(1)}%`, label: "favorite actually won", key: KEYS.accent },
        { value: `${b.games}`, label: "games" },
      ],
    };
  };
}

/**
 * In-game win probability for the home team over the 48 regulation minutes,
 * with the 50% line and the quarter breaks. history: [{elapsed, home_win_probability}].
 */
export function winProbabilityChart(history, { home = "Home", away = "Away" } = {}) {
  const points = (history || []).filter((p) => p.elapsed != null);
  const width = 560;
  const height = 150;
  const pad = { top: 12, right: 10, bottom: 18, left: 40 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const maxX = Math.max(48, ...points.map((p) => p.elapsed));
  const x = (m) => pad.left + (m / maxX) * plotW;
  const y = (v) => pad.top + (1 - v) * plotH;
  const path = points.map((p, i) => `${i ? "L" : "M"}${x(p.elapsed).toFixed(1)},${y(p.home_win_probability).toFixed(1)}`).join(" ");
  const lastPoint = points[points.length - 1];
  return `
    <svg class="chart" viewBox="0 0 ${width} ${height}" role="img"
      aria-label="${escapeHtml(home)} win probability through the game${lastPoint ? `, now ${Math.round(lastPoint.home_win_probability * 100)}%` : ""}">
      ${[12, 24, 36].map((m) => `<line x1="${x(m)}" x2="${x(m)}" y1="${pad.top}" y2="${pad.top + plotH}" class="c-grid" />`).join("")}
      <line x1="${pad.left}" x2="${width - pad.right}" y1="${y(0.5)}" y2="${y(0.5)}" class="c-axis" />
      <text x="${pad.left - 6}" y="${y(1) + 9}" text-anchor="end" class="chart-axis-label">${escapeHtml(home)}</text>
      <text x="${pad.left - 6}" y="${y(0) - 1}" text-anchor="end" class="chart-axis-label">${escapeHtml(away)}</text>
      ${["Q1", "Q2", "Q3", "Q4"].map((q, i) => `<text x="${x(i * 12 + 6)}" y="${height - 4}" text-anchor="middle" class="chart-axis-label">${q}</text>`).join("")}
      ${points.length > 1 ? `<path d="${path}" fill="none" class="c-accent-line" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />` : ""}
      ${lastPoint ? `<circle cx="${x(lastPoint.elapsed)}" cy="${y(lastPoint.home_win_probability)}" r="4.5" class="c-accent c-ring" stroke-width="2" />` : ""}
    </svg>`;
}

/**
 * Two players' careers on one axis (box impact by age): player A is the
 * accent, player B the gray base, both labeled at their ends and in the
 * legend. points: [{x: age, y, label}].
 */
export function pairLineChart(a, b, { yLabel = "box impact", format = (v) => `${v > 0 ? "+" : ""}${v.toFixed(1)}` } = {}) {
  const all = [...a.points, ...b.points].filter((p) => p.y != null && p.x != null);
  if (!all.length) return "";
  const width = 760;
  const height = 250;
  const pad = { top: 16, right: 92, bottom: 30, left: 40 };
  const xs = all.map((p) => p.x);
  const ys = all.map((p) => p.y);
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs);
  const lo = Math.min(0, ...ys) - 0.3;
  const hi = Math.max(0, ...ys) + 0.4;
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const x = (v) => pad.left + (x1 === x0 ? plotW / 2 : ((v - x0) / (x1 - x0)) * plotW);
  const y = (v) => pad.top + ((hi - v) / (hi - lo)) * plotH;
  const ticks = niceTicks(lo, hi, 4);
  const line = (s, cls, w) => {
    const pts = s.points.filter((p) => p.y != null && p.x != null);
    if (!pts.length) return "";
    const d = pts.map((p, i) => `${i ? "L" : "M"}${x(p.x).toFixed(1)},${y(p.y).toFixed(1)}`).join(" ");
    const end = pts[pts.length - 1];
    return `<path d="${d}" fill="none" class="${cls}-line" stroke-width="${w}" stroke-linejoin="round" stroke-linecap="round" />
      ${pts.map((p, i) => `<g class="chart-hit" data-tip="${s.key}:${i}" tabindex="0" aria-label="${escapeHtml(s.label)}, age ${p.x}: ${format(p.y)}">
        <circle cx="${x(p.x)}" cy="${y(p.y)}" r="11" fill="transparent" />
        <circle cx="${x(p.x)}" cy="${y(p.y)}" r="4" class="chart-mark ${cls} c-ring" stroke-width="2" /></g>`).join("")}
      <text x="${x(end.x) + 8}" y="${y(end.y) + 4}" class="chart-label">${escapeHtml(s.short || s.label)}</text>`;
  };
  return `
    <div class="chart-legend" aria-hidden="true">
      <span><i class="legend-line" style="background:var(--chart-accent)"></i>${escapeHtml(a.label)}</span>
      <span><i class="legend-line" style="background:var(--chart-base)"></i>${escapeHtml(b.label)}</span>
    </div>
    <svg class="chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(yLabel)} by age: ${escapeHtml(a.label)} and ${escapeHtml(b.label)}">
      ${ticks.map((t) => `<line x1="${pad.left}" x2="${width - pad.right}" y1="${y(t)}" y2="${y(t)}" class="c-grid" />
        <text x="${pad.left - 6}" y="${y(t) + 4}" text-anchor="end" class="chart-axis-label">${t > 0 ? "+" : ""}${t}</text>`).join("")}
      <line x1="${pad.left}" x2="${width - pad.right}" y1="${y(0)}" y2="${y(0)}" class="c-axis" />
      ${line(b, "c-base", 2)}
      ${line(a, "c-accent", 2.5)}
      ${[x0, Math.round((x0 + x1) / 2), x1].filter((v, i, arr) => arr.indexOf(v) === i).map((v) => `<text x="${x(v)}" y="${height - 8}" text-anchor="middle" class="chart-axis-label">age ${v}</text>`).join("")}
    </svg>`;
}

export function pairLineTooltip(a, b, format = (v) => `${v > 0 ? "+" : ""}${v.toFixed(2)}`) {
  return (key) => {
    const [which, index] = String(key).split(":");
    const s = which === a.key ? a : b;
    const p = s.points.filter((q) => q.y != null && q.x != null)[Number(index)];
    if (!p) return null;
    return { title: `${s.label} · age ${p.x}`, rows: [
      { value: format(p.y), label: "box impact / 100", key: which === a.key ? KEYS.accent : KEYS.base },
      ...(p.label ? [{ value: p.label, label: "" }] : []),
    ] };
  };
}

/**
 * Career arc against comparables: x is years from the matched season (0),
 * y is box impact. Comparables are thin gray lines, the player is the
 * accent line, and the seasons ahead carry the middle half of what the
 * comparables went on to do (a band) with its median dashed.
 * arc / comps[i].arc: [{offset, box_impact, season, age}]; band: [{offset, box_impact_p25, _median, _p75}]
 */
export function arcCompsChart(arc, comps, band) {
  const all = [...arc, ...comps.flatMap((c) => c.arc)].filter((p) => p.box_impact != null);
  if (!all.length) return "";
  const bandPoints = (band || []).filter((b) => b.box_impact_median != null);
  const xs = [...all.map((p) => p.offset), ...bandPoints.map((b) => b.offset)];
  const minX = Math.max(-12, Math.min(...xs));
  const maxX = Math.min(8, Math.max(...xs, 1));
  const ys = [...all.map((p) => p.box_impact), ...bandPoints.flatMap((b) => [b.box_impact_p25, b.box_impact_p75])];
  const minY = Math.min(-2, ...ys);
  const maxY = Math.max(2, ...ys);
  const width = 760;
  const height = 280;
  const pad = { top: 16, right: 18, bottom: 34, left: 44 };
  const x = (v) => pad.left + ((v - minX) / (maxX - minX || 1)) * (width - pad.left - pad.right);
  const y = (v) => pad.top + (1 - (v - minY) / (maxY - minY || 1)) * (height - pad.top - pad.bottom);
  const line = (points) => points.filter((p) => p.box_impact != null && p.offset >= minX && p.offset <= maxX)
    .map((p, i) => `${i ? "L" : "M"}${x(p.offset).toFixed(1)},${y(p.box_impact).toFixed(1)}`).join(" ");
  const ticks = niceTicks(minY, maxY, 4);
  const area = bandPoints.length > 1
    ? `<path d="M${bandPoints.map((b) => `${x(b.offset)},${y(b.box_impact_p75)}`).join(" L")} L${[...bandPoints].reverse().map((b) => `${x(b.offset)},${y(b.box_impact_p25)}`).join(" L")} Z" class="c-band" />
       <path d="M${bandPoints.map((b) => `${x(b.offset)},${y(b.box_impact_median)}`).join(" L")}" fill="none" class="c-accent-line" stroke-width="2" stroke-dasharray="5 5" />`
    : "";
  const dots = arc.filter((p) => p.box_impact != null && p.offset >= minX).map((p) =>
    `<circle cx="${x(p.offset)}" cy="${y(p.box_impact)}" r="5" class="c-accent c-ring" stroke-width="2" tabindex="0" data-tip="a${p.season}" aria-label="${escapeHtml(`Season ${p.season}`)}" />`).join("");
  const bandDots = bandPoints.map((b, i) =>
    `<rect x="${x(b.offset) - 9}" y="${pad.top}" width="18" height="${height - pad.top - pad.bottom}" fill="transparent" tabindex="0" data-tip="b${i}" aria-label="${escapeHtml(`Year +${b.offset}`)}" />`).join("");
  const xTicks = [];
  for (let t = Math.ceil(minX); t <= maxX; t += Math.max(1, Math.round((maxX - minX) / 8))) xTicks.push(t);
  return `<svg class="chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Career arc against similar players">
    ${ticks.map((t) => `<line x1="${pad.left}" x2="${width - pad.right}" y1="${y(t)}" y2="${y(t)}" class="${t === 0 ? "c-base" : "c-grid"}" />
      <text x="${pad.left - 6}" y="${y(t) + 4}" text-anchor="end" class="chart-axis-label">${t > 0 ? "+" : ""}${t}</text>`).join("")}
    <line x1="${x(0)}" x2="${x(0)}" y1="${pad.top}" y2="${height - pad.bottom}" class="c-axis" stroke-dasharray="2 4" />
    ${area}
    ${comps.map((c) => `<path d="${line(c.arc)}" fill="none" class="c-base-line is-faint" stroke-width="1.4" stroke-linejoin="round" />`).join("")}
    <path d="${line(arc)}" fill="none" class="c-accent-line" stroke-width="3" stroke-linejoin="round" stroke-linecap="round" />
    ${dots}${bandDots}
    ${xTicks.map((t) => `<text x="${x(t)}" y="${height - 10}" text-anchor="middle" class="chart-axis-label">${t === 0 ? "now" : `${t > 0 ? "+" : ""}${t}y`}</text>`).join("")}
  </svg>`;
}

export function arcCompsTooltip(arc, band) {
  const bandPoints = (band || []).filter((b) => b.box_impact_median != null);
  return (key) => {
    if (key.startsWith("a")) {
      const p = arc.find((point) => point.season === Number(key.slice(1)));
      if (!p) return null;
      return { title: `${p.season}-${String(p.season + 1).slice(-2)} · age ${p.age ?? "?"}`, rows: [
        { value: `${p.box_impact > 0 ? "+" : ""}${p.box_impact.toFixed(2)}`, label: "box impact", key: KEYS.accent },
        ...(p.percentile != null ? [{ value: `${Math.round(p.percentile)}th`, label: "percentile that season" }] : []),
        { value: `${p.games} g · ${p.mpg} mpg`, label: "" },
      ] };
    }
    const b = bandPoints[Number(key.slice(1))];
    if (!b) return null;
    return { title: `${b.offset} year${b.offset === 1 ? "" : "s"} ahead${b.age ? ` · age ${b.age}` : ""}`, rows: [
      { value: `${b.box_impact_median > 0 ? "+" : ""}${b.box_impact_median.toFixed(2)}`, label: "comparables' median", key: KEYS.accent },
      { value: `${b.box_impact_p25.toFixed(2)} to ${b.box_impact_p75.toFixed(2)}`, label: "middle half" },
      { value: `${Math.round((b.share_still_regulars ?? 0) * 100)}%`, label: "still regulars" },
    ] };
  };
}
