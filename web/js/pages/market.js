// pages/market.js — Model vs Market: the production model's win
// probabilities next to the betting market's (sportsbook lines stored by the
// daily refresh, the bookmaker's margin removed). Today's biggest
// disagreements first, then the season's scorecard: whose numbers were
// closer on the games already played, and how that count grew.
//
// Every number comes from the model_vs_market tool.

import { getModelVsMarket } from "../api.js";
import { teamColor } from "../teamColors.js";
import { emphasisLineChart, wireEmphasisChart } from "../components/charts.js";
import { emptyState, errorBanner, escapeHtml, fixed, safe, seasonLabel, unwrap } from "../components/format.js";

export const meta = {
  title: "Model vs Market",
  subtitle: "Where the model and the sportsbooks disagree, and whose numbers have been closer.",
};

const pctText = (p) => `${Math.round(p * 100)}%`;
const moneyline = (v) => (v == null ? "—" : v > 0 ? `+${v}` : String(v));

export function render(container, ctx = {}) {
  const date = ctx.query?.get?.("date") || null;
  container.innerHTML = `
    <div class="card fade-in mk-controls">
      <div class="field compact-field"><label for="mk-date">Slate date</label>
        <input type="date" class="input" id="mk-date" value="${escapeHtml(date || "")}"></div>
      <button type="button" class="btn btn-secondary" id="mk-today">Today</button>
    </div>
    <div id="mk-body"><div class="card"><div class="skeleton" style="height:420px;"></div></div></div>`;
  container.querySelector("#mk-date").addEventListener("change", (event) =>
    ctx.navigate("/market", event.target.value ? { date: event.target.value } : null));
  container.querySelector("#mk-today").addEventListener("click", () => ctx.navigate("/market", null));
  load(container, date);
}

async function load(container, date) {
  const res = unwrap(await safe(getModelVsMarket({ date })));
  if (!container.isConnected) return;
  const body = container.querySelector("#mk-body");
  if (!res.data) {
    body.innerHTML = errorBanner(res.message);
    return;
  }
  const data = res.data;
  container.querySelector("#mk-date").value = data.date;
  if (!data.lines_stored) {
    body.innerHTML = `<div class="card fade-in"><h2>No betting lines stored yet</h2>
      <p class="text-muted">Lines come from the daily refresh, which keeps the latest pregame line for today's and tomorrow's games.
      Run it once (<code>python src/live_results.py</code>, or the scheduled task the setup script creates) and this page fills in from that day on.</p></div>`;
    return;
  }
  body.innerHTML = `${scorecard(data)}${slate(data)}`;
  const chart = body.querySelector("#mk-running");
  if (chart) {
    const series = runningSeries(data.running);
    wireEmphasisChart(chart, series, { emphasis: "model", format: (v) => String(Math.round(v)) });
  }
}

function runningSeries(running) {
  return [
    { id: "market", label: "Market", points: running.map((r) => ({ x: r.date, y: r.market_closer })) },
    { id: "model", label: "Model", points: running.map((r) => ({ x: r.date, y: r.model_closer })) },
  ];
}

