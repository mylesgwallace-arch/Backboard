// pages/leaders.js — League leaders: the top players of a season in one stat
// (per game, shooting, per 100 possessions, or the box-impact model), among
// players who qualify (70% of team games; shooting also needs attempts). The
// top three stand on a podium of torn headshots; the rest are bars.
//
// Every number comes from the stat_leaders tool.

import { getStatLeaders } from "../api.js";
import { teamColor } from "../teamColors.js";
import { tornPhoto, wireTornPhoto } from "../components/tornPhoto.js";
import { csvButton, wireCsv } from "../components/csv.js";
import { errorBanner, escapeHtml, safe, seasonLabel, unwrap } from "../components/format.js";

export const meta = {
  title: "League Leaders",
  subtitle: "Who's on top: scoring, rebounding, shooting and the box-impact model, among players who qualify.",
};

const GROUPS = [
  ["Per game", ["pts", "trb", "ast", "stl", "blk", "tpm", "mpg"]],
  ["Shooting", ["ts", "tp_pct", "ft_pct"]],
  ["Rates & model", ["pts_100", "box_impact", "wins_added"]],
];
const SHORT = {
  pts: "Points", trb: "Rebounds", ast: "Assists", stl: "Steals", blk: "Blocks", tpm: "Threes", mpg: "Minutes",
  ts: "True shooting", tp_pct: "3P%", ft_pct: "FT%", pts_100: "Pts / 100", box_impact: "Box impact", wins_added: "Wins added",
};
const PERCENT = new Set(["ts", "tp_pct", "ft_pct"]);

function show(stat, value) {
  if (PERCENT.has(stat)) return `${(value * 100).toFixed(1)}%`;
  if (stat === "box_impact") return `${value > 0 ? "+" : ""}${value.toFixed(2)}`;
  return value.toFixed(1);
}

export function render(container, ctx = {}) {
  const stat = ctx.query?.get?.("stat") || "pts";
  const season = Number(ctx.query?.get?.("season")) || null;
  container.innerHTML = `
    <div class="card fade-in leaders-controls">
      ${GROUPS.map(([label, stats]) => `<div class="leader-group"><span class="leader-group-label">${label}</span>
        <div class="segmented" role="group" aria-label="${label}">
          ${stats.map((s) => `<button class="seg ${s === stat ? "is-on" : ""}" aria-pressed="${s === stat}" data-stat="${s}">${SHORT[s]}</button>`).join("")}
        </div></div>`).join("")}
      <div class="field compact-field leader-season"><label for="ld-season">Season</label>
        <div class="select-wrap"><select class="select" id="ld-season"><option>…</option></select></div></div>
    </div>
    <div id="ld-body"><div class="card"><div class="skeleton" style="height:460px;"></div></div></div>`;
  const go = (next) => ctx.navigate("/leaders", { stat: next.stat ?? stat, ...(next.season ?? season ? { season: next.season ?? season } : {}) });
  container.querySelectorAll("[data-stat]").forEach((b) => b.addEventListener("click", () => go({ stat: b.dataset.stat })));
  container.querySelector("#ld-season").addEventListener("change", (event) => go({ season: event.target.value }));
  load(container, ctx, stat, season);
}

async function load(container, ctx, stat, season) {
  const res = unwrap(await safe(getStatLeaders({ stat, season, limit: 25 })));
  if (!container.isConnected) return;
  const body = container.querySelector("#ld-body");
  if (!res.data) {
    body.innerHTML = errorBanner(res.message);
    return;
  }
  const data = res.data;
  container.querySelector("#ld-season").innerHTML = data.seasons.map((s) =>
    `<option value="${s}" ${s === data.season ? "selected" : ""}>${seasonLabel(s)}</option>`).join("");
  if (!data.leaders.length) {
    body.innerHTML = `<div class="empty-state">Nobody qualifies yet in ${escapeHtml(data.season_label)} (needs ${data.min_games} games).</div>`;
    return;
  }
  const [first, second, third] = data.leaders;
  const top = data.leaders[0].value;
  const bottom = Math.min(0, ...data.leaders.map((l) => l.value));
  const scale = (v) => (top === bottom ? 100 : Math.max(2, ((v - bottom) / (top - bottom)) * 100));
  const podium = [second, first, third].filter(Boolean).map((leader) => {
    const colors = teamColor(leader.team_id);
    return `<a class="podium-step podium-${leader.rank}" href="/players?id=${leader.person_id}">
      ${tornPhoto({ personId: leader.person_id, name: leader.name, teamColor: colors.primary, teamAlt: colors.secondary, size: "md" })}
      <span class="podium-rank">${leader.rank}</span>
      <span class="podium-name">${escapeHtml(leader.name)}</span>
      <span class="podium-value">${show(stat, leader.value)}</span>
      <span class="podium-meta">${escapeHtml(leader.team || "")} · ${leader.games} g</span>
    </a>`;
  }).join("");
  body.innerHTML = `
    <section class="card fade-in leaders-card" aria-labelledby="ld-title">
      <div class="card-header"><div><h2 id="ld-title">${escapeHtml(data.label)} · ${escapeHtml(data.season_label)}</h2>
        <p class="card-subtitle">${data.qualified_players} players qualify (${data.min_games}+ games${PERCENT.has(stat) ? " and enough attempts" : ""}).
          ${stat === "box_impact" || stat === "wins_added" ? "Box impact is a model estimate from box scores; it sees offense far better than defense." : ""}</p></div>
        <div class="card-tools">${csvButton("ld-csv")}</div></div>
      <div class="podium">${podium}</div>
      <ol class="leader-list" id="ld-list" start="4">
        ${data.leaders.slice(3).map((l) => `<li><a class="leader-row" href="/players?id=${l.person_id}">
          <span class="leader-rank">${l.rank}</span>
          <span class="leader-name">${escapeHtml(l.name)} <small>${escapeHtml(l.team || "")}</small></span>
          <span class="leader-track"><span class="leader-fill" style="width:${scale(l.value)}%; background:${teamColor(l.team_id).primary};"></span></span>
          <span class="leader-value">${show(stat, l.value)}</span></a></li>`).join("")}
      </ol>
      <table class="sr-only" id="ld-table"><thead><tr><th>Rank</th><th>Player</th><th>Team</th><th>Games</th><th>${escapeHtml(data.label)}</th></tr></thead>
        <tbody>${data.leaders.map((l) => `<tr><td>${l.rank}</td><td>${escapeHtml(l.name)}</td><td>${escapeHtml(l.team || "")}</td><td>${l.games}</td><td>${l.value}</td></tr>`).join("")}</tbody></table>
    </section>`;
  wireTornPhoto(body);
  wireCsv(body.querySelector("#ld-csv"), body.querySelector("#ld-table"), `backboard-leaders-${stat}-${data.season}`);
}
