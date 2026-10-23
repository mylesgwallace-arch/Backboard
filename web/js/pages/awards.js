// pages/awards.js — Awards Race: MVP, Rookie of the Year, Sixth Man and Most
// Improved, ranked by one box-score measure (value added: points of team
// margin above a replacement player), with a weekly replay of the MVP and
// rookie races. These are indexes, not a model of how voters vote; the page
// says so.
//
// Every number comes from the awards_race tool.

import { getAwardsRace } from "../api.js";
import { teamColor } from "../teamColors.js";
import { tornPhoto, wireTornPhoto } from "../components/tornPhoto.js";
import { emphasisLineChart, wireEmphasisChart } from "../components/charts.js";
import { emptyState, errorBanner, escapeHtml, fixed, safe, seasonLabel, signed, unwrap } from "../components/format.js";

export const meta = {
  title: "Awards Race",
  subtitle: "MVP, Rookie of the Year, Sixth Man and Most Improved, by the box score, week by week.",
};

const SHORT = { mvp: "MVP", roy: "Rookie", sixth: "Sixth Man", mip: "Most Improved" };
let cache = { key: null, data: null };

export function render(container, ctx = {}) {
  const award = ctx.query?.get?.("award") || "mvp";
  const season = Number(ctx.query?.get?.("season")) || null;
  container.innerHTML = `
    <div class="card fade-in leaders-controls">
      <div class="segmented" role="group" aria-label="Award">
        ${Object.entries(SHORT).map(([key, label]) => `<button class="seg ${key === award ? "is-on" : ""}" aria-pressed="${key === award}" data-award="${key}">${label}</button>`).join("")}
      </div>
      <div class="field compact-field leader-season"><label for="aw-season">Season</label>
        <input class="input" id="aw-season" type="number" min="1990" max="2100" value="${season ?? ""}" placeholder="latest"></div>
    </div>
    <div id="aw-body"><div class="card"><div class="skeleton" style="height:480px;"></div></div></div>`;
  const go = (next) => ctx.navigate("/awards", { award: next.award ?? award, ...((next.season ?? season) ? { season: next.season ?? season } : {}) });
  container.querySelectorAll("[data-award]").forEach((b) => b.addEventListener("click", () => go({ award: b.dataset.award })));
  container.querySelector("#aw-season").addEventListener("change", (event) => go({ season: Number(event.target.value) || null }));
  load(container, award, season);
}

async function load(container, award, season) {
  const key = String(season);
  let data = cache.key === key ? cache.data : null;
  if (!data) {
    const res = unwrap(await safe(getAwardsRace({ season })));
    if (!container.isConnected) return;
    if (!res.data) {
      container.querySelector("#aw-body").innerHTML = errorBanner(res.message);
      return;
    }
    data = res.data;
    cache = { key, data };
  }
  const body = container.querySelector("#aw-body");
  container.querySelector("#aw-season").value = data.season;
  const race = data.awards.find((a) => a.key === award) || data.awards[0];
  const candidates = race.candidates;
  if (!candidates.length) {
    body.innerHTML = `<section class="card fade-in"><h2>${escapeHtml(race.label)}</h2>${emptyState("Nobody qualifies yet.")}</section>`;
    return;
  }
  const scoreText = (c) => (race.key === "mip" ? `${signed(c.score, 2)} / game` : fixed(c.score, 0));
  const [first, second, third] = candidates;
  const podium = [second, first, third].filter(Boolean).map((c, i) => {
    const colors = teamColor(c.team_id);
    const rank = candidates.indexOf(c) + 1;
    return `<a class="podium-step podium-${rank}" href="/players?id=${c.person_id}">
      ${tornPhoto({ personId: c.person_id, name: c.name, teamColor: colors.primary, teamAlt: colors.secondary, size: "md" })}
      <span class="podium-rank">${rank}</span>
      <span class="podium-name">${escapeHtml(c.name)}</span>
      <span class="podium-value">${scoreText(c)}</span>
      <span class="podium-meta">${escapeHtml(c.team || "")} ${c.team_record ? `(${escapeHtml(c.team_record)})` : ""} · ${c.games} g</span>
    </a>`;
  }).join("");
  const top = Math.max(...candidates.map((c) => Math.abs(c.score)), 1e-6);
  const rows = candidates.map((c, i) => `<li><a class="leader-row" href="/players?id=${c.person_id}">
      <span class="leader-rank">${i + 1}</span>
      <span class="leader-name">${escapeHtml(c.name)} <small>${escapeHtml(c.team || "")} · ${fixed(c.ppg, 1)}/${fixed(c.rpg, 1)}/${fixed(c.apg, 1)}</small>
        ${c.on_pace_for_65 === false ? `<span class="badge" title="Not on pace for the 65 games MVP voting requires">pace &lt; 65 g</span>` : ""}</span>
      <span class="leader-track"><span class="leader-fill" style="width:${Math.max(2, (Math.abs(c.score) / top) * 100)}%; background:${teamColor(c.team_id).primary};"></span></span>
      <span class="leader-value">${scoreText(c)}</span></a></li>`).join("");
  const trend = data.trend[race.key];
  const series = (trend || []).map((t) => ({ id: t.person_id, label: t.name.split(" ").slice(-1)[0],
    points: t.points.map((p) => ({ x: p.date, y: p.score })) }));
  const maxY = Math.max(1, ...series.flatMap((s) => s.points.map((p) => p.y)));
  body.innerHTML = `
    <section class="card fade-in leaders-card" aria-labelledby="aw-title">
      <div class="card-header"><div><p class="kicker">${seasonLabel(data.season)} · through ${escapeHtml(data.through)}</p>
        <h2 id="aw-title">${escapeHtml(race.label)}</h2>
        <p class="card-subtitle">${escapeHtml(data.method[race.key])}</p></div></div>
      <div class="podium">${podium}</div>
      <ol class="leader-list leader-list--awards" start="1">${rows}</ol>
      <p class="table-note">PPG/RPG/APG after each name. Value added: ${escapeHtml(data.method.value)} An index from box scores, not a prediction of the vote.</p>
    </section>
    ${series.length > 1 && series[0].points.length > 1 ? `<section class="card fade-in" aria-labelledby="aw-trend-title">
      <h2 id="aw-trend-title">How the race moved</h2>
      <p class="card-subtitle">The current top ${series.length}, replayed week by week.</p>
      <div class="chart-wrap" id="aw-trend">${emphasisLineChart(series, { emphasis: series[0].id, yMin: Math.min(0, ...series.flatMap((s) => s.points.map((p) => p.y))),
        yMax: maxY, format: (v) => String(Math.round(v)), label: `${race.label} race by week`, labelTop: series.length })}</div>
    </section>` : ""}`;
  wireTornPhoto(body);
  const chart = body.querySelector("#aw-trend");
  if (chart) wireEmphasisChart(chart, series, { emphasis: series[0].id, format: (v) => String(Math.round(v)) });
}
