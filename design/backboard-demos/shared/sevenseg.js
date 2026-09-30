/* sevenseg.js: a real seven-segment display for the scoreboard pages.
 *
 * Every digit is the same eight-cell mask; unlit segments are drawn too (the
 * "ghost" cells), so absence is as designed as light. Only digits, "-", ".",
 * ":" and space are supported, which is all a scoreboard number needs.
 *
 *   SevenSeg.svg("69.3", { on: "#ff9d1c", off: "#3a2405", slant: 6 })  -> SVG string
 *
 * The SVG scales to its container's height (width auto). Colors default to
 * CSS custom properties --seg-on / --seg-off so the page themes them.
 */
(function () {
  "use strict";
  const W = 60;         // digit cell width
  const H = 104;        // digit cell height
  const GAP = 14;       // space between cells
  const H_SEG = (cx0, cx1, y) =>
    `${cx0},${y} ${cx0 + 5},${y - 5} ${cx1 - 5},${y - 5} ${cx1},${y} ${cx1 - 5},${y + 5} ${cx0 + 5},${y + 5}`;
  const V_SEG = (x, cy0, cy1) =>
    `${x},${cy0} ${x + 5},${cy0 + 5} ${x + 5},${cy1 - 5} ${x},${cy1} ${x - 5},${cy1 - 5} ${x - 5},${cy0 + 5}`;
  const POLY = {
    a: H_SEG(8, 52, 5),
    g: H_SEG(8, 52, 52),
    d: H_SEG(8, 52, 99),
    f: V_SEG(5, 8, 49),
    b: V_SEG(55, 8, 49),
    e: V_SEG(5, 55, 96),
    c: V_SEG(55, 55, 96),
  };
  const MASK = {
    "0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc",
    "5": "afgcd", "6": "afgedc", "7": "abc", "8": "abcdefg", "9": "abcdfg",
    "-": "g", " ": "",
  };

  function digit(ch, x) {
    const lit = MASK[ch] ?? "";
    const cells = Object.keys(POLY).map((k) =>
      `<polygon class="${lit.includes(k) ? "seg-on" : "seg-off"}" points="${POLY[k]}"/>`).join("");
    return `<g transform="translate(${x} 0)">${cells}</g>`;
  }

  function svg(text, { on, off, slant = 6, label } = {}) {
    let x = 0;
    let out = "";
    for (const ch of String(text)) {
      if (ch === ".") {
        out += `<circle class="seg-on" cx="${x - GAP / 2 + 1}" cy="${H - 5}" r="5.5"/>`;
        continue;
      }
      if (ch === ":") {
        out += `<circle class="seg-on" cx="${x + 5}" cy="${H * 0.3}" r="5.5"/>` +
               `<circle class="seg-on" cx="${x + 5}" cy="${H * 0.7}" r="5.5"/>`;
        x += 18;
        continue;
      }
      out += digit(ch, x);
      x += W + GAP;
    }
    const width = Math.max(x - GAP, 1);
    const style = [on && `--seg-on:${on}`, off && `--seg-off:${off}`].filter(Boolean).join(";");
    const skew = slant ? `transform="skewX(${-slant}) translate(${H * Math.tan(slant * Math.PI / 180)} 0)"` : "";
    return `<svg class="sevenseg" viewBox="0 0 ${width + (slant ? 14 : 0)} ${H}" role="img" aria-label="${label ?? text}"` +
      (style ? ` style="${style}"` : "") + `><g ${skew}>${out}</g></svg>`;
  }

  window.SevenSeg = { svg, W, H };
})();
