// components/hero.js — the full-viewport landing hero ("gameday cover").
// Pure render helper: given copy, a stat and an optional photo, returns an
// HTML string. Styles live in web/hero.css.
//
// Every effect is vector or CSS so it stays crisp and re-themes with the
// palette custom properties (--hero-c1 / --hero-c2 / --hero-paper):
//   * torn-paper photo backing  -> inline SVG <clipPath> (objectBoundingBox)
//   * ripped accent word        -> CSS clip-path polygons along one tear line
//   * halftone, paper grain     -> CSS gradients / SVG-noise data URIs
//   * player cutout placeholder -> inline SVG silhouette in a team jersey
//   * footage on the color slab -> a muted <video> reprinted as live
//     halftone dots in CSS (see "Footage" in web/hero.css); wireHero() owns
//     loading and playback
// Tear shapes come from a seeded generator, so they are identical on every
// render instead of jittering between page loads.
//
// Swapping in a real player: pass `photo: { src, alt }` pointing at a
// transparent cutout PNG under web/ (the API serves .png). It takes the
// silhouette's place with the same paper cut-out outline.
// Swapping team colors: pass `palette: heroPalette(teamId)`.

import { teamColor } from "../teamColors.js";
import { readableInk, contrastRatio, CHALK } from "../colorInk.js";

