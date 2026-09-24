// components/probabilityBar.js — the split home/away win-probability bar
// used by the Matchup Predictor result. Pure render helper: given two
// probabilities (0..1) and two colors, returns an HTML string.
//
// Blacktop Tabloid: rendered as a thick painted "tug-of-war" strip with a
// half-court marker at 50%. The numbers are printed beside the bar by the
// caller (never on it); the aria-label carries them for screen readers.

export function renderProbabilityBar({ homePct, awayPct, homeColor, awayColor }) {
  const homeWidth = Math.max(0, Math.min(100, homePct * 100));
  const awayWidth = 100 - homeWidth;
  const label = `Win probability: home ${homeWidth.toFixed(1)}%, away ${awayWidth.toFixed(1)}%`;
  return `
    <div class="prob-bar" role="img" aria-label="${label}">
      <div class="prob-bar-segment prob-bar-segment--home" style="width:${homeWidth}%; background:${homeColor};"></div>
      <div class="prob-bar-segment prob-bar-segment--away" style="width:${awayWidth}%; background:${awayColor};"></div>
      <span class="prob-bar-mid" aria-hidden="true"></span>
    </div>
  `;
}
