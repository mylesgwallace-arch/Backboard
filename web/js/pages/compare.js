// pages/compare.js — Compare players: a two-player select screen, then the VS
// screen. Each side is one player-season (by default the player's latest
// season with 20+ games): the six percentile ratings head to head, the
// season's per-game numbers, both careers' box impact by age, and each
// player's closest statistical doppelgängers (click one to make it the
// opponent).
//
// Numbers come from player_profile (seasons), player_outlook (percentiles,
// box impact, career trajectory) and similar_players; ratings are the same
// percentiles the character select shows.

import { getPlayerOutlook, getPlayerProfile, getSimilarPlayers } from "../api.js";
import { teamColor } from "../teamColors.js";
import { clashingPair, readableInk } from "../colorInk.js";
import { mountCharacterSelect, seasonCards } from "../components/characterSelect.js";
import { pairLineChart, pairLineTooltip, wireChartTooltips } from "../components/charts.js";
import { tornPhoto, wireTornPhoto } from "../components/tornPhoto.js";
import { shareImage, shareButton } from "../components/share.js";
import { uncrumple } from "../components/motion.js";
import { errorBanner, escapeHtml, fixed, safe, seasonLabel, signed, unwrap } from "../components/format.js";

export const meta = {
  title: "Compare Players",
  subtitle: "Two players, one screen: pick your fighters, then see the tale of the tape.",
};

const RATINGS = [
  ["scoring", "Scoring", ["pts_100"]],
  ["shooting", "Shooting", ["ts"]],
  ["playmaking", "Playmaking", ["ast_100"]],
  ["rebounding", "Rebounding", ["trb_100"]],
  ["defense", "Defense (box)", ["stl_100", "blk_100"]],
  ["impact", "Impact", ["box_impact"]],
];
const MIN_GAMES = 20;

export function render(container, ctx = {}) {
  const q = ctx.query;
  const a = Number(q?.get?.("a")) || null;
  const b = Number(q?.get?.("b")) || null;
  ctx.setHeadless?.(true);
  if (a && b) {
    renderVersus(container, ctx, { a, b, as: Number(q.get("as")) || null, bs: Number(q.get("bs")) || null });
    return;
  }
  container.innerHTML = `<div id="cmp-select"></div>`;
  const start = async () => {
    let locked = null;
    if (a) locked = await lockedCard(a);
    if (!container.isConnected) return;
    mountCharacterSelect(container.querySelector("#cmp-select"), {
      mode: "2p",
      locked,
      onComplete: (p1, p2) => ctx.navigate("/compare", { a: p1.person_id, b: p2.person_id }),
      onPickAllTime: (personId) => ctx.navigate("/compare", a ? { a, b: personId } : { a: personId }),
    });
  };
  start();
}

/** A card for a pre-chosen player: from the season grid, else from their outlook. */
async function lockedCard(personId) {
  const res = await seasonCards(null);
  const found = res.data?.cards.find((c) => c.person_id === personId);
  if (found) return found;
  const side = await loadSide(personId, null);
  return side.error ? null : side.card;
}

// ---------------------------------------------------------------------------
// One side: profile + the chosen season's outlook
// ---------------------------------------------------------------------------

function seasonRow(profile, season) {
  const rows = (profile.seasons?.regular || []).filter((r) => r.season === season);
  return rows.find((r) => r.is_total) || rows[0] || null;
}

function defaultSeason(profile) {
  const totals = {};
  (profile.seasons?.regular || []).forEach((r) => {
    if (r.season >= 1985 && (r.is_total || !totals[r.season])) totals[r.season] = r.g;
  });
  const seasons = Object.keys(totals).map(Number).sort((x, y) => y - x);
  return seasons.find((s) => totals[s] >= MIN_GAMES) ?? seasons[0] ?? null;
}

function ratingsFrom(outlook) {
  const pct = {};
  (outlook?.percentiles || []).forEach((p) => { pct[p.stat] = p.percentile; });
  const ratings = {};
  RATINGS.forEach(([key, , stats]) => {
    const values = stats.map((s) => pct[s]).filter((v) => v != null);
    // Shown 0-99, like the character select.
    ratings[key] = values.length ? Math.min(99, Math.round(values.reduce((x, y) => x + y, 0) / values.length)) : null;
  });
  return ratings;
}

