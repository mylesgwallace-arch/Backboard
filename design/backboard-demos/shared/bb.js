/* bb.js: the shared Backboard demo kit.
 *
 * Every demo page loads demo-data.js (real production-model output, generated
 * by tools/build_demo_data.py) and this file, so the twelve pages differ only
 * in style, never in facts. Nothing here invents a number.
 *
 *   BB.teams                 30 franchises (id, abbr, city, nick, conf, c1, c2, elo, form, pre, po)
 *   BB.predict(hi, ai)       one matchup: probabilities, projected score, "why" breakdown
 *   BB.state                 the picked matchup + subscribe(); reads ?h=BOS&a=LAL
 *   BB.bindPicker(...)       wires two <select>s (and a swap button) to BB.state
 *   BB.simulate(p, n, seed)  seeded replays of one game (for the "run it 1,000 times" moments)
 *   BB.titleBoard()          2025-26 title odds vs what actually happened
 */
(function () {
  "use strict";
  const D = window.BACKBOARD_DEMO;
  if (!D) {
    console.error("demo-data.js must load before bb.js");
    return;
  }

  const teams = D.teams;
  const byAbbr = Object.fromEntries(teams.map((t, i) => [t.abbr, i]));
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");

  /* ---------- formatting ---------- */
  const pct = (x, d = 1) => (x * 100).toFixed(d);
  const pct0 = (x) => String(Math.round(x * 100));
  const n1 = (x) => (Math.round(x * 10) / 10).toFixed(1);
  const signed = (x, d = 1) => (x >= 0 ? "+" : "-") + Math.abs(x).toFixed(d);
  const wins10 = (t) => `${Math.round(t.form.win10 * 10)}-${10 - Math.round(t.form.win10 * 10)}`;
  const fullName = (t) => `${t.city} ${t.nick}`;
  const list = (n) => Array.from({ length: n }, (_, i) => i);

  /* ---------- a matchup ---------- */
  function verdict(favP) {
    // Same thresholds the app's matchup page uses.
    if (favP < 0.55) return { key: "toss", label: "Toss-up" };
    if (favP >= 0.7) return { key: "strong", label: "High confidence" };
    return { key: "lean", label: "Lean" };
  }

  function predict(hi, ai) {
    const home = teams[hi];
    const away = teams[ai];
    const p = D.prob[hi][ai];
    const w = D.why[hi][ai];
    const s = D.score[hi][ai];
    const homeFav = p >= 0.5;
    const groups = D.groups.map((g, i) => ({
      key: g.key,
      label: g.label,
      // contribution to the HOME win probability (positive helps home)
      value: w[i],
      favors: w[i] >= 0 ? "home" : "away",
    }));
    const sorted = groups.slice().sort((a, b) => Math.abs(b.value) - Math.abs(a.value));
    return {
      hi, ai, home, away,
      pHome: p, pAway: 1 - p,
      fav: homeFav ? "home" : "away",
      favTeam: homeFav ? home : away,
      dogTeam: homeFav ? away : home,
      favP: Math.max(p, 1 - p),
      dogP: Math.min(p, 1 - p),
      verdict: verdict(Math.max(p, 1 - p)),
      // projected score: home margin with an 80% range, and points for each side
      margin: { mid: s[0], lo: s[1], hi: s[2] },
      pts: { home: s[3], away: s[4] },
      groups,
      sorted,
      remainder: w[7],
      homeCourtOnly: w[8],
      eloProb: w[9],
      boostedProb: w[10],
    };
  }

  /* ---------- selection state ---------- */
  const listeners = new Set();
  const params = new URLSearchParams(location.search);
  const fromUrl = (key) => (params.has(key) && params.get(key).toUpperCase() in byAbbr
    ? byAbbr[params.get(key).toUpperCase()] : null);
  const start = D.featured[0];
  const state = {
    hi: fromUrl("h") ?? start[0],
    ai: fromUrl("a") ?? start[1],
    set(hi, ai) {
      if (hi === ai) ai = (ai + 1) % teams.length;
      this.hi = hi;
      this.ai = ai;
      listeners.forEach((fn) => fn(this.pred()));
    },
    swap() { this.set(this.ai, this.hi); },
    pred() { return predict(this.hi, this.ai); },
    subscribe(fn, { immediate = true } = {}) {
      listeners.add(fn);
      if (immediate) fn(this.pred());
      return () => listeners.delete(fn);
    },
  };

  function optionsHTML(selected, disabled) {
    return teams.map((t, i) =>
      `<option value="${i}"${i === selected ? " selected" : ""}${i === disabled ? " disabled" : ""}>${fullName(t)}</option>`
    ).join("");
  }

  /** Wire two <select>s (home/away) and an optional swap button to BB.state. */
  function bindPicker({ homeEl, awayEl, swapEl }) {
    const paint = () => {
      homeEl.innerHTML = optionsHTML(state.hi, state.ai);
      awayEl.innerHTML = optionsHTML(state.ai, state.hi);
    };
    paint();
    homeEl.addEventListener("change", () => { state.set(Number(homeEl.value), state.ai); paint(); });
    awayEl.addEventListener("change", () => { state.set(state.hi, Number(awayEl.value)); paint(); });
    if (swapEl) swapEl.addEventListener("click", () => { state.swap(); paint(); });
    return paint;
  }

  /* ---------- seeded replays ---------- */
  function rng(seed) {
    let a = seed >>> 0;
    return () => {
      a = (a + 0x6d2b79f5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  /** n Bernoulli replays of one game. Returns an array of booleans (home won). */
  function simulate(pHome, n = 1000, seed = 42) {
    const r = rng(seed);
    return Array.from({ length: n }, () => r() < pHome);
  }

  /* ---------- season / playoffs / what-if ---------- */
  function titleBoard() {
    return teams
      .filter((t) => t.po)
      .sort((a, b) => b.po.title - a.po.title);
  }

  const RESULT_LABEL = {
    "won the title": "Won the title",
    "lost in the finals": "Lost in the Finals",
    "lost in the conf finals": "Lost in the conference finals",
    "lost in the conf semifinals": "Lost in the conference semifinals",
    "lost in the first round": "Lost in the first round",
    "lost in the play-in": "Lost in the play-in",
  };

  function whatIf() {
    const w = D.whatIf;
    const byId = Object.fromEntries(teams.map((t) => [t.id, t]));
    return {
      ...w,
      from: byId[w.fromId],
      to: byId[w.toId],
      effects: w.effects.map((e) => ({ ...e, team: byId[e.id] })),
    };
  }

  window.BB = {
    D, teams, byAbbr, meta: D.meta, groups: D.groups, calibration: D.calibration,
    featured: D.featured, reduced,
    predict, verdict, state, bindPicker, optionsHTML,
    rng, simulate, titleBoard, whatIf, RESULT_LABEL,
    fmt: { pct, pct0, n1, signed, wins10, fullName },
    list,
  };
})();
