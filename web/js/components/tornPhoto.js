// components/tornPhoto.js — a player's NBA headshot torn out of newsprint.
//
// Two jagged clip-path polygons do the tearing: the outer one cuts a sheet of
// paper (the fibrous white rim), the inner one cuts the photo, a few percent
// inside it, so every edge shows torn paper around a torn picture. The tears
// are generated from the personId, so each player's clipping is torn
// differently but identically on every visit. A strip of tape crosses the
// top edge (the brief's one collage overlap per hero). Nothing here is
// rotated text or a number: the frame is loud, the content is the photo.
//
// The image is the NBA's public headshot for that personId. Players without
// one (most players before the 1990s) get their initials on the same torn
// card instead of a broken image.

const HEADSHOT = (personId) => `https://cdn.nba.com/headshots/nba/latest/1040x760/${personId}.png`;
const THUMB = (personId) => `https://cdn.nba.com/headshots/nba/latest/260x190/${personId}.png`;

/** Deterministic pseudo-random numbers in [0, 1) from an integer seed. */
function rng(seed) {
  let state = (Number(seed) || 1) % 2147483647;
  if (state <= 0) state += 2147483646;
  return () => {
    state = (state * 16807) % 2147483647;
    return (state - 1) / 2147483646;
  };
}

/**
 * A torn rectangle as a CSS polygon(). `inset` is how far (in %) the sheet
 * sits inside its box; `depth` the largest tear (in %); edges are walked
 * clockwise in small irregular steps with an occasional deeper bite.
 */
export function tornPolygon(seed, { inset = 0, depth = 2.4, step = [1.6, 3.4] } = {}) {
  const random = rng(seed);
  const points = [];
  const lo = inset;
  const hi = 100 - inset;
  const tear = () => {
    const bite = random() < 0.12 ? depth * (1.4 + random()) : 0;
    return Math.min(depth * random() + bite, depth * 2.6);
  };
  const walk = (from, to, place) => {
    let t = from;
    while (t < to) {
      place(t, tear());
      t += step[0] + random() * (step[1] - step[0]);
    }
  };
  walk(lo, hi, (t, d) => points.push([t, lo + d]));            // top, left to right
  walk(lo, hi, (t, d) => points.push([hi - d, t]));            // right, top to bottom
  walk(lo, hi, (t, d) => points.push([hi - (t - lo), hi - d])); // bottom, right to left
  walk(lo, hi, (t, d) => points.push([lo + d, hi - (t - lo)])); // left, bottom to top
  return `polygon(${points.map(([x, y]) => `${x.toFixed(2)}% ${y.toFixed(2)}%`).join(", ")})`;
}

function initials(name) {
  return String(name || "?")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join("");
}

function escapeAttr(value) {
  return String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/**
 * Markup for the torn headshot. `teamColor` / `teamAlt` tint the halftone
 * backdrop behind the cut-out photo. Call `wireTornPhoto(root)` after
 * inserting it so a missing image falls back to initials.
 */
export function tornPhoto({ personId, name, teamColor = "#2b2e32", teamAlt = "#ff5b14", size = "lg" }) {
  const paper = tornPolygon(personId * 7 + 3, { inset: 0, depth: 2.2 });
  const photo = tornPolygon(personId * 13 + 5, { inset: 5.5, depth: 2.6 });
  return `
    <figure class="torn-photo torn-photo--${size}" style="--photo-team:${teamColor}; --photo-alt:${teamAlt};">
      <div class="torn-photo-paper" style="clip-path:${paper};"></div>
      <div class="torn-photo-image" style="clip-path:${photo};">
        <img src="${HEADSHOT(personId)}" alt="${escapeAttr(name)} headshot" loading="eager" decoding="async"
             data-initials="${escapeAttr(initials(name))}">
      </div>
      <span class="torn-photo-tape" aria-hidden="true"></span>
      <figcaption class="sr-only">${escapeAttr(name)}</figcaption>
    </figure>`;
}

/** Swap a failed headshot for the player's initials (same torn card). */
export function wireTornPhoto(root) {
  root.querySelectorAll(".torn-photo img, .player-thumb img").forEach((img) => {
    const fallback = () => {
      const holder = img.parentElement;
      if (!holder || holder.querySelector(".torn-photo-initials")) return;
      const block = document.createElement("span");
      block.className = "torn-photo-initials";
      block.setAttribute("aria-hidden", "true");
      block.textContent = img.dataset.initials || "?";
      img.replaceWith(block);
      holder.classList.add("is-fallback");
    };
    if (img.complete && img.naturalWidth === 0) fallback();
    else img.addEventListener("error", fallback, { once: true });
  });
}

/** A small square thumbnail for search results and lists. */
export function playerThumb({ personId, name }) {
  return `<span class="player-thumb" style="clip-path:${tornPolygon(personId * 3 + 1, { inset: 0, depth: 4, step: [5, 9] })};">
    <img src="${THUMB(personId)}" alt="" loading="lazy" decoding="async" data-initials="${escapeAttr(initials(name))}">
  </span>`;
}