async function loadSide(personId, season) {
  const profile = unwrap(await safe(getPlayerProfile(personId)));
  if (!profile.data) return { error: profile.message };
  const chosen = season || defaultSeason(profile.data);
  const outlook = unwrap(await safe(getPlayerOutlook({ personId, season: chosen })));
  const row = chosen ? seasonRow(profile.data, chosen) : null;
  const bio = profile.data.bio;
  const teamId = row?.team_ids?.[row.team_ids.length - 1] ?? row?.team_id ?? profile.data.last_team?.team_id;
  const games = row?.g || 0;
  const per = (key) => (row && games ? row[key] / games : null);
  const ratings = ratingsFrom(outlook.data);
  return {
    personId,
    profile: profile.data,
    outlook: outlook.data,
    outlookMessage: outlook.message,
    season: chosen,
    row,
    seasons: [...new Set((profile.data.seasons?.regular || []).map((r) => r.season))].filter((s) => s >= 1985).sort((x, y) => y - x),
    card: {
      person_id: personId,
      name: bio.name,
      team_id: teamId,
      team: row?.team || profile.data.last_team?.abbreviation || "",
      position: bio.positions?.join("-") || null,
      age: row?.age ?? null,
      games,
      mpg: per("mp") != null ? Number(per("mp").toFixed(1)) : null,
      ppg: per("pts") != null ? Number(per("pts").toFixed(1)) : null,
      rpg: per("trb") != null ? Number(per("trb").toFixed(1)) : null,
      apg: per("ast") != null ? Number(per("ast").toFixed(1)) : null,
      ratings,
      overall: ratings.impact,
      tags: (outlook.data?.tags || []).map((t) => t.label),
    },
  };
}

// ---------------------------------------------------------------------------
// The VS screen
// ---------------------------------------------------------------------------

