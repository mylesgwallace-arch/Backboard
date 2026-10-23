// components/motion.js — the app's motion vocabulary, all of it off under
// prefers-reduced-motion:
//
// * reveal: sheets below the fold drop onto the page as they scroll in;
// * countUp: a big number counts up once when it first appears;
// * uncrumple: a crumpled ball of paper unfolds into the sheet (used for a
//   result the page wants to land: the matchup verdict, the report card, the
//   VS screen). It animates only transform, clip-path and opacity through the
//   Web Animations API, so it runs at the display's frame rate, and the folds
//   are flat-shaded facets (real crumpled paper is faceted, not blurry noise).

export function reduceMotion() {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
}

// ---------------------------------------------------------------------------
// Reveal on scroll
// ---------------------------------------------------------------------------

let revealObserver = null;

function observer() {
  if (!revealObserver && "IntersectionObserver" in window) {
    revealObserver = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("is-in");
        revealObserver.unobserve(entry.target);
      });
    }, { rootMargin: "0px 0px -6% 0px", threshold: 0.04 });
  }
  return revealObserver;
}

/** Mark `elements` to drop in as they scroll into view (staggered per batch). */
export function reveal(elements) {
  const list = [...elements].filter((el) => !el.classList.contains("reveal"));
  const io = observer();
  if (reduceMotion() || !io) return;
  list.forEach((el, index) => {
    el.classList.add("reveal");
    el.style.setProperty("--reveal-delay", `${Math.min(index, 6) * 60}ms`);
    el.style.setProperty("--reveal-tilt", `${index % 2 ? 0.7 : -0.9}deg`);
    io.observe(el);
  });
}

// ---------------------------------------------------------------------------
// Count up
// ---------------------------------------------------------------------------

/** Count `el`'s text from 0 to `to` (decimals, prefix/suffix kept) once. */
export function countUp(el, to, { decimals = 0, prefix = "", suffix = "", duration = 900 } = {}) {
  const final = `${prefix}${Number(to).toFixed(decimals)}${suffix}`;
  if (reduceMotion() || !Number.isFinite(Number(to))) {
    el.textContent = final;
    return;
  }
  const start = performance.now();
  const ease = (t) => 1 - (1 - t) ** 3;
  const tick = (now) => {
    const t = Math.min(1, (now - start) / duration);
    el.textContent = `${prefix}${(Number(to) * ease(t)).toFixed(decimals)}${suffix}`;
    if (t < 1) requestAnimationFrame(tick);
    else el.textContent = final;
  };
  requestAnimationFrame(tick);
}

/** Count up every `[data-count]` inside `root` when it first scrolls into view. */
export function countUpAll(root) {
  // Each element counts once: its own text changes must not restart it.
  const items = [...root.querySelectorAll("[data-count]:not([data-counted])")];
  if (!items.length) return;
  items.forEach((el) => { el.dataset.counted = "1"; });
  const run = (el) => countUp(el, Number(el.dataset.count), {
    decimals: Number(el.dataset.decimals || 0), prefix: el.dataset.prefix || "", suffix: el.dataset.suffix || "",
  });
  if (reduceMotion() || !("IntersectionObserver" in window)) {
    items.forEach(run);
    return;
  }
  const io = new IntersectionObserver((entries) => entries.forEach((entry) => {
    if (!entry.isIntersecting) return;
    io.unobserve(entry.target);
    run(entry.target);
  }), { threshold: 0.3 });
  items.forEach((el) => io.observe(el));
}

// ---------------------------------------------------------------------------
// Uncrumple
// ---------------------------------------------------------------------------

function seeded(seed) {
  let state = (Math.abs(Math.floor(seed)) % 2147483646) + 1;
  return () => {
    state = (state * 16807) % 2147483647;
    return (state - 1) / 2147483646;
  };
}

const POINTS_PER_SIDE = 8;

/** Points walking the box's edge clockwise from the top-left, in %. */
function edgePoints(lo = 0, hi = 100) {
  const points = [];
  const span = hi - lo;
  for (let i = 0; i < POINTS_PER_SIDE; i++) points.push([lo + (span * i) / POINTS_PER_SIDE, lo]);
  for (let i = 0; i < POINTS_PER_SIDE; i++) points.push([hi, lo + (span * i) / POINTS_PER_SIDE]);
  for (let i = 0; i < POINTS_PER_SIDE; i++) points.push([hi - (span * i) / POINTS_PER_SIDE, hi]);
  for (let i = 0; i < POINTS_PER_SIDE; i++) points.push([lo, hi - (span * i) / POINTS_PER_SIDE]);
  return points;
}

/** The same points pulled into a lumpy ball around the centre (a wad of paper). */
function ballPoints(random, aspect) {
  return edgePoints().map(([x, y], i) => {
    const angle = Math.atan2(y - 50, x - 50);
    const radius = (17 + random() * 9) * (i % 2 ? 0.8 : 1);
    return [50 + Math.cos(angle) * radius / Math.max(1, aspect * 0.6), 50 + Math.sin(angle) * radius];
  });
}

function jitter(points, random, amount) {
  return points.map(([x, y]) => [x + (random() - 0.5) * amount, y + (random() - 0.5) * amount]);
}

function polygon(points) {
  return `polygon(${points.map(([x, y]) => `${x.toFixed(2)}% ${y.toFixed(2)}%`).join(", ")})`;
}

/**
 * Flat-shaded fold facets: a jittered grid split into triangles, each lit
 * from the top left by a random tilt. Returns two SVG strings: shadows
 * (multiplied) and highlights (screened), plus the crease lines.
 */