/** Small deterministic PRNG (mulberry32) so tears are stable per seed. */
function seeded(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const r3 = (n) => Math.round(n * 1000) / 1000;

/**
 * Torn backing in objectBoundingBox units (0..1): ragged top and left
 * edges; right and bottom stay straight because they bleed off the hero.
 */
function tornBackingPath(seed) {
  const rand = seeded(seed);
  const points = [];
  for (let x = 0.05; x < 1; x += 0.018 + rand() * 0.03) {
    points.push([x, 0.012 + rand() * 0.028]);
  }
  points.push([1, 0.02], [1, 1], [0.03, 1]);
  for (let y = 0.97; y > 0.04; y -= 0.014 + rand() * 0.026) {
    points.push([0.004 + rand() * 0.045, y]);
  }
  return "M" + points.map(([x, y]) => `${r3(x)} ${r3(y)}`).join(" L") + " Z";
}

/**
 * One horizontal tear line through a word, as two complementary CSS
 * polygons (upper and lower piece) that share the exact same jagged edge.
 */
function ripPolygons(seed) {
  const rand = seeded(seed);
  const line = [];
  for (let x = 0; x < 100; x += 3 + rand() * 6) {
    line.push([x, 50 + (rand() - 0.5) * 14]);
  }
  line.push([100, 48 + rand() * 6]);
  const edge = line.map(([x, y]) => `${r3(x)}% ${r3(y)}%`);
  const top = `polygon(0% -10%, 100% -10%, ${[...edge].reverse().join(", ")})`;
  const bottom = `polygon(${edge.join(", ")}, 100% 110%, 0% 110%)`;
  return { top, bottom };
}

// Mirrors --hero-base in web/hero.css; used to check colors against it.
const HERO_BASE = "#101114";

/**
 * Hero palette from a team's brand colors. The primary becomes the slab
 * color unless it nearly vanishes on the dark base (Nets black, Lakers
 * purple), in which case the two swap. Text on each color, and the accent
 * word's color, are picked by computed contrast so any team in
 * teamColors.js stays AA-legible.
 */
export function heroPalette(teamId) {
  const { primary, secondary } = teamColor(teamId);
  const swap = contrastRatio(primary, HERO_BASE) < 2
    && contrastRatio(secondary, HERO_BASE) > contrastRatio(primary, HERO_BASE);
  const [c1, c2] = swap ? [secondary, primary] : [primary, secondary];
  const accent = [c1, c2].find((c) => contrastRatio(c, HERO_BASE) >= 3) || CHALK;
  return {
    c1,
    c2,
    onC1: readableInk(c1),
    onC2: readableInk(c2),
    accent,
  };
}

function paletteStyle(palette) {
  if (!palette) return "";
  const vars = [
    palette.c1 && `--hero-c1:${palette.c1}`,
    palette.c2 && `--hero-c2:${palette.c2}`,
    palette.onC1 && `--hero-on-c1:${palette.onC1}`,
    palette.onC2 && `--hero-on-c2:${palette.onC2}`,
    palette.accent && `--hero-accent:${palette.accent}`,
  ].filter(Boolean);
  return vars.length ? ` style="${vars.join(";")}"` : "";
}

function escapeText(value) {
  return String(value).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

/** Placeholder cutout: a player bust in a tank-top jersey, palette-driven. */
function silhouette(jerseyNumber, wordmark) {
  // Anton at 40px fits 8 letters between the armhole trims; longer words
  // shrink so they never run into the trim.
  const wordmarkSize = Math.min(40, Math.floor(330 / Math.max(wordmark.length, 1)));
  return `
    <svg class="hero-figure-svg" viewBox="0 0 520 720" preserveAspectRatio="xMidYMax meet" aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id="hero-skin" x1="0" y1="0" x2="1" y2="0.4">
          <stop offset="0" stop-color="#34363b"/>
          <stop offset="0.55" stop-color="#232428"/>
          <stop offset="1" stop-color="#1a1b1e"/>
        </linearGradient>
        <!-- Halftone screens: light dots for the key light, dark for shade. -->
        <pattern id="hero-ht-light" width="9" height="9" patternUnits="userSpaceOnUse" patternTransform="rotate(30)">
          <circle cx="4.5" cy="4.5" r="2.1" fill="#f4f1ea"/>
        </pattern>
        <pattern id="hero-ht-dark" width="9" height="9" patternUnits="userSpaceOnUse" patternTransform="rotate(30)">
          <circle cx="4.5" cy="4.5" r="2.3" fill="#000"/>
        </pattern>
        <radialGradient id="hero-key-fade" cx="0.3" cy="0.3" r="0.55">
          <stop offset="0" stop-color="#6a6a6a"/>
          <stop offset="1" stop-color="#000"/>
        </radialGradient>
        <linearGradient id="hero-shade-fade" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0.45" stop-color="#000"/>
          <stop offset="1" stop-color="#8a8a8a"/>
        </linearGradient>
        <mask id="hero-key-mask" maskUnits="userSpaceOnUse" x="0" y="0" width="520" height="720">
          <rect width="520" height="720" fill="url(#hero-key-fade)"/>
        </mask>
        <mask id="hero-shade-mask" maskUnits="userSpaceOnUse" x="0" y="0" width="520" height="720">
          <rect width="520" height="720" fill="url(#hero-shade-fade)"/>
        </mask>
        <clipPath id="hero-head-clip"><ellipse cx="260" cy="150" rx="60" ry="76"/></clipPath>
        <!-- Bust cropped at the waist: head, ears, neck, shoulders, arms. -->
        <g id="hero-skin-shape">
          <path d="M230 236 C198 250 150 260 118 286 C84 312 70 362 72 422 L64 720 L456 720 L448 422 C450 362 436 312 402 286 C370 260 322 250 290 236 Z"/>
          <path d="M232 196 L288 196 L298 256 L222 256 Z"/>
          <ellipse cx="199" cy="160" rx="10" ry="19"/>
          <ellipse cx="321" cy="160" rx="10" ry="19"/>
          <ellipse cx="260" cy="150" rx="60" ry="76"/>
        </g>
        <path id="hero-jersey-shape" d="M194 256 L222 254 C232 298 246 320 260 326 C274 320 288 298 298 254 L326 256 C332 328 350 380 372 418 L376 720 L144 720 L148 418 C170 380 188 328 194 256 Z"/>
      </defs>
      <use href="#hero-skin-shape" fill="url(#hero-skin)"/>
      <use href="#hero-skin-shape" fill="url(#hero-ht-light)" mask="url(#hero-key-mask)"/>
      <rect x="190" y="98" width="140" height="22" fill="var(--hero-c1)" clip-path="url(#hero-head-clip)"/>
      <!-- arm/torso shading so the arms read as separate from the body -->
      <path d="M150 440 L146 720 M370 440 L374 720" stroke="#0e0f11" stroke-width="5" stroke-opacity="0.55" fill="none"/>
      <!-- tank-top jersey, shaded on the far side with a dark screen -->
      <use href="#hero-jersey-shape" fill="var(--hero-c2)"/>
      <use href="#hero-jersey-shape" fill="url(#hero-ht-dark)" mask="url(#hero-shade-mask)"/>
      <path fill="none" stroke="var(--hero-c1)" stroke-width="9" stroke-linejoin="round" d="M222 256 C232 298 246 320 260 326 C274 320 288 298 298 256"/>
      <path fill="none" stroke="var(--hero-c1)" stroke-width="9" stroke-linejoin="round" d="M194 258 C188 328 170 380 148 418 M326 258 C332 328 350 380 372 418"/>
      <text x="260" y="404" text-anchor="middle" class="hero-figure-wordmark" style="font-size:${wordmarkSize}px" fill="var(--hero-on-c2)">${escapeText(wordmark)}</text>
      <text x="260" y="560" text-anchor="middle" class="hero-figure-number" fill="var(--hero-on-c2)" stroke="var(--hero-c1)" stroke-width="5" paint-order="stroke">${escapeText(jerseyNumber)}</text>
    </svg>
  `;
}

/**
 * The slab's footage layer. Sources carry `data-src` only, so nothing is
 * downloaded until wireHero() decides this viewer should get motion; until
 * then (and for phones, reduced motion or Save-Data) the poster is printed
 * instead.
 */
function renderFootage({ poster, sources }) {
  return `
    <div class="hero-footage">
      <video class="hero-footage-video" muted loop playsinline preload="none" tabindex="-1"
        disablepictureinpicture disableremoteplayback poster="${escapeText(poster)}">
        ${sources.map((source) => `<source data-src="${escapeText(source.src)}" type="${escapeText(source.type)}">`).join("")}
      </video>
    </div>`;
}

/**
 * @param {object} props
 * @param {string}   props.tag          small label pill above the headline
 * @param {string[]} props.headline     [top, ransom, accent] — 3 words, stacked
 * @param {string}   [props.sticker]    optional slanted "exclamation" accent
 * @param {string}   props.deck         one-sentence supporting copy
 * @param {{href: string, label: string}} props.cta  the single primary action
 * @param {{value: string, label: string}} [props.stat]  stat badge
 * @param {{src: string, alt: string}} [props.photo]     real player cutout
 * @param {{poster: string, sources: {src: string, type: string}[]}} [props.footage]
 *        ambient loop printed into the color slab; call wireHero() after
 *        mounting to load and play it
 * @param {object}   [props.palette]    {c1, c2, onC1, onC2, accent} (see heroPalette)
 * @param {string}   [props.jerseyNumber] placeholder jersey number
 * @param {string}   [props.wordmark]     placeholder jersey wordmark
 */
export function renderHero({
  tag,
  headline,
  sticker,
  deck,
  cta,
  stat,
  photo,
  footage,
  palette,
  jerseyNumber = "00",
  wordmark = "BACKBOARD",
}) {
  const [top, ransom, accent] = headline;
  const rip = ripPolygons(23);
  const ransomLetters = [...ransom]
    .map((ch, i) => `<span class="ransom-chip ransom-chip--${(i % 3) + 1}">${escapeText(ch)}</span>`)
    .join("");

  const figure = photo
    ? `<img class="hero-figure-img" src="${escapeText(photo.src)}" alt="${escapeText(photo.alt || "")}">`
    : silhouette(jerseyNumber, wordmark);

  return `
    <section class="hero"${paletteStyle(palette)} aria-labelledby="hero-title">
      <svg class="hero-defs" width="0" height="0" aria-hidden="true" focusable="false">
        <clipPath id="hero-torn-backing" clipPathUnits="objectBoundingBox">
          <path d="${tornBackingPath(7)}"/>
        </clipPath>
      </svg>

      <div class="hero-layer hero-block hero-block--c2" aria-hidden="true"></div>
      <div class="hero-layer hero-block hero-block--c1${footage ? " has-footage" : ""}" aria-hidden="true">${footage ? renderFootage(footage) : ""}</div>

      <div class="hero-visual" aria-hidden="${photo ? "false" : "true"}">
        <div class="hero-backing"><div class="hero-backing-paper"></div></div>
        <div class="hero-figure">${figure}</div>
      </div>

      <div class="hero-copy">
        <p class="hero-tag">${escapeText(tag)}</p>

        <h1 class="hero-title" id="hero-title">
          <span class="sr-only">${escapeText(`${top} ${ransom} ${accent}`)}</span>
          <span class="hero-title-visual" aria-hidden="true">
            <span class="hero-word hero-word--top">${escapeText(top)}</span>
            <span class="hero-word hero-word--ransom">${ransomLetters}${sticker ? `<span class="hero-sticker">${escapeText(sticker)}</span>` : ""}</span>
            <span class="hero-word hero-word--accent">
              <span class="rip-piece rip-piece--top"><span class="rip" style="clip-path:${rip.top}">${escapeText(accent)}</span></span>
              <span class="rip-piece rip-piece--bottom"><span class="rip" style="clip-path:${rip.bottom}">${escapeText(accent)}</span></span>
            </span>
          </span>
        </h1>

        <p class="hero-deck">${escapeText(deck)}</p>

        <div class="hero-actions">
          <a class="hero-cta" href="${escapeText(cta.href)}">
            ${escapeText(cta.label)}
            <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false"><path d="M4 12h14M13 6l6 6-6 6" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="square"/></svg>
          </a>
          <svg class="hero-arrow" viewBox="0 0 120 70" aria-hidden="true" focusable="false">
            <path d="M114 8 C96 44 62 58 18 50" fill="none" stroke="currentColor" stroke-width="3.2" stroke-linecap="round"/>
            <path d="M30 38 L16 50 L32 60" fill="none" stroke="currentColor" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </div>
      </div>

      ${stat ? `
        <div class="hero-badge">
          <span class="hero-badge-value">${escapeText(stat.value)}</span>
          <span class="hero-badge-label">${escapeText(stat.label)}</span>
        </div>` : ""}

      ${footage ? `
        <button type="button" class="hero-footage-toggle" data-state="playing" aria-label="Pause background video" title="Pause background video" hidden>
          <svg class="icon-pause" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M7 5h4v14H7zM13 5h4v14h-4z" fill="currentColor"/></svg>
          <svg class="icon-play" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M8 5v14l11-7z" fill="currentColor"/></svg>
        </button>` : ""}
    </section>
  `;
}

/**
 * Loads and plays a rendered hero's footage, but only for viewers who get
 * motion: tablet/desktop widths (the slab is a small strip on phones),
 * no prefers-reduced-motion and no Save-Data. Everyone else keeps the
 * printed poster. The download starts after the page has loaded and gone
 * idle, playback pauses while the hero is off screen or the tab is hidden,
 * and the toggle gives a persistent pause (WCAG 2.2.2). Listeners are
 * dropped when the hero is removed from `root`.
 */
export function wireHero(root) {
  const video = root.querySelector(".hero-footage-video");
  const toggle = root.querySelector(".hero-footage-toggle");
  if (!video || !toggle) return;

  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const wideEnough = window.matchMedia("(min-width: 861px)");
  const saveData = Boolean(navigator.connection && navigator.connection.saveData);
  const listeners = new AbortController();
  const { signal } = listeners;
  let loaded = false;
  let ready = false;
  let userPaused = false;
  let inView = true;
  let observer = null;
  // The router swaps the hero slot's contents on navigation; stop everything
  // once this hero's video is no longer in the page.
  const mutations = new MutationObserver(() => {
    if (!video.isConnected) teardown();
  });

  const motionAllowed = () => !reducedMotion.matches && wideEnough.matches && !saveData;

  const syncToggle = () => {
    const label = video.paused ? "Play background video" : "Pause background video";
    toggle.dataset.state = video.paused ? "paused" : "playing";
    toggle.setAttribute("aria-label", label);
    toggle.title = label;
  };

  const update = () => {
    if (!video.isConnected) {
      teardown();
      return;
    }
    if (!ready || !motionAllowed()) {
      toggle.hidden = true;
      if (!video.paused) video.pause();
      return;
    }
    toggle.hidden = false;
    if (!loaded) {
      loaded = true;
      video.querySelectorAll("source[data-src]").forEach((source) => {
        source.src = source.dataset.src;
      });
      video.load();
    }
    if (userPaused || !inView || document.hidden) {
      video.pause();
    } else {
      const attempt = video.play();
      if (attempt && attempt.catch) attempt.catch(syncToggle);
    }
    syncToggle();
  };

  function teardown() {
    listeners.abort();
    if (observer) observer.disconnect();
    mutations.disconnect();
    video.pause();
  }

  video.addEventListener("play", syncToggle, { signal });
  video.addEventListener("pause", syncToggle, { signal });
  toggle.addEventListener("click", () => {
    userPaused = !video.paused;
    update();
  }, { signal });
  reducedMotion.addEventListener("change", update, { signal });
  wideEnough.addEventListener("change", update, { signal });
  document.addEventListener("visibilitychange", update, { signal });

  if ("IntersectionObserver" in window) {
    observer = new IntersectionObserver(([entry]) => {
      inView = entry.isIntersecting;
      update();
    });
    observer.observe(video);
  }

  mutations.observe(root, { childList: true });

  // Defer the download until after first paint and the page's own loading.
  const start = () => {
    const whenIdle = window.requestIdleCallback || ((fn) => setTimeout(fn, 200));
    whenIdle(() => {
      ready = true;
      update();
    }, { timeout: 2000 });
  };
  if (document.readyState === "complete") start();
  else window.addEventListener("load", start, { once: true, signal });
}