async function renderVersus(container, ctx, params) {
  container.innerHTML = `<div class="vs-arena is-loading"><div class="skeleton" style="height:420px;"></div></div>`;
  const [left, right] = await Promise.all([loadSide(params.a, params.as), loadSide(params.b, params.bs)]);
  if (!container.isConnected) return;
  if (left.error || right.error) {
    container.innerHTML = errorBanner(left.error || right.error);
    return;
  }
  const pair = clashingPair(teamColor(left.card.team_id), teamColor(right.card.team_id));
  const colors = [pair.first, pair.second];
  document.title = `${left.card.name} vs ${right.card.name} · Backboard`;
  const go = (next) => ctx.navigate("/compare", {
    a: next.a ?? params.a, b: next.b ?? params.b,
    ...(next.as ?? left.season ? { as: next.as ?? left.season } : {}),
    ...(next.bs ?? right.season ? { bs: next.bs ?? right.season } : {}),
  });

  const sideMarkup = (side, color, slot) => {
    const c = side.card;
    const alt = teamColor(c.team_id).secondary;
    return `
      <div class="vs-side vs-side--${slot}" style="--team:${color}; --team-alt:${alt}; --team-ink:${readableInk(color)};">
        <span class="vs-slot">${slot === "left" ? "P1" : "P2"}</span>
        ${tornPhoto({ personId: c.person_id, name: c.name, teamColor: color, teamAlt: alt })}
        <div class="vs-id">
          <p class="vs-meta">${escapeHtml([c.team, c.position, c.age != null ? `age ${c.age}` : null].filter(Boolean).join(" · "))}</p>
          <h2 class="vs-name"><a href="/players?id=${c.person_id}">${escapeHtml(c.name)}</a></h2>
          <div class="vs-season field compact-field"><label for="vs-season-${slot}">Season</label>
            <div class="select-wrap"><select class="select" id="vs-season-${slot}" data-slot="${slot}">
              ${side.seasons.map((s) => `<option value="${s}" ${s === side.season ? "selected" : ""}>${seasonLabel(s)}</option>`).join("")}
            </select></div></div>
          <div class="vs-ovr"><b>${c.overall ?? "—"}</b><span>Impact<br>percentile</span></div>
        </div>
      </div>`;
  };

  const tally = RATINGS.reduce((acc, [key]) => {
    const l = left.card.ratings[key];
    const r = right.card.ratings[key];
    if (l == null || r == null || l === r) return acc;
    acc[l > r ? 0 : 1] += 1;
    return acc;
  }, [0, 0]);

  container.innerHTML = `
    <section class="vs-arena" id="vs-arena" aria-label="${escapeHtml(left.card.name)} versus ${escapeHtml(right.card.name)}"
      style="--left:${colors[0]}; --right:${colors[1]};">
      ${sideMarkup(left, colors[0], "left")}
      <div class="vs-splat" aria-hidden="true"><span>VS</span></div>
      ${sideMarkup(right, colors[1], "right")}
    </section>
    <div class="vs-actions">
      <button type="button" class="btn btn-secondary" id="vs-swap">⇄ Swap sides</button>
      <a class="btn btn-secondary" href="/compare?a=${params.a}">Re-pick P2</a>
      <a class="btn btn-secondary" href="/compare">New matchup</a>
      ${shareButton("vs-share")}
    </div>

    <section class="card fade-in" aria-labelledby="vs-tape">
      <div class="card-header"><div><h2 id="vs-tape">Tale of the tape</h2>
        <p class="card-subtitle">Percentiles among qualified players in each one's own season (${escapeHtml(seasonLabel(left.season))} and ${escapeHtml(seasonLabel(right.season))}), so different eras compare fairly.
          ${tally[0] + tally[1] ? `${escapeHtml(left.card.name)} rates higher in ${tally[0]}, ${escapeHtml(right.card.name)} in ${tally[1]}.` : ""}
          Defense here is steals and blocks only.</p></div></div>
      <div class="tot" role="table" aria-label="Ratings head to head">
        <div class="tot-head" role="row"><span role="columnheader">${escapeHtml(left.card.name)}</span><span role="columnheader">Rating</span><span role="columnheader">${escapeHtml(right.card.name)}</span></div>
        ${RATINGS.map(([key, label]) => {
          const l = left.card.ratings[key];
          const r = right.card.ratings[key];
          const lead = l != null && r != null && l !== r ? (l > r ? "left" : "right") : "";
          return `<div class="tot-row ${lead ? `lead-${lead}` : ""}" role="row">
            <span class="tot-side tot-left" role="cell"><b>${l ?? "—"}</b><span class="tot-bar"><span style="width:${l ?? 0}%; background:${colors[0]};"></span></span></span>
            <span class="tot-label" role="rowheader">${label}</span>
            <span class="tot-side tot-right" role="cell"><span class="tot-bar"><span style="width:${r ?? 0}%; background:${colors[1]};"></span></span><b>${r ?? "—"}</b></span>
          </div>`;
        }).join("")}
      </div>
      ${left.outlookMessage || right.outlookMessage ? `<p class="table-note">${escapeHtml(left.outlookMessage || right.outlookMessage)}</p>` : ""}
    </section>

    <section class="card fade-in" aria-labelledby="vs-numbers">
      <div class="card-header"><h2 id="vs-numbers">The numbers</h2></div>
      <div class="table-scroll"><table class="data-table vs-table">
        <thead><tr><th scope="col">Per game</th><th class="num" scope="col">${escapeHtml(left.card.name)}<br><small>${seasonLabel(left.season)}</small></th>
          <th class="num" scope="col">${escapeHtml(right.card.name)}<br><small>${seasonLabel(right.season)}</small></th></tr></thead>
        <tbody>${numberRows(left, right).map(([label, l, r, better]) => `<tr>
          <th scope="row">${label}</th>
          <td class="num ${better === "left" ? "is-best" : ""}">${l}</td>
          <td class="num ${better === "right" ? "is-best" : ""}">${r}</td></tr>`).join("")}</tbody>
      </table></div>
    </section>

    <section class="card fade-in" aria-labelledby="vs-arcs">
      <div class="card-header"><div><h2 id="vs-arcs">Careers, side by side</h2>
        <p class="card-subtitle">Box impact (the model's estimate of team net rating per 100 possessions on the floor, vs an average player) at each age.</p></div></div>
      <div class="chart-wrap" id="vs-arc-chart"></div>
    </section>

    <section class="card fade-in" aria-labelledby="vs-twins">
      <div class="card-header"><div><h2 id="vs-twins">Doppelgängers</h2>
        <p class="card-subtitle">The seasons in history most like each one's (box-score style, relative to their league). Pick one to make it the opponent.</p></div></div>
      <div class="grid-2" id="vs-twin-lists">
        <div class="skeleton" style="height:220px;"></div><div class="skeleton" style="height:220px;"></div>
      </div>
    </section>`;

  wireTornPhoto(container);
  const arena = container.querySelector("#vs-arena");
  uncrumple(arena.querySelector(".vs-splat"), { seed: params.a + params.b, duration: 900, delay: 250 });
  container.querySelectorAll("[data-slot]").forEach((select) => select.addEventListener("change", () =>
    go(select.dataset.slot === "left" ? { as: Number(select.value) } : { bs: Number(select.value) })));
  container.querySelector("#vs-swap").addEventListener("click", () =>
    ctx.navigate("/compare", { a: params.b, b: params.a, ...(right.season ? { as: right.season } : {}), ...(left.season ? { bs: left.season } : {}) }));
  container.querySelector("#vs-share").addEventListener("click", () => shareImage({
    kicker: "Compare players",
    title: `${left.card.name} vs ${right.card.name}`,
    left: { name: left.card.name, sub: `${left.card.team} · ${seasonLabel(left.season)}`, value: left.card.overall ?? "—", label: "impact percentile", color: colors[0] },
    right: { name: right.card.name, sub: `${right.card.team} · ${seasonLabel(right.season)}`, value: right.card.overall ?? "—", label: "impact percentile", color: colors[1] },
    lines: [`Rates higher: ${left.card.name} in ${tally[0]} of 6, ${right.card.name} in ${tally[1]} of 6.`],
  }, { filename: "backboard-compare.png" }));

  // Career arcs by age.
  const arc = (side, key) => ({
    key, label: side.card.name, short: side.card.name.split(" ").pop(),
    points: (side.outlook?.trajectory || []).map((t) => ({ x: t.age, y: t.box_impact, label: t.season_label })),
  });
  const arcs = [arc(left, "a"), arc(right, "b")];
  const arcMount = container.querySelector("#vs-arc-chart");
  arcMount.innerHTML = pairLineChart(arcs[0], arcs[1]) || `<p class="empty-state">No seasons from 1985-86 on to chart.</p>`;
  wireChartTooltips(arcMount, pairLineTooltip(arcs[0], arcs[1]));

  // Doppelgängers.
  const twins = await Promise.all([left, right].map((side) =>
    side.season ? safe(getSimilarPlayers({ personId: side.personId, season: side.season, limit: 6 })).then(unwrap) : { data: null, message: "No season." }));
  if (!container.isConnected) return;
  container.querySelector("#vs-twin-lists").innerHTML = twins.map((res, i) => {
    const side = i ? right : left;
    if (!res.data) return `<div class="empty-state">${escapeHtml(res.message)}</div>`;
    return `<div><h3 class="section-title">Like ${escapeHtml(side.card.name)}, ${seasonLabel(side.season)}</h3>
      <ol class="twin-list">${res.data.comps.map((c) => `<li><button type="button" class="twin" data-twin="${c.person_id}" data-season="${c.season}" data-slot="${i ? "left" : "right"}">
        <span class="twin-sim">${Math.round(c.similarity)}</span>
        <span class="twin-name">${escapeHtml(c.name)} <small>${escapeHtml(c.season_label)} · age ${c.age ?? "?"}</small></span>
        <span class="twin-go">VS ▸</span></button></li>`).join("")}</ol></div>`;
  }).join("");
  container.querySelectorAll("[data-twin]").forEach((button) => button.addEventListener("click", () => {
    const id = Number(button.dataset.twin);
    const season = Number(button.dataset.season);
    // A twin of P1 challenges P1 (replaces P2), and vice versa.
    go(button.dataset.slot === "right" ? { b: id, bs: season } : { a: id, as: season });
  }));
}

