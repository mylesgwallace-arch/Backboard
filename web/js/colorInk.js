// colorInk.js — picks legible text ("ink") for any background color, most
// importantly a team's brand color from teamColors.js, by computed WCAG 2.x
// contrast rather than by eye. Pure helpers with no app state: pages and
// components call these while building markup.
//
// INK and CHALK mirror the --ink and --chalk tokens in styles.css. The ladder
// tries the brand's own warm ink/chalk first and falls back to pure black /
// white; every primary and secondary color in teamColors.js reaches >= 4.5:1
// on at least one rung (lowest: 4.55:1).

export const INK = "#141312";
export const CHALK = "#f4f1ea";
const LADDER = [INK, CHALK, "#000000", "#ffffff"];

function channels(hex) {
  let value = String(hex).trim().replace("#", "");
  if (value.length === 3) value = value.split("").map((c) => c + c).join("");
  if (!/^[0-9a-f]{6}$/i.test(value)) return null;
  return [0, 2, 4].map((i) => parseInt(value.slice(i, i + 2), 16));
}

export function relativeLuminance(hex) {
  const rgb = channels(hex);
  if (!rgb) return null;
  const [r, g, b] = rgb.map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrastRatio(a, b) {
  const la = relativeLuminance(a);
  const lb = relativeLuminance(b);
  if (la == null || lb == null) return 1;
  const [hi, lo] = la > lb ? [la, lb] : [lb, la];
  return (hi + 0.05) / (lo + 0.05);
}

/** The first ink on the ladder that reaches `min` contrast on `background`. */
export function readableInk(background, min = 4.5) {
  let best = LADDER[0];
  let bestRatio = 0;
  for (const candidate of LADDER) {
    const ratio = contrastRatio(background, candidate);
    if (ratio >= min) return candidate;
    if (ratio > bestRatio) {
      best = candidate;
      bestRatio = ratio;
    }
  }
  return best;
}

/**
 * Halftone dots drawn on a color slab use the color *opposite* the text ink,
 * so a dot sitting behind a letter can only raise that letter's contrast.
 */
export function halftoneDotColor(background) {
  const ink = readableInk(background);
  return relativeLuminance(ink) < 0.5 ? "#ffffff" : "#000000";
}

/**
 * Inline custom properties for a team-color slab: the fill, its computed ink
 * and the halftone dot color. Returned as a style-attribute fragment.
 */
export function teamSlabStyle(background) {
  return `--team-color:${background}; --team-ink:${readableInk(background)}; --team-dots:${halftoneDotColor(background)};`;
}

/**
 * "Clash, don't blend": when two teams on screen share a near-identical
 * primary (e.g. Bulls/Raptors #CE1141), the second team switches to its own
 * secondary color so the halves stay distinguishable. Colors always come from
 * the palettes passed in (teamColors.js); nothing is invented.
 */
export function clashingPair(firstPalette, secondPalette, minRatio = 1.3) {
  const first = firstPalette.primary;
  let second = secondPalette.primary;
  if (contrastRatio(first, second) < minRatio && secondPalette.secondary) {
    second = secondPalette.secondary;
  }
  return { first, second };
}
