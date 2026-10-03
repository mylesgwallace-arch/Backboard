/* zine.js: the shared shell for every Street Zine page.
 *
 *   - injects the torn-edge SVG filters
 *   - builds the Limelight nav (a plain-JS port of the limelight-nav component:
 *     a light bar that slides to the active tab, with its beam falling on the icon)
 *   - adds the footer
 *   - exposes window.Z: small helpers the pages share
 *
 * Load after demo-data.js, zine-data.js and bb.js.
 */
(function () {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
  const reduce = () => reduced.matches;

  /* ---------- torn-edge filters ---------- */
  document.body.insertAdjacentHTML("afterbegin", `<svg width="0" height="0" style="position:absolute" aria-hidden="true" focusable="false"><defs>
    <filter id="tear-a" x="-8%" y="-8%" width="116%" height="116%"><feTurbulence type="fractalNoise" baseFrequency="0.018 0.026" numOctaves="3" seed="4" result="lo"/><feDisplacementMap in="SourceGraphic" in2="lo" scale="17" xChannelSelector="R" yChannelSelector="G" result="d1"/><feTurbulence type="fractalNoise" baseFrequency="0.4" numOctaves="2" seed="9" result="hi"/><feDisplacementMap in="d1" in2="hi" scale="6" xChannelSelector="G" yChannelSelector="B"/></filter>
    <filter id="tear-b" x="-8%" y="-8%" width="116%" height="116%"><feTurbulence type="fractalNoise" baseFrequency="0.024 0.016" numOctaves="3" seed="21" result="lo"/><feDisplacementMap in="SourceGraphic" in2="lo" scale="14" xChannelSelector="G" yChannelSelector="R" result="d1"/><feTurbulence type="fractalNoise" baseFrequency="0.5" numOctaves="2" seed="2" result="hi"/><feDisplacementMap in="d1" in2="hi" scale="5" xChannelSelector="R" yChannelSelector="B"/></filter>
    <filter id="tear-c" x="-8%" y="-8%" width="116%" height="116%"><feTurbulence type="fractalNoise" baseFrequency="0.03 0.02" numOctaves="4" seed="33" result="lo"/><feDisplacementMap in="SourceGraphic" in2="lo" scale="20" xChannelSelector="R" yChannelSelector="B" result="d1"/><feTurbulence type="fractalNoise" baseFrequency="0.45" numOctaves="2" seed="14" result="hi"/><feDisplacementMap in="d1" in2="hi" scale="7" xChannelSelector="B" yChannelSelector="G"/></filter>
    <filter id="tear-edge" x="0" y="-60%" width="100%" height="220%"><feTurbulence type="fractalNoise" baseFrequency="0.012 0.06" numOctaves="4" seed="6" result="lo"/><feDisplacementMap in="SourceGraphic" in2="lo" scale="26" xChannelSelector="R" yChannelSelector="G" result="d1"/><feTurbulence type="fractalNoise" baseFrequency="0.5" numOctaves="2" seed="11" result="hi"/><feDisplacementMap in="d1" in2="hi" scale="7" xChannelSelector="G" yChannelSelector="B"/></filter>
  </defs></svg>`);

  /* ---------- the Limelight nav ---------- */
  const icon = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">${d}</svg>`;
  const PAGES = [
    { id: "home", href: "index.html", label: "Home", icon: icon('<path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 22V12h6v10"/>') },
    { id: "matchups", href: "matchups.html", label: "Matchups", icon: icon('<path d="M13 2 4 14h6l-1 8 9-12h-6l1-8Z"/>') },
    { id: "teams", href: "teams.html", label: "Teams", icon: icon('<circle cx="9" cy="8" r="3"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><circle cx="17.5" cy="9" r="2.4"/><path d="M15.7 13.2A5.6 5.6 0 0 1 21.5 20"/>') },
    { id: "season", href: "season.html", label: "Season", icon: icon('<rect x="3.5" y="5" width="17" height="15" rx="1.5"/><path d="M3.5 10h17M8 3v4M16 3v4"/><path d="M8 15h3M13 15h3"/>') },
    { id: "players", href: "players.html", label: "Players", icon: icon('<circle cx="9" cy="8" r="3.2"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M18 8v6M15 11h6"/>') },
    { id: "ask", href: "assistant.html", label: "Ask", icon: icon('<path d="M4 5.5h16v10.2H9.8L5 20V15.7H4Z"/><path d="M8 9.6h8M8 12.6h5"/>') },
  ];
  const current = document.body.dataset.page || "home";
  const activeIndex = Math.max(0, PAGES.findIndex((p) => p.id === current));

  const brand = `<a class="zbrand" href="index.html" aria-label="Backboard home"><svg viewBox="0 0 48 48" aria-hidden="true" focusable="false"><rect x="4" y="3" width="40" height="26" fill="none" stroke="#ecebe5" stroke-width="3"/><rect x="16" y="12" width="16" height="11" fill="none" stroke="#ee3a1f" stroke-width="3"/><ellipse cx="24" cy="33.2" rx="10.2" ry="2.8" fill="none" stroke="#ee3a1f" stroke-width="3.2"/><path d="M15 34 18.5 46M33 34 29.5 46M24 36v10" fill="none" stroke="#ecebe5" stroke-width="1.6" stroke-linecap="round"/></svg>Backboard</a>`;
  const nav = `<nav class="zn" aria-label="Main">${PAGES.map((p, i) =>
    `<a class="zi" href="${p.href}"${i === activeIndex ? ' aria-current="page"' : ""}>${p.icon}<span>${p.label}</span></a>`).join("")}<div class="zlime" aria-hidden="true"></div></nav>`;
  document.body.insertAdjacentHTML("afterbegin", `<header class="zhead">${brand}${nav}</header>`);

  const items = $$(".zi");
  const lime = $(".zlime");
  const leftFor = (i) => items[i].offsetLeft + items[i].offsetWidth / 2 - lime.offsetWidth / 2;
  const place = (i, animate) => {
    lime.classList.toggle("ready", animate && !reduce());
    lime.style.left = leftFor(i) + "px";
  };
  // The page is a new document on every click, so the bar starts under the tab
  // you came from and slides to the tab you are on.
  let from = activeIndex;
  try { const s = sessionStorage.getItem("zn-prev"); if (s !== null && items[Number(s)]) from = Number(s); sessionStorage.setItem("zn-prev", String(activeIndex)); } catch (e) { /* storage blocked: no slide */ }
  place(from, false);
  if (from !== activeIndex) setTimeout(() => place(activeIndex, true), 60);
  const settle = () => place(activeIndex, false);
  window.addEventListener("resize", settle);
  window.addEventListener("load", settle);
  // Pages build their content in script, so a #section link has to scroll after that is done.
  window.addEventListener("load", () => { const t = location.hash.length > 1 && document.getElementById(decodeURIComponent(location.hash.slice(1))); if (t) t.scrollIntoView({ block: "start", behavior: "instant" }); });
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => { if (from === activeIndex) settle(); });

  /* ---------- footer ---------- */
  const D = window.BB && BB.meta;
  document.body.insertAdjacentHTML("beforeend", `<footer class="zfoot"><div class="wrap"><p>${D ? `Historical NBA data through ${D.latestGame}. Team strength frozen ${D.strengthFrozen}. Every number comes from the production model. Hero footage is a 90s dunk montage edit.` : ""}</p><p>Probabilities are not guarantees.</p></div></footer>`);

  /* ---------- helpers ---------- */
  const INK = "#101010", PAPER = "#f3f1ea";
  const lum = (hex) => { const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4))); return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]; };
  const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
  const inkOn = (hex) => (ratio(hex, INK) >= ratio(hex, PAPER) ? INK : PAPER);
  /** A team face color that stays readable on a given sheet. */
  const colorOn = (t, bg) => (ratio(t.c1, bg) >= 1.6 ? t.c1 : t.c2);

  function countTo(el, to, dec = 1, ms = 900) {
    const from = el._v == null ? 0 : el._v; el._v = to;
    const show = (v) => { el.textContent = dec ? v.toFixed(dec) : String(Math.round(v)); };
    if (reduce()) { show(to); return; }
    const t0 = performance.now();
    const step = (t) => { const k = Math.min((t - t0) / ms, 1); show(from + (to - from) * (1 - Math.pow(1 - k, 3))); if (k < 1) requestAnimationFrame(step); };
    requestAnimationFrame(step);
  }
  const seen = new WeakSet();
  const inView = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting && !seen.has(e.target)) { seen.add(e.target); const c = e.target._count; if (c) c(); } }), { threshold: 0.3 });
  const onFirstView = (el, fn) => { el._count = fn; inView.observe(el); };

  function tiles(row) {
    const r = BB.rng(Number(row.dataset.seed));
    const faces = ["w", "k", "w", "r", "w", "k"];
    row.innerHTML = [...row.dataset.tiles].map((ch) => {
      const face = faces[Math.floor(r() * faces.length)];
      const serif = r() < 0.16 ? " f2" : "";
      const rot = ((r() - 0.5) * 5).toFixed(1), y = ((r() - 0.5) * 0.06).toFixed(3), s = (0.96 + r() * 0.08).toFixed(2);
      return `<span class="tile ${face}${serif}" style="--r:${rot}deg;--y:${y}em;--s:${s}em">${ch}</span>`;
    }).join("");
  }

  let fid = 0;
  function uncrumple(el) {
    if (reduce()) return;
    const id = "crumple" + fid++;
    const f = document.createElementNS(NS, "filter");
    f.setAttribute("id", id); f.setAttribute("x", "-12%"); f.setAttribute("y", "-12%"); f.setAttribute("width", "124%"); f.setAttribute("height", "124%");
    f.innerHTML = `<feTurbulence type="fractalNoise" baseFrequency="0.014 0.024" numOctaves="3" seed="${3 + fid}" result="n"/>
      <feDisplacementMap in="SourceGraphic" in2="n" scale="60" xChannelSelector="R" yChannelSelector="G" result="w"/>
      <feDiffuseLighting in="n" lighting-color="#fff" surfaceScale="8" diffuseConstant="1.1" result="l"><feDistantLight azimuth="235" elevation="50"/></feDiffuseLighting>
      <feComponentTransfer in="l" result="l2"><feFuncR type="linear" slope="1" intercept="0"/><feFuncG type="linear" slope="1" intercept="0"/><feFuncB type="linear" slope="1" intercept="0"/></feComponentTransfer>
      <feComposite in="l2" in2="w" operator="in" result="lc"/><feBlend in="lc" in2="w" mode="multiply"/>`;
    $("svg defs").appendChild(f);
    const disp = f.querySelector("feDisplacementMap"), funcs = f.querySelectorAll("feFuncR,feFuncG,feFuncB");
    el.style.filter = `url(#${id})`;
    let i = 0; const N = 9;
    const tick = () => {
      const p = 1 - i / N;
      disp.setAttribute("scale", (60 * p).toFixed(1));
      funcs.forEach((fn) => { fn.setAttribute("slope", p.toFixed(2)); fn.setAttribute("intercept", (1 - p).toFixed(2)); });
      el.style.transform = `scale(${(0.8 + 0.2 * (1 - p)).toFixed(3)}) rotate(${(-6 * p).toFixed(2)}deg)`;
      if (i++ < N) setTimeout(tick, 105); else { el.style.filter = ""; el.style.transform = ""; f.remove(); }
    };
    tick();
  }

  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const teamById = (id) => BB.teams.find((t) => t.id === id);
  const teamByAbbr = (a) => BB.teams[BB.byAbbr[a]];
  /** Real 2025-26 regular-season record. The demo data only carries playoff teams, so the build adds all 30. */
  const rec = (t) => { const r = window.BACKBOARD_ZINE && BACKBOARD_ZINE.records && BACKBOARD_ZINE.records[t.id]; return r || (t.po ? { w: t.po.wins, l: 82 - t.po.wins } : { w: 0, l: 0 }); };
  const wins = (t) => rec(t).w;
  const wl = (t) => `${rec(t).w}-${rec(t).l}`;

  /** Head-to-head from the build: games and team A wins for the pair, either order. */
  function h2h(idA, idB) {
    const Zd = window.BACKBOARD_ZINE;
    if (!Zd) return null;
    const [lo, hi] = idA < idB ? [idA, idB] : [idB, idA];
    const games = Zd.h2h.games[`${lo}-${hi}`];
    if (games == null) return null;
    const loWins = Zd.h2h.winsA[`${lo}-${hi}`];
    return { games, a: idA === lo ? loWins : games - loWins, b: idA === lo ? games - loWins : loWins };
  }

  /** The live engine, when one is running. Pages work without it. */
  const engine = {
    base: null,
    candidates() {
      const out = [];
      const q = new URLSearchParams(location.search).get("api");
      if (q) out.push(q.replace(/\/$/, ""));
      try { const s = localStorage.getItem("bb-api"); if (s) out.push(s); } catch (e) { /* ignore */ }
      if (location.protocol === "http:" && /^(localhost|127\.0\.0\.1)$/.test(location.hostname) && location.port !== "4180") out.push("");
      out.push("http://127.0.0.1:8000", "http://127.0.0.1:8010");
      return [...new Set(out)];
    },
    async probe() {
      if (this.base !== null) return this.base;
      for (const base of this.candidates()) {
        try {
          const ctl = new AbortController(); const to = setTimeout(() => ctl.abort(), 1500);
          const r = await fetch(base + "/health", { signal: ctl.signal }); clearTimeout(to);
          const j = await r.json();
          if (j && j.status === "ok") { this.base = base; try { localStorage.setItem("bb-api", base); } catch (e) { /* ignore */ } return base; }
        } catch (e) { /* try the next */ }
      }
      this.base = false;
      return false;
    },
    async post(path, body) {
      const base = await this.probe();
      if (base === false) throw new Error("engine offline");
      const r = await fetch(base + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      return r.json();
    },
  };

  window.Z = { $, $$, reduce, countTo, onFirstView, inView, seen, tiles, uncrumple, inkOn, ratio, colorOn, esc, teamById, teamByAbbr, wins, wl, h2h, engine, PAGES };
})();