function numberRows(left, right) {
  const L = left.row || {};
  const R = right.row || {};
  const per = (row, key) => (row.g ? row[key] / row.g : null);
  const rows = [
    ["Points", per(L, "pts"), per(R, "pts"), 1],
    ["Rebounds", per(L, "trb"), per(R, "trb"), 1],
    ["Assists", per(L, "ast"), per(R, "ast"), 1],
    ["Steals", per(L, "stl"), per(R, "stl"), 1],
    ["Blocks", per(L, "blk"), per(R, "blk"), 1],
    ["Turnovers", per(L, "tov"), per(R, "tov"), -1],
    ["Minutes", per(L, "mp"), per(R, "mp"), 0],
    ["True shooting", L.ts_pct, R.ts_pct, 1, "pct"],
    ["3P%", L.tp_pct, R.tp_pct, 1, "pct"],
    ["Games", L.g, R.g, 0, "int"],
    ["Box impact", left.outlook?.impact?.box_impact, right.outlook?.impact?.box_impact, 1, "signed"],
    ["Wins added", left.outlook?.impact?.wins_added, right.outlook?.impact?.wins_added, 1, "signed"],
  ];
  return rows.map(([label, l, r, direction, kind]) => {
    const show = (v) => (v == null || !Number.isFinite(Number(v)) ? "—"
      : kind === "pct" ? `${(v * 100).toFixed(1)}%` : kind === "int" ? String(v) : kind === "signed" ? signed(v, kind === "signed" && label === "Box impact" ? 2 : 1) : fixed(v));
    let better = "";
    if (direction && l != null && r != null && l !== r) better = (l > r) === (direction > 0) ? "left" : "right";
    return [label, show(l), show(r), better];
  });
}
