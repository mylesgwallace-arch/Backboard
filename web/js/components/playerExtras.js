// components/playerExtras.js — two Players tabs:
//
// * Next game: the player's forecast line (points, rebounds, assists, threes)
//   for their team's next game, with an 80% range and the last ten games
//   beside it (player_game_forecast);
// * Career arc: box impact season by season against the most similar
//   players' whole careers, with the range those careers took next
//   (career_arc).
//
// Numbers come from the tools; this module only lays them out.

import { getCareerArc, getPlayerForecast } from "../api.js";
import { arcCompsChart, arcCompsTooltip, wireChartTooltips } from "./charts.js";
import { fetchTeams } from "./teamSelect.js";
import { emptyState, errorBanner, escapeHtml, fixed, safe, signed, unwrap } from "./format.js";

const STAT_ORDER = ["pts", "reb", "ast", "tpm"];

export async function renderNextGame(panel, personId, isCurrent) {
  panel.innerHTML = `<div class="skeleton" style="height:220px;"></div>`;
  const [res, teams] = await Promise.all([
    safe(getPlayerForecast({ personId })).then(unwrap),
    fetchTeams().catch(() => []),
  ]);
  if (!isCurrent()) return;
  if (!res.data) {
    panel.innerHTML = errorBanner(res.message);
    return;
  }
  const f = res.data;
  const name = (id) => teams.find((t) => t.team_id === id)?.abbreviation || id;
  const bars = STAT_ORDER.map((key) => {
    const s = f.stats[key];
    // Each stat on its own scale, wide enough for the range and the recent games.
    const max = Math.max(1, s.high, ...f.recent_games.map((g) => g[key])) * 1.08;
    const left = (s.low / max) * 100;
    const width = Math.max(1.5, ((s.high - s.low) / max) * 100);
    const mid = (s.expected / max) * 100;
    const recent = f.recent_games.map((g) => g[key]);
    return `<div class="fc-row">
      <span class="fc-label">${escapeHtml(s.label)}</span>
      <span class="fc-track" role="img" aria-label="${escapeHtml(s.label)}: ${fixed(s.expected, 1)} expected, ${s.low} to ${s.high} likely">
        <span class="fc-range" style="left:${left}%; width:${width}%"></span>
        ${recent.map((v) => `<i class="fc-dot" style="left:${Math.min(100, (v / max) * 100)}%"></i>`).join("")}
        <span class="fc-mid" style="left:${mid}%"></span>
      </span>
      <span class="fc-value"><b>${fixed(s.expected, 1)}</b> <small>${s.low}–${s.high}</small></span>
      <span class="fc-meta">season ${s.season_average == null ? "—" : fixed(s.season_average, 1)} · opp ×${fixed(s.opponent_factor, 2)}</span>
    </div>`;
  }).join("");
  const game = f.game;
  const where = f.home == null ? "" : f.home ? "vs" : "at";
  const backtest = f.backtest?.stats;
  panel.innerHTML = `
    <div class="fc-head">
      <p class="kicker">${game ? `Next game · ${escapeHtml(game.date.slice(0, 10))}` : "Forecast"}</p>
      <h3 class="fc-title">${escapeHtml(where)} ${escapeHtml(String(name(f.opponent_id)))} · ${fixed(f.expected_minutes, 0)} minutes expected</h3>
    </div>
    <div class="fc-list">${bars}</div>
    <p class="table-note">Bar: the 80% range · line: the forecast · dots: the last ${f.recent_games.length} games.
      Opponent factor: what this opponent has allowed lately against the league, half-weighted. Assumes ${escapeHtml(f.name)} plays.</p>
    ${backtest ? `<details class="collapsible mt-1"><summary>How good are these forecasts?</summary>
      <div class="table-scroll"><table class="data-table compact-table"><thead><tr><th>Stat</th><th class="num">Avg miss</th><th class="num">Season-average miss</th><th class="num">In the 80% range</th></tr></thead>
      <tbody>${STAT_ORDER.map((key) => backtest[key] ? `<tr><th>${escapeHtml(backtest[key].label)}</th><td class="num">${fixed(backtest[key].mae, 2)}</td>
        <td class="num">${fixed(backtest[key].naive_mae, 2)}</td><td class="num">${Math.round(backtest[key].interval_coverage * 100)}%</td></tr>` : "").join("")}</tbody></table></div>
      <p class="table-note">${f.backtest.test_player_games.toLocaleString("en-US")} player-games in ${f.backtest.test_seasons.join("–")}, forecast from earlier games only. Whole-number stats land inside the range a little more than 80% of the time.</p>
    </details>` : ""}
    <div class="table-scroll mt-1"><table class="data-table compact-table"><thead><tr><th>Date</th><th>Opp</th><th class="num">Min</th><th class="num">Pts</th><th class="num">Reb</th><th class="num">Ast</th><th class="num">3PM</th></tr></thead>
      <tbody>${f.recent_games.map((g) => `<tr><td>${escapeHtml(g.date)}</td><td>${escapeHtml(String(name(g.opponent_id)))}</td>
        <td class="num">${fixed(g.minutes, 0)}</td><td class="num">${g.pts}</td><td class="num">${g.reb}</td><td class="num">${g.ast}</td><td class="num">${g.tpm}</td></tr>`).join("")}</tbody></table></div>`;
}

