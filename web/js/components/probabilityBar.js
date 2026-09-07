// components/probabilityBar.js — the split home/away win-probability bar
// used by the Matchup Predictor result. Pure render helper: given two
// probabilities (0..1) and two colors, returns an HTML string.

export function renderProbabilityBar({ homePct, awayPct, homeColor, awayColor }) {
  const homeWidth = Math.max(0, Math.min(100, homePct * 100));
  const awayWidth = 100 - homeWidth;
  return `
    <div class="prob-bar" role="img" aria-label="Win probability comparison">
      <div class="prob-bar-segment" style="width:${homeWidth}%; background:${homeColor};"></div>
      <div class="prob-bar-segment" style="width:${awayWidth}%; background:${awayColor};"></div>
    </div>
  `;
}
