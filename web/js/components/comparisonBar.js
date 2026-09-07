// components/comparisonBar.js — a divided bar showing the relative
// magnitude of a single stat between the home and away team (e.g. recent
// win rate, scoring margin). Not a new statistic: it visualizes whatever
// numeric value the backend already returned in `team_context`.

export function renderComparisonRow({
  label,
  homeValue,
  awayValue,
  homeColor,
  awayColor,
  formatValue,
}) {
  const format = formatValue || ((value) => String(Math.round(value * 100) / 100));
  const homeAbs = Math.abs(homeValue);
  const awayAbs = Math.abs(awayValue);
  const total = homeAbs + awayAbs || 1;
  const homeShare = (homeAbs / total) * 100;
  const awayShare = 100 - homeShare;

  return `
    <div class="compare-row">
      <div class="compare-row-head"><span class="metric-name">${label}</span></div>
      <div class="compare-values">
        <span>${format(homeValue)}</span>
        <span>${format(awayValue)}</span>
      </div>
      <div class="compare-track">
        <div class="compare-fill home" style="width:${homeShare}%; background:${homeColor};"></div>
        <div class="compare-fill away" style="width:${awayShare}%; background:${awayColor};"></div>
      </div>
    </div>
  `;
}