function facetLayers(random, cols = 7, rows = 5) {
  const grid = [];
  for (let r = 0; r <= rows; r++) {
    const row = [];
    for (let c = 0; c <= cols; c++) {
      const edgeX = c === 0 || c === cols;
      const edgeY = r === 0 || r === rows;
      row.push([
        (c / cols) * 100 + (edgeX ? 0 : (random() - 0.5) * (70 / cols)),
        (r / rows) * 100 + (edgeY ? 0 : (random() - 0.5) * (70 / rows)),
      ]);
    }
    grid.push(row);
  }
  let shadows = "";
  let lights = "";
  let creases = "";
  const tri = (a, b, c) => {
    const tilt = random() * 2 - 1; // -1 facing away from the light, +1 facing it
    const points = [a, b, c].map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
    if (tilt < 0) shadows += `<polygon points="${points}" fill="rgba(20,16,10,${(0.12 + -tilt * 0.38).toFixed(3)})"/>`;
    else lights += `<polygon points="${points}" fill="rgba(255,255,255,${(0.1 + tilt * 0.45).toFixed(3)})"/>`;
    creases += `<polygon points="${points}"/>`;
  };
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const [a, b, d, e] = [grid[r][c], grid[r][c + 1], grid[r + 1][c], grid[r + 1][c + 1]];
      if (random() < 0.5) {
        tri(a, b, e);
        tri(a, e, d);
      } else {
        tri(a, b, d);
        tri(b, e, d);
      }
    }
  }
  return { shadows, lights, creases };
}

function overlay(className, content, extra = "") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", `crumple-layer ${className}`);
  svg.setAttribute("viewBox", "0 0 100 100");
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("aria-hidden", "true");
  svg.innerHTML = content + extra;
  return svg;
}

/**
 * Unfold `el` from a crumpled ball of paper. Resolves when it has landed.
 * `seed` makes the folds repeatable for the same content.
 */
export function uncrumple(el, { seed = Date.now(), duration = 1100, delay = 0 } = {}) {
  if (!el || reduceMotion() || typeof el.animate !== "function") return Promise.resolve();
  const random = seeded(seed);
  const rect = el.getBoundingClientRect();
  const aspect = rect.height ? rect.width / rect.height : 1;
  const ball = polygon(ballPoints(random, aspect));
  const rumpled = polygon(jitter(edgePoints(-1, 101), random, 9));
  const settling = polygon(jitter(edgePoints(-2, 102), random, 2.5));
  const flat = polygon(edgePoints(-3, 103));
  const { shadows, lights, creases } = facetLayers(random);
  const shade = overlay("crumple-shade", shadows, `<g class="crumple-creases">${creases}</g>`);
  const light = overlay("crumple-light", lights);
  el.classList.add("is-crumpling");
  el.append(shade, light);

  const spin = random() < 0.5 ? -1 : 1;
  // Each phase has its own easing (the effect itself runs linear): the wad
  // pops in and hangs for a beat, the sheet pulls open, then settles flat.
  const sheet = el.animate([
    { offset: 0, clipPath: ball, opacity: 0, easing: "cubic-bezier(0.3, 1.6, 0.5, 1)",
      transform: `perspective(1100px) translateY(26px) scale(0.32) rotate(${spin * 24}deg) rotateX(34deg) rotateY(${-spin * 26}deg)` },
    { offset: 0.14, clipPath: ball, opacity: 1, easing: "cubic-bezier(0.55, 0, 0.35, 1)",
      transform: `perspective(1100px) translateY(10px) scale(0.48) rotate(${spin * 14}deg) rotateX(26deg) rotateY(${-spin * 20}deg)` },
    { offset: 0.5, clipPath: rumpled, easing: "cubic-bezier(0.2, 0.7, 0.3, 1)",
      transform: `perspective(1100px) translateY(-6px) scale(0.95) rotate(${-spin * 2.5}deg) rotateX(9deg) rotateY(${spin * 6}deg)` },
    { offset: 0.78, clipPath: settling, easing: "cubic-bezier(0.25, 0.8, 0.3, 1)",
      transform: `perspective(1100px) translateY(2px) scale(1.012) rotate(${spin * 0.6}deg) rotateX(-1.5deg) rotateY(0deg)` },
    { offset: 1, clipPath: flat, opacity: 1, transform: "perspective(1100px) none" },
  ], { duration, delay, easing: "linear", fill: "backwards" });
  const folds = [
    { offset: 0, opacity: 1 },
    { offset: 0.5, opacity: 0.85 },
    { offset: 0.78, opacity: 0.3 },
    { offset: 1, opacity: 0 },
  ];
  const timing = { duration, delay, easing: "linear", fill: "both" };
  shade.animate(folds, timing);
  light.animate(folds, timing);
  return sheet.finished.catch(() => {}).then(() => {
    shade.remove();
    light.remove();
    el.classList.remove("is-crumpling");
  });
}

/** Uncrumple `el` the first time it scrolls into view. */
export function uncrumpleOnView(el, options) {
  if (!el || reduceMotion() || !("IntersectionObserver" in window)) return;
  el.classList.add("crumple-pending");
  const io = new IntersectionObserver((entries) => entries.forEach((entry) => {
    if (!entry.isIntersecting) return;
    io.disconnect();
    el.classList.remove("crumple-pending");
    uncrumple(el, options);
  }), { threshold: 0.25 });
  io.observe(el);
}
