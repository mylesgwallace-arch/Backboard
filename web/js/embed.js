// embed.js — Backboard widgets for other sites (<iframe src="/embed/...">):
//
// * /embed/team?team=<id>   record, playoff and title odds, the next game;
// * /embed/game?home=<id>&away=<id>   the model's win probability and
//   projected score, and the live win probability while the game is on.
//
// ?theme=light suits a light host page. Every number comes from the same
// tools the site uses; each widget links back to its full page.

import {
  getLiveScoreboard, getOddsHistory, getTeamSchedule, listTeams, predictMargin, predictMatchup,
} from "./api.js";
import { teamColor } from "./teamColors.js";
import { clashingPair, readableInk } from "./colorInk.js";
import { escapeHtml, fixed, safe, unwrap } from "./components/format.js";

const params = new URLSearchParams(location.search);
if (params.get("theme") === "light") document.documentElement.dataset.theme = "light";
const mount = document.querySelector("#w");
const origin = location.origin;
const pct = (p) => (p == null ? "—" : `${Math.round(p * 100)}%`);

function fail(message) {
  mount.innerHTML = `<p class="err">${escapeHtml(message)}</p>`;
}

function footer(href, label) {
  return `<div class="foot"><span>Backboard · model estimate</span><a href="${origin}${href}" target="_blank" rel="noopener">${escapeHtml(label)} ↗</a></div>`;
}

async function teamsById() {
  const res = unwrap(await safe(listTeams()));
  return Object.fromEntries((res.data?.teams || []).map((t) => [t.team_id, t]));
}

async function teamWidget(teamId) {
  const [teams, scheduleRes, oddsRes] = await Promise.all([
    teamsById(),
    safe(getTeamSchedule({ teamId })).then(unwrap),
    safe(getOddsHistory({ teamId })).then(unwrap),
  ]);
  const team = teams[teamId];
  if (!team) return fail("Unknown team.");
  const colors = teamColor(teamId);
  const latest = oddsRes.data?.teams?.[0]?.points?.slice(-1)[0];
  const schedule = scheduleRes.data;
  const next = schedule?.games?.find((g) => !g.played);
  mount.innerHTML = `
    <span class="k">${escapeHtml(team.abbreviation)} · ${schedule ? escapeHtml(`${schedule.season}-${String(schedule.season + 1).slice(-2)}`) : ""}</span>
    <div class="slab" style="--slab:${colors.primary}; --slab-ink:${readableInk(colors.primary)}; margin-top:6px;">
      <span><span class="h" style="display:block;margin:0;color:inherit;">${escapeHtml(team.full_name)}</span></span>
      <b>${escapeHtml(schedule?.record || "")}</b>
    </div>
    <ul class="facts">
      ${latest ? `<li>Playoffs <b>${pct(latest.p_made_playoffs)}</b></li><li>Title <b>${pct(latest.p_champion)}</b></li>
        <li>Proj. wins <b>${fixed(latest.mean_wins, 1)}</b></li>` : schedule ? `<li>Expected wins left <b>${fixed(schedule.remaining.expected_wins, 1)}</b></li>` : ""}
      ${next ? `<li>Next: ${next.home ? "vs" : "at"} <b>${escapeHtml(next.opponent_abbreviation)}</b> ${escapeHtml(next.date.slice(5))} · <b>${pct(next.win_probability)}</b> to win</li>` : ""}
    </ul>
    ${footer(`/teams?team=${teamId}`, "Full team page")}`;
}

function liveChart(history) {
  if (!history?.length) return "";
  const points = history.filter((h) => h.elapsed != null);
  if (points.length < 2) return "";
  const end = Math.max(48, ...points.map((p) => p.elapsed));
  const path = points.map((p, i) => `${i ? "L" : "M"}${((p.elapsed / end) * 300).toFixed(1)},${(70 - p.home_win_probability * 70).toFixed(1)}`).join(" ");
  return `<svg viewBox="0 0 300 70" preserveAspectRatio="none" role="img" aria-label="Home win probability during the game">
    <line x1="0" x2="300" y1="35" y2="35" stroke="rgba(16,16,16,.2)" stroke-dasharray="3 3"/>
    <path d="${path}" fill="none" stroke="#ee3a1f" stroke-width="2.5" vector-effect="non-scaling-stroke"/></svg>`;
}

async function gameWidget(home, away) {
  const [teams, matchRes, marginRes, liveRes] = await Promise.all([
    teamsById(),
    safe(predictMatchup({ homeTeamId: home, awayTeamId: away })).then(unwrap),
    safe(predictMargin({ homeTeamId: home, awayTeamId: away })).then(unwrap),
    safe(getLiveScoreboard()).then(unwrap),
  ]);
  if (!teams[home] || !teams[away]) return fail("Unknown teams.");
  if (!matchRes.data) return fail(matchRes.message);
  const live = (liveRes.data?.games || []).find((g) => g.home_id === home && g.away_id === away && g.state !== "pre");
  const p = live?.live_home_win_probability ?? matchRes.data.prediction.home_win_probability;
  const pair = clashingPair(teamColor(home), teamColor(away));
  const side = (team, color, prob, score) => `<div class="slab" style="--slab:${color}; --slab-ink:${readableInk(color)}">
    <span style="font:700 11px 'Courier Prime',monospace;">${escapeHtml(team.abbreviation)}</span>
    <b>${score != null ? score : pct(prob)}</b>${score != null ? `<span>${pct(prob)} now</span>` : ""}</div>`;
  const margin = marginRes.data;
  mount.innerHTML = `
    <span class="k">${live ? (live.state === "in" ? `Live · ${escapeHtml(live.status_detail || "")}` : "Final") : "Model pick"}</span>
    <div class="row" style="margin-top:6px;">
      ${side(teams[away], pair.second, 1 - p, live?.away_score)}
      <span class="at">@</span>
      ${side(teams[home], pair.first, p, live?.home_score)}
    </div>
    <div class="bar"><span style="flex:${1 - p}; background:${pair.second}"></span><span style="flex:${p}; background:${pair.first}"></span></div>
    ${live ? liveChart(live.history) : margin ? `<ul class="facts"><li>Projected <b>${escapeHtml(teams[away].abbreviation)} ${fixed(margin.predicted_away_points, 0)} – ${fixed(margin.predicted_home_points, 0)} ${escapeHtml(teams[home].abbreviation)}</b></li></ul>` : ""}
    ${footer(`/matchups?home=${home}&away=${away}`, "Full matchup")}`;
  if (live?.state === "in") setTimeout(() => gameWidget(home, away), 30000);
}

const kind = location.pathname.replace(/\/+$/, "").split("/").pop();
if (kind === "team" && Number(params.get("team"))) teamWidget(Number(params.get("team")));
else if (kind === "game" && Number(params.get("home")) && Number(params.get("away"))) gameWidget(Number(params.get("home")), Number(params.get("away")));
else fail("Unknown widget. Use /embed/team?team=<id> or /embed/game?home=<id>&away=<id>.");