export async function renderCareerArc(panel, personId, isCurrent) {
  panel.innerHTML = `<div class="skeleton" style="height:300px;"></div>`;
  const res = unwrap(await safe(getCareerArc({ personId })));
  if (!isCurrent()) return;
  if (!res.data) {
    panel.innerHTML = errorBanner(res.message);
    return;
  }
  const data = res.data;
  if (!data.arc.length) {
    panel.innerHTML = emptyState("No seasons with box-impact numbers.");
    return;
  }
  const next = data.band.find((b) => b.box_impact_median != null);
  panel.innerHTML = `
    <p class="text-muted chart-intro">${escapeHtml(data.name)}'s box impact by season (red) against the ${data.comps.length} most similar players at age ${data.age ?? "?"} (gray), lined up on the matching season.
      Ahead: the middle half of what the 30 closest went on to do.</p>
    <div class="chart-wrap" id="arc-chart">${arcCompsChart(data.arc, data.comps, data.band)}</div>
    <div class="stat-grid mt-1">
      <div class="stat-tile"><div class="stat-label">Peak · ${data.peak.season}-${String(data.peak.season + 1).slice(-2)}, age ${data.peak.age ?? "?"}</div>
        <div class="stat-value">${signed(data.peak.box_impact, 2)}</div></div>
      ${next ? `<div class="stat-tile"><div class="stat-label">Next season, comparables' median</div>
        <div class="stat-value">${signed(next.box_impact_median, 2)}</div>
        <div class="text-muted">middle half ${signed(next.box_impact_p25, 2)} to ${signed(next.box_impact_p75, 2)}</div></div>
      <div class="stat-tile"><div class="stat-label">Still regulars in 3 years</div>
        <div class="stat-value">${data.band[2]?.share_still_regulars == null ? "—" : `${Math.round(data.band[2].share_still_regulars * 100)}%`}</div>
        <div class="text-muted">of comparables with a known future</div></div>` : ""}
    </div>
    <div class="section-title mt-2">The comparables</div>
    <div class="table-scroll"><table class="data-table compact-table"><thead><tr><th>Player</th><th>Matched season</th><th class="num">Age</th><th class="num">Similarity</th><th class="num">Then</th><th class="num">Best after</th></tr></thead>
      <tbody>${data.comps.map((c) => {
        const then = c.arc.find((p) => p.offset === 0);
        const after = c.arc.filter((p) => p.offset > 0 && p.box_impact != null);
        const best = after.length ? Math.max(...after.map((p) => p.box_impact)) : null;
        return `<tr><th><a href="/players?id=${c.person_id}">${escapeHtml(c.name)}</a></th><td>${c.matched_season}-${String(c.matched_season + 1).slice(-2)}</td>
          <td class="num">${c.age ?? "—"}</td><td class="num">${fixed(c.similarity, 0)}</td><td class="num">${then ? signed(then.box_impact, 2) : "—"}</td>
          <td class="num">${best == null ? "—" : signed(best, 2)}</td></tr>`;
      }).join("")}</tbody></table></div>
    <p class="table-note">${escapeHtml(data.method)}</p>`;
  wireChartTooltips(panel.querySelector("#arc-chart"), arcCompsTooltip(data.arc, data.band));
}
