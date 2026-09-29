// components/reliabilityChart.js — "when the model says 70%, does the home
// team win 70% of the time?" A single-series reliability chart drawn from
// validation_report("calibration"): one dot per probability bin (predicted on
// x, observed on y) with its 90% interval, against the dashed diagonal of
// perfect calibration. Hovering a bin shows its numbers; a table view sits
// underneath for screen readers and exact values.

const SIZE = { width: 360, height: 300, left: 44, right: 14, top: 12, bottom: 38 };

export function renderReliabilityCard(report) {
  const bins = report.overall.bins;
  const { width, height, left, right, top, bottom } = SIZE;
  const plotW = width - left - right;
  const plotH = height - top - bottom;
  const x = (p) => left + p * plotW;
  const y = (p) => top + (1 - p) * plotH;
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  const grid = ticks.map((t) => `
    <line x1="${x(0)}" x2="${x(1)}" y1="${y(t)}" y2="${y(t)}" stroke="var(--border)" stroke-width="1" opacity="0.5"/>
    ${t > 0 ? `<text x="${left - 6}" y="${y(t) + 4}" text-anchor="end" fill="var(--text-muted)" font-size="11">${Math.round(t * 100)}%</text>` : ""}
    <text x="${x(t)}" y="${height - bottom + 16}" text-anchor="middle" fill="var(--text-muted)" font-size="11">${Math.round(t * 100)}%</text>`).join("");
  const path = bins.map((b, i) => `${i ? "L" : "M"}${x(b.mean_predicted).toFixed(1)},${y(b.observed_rate).toFixed(1)}`).join(" ");
  const marks = bins.map((b, i) => {
    const [lo, hi] = b.observed_90pct_interval;
    const cx = x(b.mean_predicted);
    return `
      <g class="rel-bin" data-index="${i}">
        <line x1="${cx}" x2="${cx}" y1="${y(lo)}" y2="${y(hi)}" stroke="var(--accent)" stroke-width="2" opacity="0.55"/>
        <circle cx="${cx}" cy="${y(b.observed_rate)}" r="5" fill="var(--accent)" stroke="var(--bg-elevated)" stroke-width="2"/>
        <rect x="${cx - 16}" y="${top}" width="32" height="${plotH}" fill="transparent"/>
      </g>`;
  }).join("");
  const rows = bins.map((b) => `<tr>
      <td>${pct(b.bin[0], 0)}–${pct(b.bin[1], 0)}</td><td class="num">${b.games.toLocaleString()}</td>
      <td class="num">${pct(b.mean_predicted)}</td><td class="num">${pct(b.observed_rate)}</td>
      <td class="num">${pct(b.observed_90pct_interval[0])}–${pct(b.observed_90pct_interval[1])}</td></tr>`).join("");
  return `
    <div class="card fade-in" id="reliability-card">
      <div class="card-header"><div>
        <h2>Is the model calibrated?</h2>
        <p class="card-subtitle">Home-win probability vs how often the home team actually won, on
        ${report.overall.games.toLocaleString()} holdout games the model never trained on (from ${escapeHtml(report.holdout_start.slice(0, 10))}).
        Dots on the dashed line would be perfect. Expected calibration error ${report.overall.expected_calibration_error.toFixed(3)}.</p>
      </div></div>
      <div style="position:relative; max-width:${width}px;">
        <svg viewBox="0 0 ${width} ${height}" width="100%" role="img"
          aria-label="Reliability chart: observed home-win rate against predicted probability, by bin">
          ${grid}
          <line x1="${x(0)}" y1="${y(0)}" x2="${x(1)}" y2="${y(1)}" stroke="var(--text-muted)" stroke-width="1.5" stroke-dasharray="4 4"/>
          <path d="${path}" fill="none" stroke="var(--accent)" stroke-width="2" opacity="0.8"/>
          ${marks}
          <text x="${left + plotW / 2}" y="${height - 4}" text-anchor="middle" fill="var(--text-secondary)" font-size="11">Predicted home-win probability</text>
          <text transform="translate(11 ${top + plotH / 2}) rotate(-90)" text-anchor="middle" fill="var(--text-secondary)" font-size="11">Home team won</text>
        </svg>
        <div class="rel-tooltip" role="status" aria-live="polite" hidden
          style="position:absolute; pointer-events:none; background:var(--bg-elevated-2); border:1px solid var(--border-strong);
          color:var(--text-primary); padding:0.35rem 0.5rem; font-size:0.8rem; border-radius:4px; white-space:nowrap;"></div>
      </div>
      <p class="text-muted mt-1">Above 50% the model rates home teams a few points too high (e.g. it says 75%, they won about 71%),
      consistent with home-court advantage shrinking in recent seasons.</p>
      <details class="collapsible mt-1"><summary>Table view</summary>
        <table class="data-table mt-1"><thead><tr><th scope="col">Bin</th><th scope="col" class="num">Games</th>
          <th scope="col" class="num">Predicted</th><th scope="col" class="num">Home won</th><th scope="col" class="num">90% interval</th></tr></thead>
          <tbody>${rows}</tbody></table>
      </details>
    </div>`;
}

/** Wire hover tooltips after the card is in the DOM. */
export function attachReliabilityHover(root, report) {
  const card = root.querySelector("#reliability-card");
  if (!card) return;
  const tooltip = card.querySelector(".rel-tooltip");
  const svg = card.querySelector("svg");
  card.querySelectorAll(".rel-bin").forEach((group) => {
    const bin = report.overall.bins[Number(group.dataset.index)];
    group.addEventListener("mouseenter", () => {
      tooltip.innerHTML = `<strong>${pct(bin.bin[0], 0)}–${pct(bin.bin[1], 0)}</strong> · ${bin.games.toLocaleString()} games<br>
        Predicted ${pct(bin.mean_predicted)} · home won ${pct(bin.observed_rate)}`;
      tooltip.hidden = false;
      const box = svg.getBoundingClientRect();
      const circle = group.querySelector("circle").getBoundingClientRect();
      const leftPx = Math.min(circle.left - box.left + 12, box.width - 170);
      tooltip.style.left = `${Math.max(0, leftPx)}px`;
      tooltip.style.top = `${Math.max(0, circle.top - box.top - 44)}px`;
    });
    group.addEventListener("mouseleave", () => { tooltip.hidden = true; });
  });
}

function pct(value, digits = 1) { return `${(value * 100).toFixed(digits)}%`; }
function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