function scorecard(data) {
  const card = data.scorecard;
  if (!card) {
    return `<section class="card fade-in"><h2>${seasonLabel(data.season)} scorecard</h2>
      <p class="text-muted">No game with a stored line has been played yet this season.</p></section>`;
  }
  const better = (a, b) => (a < b ? "is-better" : "");
  const leader = card.model_closer === card.market_closer ? "Dead even"
    : card.model_closer > card.market_closer ? "The model leads" : "The market leads";
  // A y range that splits into whole-number ticks.
  const max = Math.max(4, Math.ceil(Math.max(card.model_closer, card.market_closer) / 4) * 4);
  return `<section class="card fade-in mk-score" aria-labelledby="mk-score-title">
    <div class="card-header"><div><p class="kicker">${seasonLabel(data.season)} · ${card.games} games with a line</p>
      <h2 class="tabloid-headline" id="mk-score-title">${leader}</h2></div></div>
    <div class="mk-tally">
      <div class="mk-side"><span class="kicker">Model closer</span><b class="mk-big" data-count="${card.model_closer}">${card.model_closer}</b></div>
      <span class="mk-vs" aria-hidden="true">vs</span>
      <div class="mk-side"><span class="kicker">Market closer</span><b class="mk-big" data-count="${card.market_closer}">${card.market_closer}</b></div>
    </div>
    <div class="table-scroll"><table class="data-table compact-table mk-grades"><thead><tr><th></th><th class="num">Log loss</th><th class="num">Brier</th><th class="num">Picks right</th></tr></thead>
      <tbody>
        <tr><th>Model</th><td class="num ${better(card.model.log_loss, card.market.log_loss)}">${fixed(card.model.log_loss, 3)}</td>
          <td class="num ${better(card.model.brier, card.market.brier)}">${fixed(card.model.brier, 3)}</td><td class="num">${Math.round(card.model.accuracy * 100)}%</td></tr>
        <tr><th>Market</th><td class="num ${better(card.market.log_loss, card.model.log_loss)}">${fixed(card.market.log_loss, 3)}</td>
          <td class="num ${better(card.market.brier, card.model.brier)}">${fixed(card.market.brier, 3)}</td><td class="num">${Math.round(card.market.accuracy * 100)}%</td></tr>
      </tbody></table></div>
    <p class="table-note">Lower log loss and Brier are better. The two picked different winners ${card.picks_disagreed} times; the model was right in ${card.model_won_disagreements}.
      On average they were ${fixed(card.mean_abs_gap * 100, 1)} points apart.</p>
    ${data.running.length > 1 ? `<div class="chart-wrap mt-1" id="mk-running">${emphasisLineChart(runningSeries(data.running), {
      emphasis: "model", yMin: 0, yMax: max, format: (v) => String(Math.round(v)), label: "Games where each was closer, running count", labelTop: 2 })}</div>` : ""}
  </section>`;
}

function slate(data) {
  if (!data.slate.length) {
    return `<section class="card fade-in"><h2>${escapeHtml(data.date)}</h2>${emptyState("No stored lines for games on this day.")}</section>`;
  }
  const rows = data.slate.map((g) => {
    const width = Math.min(50, Math.abs(g.gap) * 250);
    const towardHome = g.gap > 0;
    const color = teamColor(towardHome ? g.home.teamId : g.away.teamId).primary;
    return `<tr class="${g.big_gap ? "is-hot" : ""}">
      <th><a href="/matchups?home=${g.home.teamId}&away=${g.away.teamId}">${escapeHtml(g.away.abbreviation)} @ ${escapeHtml(g.home.abbreviation)}</a>
        ${g.status === "final" ? `<small class="text-muted">final · ${g.home_won ? escapeHtml(g.home.abbreviation) : escapeHtml(g.away.abbreviation)} won</small>` : ""}</th>
      <td class="num">${pctText(g.model_home_probability)}</td>
      <td class="num">${pctText(g.market_home_probability)}</td>
      <td class="mk-gap-cell"><span class="mk-gap" role="img" aria-label="Model ${g.gap > 0 ? "higher" : "lower"} on the home team by ${Math.round(Math.abs(g.gap) * 100)} points">
        <span class="mk-gap-fill" style="${towardHome ? "left:50%" : `right:50%`}; width:${width}%; background:${color};"></span></span>
        <small>${g.gap > 0 ? "+" : "−"}${Math.round(Math.abs(g.gap) * 100)}</small></td>
      <td class="num">${g.home_spread == null ? "—" : `${g.home_spread > 0 ? "+" : ""}${g.home_spread}`}</td>
      <td class="num">${moneyline(g.away_moneyline)} / ${moneyline(g.home_moneyline)}</td>
    </tr>`;
  }).join("");
  return `<section class="card fade-in" aria-labelledby="mk-slate-title">
    <div class="card-header"><div><h2 id="mk-slate-title">${escapeHtml(data.date)}: biggest disagreements first</h2>
      <p class="card-subtitle">Home win probability. A gap of ${Math.round(data.big_gap * 100)}+ points is marked. The bar leans toward the team the model likes more than the market does.</p></div></div>
    <div class="table-scroll"><table class="data-table mk-table"><thead><tr><th>Game</th><th class="num">Model</th><th class="num">Market</th><th>Gap</th>
      <th class="num">Home spread</th><th class="num">Moneylines (away / home)</th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="table-note">${escapeHtml(data.slate[0].provider || "Sportsbook")} lines via ESPN. The market number removes the bookmaker's margin; with no moneyline it comes from the spread.</p>
  </section>`;
}
